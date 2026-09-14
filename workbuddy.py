#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
WorkBuddy 专用签到 API 客户端（仅标准库，无第三方依赖）

原理（参考开源 workbuddy-checkin 协议，本项目内以 Python 实现）：
  - WorkBuddy 桌面端 v5.3.8+ 会把当前登录态明文写入
      ~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info
    其中 j["auth"]["accessToken"] 为 Bearer JWT（等同账号密码）。
  - 本模块读取该文件（仅内存使用）后，调用腾讯官方接口：
      POST https://copilot.tencent.com/billing/meter/checkin-status   查询今日是否已签到
      POST https://copilot.tencent.com/billing/meter/daily-checkin    执行签到（幂等）
    header: Authorization: Bearer <token>，body: {}

凭据安全：accessToken / refreshToken 只在函数内存中传递，
绝不写入日志、不回显到 stdout / UI，accounts.json 只保存非敏感快照字段。

用法：
    from workbuddy import WorkBuddyClient
    c = WorkBuddyClient(token="...", uid="...", domain="...")
    print(c.status())    # {"ok": True, "today_checked_in": True, "credit": ..., ...}
    print(c.checkin())   # {"ok": True, "already": False, "credit": 100, "streak_days": 5, ...}
"""

import json
import os
import urllib.error
import urllib.request
import uuid

BASE = "https://copilot.tencent.com"
TIMEOUT = 30
_UA = "WorkBuddyCheckin/1.0 (macOS; AutoCheck)"

# 新版明文登录态路径（WorkBuddy v5.3.8+，macOS）
AUTH_REL = os.path.join(
    "Library", "Application Support", "CodeBuddyExtension",
    "Data", "Public", "auth", "workbuddy-desktop.info",
)


def _loads_body(text: str) -> dict:
    """安全解析 JSON 响应；失败时降级为 raw 文本视图。"""
    try:
        body = json.loads(text or "{}")
        return body if isinstance(body, dict) else {"raw": str(body)[:200]}
    except json.JSONDecodeError:
        return {"raw": (text or "")[:200]}


def _dig(obj, key):
    """在可能被 data/result 信封包裹的响应里查找字段，兼容多层嵌套。"""
    if isinstance(obj, dict):
        if obj.get(key) is not None:
            return obj[key]
        for k in ("data", "result", "resp", "response"):
            if isinstance(obj.get(k), dict):
                v = _dig(obj[k], key)
                if v is not None:
                    return v
    return None


def _resp_msg(body) -> str:
    """从响应里提取人类可读的错误/提示信息（不含凭证）。"""
    if isinstance(body, dict):
        for k in ("message", "msg", "error", "reason"):
            if body.get(k):
                return str(body[k])
    return ""


def _is_unknown_tier(code: int, body) -> bool:
    """连登兑换档位参数被服务端判为非法档位的判定。"""
    if code != 400:
        return False
    msg = _resp_msg(body).lower()
    return "unknown tier" in msg or "invalid tier" in msg


def auth_file_path() -> str:
    return os.path.join(os.path.expanduser("~"), AUTH_REL)


class WorkBuddyError(RuntimeError):
    pass


def read_auth() -> dict:
    """读取本机 WorkBuddy 明文登录态，返回脱敏安全视图（不含 token）。

    返回 dict：
      {found, has_token, uid, nickname, uin, domain, enterprise_id,
       expires_at, reason}
    found=False 时 reason 说明失败原因（用于 UI 提示）。
    """
    out = {
        "found": False,
        "has_token": False,
        "uid": "",
        "nickname": "",
        "uin": "",
        "domain": "",
        "enterprise_id": "",
        "expires_at": "",
        "reason": "",
    }
    path = auth_file_path()
    if not os.path.exists(path):
        out["reason"] = "未找到 WorkBuddy 登录态文件，请先打开 WorkBuddy 桌面端登录"
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            j = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        out["reason"] = f"读取 WorkBuddy 登录态失败：{e}"
        return out

    auth = j.get("auth") or {}
    acct = j.get("account") or {}
    token = auth.get("accessToken") or ""
    out["found"] = True
    out["has_token"] = bool(isinstance(token, str) and token.strip())
    out["uid"] = acct.get("uid") or ""
    out["nickname"] = acct.get("nickname") or ""
    out["uin"] = acct.get("uin") or ""
    out["domain"] = auth.get("domain") or ""
    out["enterprise_id"] = acct.get("enterpriseId") or ""
    exp = auth.get("expiresAt") or auth.get("expiresIn")
    out["expires_at"] = str(exp) if exp is not None else ""
    if not out["has_token"]:
        out["reason"] = "本机 WorkBuddy 登录态中缺少 accessToken，请重新登录 WorkBuddy 桌面端"
    elif not out["uid"]:
        out["reason"] = "本机 WorkBuddy 登录态中缺少账号 UID"
    return out


def _read_token() -> str:
    """读取 accessToken（仅内存返回，供请求头使用；绝不落盘/打印）。"""
    out = read_auth()
    if not out["found"]:
        raise WorkBuddyError(out["reason"] or "未找到 WorkBuddy 登录态")
    if not out["has_token"]:
        raise WorkBuddyError(out.get("reason") or "WorkBuddy 登录态缺少 accessToken")
    path = auth_file_path()
    with open(path, "r", encoding="utf-8") as f:
        j = json.load(f)
    token = (j.get("auth") or {}).get("accessToken") or ""
    if not token:
        raise WorkBuddyError("WorkBuddy 登录态缺少 accessToken")
    return token


class WorkBuddyClient:
    """WorkBuddy 每日积分签到客户端。

    token 由调用方从本机登录态读取后传入（仅内存持有），绝不落盘。
    """

    def __init__(self, token: str, uid: str = "", domain: str = "",
                 enterprise_id: str = ""):
        self.token = (token or "").strip()
        self.uid = (uid or "").strip()
        self.domain = (domain or "").strip()
        self.enterprise_id = (enterprise_id or "").strip()
        if not self.token:
            raise WorkBuddyError("缺少 accessToken")

    def _post(self, path: str) -> tuple:
        headers = {
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _UA,
        }
        req = urllib.request.Request(
            BASE + path, data=b"{}", headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")

    @staticmethod
    def _parse(text: str, status: int) -> dict:
        try:
            body = json.loads(text)
            if not isinstance(body, dict):
                body = {"raw": text[:200]}
        except json.JSONDecodeError:
            body = {"raw": text[:200]}
        result = {
            "http": status,
            "code": body.get("code", -1),
            "message": body.get("message") or body.get("msg") or f"HTTP {status}",
            "ok": False,
            "raw": body,
        }
        return result

    def checkin(self) -> dict:
        """执行每日签到。幂等：code=10001（今日已签到）视为成功。

        返回 dict：{ok, http, code, message, already, credit, streak_days}
        """
        if not self.token:
            raise WorkBuddyError("缺少 accessToken")
        status, text = self._post("/billing/meter/daily-checkin")
        r = self._parse(text, status)
        if status in (401, 403):
            r["message"] = (
                f"令牌已过期或无权限（HTTP {status}），请打开 WorkBuddy 桌面端刷新登录态"
            )
            return r
        code = r["code"]
        data = r.get("raw", {}).get("data") if isinstance(r.get("raw"), dict) else {}
        if isinstance(data, dict):
            r["credit"] = data.get("credit", 0)
            r["streak_days"] = data.get("streak_days", 0)
        else:
            r["credit"], r["streak_days"] = 0, 0
        if status == 200 and code == 0:
            r["ok"] = True
            r["already"] = False
            return r
        if code == 10001:
            # 幂等：今日已签到，视为成功，不重复领取
            r["ok"] = True
            r["already"] = True
            r["message"] = "今日已签到（幂等）"
            return r
        return r

    def status(self) -> dict:
        """查询今日签到状态。

        返回 dict：{ok, http, code, message, today_checked_in, credit, streak_days}
        """
        if not self.token:
            raise WorkBuddyError("缺少 accessToken")
        status, text = self._post("/billing/meter/checkin-status")
        r = self._parse(text, status)
        if status in (401, 403):
            r["message"] = (
                f"令牌已过期或无权限（HTTP {status}），请打开 WorkBuddy 桌面端刷新登录态"
            )
            return r
        data = r.get("raw", {}).get("data") if isinstance(r.get("raw"), dict) else {}
        if isinstance(data, dict):
            r["today_checked_in"] = bool(data.get("today_checked_in", False))
            r["credit"] = data.get("credit", 0)
            r["streak_days"] = data.get("streak_days", 0)
        else:
            r["today_checked_in"] = False
            r["credit"], r["streak_days"] = 0, 0
        r["ok"] = (status == 200 and r["code"] == 0)
        return r

    # ------------------------------------------------------------------ #
    # 成长中心（参考开源 workbuddy-auto-signin 协议思路，纯标准库实现）     #
    # 六步：旅行礼物/派 Buddy → 领任务&领任务奖 → 断登补登 → 连登兑换 →       #
    # 抽盲盒 → 能量开 Buddy 盲盒。每步"先查状态、未达标不写"，写操作带        #
    # client_token 防重放。token 仅内存传递，不落盘、不回显。                #
    # ------------------------------------------------------------------ #
    GROWTH_BASE = "/v2/activity/growth"

    def _growth_request(self, path: str, method: str = "GET", payload: dict | None = None) -> tuple:
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/json",
            "User-Agent": _UA,
        }
        if self.uid:
            headers["X-User-Id"] = self.uid
        if self.enterprise_id:
            headers["X-Enterprise-Id"] = self.enterprise_id
            headers["X-Tenant-Id"] = self.enterprise_id
        if self.domain:
            headers["X-Domain"] = self.domain
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            BASE + self.GROWTH_BASE + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.status, _loads_body(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            return e.code, _loads_body(e.read().decode("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001
            return -1, {"error": str(e)}

    @staticmethod
    def _client_token(prefix: str = "u") -> str:
        """活动写接口要求的防重放 token（官方前端 uuid 前缀格式）。"""
        return "%s-%s" % (prefix, uuid.uuid4())

    def _growth_travel(self) -> tuple:
        """Buddy 旅行：State=arrived 领礼物；idle 且未达每日上限则派发。"""
        code, body = self._growth_request("/buddy/travel/status")
        if code in (401, 403):
            return False, "登录态已失效（HTTP %s）" % code, 0
        if code != 200:
            return False, "查旅行状态失败（HTTP %s）" % code, 0
        state = _dig(body, "state")
        daily_limit = bool(_dig(body, "daily_limit_reached"))
        if state == "arrived":
            rec = _dig(body, "record_id")
            c, cb = self._growth_request(
                "/buddy/travel/claim", "POST", {"record_id": rec}
            )
            if c in (401, 403):
                return False, "登录态已失效（HTTP %s）" % c, 0
            reward = _dig(cb, "reward_credit")
            if c == 200 and reward is not None:
                got = int(reward or 0)
                return True, "领旅行礼物 +%s 积分" % got, got
            return False, "领旅行礼物失败：%s（HTTP %s）" % (_resp_msg(cb), c), 0
        if state == "traveling":
            loc = (_dig(body, "location") or {}).get("name", "?")
            return True, "Buddy 旅行中（%s）" % loc, 0
        if state == "idle":
            if daily_limit:
                return True, "今日旅行名额已用完", 0
            cc, cbody = self._growth_request("/buddy/travel/config")
            if cc != 200:
                return False, "查旅行配置失败（HTTP %s）" % cc, 0
            locs = _dig(cbody, "locations")
            if not (isinstance(locs, list) and locs and isinstance(locs[0], dict)):
                return False, "无可用旅行目的地", 0
            loc = locs[0]
            d, db = self._growth_request(
                "/buddy/travel/depart", "POST", {"location_id": loc.get("id")}
            )
            if d in (401, 403):
                return False, "登录态已失效（HTTP %s）" % d, 0
            if d == 200:
                loc_r = _dig(db, "location") or {}
                lname = loc_r.get("name", "?")
                dur = loc_r.get("duration_hours", "?")
                return True, "派 Buddy 去%s（%s 小时后回）" % (lname, dur), 0
            return False, "派 Buddy 失败：%s（HTTP %s）" % (_resp_msg(db), d), 0
        return True, "", 0

    def _growth_tasks(self) -> tuple:
        """任务：未领取的任务先领取（进度从领取起计）；已完成且已领取的领奖。"""
        code, body = self._growth_request("/tasks")
        if code in (401, 403):
            return False, "登录态已失效（HTTP %s）" % code, 0
        if code != 200:
            return False, "查任务列表失败（HTTP %s）" % code, 0
        tasks = _dig(body, "tasks") or []
        msgs, got = [], 0
        for t in tasks:
            if not isinstance(t, dict):
                continue
            st = t.get("accept_status")
            if st == "claimed" or t.get("locked"):
                continue
            prog = t.get("progress") or {}
            target = int(prog.get("target") or 1) or 1
            done = int(prog.get("current") or 0) >= target
            if not done and st:
                continue  # 已领取、未完成——等用户完成，不重复领取
            claiming = bool(done and st)
            a, ab = self._growth_request(
                "/tasks/accept", "POST", {"task_code": t.get("task_code")}
            )
            if a in (401, 403):
                msgs.append("登录态已失效（HTTP %s）" % a)
                break
            title = t.get("title") or t.get("task_code") or "?"
            if a == 200:
                if claiming:
                    rc = int(_dig(ab, "credit") or t.get("reward_credit") or 0)
                    re_ = int(_dig(ab, "energy") or t.get("reward_energy") or 0)
                    got += rc
                    msgs.append("领任务奖「%s」+credit%s+energy%s" % (title, rc, re_))
                else:
                    msgs.append("领取任务「%s」（进度开始计）" % title)
            else:
                msgs.append("「%s」失败：%s（HTTP %s）" % (title, _resp_msg(ab), a))
        return True, "；".join(msgs) or "", got

    def _growth_makeup(self, max_per_run: int = 1) -> tuple:
        """断登自动补登：有补登卡且服务端给出可补日期时，每轮最多补 max_per_run 天。"""
        code, body = self._growth_request("/streak")
        if code in (401, 403):
            return False, "登录态已失效（HTTP %s）" % code, 0
        if code != 200:
            return False, "查连登状态失败（HTTP %s）" % code, 0
        cards_obj = _dig(body, "makeup_cards")
        cards = (int(cards_obj.get("balance") or 0)
                 if isinstance(cards_obj, dict) else int(cards_obj or 0))
        streak_obj = _dig(body, "streak") or {}
        dates = (streak_obj.get("makeup_dates")
                 if isinstance(streak_obj, dict) else None) or _dig(body, "makeup_dates") or []
        if cards <= 0 or not (isinstance(dates, list) and dates):
            return True, "", 0
        msgs, left = [], cards
        for d in dates[:max(min(cards, max_per_run), 1)]:
            u, ub = self._growth_request(
                "/makeup-cards/use", "POST",
                {"target_date": d, "client_token": self._client_token()},
            )
            if u in (401, 403):
                msgs.append("登录态已失效（HTTP %s）" % u)
                break
            if u == 200:
                left_obj = _dig(ub, "makeup_cards")
                left = (int(left_obj.get("balance") or left - 1)
                        if isinstance(left_obj, dict) else int(left_obj or left - 1))
                msgs.append("补登 %s（剩 %s 张卡）" % (d, left))
            else:
                msgs.append("补登 %s 失败：%s（HTTP %s）" % (d, _resp_msg(ub), u))
        return True, "；".join(msgs) or "", 0

    def _growth_redeem(self) -> tuple:
        """连登奖励兑换：入门 7 天 / 进阶 14 天 / 巅峰 28 天三档。"""
        code, body = self._growth_request("/redeem/summary")
        if code in (401, 403):
            return False, "登录态已失效（HTTP %s）" % code, 0
        if code != 200:
            return False, "查连登兑换失败（HTTP %s）" % code, 0
        got, msgs = 0, []
        for tier, label, days in (("starter", "入门", 7),
                                  ("advanced", "进阶", 14),
                                  ("legendary", "巅峰", 28)):
            status_ = _dig(body, tier + "_status")
            if not status_ or status_ in ("claimed", "locked"):
                continue
            r, rb = self._growth_request(
                "/redeem", "POST", {"tier": days, "client_token": self._client_token()}
            )
            if _is_unknown_tier(r, rb):  # 天数档位不被识别时退回档位名重试
                r, rb = self._growth_request(
                    "/redeem", "POST", {"tier": tier, "client_token": self._client_token()}
                )
            if r in (401, 403):
                msgs.append("登录态已失效（HTTP %s）" % r)
                break
            if r == 200:
                credit = int(_dig(rb, "credit") or 0)
                got += credit
                msgs.append("连登兑换「%s」+%s 积分" % (label, credit))
            else:
                msgs.append("连登兑换「%s」失败：%s（HTTP %s）" % (label, _resp_msg(rb), r))
        return True, "；".join(msgs) or "", got

    def _growth_lottery(self, max_draws: int = 1) -> tuple:
        """抽奖/开盲盒：有机会才抽，每轮最多 max_draws 次。"""
        code, body = self._growth_request("/lottery/chances")
        if code in (401, 403):
            return False, "登录态已失效（HTTP %s）" % code, 0
        if code != 200:
            return False, "查抽奖机会失败（HTTP %s）" % code, 0
        chances = int(_dig(body, "balance") or 0)
        if chances <= 0:
            return True, "", 0
        msgs = []
        for _ in range(min(chances, max_draws)):
            d, db = self._growth_request(
                "/lottery/draw", "POST", {"client_token": self._client_token()}
            )
            if d in (401, 403):
                msgs.append("登录态已失效（HTTP %s）" % d)
                break
            if d == 200:
                prize = _dig(db, "prize_name") or _dig(db, "prize") or "未知"
                msgs.append("开盲盒获得：%s" % prize)
            else:
                msgs.append("开盲盒失败：%s（HTTP %s）" % (_resp_msg(db), d))
        return True, "；".join(msgs) or "", 0

    def _growth_buddy_box(self) -> tuple:
        """能量开 Buddy 盲盒：能量攒够 cost 就开。"""
        code, body = self._growth_request("/buddy/quota")
        if code in (401, 403):
            return False, "登录态已失效（HTTP %s）" % code, 0
        if code != 200:
            return False, "查 Buddy 能量失败（HTTP %s）" % code, 0
        affordable = int(_dig(body, "affordable") or 0)
        if affordable <= 0:
            return True, "", 0
        max_open = int(_dig(body, "max_open_count") or 1) or 1
        count = min(affordable, max_open)
        o, ob = self._growth_request(
            "/buddy/open", "POST", {"count": count, "client_token": self._client_token()}
        )
        if o in (401, 403):
            return False, "登录态已失效（HTTP %s）" % o, 0
        if o == 200:
            name = _dig(ob, "buddy") or _dig(ob, "name") or _dig(ob, "buddies") or "新 Buddy"
            return True, "开 Buddy 盲盒 ×%s（%s）" % (count, name), 0
        return False, "开 Buddy 盲盒失败：%s（HTTP %s）" % (_resp_msg(ob), o), 0

    def growth(self, makeup_max: int = 1) -> dict:
        """成长中心全流程编排（先查后领、逐步独立、幂等、token 仅内存）。

        返回 dict：{ok, auth_lost, http, credited, energy, streak_days,
                    steps, summary}
        """
        steps, credited, auth_lost = [], 0, False

        def _exec(label: str, fn) -> None:
            nonlocal credited, auth_lost
            try:
                ok, msg, got = fn()
                if msg:
                    steps.append(msg)
                if got:
                    credited += int(got)
                if not ok and "登录态已失效" in msg:
                    auth_lost = True
            except Exception as e:  # noqa: BLE001
                steps.append("%s异常：%s: %s" % (label, type(e).__name__, e))

        _exec("旅行", self._growth_travel)
        _exec("任务", self._growth_tasks)
        _exec("补登", lambda: self._growth_makeup(max_per_run=makeup_max))
        _exec("连登兑换", self._growth_redeem)
        _exec("抽盲盒", self._growth_lottery)
        _exec("能量盲盒", self._growth_buddy_box)

        energy, streak_days = None, None
        st_code, st_body = self._growth_request("/energy")
        if st_code == 200:
            energy = _dig(st_body, "balance")
        sk_code, sk_body = self._growth_request("/streak")
        if sk_code == 200:
            s_obj = _dig(sk_body, "streak") or {}
            streak_days = (s_obj.get("days") if isinstance(s_obj, dict)
                           else _dig(sk_body, "days"))
            if streak_days is None:
                streak_days = _dig(sk_body, "days")

        tail = []
        if energy is not None:
            tail.append("能量 %s" % energy)
        if streak_days is not None:
            tail.append("连签 %s 天" % streak_days)
        if credited:
            tail.append("本次 +共 %s 积分" % credited)
        summary = "；".join(steps) if steps else "成长中心无可领取项"
        if tail:
            summary += "（" + "，".join(tail) + "）"
        return {
            "ok": not auth_lost,
            "auth_lost": auth_lost,
            "http": 0,
            "credited": credited,
            "energy": energy,
            "streak_days": streak_days,
            "steps": steps,
            "summary": summary,
        }


# ---------- CLI ----------
def _probe() -> str:
    """打印脱敏探测 JSON（不含 token），供 Swift 侧一键添加/刷新账号。"""
    out = read_auth()
    return json.dumps(out, ensure_ascii=False)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="WorkBuddy 登录态探测 / 签到自检")
    parser.add_argument("--probe", action="store_true", help="探测本机 WorkBuddy 登录态（输出脱敏 JSON）")
    parser.add_argument("--credits", action="store_true", help="查询签到状态/积分（自检）")
    parser.add_argument("--checkin", action="store_true", help="执行签到（自检）")
    parser.add_argument("--growth", action="store_true", help="执行成长中心六步（自检）")
    args = parser.parse_args()

    if args.probe:
        print(_probe())
        return

    token = _read_token()
    client = WorkBuddyClient(token)
    if args.growth:
        r = client.growth()
        print(r["summary"])
        if r.get("auth_lost"):
            print("成长中心中止：登录态已失效")
        return
    if args.credits:
        r = client.status()
        if r.get("ok"):
            print(
                f"今日已签到={r.get('today_checked_in')} 累计积分={r.get('credit')} "
                f"连续天数={r.get('streak_days')}"
            )
        else:
            print(f"查询失败：{r.get('message')}（HTTP {r.get('http')}）")
    elif args.checkin:
        r = client.checkin()
        if r.get("ok"):
            mark = "今日已签到，无需重复" if r.get("already") else "签到成功"
            print(f"{mark} credit={r.get('credit')} streak_days={r.get('streak_days')}")
        else:
            print(f"签到失败：{r.get('message')}（HTTP {r.get('http')}）")
    else:
        print("用法：--probe / --credits / --checkin")


if __name__ == "__main__":
    main()
