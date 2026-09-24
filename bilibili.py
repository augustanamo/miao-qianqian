#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bilibili 每日签到 + 权益领取客户端（纯标准库实现）。

**B站已没有"成长签到"这回事了。** 实测（2026-09）：
  - `/x/member/web/exp/reward`（成长中心每日任务）只能**读**当日状态，
    POST 过去是 `405`；每日登录经验改由平台自行结算，实测「访问首页 HTML /
    调 nav / 拉推荐流 / 补 buvid3」都不会把它置为已完成 → 纯 API 拿不到；
  - 直播签到 `DoSign` 返回 `code 1「签到活动已下线，无法使用」`；
  - **漫画签到 `manga.bilibili.com/twirp/activity.v1.Activity/ClockIn` 仍可用**
    （首次 `{"code":0}`，重复签到 `{"code":1,"msg":"不能重复签到~"}`）。

所以本客户端的**签到动作** = 漫画签到（奖励是漫读券）。

在此之上还有三个「领取」动作（2026-09 实测接口都活着，只需 Cookie + `bili_jct`
当 csrf）。本客户端**不做**"替你操作账号"的刷经验任务（观看 / 分享 / 投币），
虽然那三个接口同样存在：
  - 大会员每月福利 `POST /x/vip/privilege/receive?type=1|2`
    —— 先只读 `GET /x/vip/privilege/my` 看状态；**B币券只发给年度大会员**，
    月度大会员静默跳过（与官方社区实现同判定），**不记失败**、不每天刷屏。
  - 漫画会员权益 `POST manga.../twirp/user.v1.User/GetVipReward?reason_id=1`
    —— 任何大会员每月一次，给漫读券。
  - 银瓜子换硬币 `POST api.live.bilibili.com/xlive/revenue/v1/wallet/silver2coin`
    —— 先读 `getStatus`，`silver_2_coin_left <= 0` 就不动（天然幂等守卫）。

接口清单：
  - GET  https://api.bilibili.com/x/web-interface/nav
       验证登录态（SESSDATA），返回 uname / 硬币 / 等级 / vipType
  - GET  https://api.bilibili.com/x/member/web/exp/reward
       只读：当日成长任务完成态（login / watch / share / coins），仅作附加信息
  - POST https://manga.bilibili.com/twirp/activity.v1.Activity/ClockIn
       签到动作（幂等）
  - GET  https://api.bilibili.com/x/vip/privilege/my          只读：权益状态
  - POST https://api.bilibili.com/x/vip/privilege/receive     领每月福利
  - POST https://manga.bilibili.com/twirp/user.v1.User/GetVipReward  领漫画权益
  - GET  https://api.live.bilibili.com/xlive/revenue/v1/wallet/getStatus    只读
  - POST https://api.live.bilibili.com/xlive/revenue/v1/wallet/silver2coin  换硬币

凭据安全：cookie 等同账号密码，读取后仅内存使用，绝不写日志、
绝不回显到 stdout / UI。accounts.json 仅保存用户主动添加的 cookie
字段与 nickname/uid 非敏感快照。

用法：
    from bilibili import BilibiliClient, client_from_account
    c = client_from_account(acc)
    print(c.status())   # {ok, signed_today, nickname, credits, ...}
    print(c.checkin())  # {ok, already, credits, ...}
CLI：
    python3 bilibili.py --probe  --name <账号名>
    python3 bilibili.py --checkin --name <账号名>
    python3 bilibili.py --credits --name <账号名>
"""

import json
import random
import re
import string
import sys
import urllib.error
import urllib.parse
import urllib.request

import subtasks
from cookie_manager import CookieError, find_account, get_cookie, load_config

API = "https://api.bilibili.com"
LIVE_API = "https://api.live.bilibili.com"
MANGA_API = "https://manga.bilibili.com"
TIMEOUT = 30
# 注意：这里**不需要**任何"签到奖励"常量——B站的成长签到（每日登录经验）已经
# 不由 API 发放，实测靠调接口拿不到，别再造一个假的数字填进积分栏。
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 BiliCheckin/1.0"
REFERER = "https://www.bilibili.com/"
# 大会员相关接口的 Referer 必须在 big.bilibili.com 域下，否则容易被风控（-412）
BIG_REFERER = "https://big.bilibili.com/mobile/bigPoint/task"
LIVE_REFERER = "https://link.bilibili.com/"
MANGA_REFERER = "https://manga.bilibili.com/"

# nav.data.vipType：0 不是大会员 / 1 月度大会员 / 2 年度大会员
VIP_NONE, VIP_MONTHLY, VIP_ANNUAL = 0, 1, 2
# /x/vip/privilege/receive 的 type：1=年度大会员每月赠送的 B 币券，2=大会员福利/权益
VIP_PRIV_BCOIN, VIP_PRIV_BENEFIT = 1, 2
# 漫画会员权益的 reason_id（社区实现固定传 1）
MANGA_VIP_REASON = 1

# 权益子任务的固定顺序：桌面端任务表「今日任务」列按同一顺序渲染图标。
# key 是机器标识（Swift 靠它选图标），改动等于改契约——加了新 key 要同步
# native/Sources/AutoCheck 里的符号映射表，否则会退化成默认图标。
PERK_ORDER = (("manga_vip", "漫画权益"),
              ("silver2coin", "银瓜子换硬币"),
              ("vip_bcoin", "大会员B币券"),
              ("vip_benefit", "大会员福利"))


class BilibiliError(RuntimeError):
    pass


def _read_body(resp) -> str:
    return resp.read().decode("utf-8", errors="replace")


def _parse_json(text: str) -> dict:
    try:
        body = json.loads(text or "{}")
        return body if isinstance(body, dict) else {"code": -1, "message": "非 JSON 响应"}
    except json.JSONDecodeError:
        return {"code": -1, "message": "响应解析失败", "raw": (text or "")[:200]}


def _origin_of(referer: str) -> str:
    """从 Referer 推出 Origin（B站部分接口对 Origin 敏感）。"""
    m = re.match(r"(https?://[^/]+)", referer or "")
    return m.group(1) if m else ""


def _cookie_field(cookie: str, name: str) -> str:
    """按名字从 Cookie 串里取一个字段。

    只用来取 `bili_jct`（POST 类接口要把它当 csrf 传）——取不到就不能做任何
    领取动作，必须明确告知用户，而不是让请求静默失败返回一个看不懂的错误码。
    """
    for part in (cookie or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v.strip()
    return ""


def _looks_already(msg: str) -> bool:
    """平台用文案表达"本期已领"（各家措辞不同，别只匹配一种）。"""
    m = (msg or "").lower()
    return any(k in m for k in ("已领", "重复", "已经领取", "already", "duplicate"))


def client_from_account(acc: dict) -> "BilibiliClient":
    """从账号条目构造客户端；cookie 缺失时抛 CookieError。"""
    from cookie_manager import require_cookie
    cookie = require_cookie(acc, "bilibili")
    return BilibiliClient(cookie)


class BilibiliClient:
    """Bilibili 每日签到 + 权益领取客户端。cookie 由调用方传入（仅内存持有）。"""

    def __init__(self, cookie: str):
        self.cookie = (cookie or "").strip()
        if not self.cookie:
            raise CookieError("缺少 Bilibili cookie（SESSDATA）")
        # bili_jct 是 cookie 里的 csrf token，所有 POST 类接口（领福利/换硬币）都要它。
        self.csrf = _cookie_field(self.cookie, "bili_jct")

    # ---- 通用请求 ----

    def _call(self, method: str, url: str, params: dict | None = None,
              body: dict | None = None, referer: str = REFERER) -> tuple:
        """发一个请求，返回 (http, body_dict)。

        params 拼到 query，body 以表单形式发。Origin 由 Referer 推出——
        B站的风控会看这一对，缺了容易回 `-412`（实测 `exp/reward` 会偶发 412，
        重打即恢复，属于对方风控抖动，不是参数错）。
        """
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        data = None
        if method == "POST":
            data = urllib.parse.urlencode(body).encode() if body else b""
        headers = {
            "User-Agent": UA,
            "Referer": referer,
            "Origin": _origin_of(referer),
            "Cookie": self.cookie,
            "Accept": "application/json, text/plain, */*",
        }
        if method == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.status, _parse_json(_read_body(resp))
        except urllib.error.HTTPError as e:
            return e.code, _parse_json(e.read().decode("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001
            return -1, {"code": -1, "message": f"网络错误：{type(e).__name__}"}

    def _get(self, path: str, params: dict | None = None) -> tuple:
        return self._call("GET", API + path, params=params)

    # ---- 状态查询（无副作用） ----
    def nav(self) -> dict:
        """查询登录态与账户信息。

        返回 {ok, is_login, nickname, uid, money, level, vip_status, vip_type, message, http}
        `vip_type` 0/1/2 = 非会员 / 月度大会员 / 年度大会员 —— 决定能不能领 B 币券。
        """
        http, body = self._get("/x/web-interface/nav")
        r = {
            "http": http,
            "code": body.get("code", -1),
            "message": body.get("message") or f"HTTP {http}",
            "ok": False,
            "is_login": False,
            "nickname": "",
            "uid": "",
            "money": 0,
            "level": 0,
            "vip_status": 0,
            "vip_type": VIP_NONE,
        }
        if http in (401, 403):
            r["message"] = f"Cookie 被拒绝（HTTP {http}），请重新获取 Bilibili cookie"
            return r
        data = body.get("data") or {}
        if http == 200 and body.get("code") == 0 and data.get("isLogin"):
            r.update(
                ok=True,
                is_login=True,
                nickname=data.get("uname") or "",
                uid=str(data.get("mid") or ""),
                money=data.get("money") or 0,
                level=(data.get("level_info") or {}).get("current_level") or 0,
                vip_status=int(data.get("vipStatus") or 0),
                vip_type=int(data.get("vipType") or 0),
            )
            return r
        if body.get("code") == -101:
            r["message"] = "Cookie 已失效（未登录），请重新获取 Bilibili cookie"
        return r

    def _reward(self) -> tuple:
        """拉取「每日任务」状态（成长中心的领取入口）。返回 (http, body, data)。

        注意：这是**只读**接口。实测 2026-09 用 POST 打过去是 `405 Method Not Allowed`，
        即它只能查询，不能"领取"——它从来不是签到动作。
        """
        http, body = self._get("/x/member/web/exp/reward")
        data = body.get("data")
        return http, body, (data if isinstance(data, dict) else {})

    # B站唯一的每日签到入口（漫画客户端）。实测 2026-09 仍可用。
    MANGA_CLOCKIN = MANGA_API + "/twirp/activity.v1.Activity/ClockIn"
    # 漫画会员权益（每月一次，给漫读券）
    MANGA_VIP_REWARD = MANGA_API + "/twirp/user.v1.User/GetVipReward"

    def _manga_clockin(self) -> tuple:
        """漫画客户端签到。返回 (http, body)。

        首次签到回 `{"code":0}`；重复签到回 `{"code":1,"msg":"不能重复签到~"}`
        （凭据无效时回 `{"code":"internal","msg":"请求错误"}`，注意 code 有时是字符串）。
        """
        return self._call("POST", self.MANGA_CLOCKIN, body={"platform": "android"},
                          referer=MANGA_REFERER)

    # ---- 权益领取（都是幂等的：要么平台自己拦，要么我们先读再决定） ----

    def vip_privilege(self) -> dict:
        """GET /x/vip/privilege/my —— 只读，大会员每月福利的状态。

        返回 {ok, is_vip, is_annual, items, next_days, message, http}
        `items` 是原始列表（type/state/next_receive_days 等），仅供展示；
        **不拿它当"能不能领"的判据**——那由平台在 receive 时自己回答，
        我们只按官方同款规则（是否年度大会员）决定要不要发起。
        """
        http, body = self._call("GET", API + "/x/vip/privilege/my", referer=BIG_REFERER)
        r = {"ok": False, "is_vip": False, "is_annual": False, "items": [],
             "next_days": None, "http": http, "code": body.get("code"),
             "message": body.get("message") or f"HTTP {http}"}
        data = body.get("data")
        if http == 200 and body.get("code") == 0 and isinstance(data, dict):
            items = data.get("list") if isinstance(data.get("list"), list) else []
            r.update(ok=True, is_vip=bool(data.get("is_vip")), items=items,
                     is_annual=bool(data.get("vip_is_annual")))
            # next_receive_days 越小越接近可领；-1 表示该项对本账号不适用
            days = [it.get("next_receive_days") for it in items
                    if isinstance(it, dict) and isinstance(it.get("next_receive_days"), int)
                    and it.get("next_receive_days") >= 0]
            r["next_days"] = min(days) if days else None
        return r

    def receive_vip_privilege(self, ptype: int) -> dict:
        """POST /x/vip/privilege/receive?type=&csrf= —— 领大会员每月福利。

        `type` 1=年度大会员每月赠送的 B 币券，2=大会员福利/权益。
        幂等：已领过时平台会明确回报（文案含"已领/重复"），我们据此判 already。
        """
        return self._receive(f"每月福利(type={ptype})",
                             lambda: self._call(
                                 "POST", API + "/x/vip/privilege/receive",
                                 params={"type": ptype, "csrf": self.csrf},
                                 referer=BIG_REFERER))

    def receive_manga_vip_reward(self, reason_id: int = MANGA_VIP_REASON) -> dict:
        """POST manga.../twirp/user.v1.User/GetVipReward?reason_id= —— 领漫画会员权益。

        任何大会员每月一次，奖励是**漫读券**（`data.amount` 是张数）。
        """
        return self._receive("漫画权益",
                             lambda: self._call(
                                 "POST", self.MANGA_VIP_REWARD,
                                 params={"reason_id": reason_id, "platform": "android"},
                                 referer=MANGA_REFERER))

    def _receive(self, label: str, call) -> dict:
        """把"领取"类接口的响应归一成 {ok, already, amount, code, message, http}。

        判定顺序与前端一致：`code == 0` 成功 → 文案含"已领/重复"算幂等成功 →
        其余都是未领到（把原文带出去，便于人工判断是"无此权益"还是"接口变了"）。
        """
        if not self.csrf:
            return {"ok": False, "already": False, "amount": None, "http": 0, "code": -1,
                    "message": "Cookie 里没有 bili_jct（csrf），无法领取"}
        http, body = call()
        code = body.get("code")
        msg = str(body.get("message") or body.get("msg") or "").strip()
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        amount = data.get("amount")
        if isinstance(amount, (int, float)) and amount:
            amount = float(amount)
        else:
            amount = None
        r = {"ok": False, "already": False, "amount": amount, "http": http,
             "code": code, "message": msg}
        # twirp 族（漫画）成功时 code 是数字 0；失败可能是字符串 "internal"
        if http == 200 and str(code) == "0":
            r["ok"] = True
            return r
        if _looks_already(msg):
            r.update(ok=True, already=True)
            return r
        if not msg:
            r["message"] = f"HTTP {http}（code {code}）"
        return r

    def live_wallet(self) -> dict:
        """GET /xlive/revenue/v1/wallet/getStatus —— 只读。

        返回 {ok, silver, coin, silver_2_coin_left, message, http}
        `silver_2_coin_left` 是**今日剩余兑换次数** —— 它是银瓜子换硬币的天然
        幂等守卫（<=0 说明今天已换过，不必再请求）。
        """
        http, body = self._call("GET", LIVE_API + "/xlive/revenue/v1/wallet/getStatus",
                                referer=LIVE_REFERER)
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        r = {"ok": False, "silver": None, "coin": None, "silver_2_coin_left": None,
             "http": http, "code": body.get("code"),
             "message": body.get("message") or f"HTTP {http}"}
        if http == 200 and body.get("code") == 0 and data:
            r.update(ok=True,
                     silver=_num(data.get("silver")),
                     coin=_num(data.get("coin")),
                     silver_2_coin_left=_num(data.get("silver_2_coin_left")))
        return r

    def exchange_silver2coin(self) -> dict:
        """POST /xlive/revenue/v1/wallet/silver2coin —— 银瓜子换硬币。

        需要 csrf（同时给 `csrf` 与 `csrf_token` 两个字段名——社区实现两个都发）
        和一个随机 `visit_id`。返回 {ok, coin, silver, code, message, http}。
        **调用前先看 `live_wallet()["silver_2_coin_left"]`**，别盲目重复换。
        """
        if not self.csrf:
            return {"ok": False, "coin": None, "silver": None, "http": 0, "code": -1,
                    "message": "Cookie 里没有 bili_jct（csrf），无法兑换"}
        http, body = self._call(
            "POST", LIVE_API + "/xlive/revenue/v1/wallet/silver2coin",
            body={"csrf": self.csrf, "csrf_token": self.csrf,
                  "visit_id": _visit_id()},
            referer=LIVE_REFERER)
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        msg = str(body.get("message") or body.get("msg") or "").strip()
        r = {"ok": False, "coin": _num(data.get("coin")), "silver": _num(data.get("silver")),
             "http": http, "code": body.get("code"), "message": msg}
        if http == 200 and body.get("code") == 0:
            r["ok"] = True
        elif not msg:
            r["message"] = f"HTTP {http}（code {body.get('code')}）"
        return r

    def checkin(self) -> dict:
        """每日签到 + 权益领取（全部幂等）。

        **B站的"签到"已经改名换姓了。** 实测（2026-09）三件事：
          · 成长中心 `/x/member/web/exp/reward` 只能读当日任务态（POST → 405），
            每日登录经验改由平台自行结算 —— 实测「访问首页 / 调 nav / 拉推荐流 /
            补 buvid3」都不会把它置为已完成，纯 API 拿不到；
          · 直播签到 DoSign 返回 `code 1「签到活动已下线，无法使用」`；
          · 漫画签到 ClockIn 仍然可用（首次 code 0，重复回「不能重复签到~」）。
        所以本客户端的"签到"= **漫画签到**（奖励漫读券），成长任务只作为附加信息展示。

        领取动作（不影响签到成败，失败只写在文案里）：
          · 大会员每月福利：**只对年度大会员发起**（B币券是年度专属；社区实现同此
            判定）。月度大会员静默跳过——不是失败，也不该每天刷屏。
          · 漫画会员权益：任何大会员每月一次。
          · 银瓜子换硬币：先读剩余次数，>0 才换。

        返回 {ok, already, credits, message, http, code, restricted, daily, tasks}
        - already=True：今日已签过（平台幂等），本次未重复领取；
        - restricted=True：平台侧不可用，不是 cookie 有问题；
        - tasks：子任务清单（漫画签到 / 漫画权益 / 银瓜子换硬币 / 大会员福利…），
          桌面端任务表按顺序渲染成小图标，见 subtasks.py。
        """
        r = {
            "http": 0,
            "code": -1,
            "message": "",
            "ok": False,
            "already": False,
            "credits": 0.0,
            "restricted": False,
            "daily": {},
            # 子任务清单（桌面端任务表右侧图标的唯一来源）。顺序即展示顺序。
            "tasks": [],
        }

        # 1) 先确认登录态。cookie 失效必须报"失败"（要用户重登），
        #    绝不能和"平台改版/活动下线"混为一谈。
        n = self.nav()
        if not n.get("ok"):
            # 登录态都没过，一个动作都没做 → 不产出子任务。空列表的意思是
            # "这次不知道各子任务的状态"，不等于"都失败了"，别让界面摆一排红叉。
            r.update(http=n["http"], code=n["code"], message=n["message"])
            return r

        # 2) 成长任务状态：只读，仅用于文案（不再作为成功与否的依据）
        http, body, data = self._reward()
        if http == 200 and body.get("code") == 0 and data:
            coins_exp = data.get("coins")
            r["daily"] = {
                "login": bool(data.get("login")),
                "watch": bool(data.get("watch")),
                "share": bool(data.get("share")),
                "coins": int(coins_exp) if isinstance(coins_exp, (int, float)) else 0,
            }
        tail = self._daily_tail(r["daily"])

        # 3) 漫画签到（唯一的签到动作）
        m_http, m_body = self._manga_clockin()
        m_code = str(m_body.get("code"))
        m_msg = str(m_body.get("msg") or "").strip()
        r.update(http=m_http, code=m_body.get("code", -1))
        if m_http in (401, 403):
            r["message"] = f"Cookie 被拒绝（HTTP {m_http}），请重新获取 Bilibili cookie"
            # 签到这一个动作确实尝试过且被拒 → 它记失败；权益那几项根本没走到，
            # 不产出（"没走到"和"失败"是两回事）。
            r["tasks"] = [subtasks.task("manga_signin", "漫画签到", subtasks.FAIL, r["message"])]
            return r

        ok = already = False
        if m_code == "0":
            ok, base = True, "漫画签到成功（奖励漫读券）"
            sign_state, sign_detail = subtasks.DONE, "签到成功（奖励漫读券）"
        elif "重复" in m_msg or "duplicate" in m_msg.lower():
            ok = already = True
            base = "今日已签到（漫画签到，未重复领取）"
            # 已签到是**幂等命中**，不是"这次做了什么"——所以记 idle 而非 done，
            # 否则每天第二次运行还会多出一个绿图标。
            sign_state, sign_detail = subtasks.IDLE, "今日已签到，未重复领取"
        else:
            base = ""
            sign_state, sign_detail = subtasks.FAIL, (
                f"HTTP {m_http}，code {m_body.get('code')}：{m_msg or '未知'}")
        sign_task = subtasks.task("manga_signin", "漫画签到", sign_state, sign_detail)

        # 4) 权益领取。签到本身成不成，都照领——它们是独立动作，
        #    所以这里**不改写 ok / restricted**，只把结果拼进文案与子任务清单。
        perk_notes, perk_tasks = self._claim_perks(n.get("vip_type") or VIP_NONE)
        perk_s = ("；" + "；".join(perk_notes)) if perk_notes else ""
        r["tasks"] = [sign_task] + perk_tasks

        if ok:
            r.update(ok=True, already=already, message=base + tail + perk_s)
            return r

        # 5) 漫画接口不可用：报"受限"，并把成长任务状态带上，便于人工判断
        r["restricted"] = True
        r["message"] = (f"B站签到接口不可用（HTTP {m_http}，code {m_body.get('code')}"
                        f"：{m_msg or '未知'}）；B站已取消成长中心签到，直播签到也已下线"
                        + tail + perk_s)
        return r

    # ---- 权益领取的具体动作 ----

    def _claim_perks(self, vip_type: int) -> tuple:
        """依次领取权益，返回 (给日志的短句列表, 子任务清单)。

        原则：**收益类动作不决定签到成败**，所以这里无论成败都只产出文案。
        另一条同样重要：**安静是默认**。「本期已领取」「今天已经换过」都是正常稳态，
        不该每天在日志里留一句——那正是当初把京东停用掉的原因。只有两种值得说：
          · **本次真的领到了**（有收益，要留痕）；
          · **没领到**（把原因带出去，便于人工判断是"无此权益"还是"接口变了"）。

        子任务清单与日志短句是**两个口径**，别互相顶替：日志回答"今天值不值得说
        一句"，图标回答"这件事现在是什么状态"。所以「已领取」在日志里是静默的
        （短句为空），在子任务里却是 idle（灰勾）——两边都要对。
        顺序由 PERK_ORDER 固定，桌面端按同一顺序渲染图标，两边不会错位。
        """
        notes, tasks = [], []

        if not self.csrf:
            # 缺 csrf 只影响领取，签到照常。日志只说一句，但每一项都记失败：
            # 它们是"这次没做成、且重登一次就能修好"的动作。
            for key, label in PERK_ORDER:
                tasks.append(subtasks.task(key, label, subtasks.FAIL, "Cookie 缺 bili_jct，无法领取"))
            return (["权益未领：Cookie 缺 bili_jct（请在浏览器登录时抓取完整凭据）"], tasks)

        annual = vip_type == VIP_ANNUAL
        member = vip_type in (VIP_MONTHLY, VIP_ANNUAL)

        # (1) 漫画会员权益：任何大会员每月一次。
        #     没有只读接口可查，只能靠平台的"已领过"回复判已领。注意 B站把这句写成了
        #     「已经领取过该奖励**或者未达到领取条件**哦~」——两种情形合并了；调用前
        #     已确认是大会员，所以按"已领取"理解（社区实现也这么做）。
        if member:
            n, t = self._perk_task("manga_vip", "漫画权益",
                                   self.receive_manga_vip_reward(), unit=" 张漫读券")
            notes.append(n); tasks.append(t)
        else:
            tasks.append(subtasks.task("manga_vip", "漫画权益", subtasks.NA, "不适用：需要大会员"))

        # (2) 银瓜子换硬币：先读剩余次数，>0 才动（这就是幂等守卫）
        n, t = self._silver_task()
        notes.append(n); tasks.append(t)

        # (3) 大会员每月福利：B币券只发给年度大会员。其它情况记 na，界面不渲染——
        #     让月度会员长年看着一个灰的券图标，只会以为是自己漏领了。
        if annual:
            for ptype, key, label in ((VIP_PRIV_BCOIN, "vip_bcoin", "大会员B币券"),
                                      (VIP_PRIV_BENEFIT, "vip_benefit", "大会员福利")):
                n, t = self._perk_task(key, label, self.receive_vip_privilege(ptype))
                notes.append(n); tasks.append(t)
        else:
            for key, label in (("vip_bcoin", "大会员B币券"), ("vip_benefit", "大会员福利")):
                tasks.append(subtasks.task(key, label, subtasks.NA, "不适用：仅年度大会员"))

        return [n for n in notes if n], tasks

    def _silver_task(self) -> tuple:
        """银瓜子换硬币：返回 (日志短句可能为空串, 子任务)。

        三种"今天不用动"要分开写清，别看结果都是灰勾：今天已换过 / 平台没给剩余
        次数 / 查钱包失败。最后一种必须报出来——子任务图标是红的，日志里就得有
        对应的一句，否则界面和日志互相矛盾。
        """
        w = self.live_wallet()
        if not w.get("ok"):
            reason = w.get("message") or "查直播钱包失败"
            return (f"银瓜子换硬币：未能确认（{reason}）",
                    subtasks.task("silver2coin", "银瓜子换硬币", subtasks.FAIL, reason))
        left = w.get("silver_2_coin_left")
        if not isinstance(left, (int, float)):
            return ("", subtasks.task("silver2coin", "银瓜子换硬币", subtasks.IDLE, "平台未给出今日剩余次数"))
        if left <= 0:
            return ("", subtasks.task("silver2coin", "银瓜子换硬币", subtasks.IDLE, "今日已兑换"))
        ex = self.exchange_silver2coin()
        if ex.get("ok"):
            silver = ex.get("silver")
            sil_s = f"银瓜子余 {silver:g}" if isinstance(silver, (int, float)) else ""
            note = f"银瓜子换硬币：成功{'（' + sil_s + '）' if sil_s else ''}"
            return (note, subtasks.task("silver2coin", "银瓜子换硬币", subtasks.DONE, sil_s or "兑换成功"))
        reason = ex.get("message") or "未知原因"
        return (f"银瓜子换硬币：未换成（{reason}）",
                subtasks.task("silver2coin", "银瓜子换硬币", subtasks.FAIL, reason))

    @staticmethod
    def _perk_task(key: str, label: str, res: dict, unit: str = "") -> tuple:
        """把一次领取的结果同时写成 (日志短句, 子任务)。

        「已领取」是唯一一处两个口径分开的地方：短句给空串（安静是默认），子任务
        记 idle（图标要在，只是灰的）。顺带一个措辞纪律：**不写"本次已领"**——
        「已领取」只说明这次没什么可报的，说明不了是本工具领的，用户也可能自己
        在 App 里领过，别揽功。
        """
        if res.get("already"):
            return "", subtasks.task(key, label, subtasks.IDLE, "本期已领取")
        if res.get("ok"):
            amt = res.get("amount")
            if isinstance(amt, (int, float)) and amt:
                got = f"领取成功 +{amt:g}{unit}"
                return f"{label}：{got}", subtasks.task(key, label, subtasks.DONE, got)
            return f"{label}：领取成功", subtasks.task(key, label, subtasks.DONE, "领取成功")
        reason = res.get("message") or "未知原因"
        return f"{label}：未领到（{reason}）", subtasks.task(key, label, subtasks.FAIL, reason)

    @staticmethod
    def _daily_tail(daily: dict) -> str:
        """当日任务完成情况（简短，附在签到结果后面）。

        措辞要点：这些 ✗ 只是"本工具没帮你做"，**不是签到失败**，所以前缀必须
        写明，否则 `登录✗ 观看✗ 分享✗` 会被读成失败。

        注意别写成"接口无法代办"——实测（2026-09，假凭据 + 对照实验）观看
        `/x/click-interface/web/heartbeat`、分享 `/x/web-interface/share/add`、
        投币 `/x/web-interface/coin/add` 三条接口都活着，BiliBiliToolPro 之类的
        工具正是靠它们刷满 65 经验的。本项目目前只做签到与领取，不做这些"替你操作
        账号"的任务，所以文案说的是"本工具未代办"，而不是"平台没有接口"。
        「登录」那条例外：每日登录经验由平台自行结算，任何请求都不会把它置为
        已完成，确实拿不到。
        """
        if not daily:
            return ""
        marks = []
        for key, label in (("login", "登录"), ("watch", "观看"), ("share", "分享")):
            marks.append(f"{label}{'✓' if daily.get(key) else '✗'}")
        coins = daily.get("coins") or 0
        marks.append(f"投币 {coins}/50 经验")
        return "；B站每日任务（本工具未代办，仅「登录」由平台自行结算）：" + " ".join(marks)

    def status(self) -> dict:
        """查询今日签到状态（无副作用）。返回 {ok, signed_today, nickname, credits, ...}"""
        n = self.nav()
        r = {
            "http": n["http"],
            "code": n["code"],
            "message": n["message"],
            "ok": n["ok"],
            "signed_today": None,  # 无公开只读接口，签到态需调 checkin 探测
            "nickname": n["nickname"],
            "uid": n["uid"],
            "credits": n["money"],
            "summary": "",
        }
        if n["ok"]:
            bits = [f"硬币 {n['money']:g}", f"等级 {n['level']}"]
            vip = _vip_label(n.get("vip_type"), n.get("vip_status"))
            if vip:
                bits.append(vip)
            r["summary"] = "，".join(bits)
        return r


def _num(v):
    """接口里的数字可能是字符串，统一成 int/float；拿不准就返回 None。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            return float(v) if "." in v else int(v)
        except ValueError:
            return None
    return None


def _vip_label(vip_type, vip_status) -> str:
    """把 nav 的 vipType 翻成人话。非会员返回空串（不展示）。"""
    if not vip_status:
        return ""
    return {VIP_MONTHLY: "月度大会员", VIP_ANNUAL: "年度大会员"}.get(vip_type, "大会员")


def _visit_id() -> str:
    """直播接口要求的随机 visit_id（形如 `8u0w3cesz1o0`，社区实现同款）。"""
    body = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(10))
    return f"{random.randint(1, 9)}{body}0"


# ---------- CLI ----------
def _load_acc(name: str) -> dict:
    acc = find_account(load_config(), name)
    if acc is None:
        sys.stderr.write(f"未找到账号：{name}\n")
        sys.exit(2)
    return acc


def _probe(acc: dict) -> dict:
    """脱敏探测 JSON（不含 cookie 明文），供 Swift 侧一键添加/刷新账号。"""
    try:
        cookie = get_cookie(acc, "bilibili")
        has = bool(cookie)
        reason = "" if has else "账号未配置 cookie，请粘贴 SESSDATA 后重试"
        if has:
            try:
                nav = BilibiliClient(cookie).nav()
                if nav.get("ok"):
                    return {
                        "found": True,
                        "healthy": True,
                        "has_cookie": True,
                        "nickname": nav.get("nickname", ""),
                        "uid": nav.get("uid", ""),
                        "credits": nav.get("money", 0),
                        "message": "",
                        "reason": "",
                    }
                reason = nav.get("message", "登录态验证失败")
            except Exception as e:  # noqa: BLE001
                reason = f"登录态验证失败：{type(e).__name__}"
        return {
            "found": has,
            "healthy": False,
            "has_cookie": has,
            "nickname": "",
            "uid": "",
            "credits": 0,
            "message": reason,
            "reason": reason,
        }
    except Exception as e:  # noqa: BLE001
        return {"found": False, "healthy": False, "has_cookie": False, "nickname": "", "uid": "",
                "credits": 0, "message": f"读取失败：{type(e).__name__}",
                "reason": f"读取失败：{type(e).__name__}"}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Bilibili 每日签到客户端")
    parser.add_argument("--probe", action="store_true", help="探测账号登录态（输出脱敏 JSON）")
    parser.add_argument("--checkin", action="store_true", help="执行签到")
    parser.add_argument("--credits", action="store_true", help="查询账号状态/积分")
    parser.add_argument("--name", required=True, help="accounts.json 中的账号名")
    args = parser.parse_args()

    acc = _load_acc(args.name)
    if args.probe:
        print(json.dumps(_probe(acc), ensure_ascii=False))
        return

    try:
        client = client_from_account(acc)
    except CookieError as e:
        print(f"签到失败：{e}")
        sys.exit(1)

    if args.credits:
        r = client.status()
        if r.get("ok"):
            print(f"今日状态查询成功，{r.get('summary')}（昵称 {r.get('nickname')}）")
        else:
            print(f"查询失败：{r.get('message')}（HTTP {r.get('http')}）")
            sys.exit(1)
    elif args.checkin:
        r = client.checkin()
        if r.get("ok"):
            # 直接把 message 打出来——它已经包含"今日已签到"这句，后面还挂着
            # 当日任务状态与**本次领取到的权益**。原来在 already 时只打一句固定的
            # "今日已签到，无需重复"，会把领取结果整段吞掉（同日第二次运行就什么都
            # 看不到）。漫画签到的奖励是**漫读券不是硬币**，credits 恒 0，所以也不能
            # 套「本次获得 N 硬币」的模板——那会打印「获得 0 硬币」。
            print(r.get("message") or "签到成功")
        else:
            # 受限/失败都由 message 自己说清楚（restricted=True 时是"平台侧不可用"），
            # 这里不套「签到失败：」前缀——checkin.py 的 run_cookie_acc 会统一加，
            # 重复加会出现「签到失败：签到失败：」。
            print(r.get("message") or "签到失败")
            sys.exit(1)
    else:
        print("用法：--probe / --checkin / --credits（均需 --name）")


if __name__ == "__main__":
    main()
