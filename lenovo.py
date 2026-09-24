#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""联想智选每日签到客户端（纯标准库实现）。

接口思路参考开源项目协议（lenovo-sign / mclub 签到），代码为本项目内
独立实现，不照搬任何仓库源码：
  - GET https://mclub.lenovo.com.cn/signlist/
       签到页（需登录 cookie），页面内嵌 `$CONFIG`（逐行 `$CONFIG.key = 值`），
       含 token（Laravel CSRF 令牌）、signPath（提交路径，实测 "/signadd"）、
       signState（"1" = 今日已签，未签时为空串）、lenovoId、userAgent
  - POST https://mclub.lenovo.com.cn/signadd
       执行签到（当天已签时不调用，幂等）。**必须与上一步共用同一个 cookie jar**：
       页面响应下发 wap_session，CSRF 校验要求「页面 _token」与「同会话 session
       cookie」配对。且该接口有服务端抖动（约 20% 通过率），见 checkin()。
  - POST https://reg.lenovo.com.cn/auth/v2/doLogin
       账密自动登录（password 做 Base64），用于"账密登录抓 cookie"模式

凭据安全：cookie / 登录密码均等同账号密码，读取后仅内存使用，
绝不写日志、绝不回显到 stdout / UI。accounts.json 仅保存用户主动
添加的 cookie 字段与 nickname/uid 非敏感快照。

用法：
    from lenovo import LenovoClient, client_from_account
    c = client_from_account(acc)
    print(c.status())   # {ok, signed_today, nickname, credits, ...}
    print(c.checkin())  # {ok, already, credits, message, ...}
CLI：
    python3 lenovo.py --probe  --name <账号名>
    python3 lenovo.py --checkin --name <账号名>
    python3 lenovo.py --credits --name <账号名>
    python3 lenovo.py --login-account <手机号> --login-password <密码> --name <账号名>
"""

import base64
import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from cookie_manager import CookieError, find_account, get_cookie, load_config, set_cookie

MCLUB = "https://mclub.lenovo.com.cn"
SIGNLIST = MCLUB + "/signlist/"
SIGNADD = MCLUB + "/signadd"
LOGIN = "https://reg.lenovo.com.cn/auth/v2/doLogin"
TIMEOUT = 30

# /signadd 的 CSRF 抖动重试上限。实测单次通过率约 20%（真实浏览器同样如此，
# 见 checkin() 的说明），20 次可把失败概率压到 ~1%。已签到时不会走这条循环。
SIGN_ATTEMPTS = 20

# 联想 App（mclub 手机站）UA，降低被风控识别的概率
UA = ("Mozilla/5.0 (Linux; Android 12; Lenovo L78051 Build/SKQ1.220119.001; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/112.0.5615.136 "
      "Mobile Safari/537.36 LenovoApp/11.2.1")


class LenovoError(RuntimeError):
    pass


def _post_form(url: str, data: dict, cookie: str = "") -> tuple:
    """application/x-www-form-urlencoded POST，返回 (status, text)。"""
    body = urllib.parse.urlencode(data).encode("utf-8")
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": SIGNLIST,
    }
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return -1, json.dumps({"message": f"网络错误：{type(e).__name__}"})


def _get(url: str, cookie: str = "") -> tuple:
    headers = {
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": MCLUB,
    }
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return -1, ""


# ---------- 带 cookie jar 的会话 ----------
# 为什么必须有：mclub 是 Laravel 应用，签到页响应会**下发 wap_session**，
# 而 /signadd 的 CSRF 校验要求「页面里的 _token」与「同一会话的 session cookie」
# 配对。旧实现每次请求都是一条全新连接、把 Set-Cookie 直接丢掉，于是提交签到
# 恒定 `419 CSRF token mismatch.`（实测 2026-09）。
# 会话必须贯穿「拉签到页 → 提交签到」两步，所以这里显式持有 opener。
def _new_session(cookie: str) -> tuple:
    """用已有 cookie 播种一个会话。返回 (opener, cookie_jar)。"""
    jar = http.cookiejar.CookieJar()
    for part in (cookie or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        jar.set_cookie(http.cookiejar.Cookie(
            0, k.strip(), v.strip(), None, False,
            "mclub.lenovo.com.cn", False, False, "/", True, False, None, False, None, None, {}))
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar


def _sess_get(op, url: str) -> tuple:
    """会话内 GET（Set-Cookie 由 jar 自动接管）。"""
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9", "Referer": MCLUB}
    try:
        with op.open(urllib.request.Request(url, headers=headers, method="GET"),
                     timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return -1, ""


def _sess_post(op, url: str, data: dict) -> tuple:
    """会话内表单 POST。头部按签到页 JS 的真实写法补齐（含 X-Requested-With）。"""
    body = urllib.parse.urlencode(data).encode("utf-8")
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": SIGNLIST,
    }
    try:
        with op.open(urllib.request.Request(url, data=body, headers=headers, method="POST"),
                     timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return -1, json.dumps({"message": f"网络错误：{type(e).__name__}"})


def _readable(text: str, limit: int = 120) -> str:
    """把 HTML 错误页压成一行可读文案。

    旧实现直接把整页 `<!DOCTYPE html>…` 塞进消息里，界面上就是一大坨标签。
    """
    t = (text or "").strip()
    if t[:1] == "<":
        t = re.sub(r"<script.*?</script>", " ", t, flags=re.S | re.I)
        t = re.sub(r"<style.*?</style>", " ", t, flags=re.S | re.I)
        t = re.sub(r"<[^>]+>", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
    return t[:limit] or "(空响应)"


# 签到页真实写法（2026-09 实测）：先 `$CONFIG = {};` 再逐行 `$CONFIG.xxx = 值;`
#     $CONFIG = {};
#     $CONFIG.token = "lv91...";
#     $CONFIG.lenovoId = "0";
#     $CONFIG.loginName = "";
#     $CONFIG.signState = ""        ← 注意：最后一项没有分号
# 旧正则 `\$CONFIG\s*=\s*(\{.*?\})\s*;` 只会命中第一行的空对象 `{}`，
# 于是「永远解析不到字段 → 登录态校验永远失败」。
#
# 值只允许「带引号字符串」或「不含空白/;/<> 的裸值」，避免最后一项缺分号时
# 一路吞掉后面的 <script>/CSS 文本。
_CONFIG_VALUE = r'("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|[^\s;<>]*)'
_CONFIG_ASSIGN = re.compile(r"\$CONFIG\.([A-Za-z_$][\w$]*)\s*=\s*" + _CONFIG_VALUE, re.S)
_CONFIG_LITERAL = re.compile(r"\$CONFIG\s*=\s*(\{.*?\})\s*;", re.S)


def _js_value(raw: str):
    """把 JS 字面量转成 Python 值（字符串去引号，数字/布尔/null 归一）。"""
    v = (raw or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    low = v.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "undefined", ""):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def _parse_config(html: str) -> dict:
    """从签到页 HTML 提取 $CONFIG 字段。

    两种写法都支持（赋值式优先，其次对象字面量）；解析失败返回空 dict（不抛异常）。
    """
    cfg: dict = {}
    for m in _CONFIG_ASSIGN.finditer(html):
        cfg[m.group(1)] = _js_value(m.group(2))
    if cfg:
        return cfg

    m = _CONFIG_LITERAL.search(html)
    if not m:
        return {}
    raw = m.group(1)
    # 将 JS 对象里单引号键/值统一为双引号（简易处理，够覆盖 $CONFIG 场景）
    raw = re.sub(r"([{,]\s*)([A-Za-z_$][\w$]*)\s*:", r'\1"\2":', raw)
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        # 二次容错：尝试提取关键字段
        out = {}
        for key in ("signState", "rowKey", "memberSource", "memberId",
                    "nickname", "point", "token"):
            mm = re.search(r'"%s"\s*:\s*"?([^",}]+)"?' % key, raw)
            if mm:
                out[key] = mm.group(1)
        return out


def _is_logged_in(cfg: dict) -> bool:
    """判断签到页是否处于已登录态。

    未登录时页面同样有 $CONFIG（含 token），但 `lenovoId` 为 "0"、`loginName` 为空、
    `signState` 为空串 —— 只靠「能解析到 $CONFIG」会把未登录误判成已登录。
    """
    if not cfg:
        return False
    for key in ("lenovoId", "memberId"):
        v = cfg.get(key)
        if v not in (None, "", 0, "0"):
            return True
    for key in ("loginName", "nickname", "userName", "mobile"):
        if str(cfg.get(key) or "").strip():
            return True
    # 已登录时页面会带回签到状态（"0" 未签 / "1" 已签）；未登录是空串
    return str(cfg.get("signState") if cfg.get("signState") is not None else "").strip() != ""


def _cfg_nickname(cfg: dict) -> str:
    """取展示用昵称：nickname → loginName → 空。"""
    for key in ("nickname", "loginName", "userName"):
        v = str(cfg.get(key) or "").strip()
        if v:
            return v
    return ""


def client_from_account(acc: dict) -> "LenovoClient":
    """从账号条目构造客户端；cookie 缺失时抛 CookieError。"""
    from cookie_manager import require_cookie
    cookie = require_cookie(acc, "lenovo")
    return LenovoClient(cookie)


class LenovoClient:
    """联想 mclub 每日签到客户端。cookie 由调用方传入（仅内存持有）。"""

    def __init__(self, cookie: str):
        self.cookie = (cookie or "").strip()
        if not self.cookie:
            raise CookieError("缺少联想智选 cookie（mclub.lenovo.com.cn）")
        # 会话持有者：拉签到页时建立，提交签到时复用（CSRF 要求同会话）
        self._op = None

    # ---- 签到页与 $CONFIG ----
    def sign_page(self) -> tuple:
        """拉取签到页 HTML 并解析 $CONFIG。返回 (ok, config, message)。

        ⚠️ 必须走**同一个 cookie jar 会话**：签到页响应会下发 `wap_session`，
        而 /signadd 的 CSRF 校验要求「页面里的 _token」与「该会话的 session
        cookie」配对。旧实现每次都是新连接、丢掉 Set-Cookie，于是提交签到
        恒定 `419 CSRF token mismatch.`（实测 2026-09）。
        会话缓存在 self._op，供 _post_sign() 复用。
        """
        self._op, _ = _new_session(self.cookie)
        http, html = _sess_get(self._op, SIGNLIST)
        if http == -1:
            return False, {}, "网络请求失败"
        if http in (401, 403):
            return False, {}, f"Cookie 被拒绝（HTTP {http}），请重新获取联想 cookie"
        if http != 200:
            return False, {}, f"签到页返回 HTTP {http}"
        cfg = _parse_config(html)
        if not cfg:
            return False, {}, "无法解析签到页（可能未登录或页面结构变更）"
        if not _is_logged_in(cfg):
            return False, cfg, "联想登录态无效（未登录），请重新获取 mclub 的 cookie"
        return True, cfg, ""

    def signed_today(self, cfg: dict) -> bool:
        """signState：'1'/1/true 表示今日已签到。"""
        v = cfg.get("signState")
        if v in (1, "1", True, "true", "True"):
            return True
        if v in (0, "0", False, "false", "False"):
            return False
        # 部分版本用 LOGIN_STATUS / checkin 字段，做宽匹配
        for k in ("loginStatus", "checkin", "hasSign"):
            if cfg.get(k) in (1, "1", True, "true"):
                return True
        return False

    def _post_sign(self, cfg: dict) -> tuple:
        """单次提交签到（POST 到页面给的 signPath）。返回 (http, text)。

        参数名必须是 Laravel 的 CSRF 字段 **`_token`**，值取页面 `$CONFIG.token`。
        旧实现发的是 `token` / `rowKey`：
          · `$CONFIG` 里**根本没有 rowKey**（只有 token / signPath / lenovoId /
            userAgent / signState / loginName …）；
          · Laravel 只认 `_token`（或 X-XSRF-TOKEN 头），发 `token` 等于没带
            CSRF token → 恒定 419。
        其余参数与前端 signin.js 的 `$.post($CONFIG.signPath, {...})` 逐一对齐：
        `memberSource` = 页面 `userAgent`（实测 "0"）、`pss`/`deviceId`/`deviceToken`
        在纯 H5 环境就是空串（页面里 `pss` 只在原生 App 的 `HomeIntent.getP()`
        存在时才有值），`lenovoId` = 页面 `lenovoId`。
        """
        token = str(cfg.get("token") or "").strip()
        if not token:
            return -1, "签到页未返回 _token（可能登录态已失效），请重新登录联想"
        data = {
            "_token": token,
            "memberSource": str(cfg.get("userAgent") or "0"),
            "pss": "",
            "deviceId": "",
            "deviceToken": "",
            "lenovoId": str(cfg.get("lenovoId") or cfg.get("memberId") or ""),
        }
        if self._op is None:                 # 直接调 _post_sign 的兜底
            self.sign_page()
        return _sess_post(self._op, MCLUB + str(cfg.get("signPath") or "/signadd"), data)

    def _interpret(self, text: str, cfg: dict, http: int) -> dict:
        """把 /signadd 的响应翻译成统一结果 {ok, already, credits, message, http, code}。

        实测的两种响应形态（2026-09）：
          首次签到成功：
            {"success":true,"continueCount":1,"ledouValue":20,"scoreValue":10,
             "rewardTips":"获得20乐豆\n10积分\n2成长值", ...}   ← **没有 code 字段**
          重复签到：
            {"code":0,"msg":"用户已签到","postinfo":{...}}
        前端判成功用的是 `data.success`（见 signin.js 的 `if (data.code == 200)` 与
        `shownote(1, data)`），所以**不能只认 code**——只认 code 会把成功判成失败。
        """
        r = {"ok": False, "already": False, "credits": 0, "message": "",
             "http": http, "code": -1}
        if http == -1:
            r["message"] = "网络请求失败"
            return r
        if http in (401, 403):
            r["message"] = f"Cookie 被拒绝（HTTP {http}），请重新获取联想 cookie"
            return r

        body = None
        try:
            parsed = json.loads(text or "{}")
            if isinstance(parsed, dict):
                body = parsed
                r["code"] = parsed.get("code", -1)
        except json.JSONDecodeError:
            body = None

        if body is not None:
            ledou = self._to_int(body.get("ledouValue"))
            score = self._to_int(body.get("scoreValue"))
            if body.get("success") is True:
                r.update(ok=True, credits=ledou,
                         message=self._reward_msg(ledou, score))
                return r
            code = body.get("code")
            msg = str(body.get("msg") or body.get("message") or "").strip()
            if code is not None and str(code) in ("0", "200"):
                if "已签" in msg or "重复" in msg:
                    r.update(ok=True, already=True, code=0,
                             message="今日已签到（幂等）")
                    return r
                r.update(ok=True, code=0, credits=self._to_int(body.get("ledouValue")),
                         message=msg or self._reward_msg(ledou, score))
                return r
            # 不要在这里再写一次"签到失败："前缀——run_cookie_acc 统一加，
            # 否则日志会出现"签到失败：签到失败：xxx"的双重前缀。
            r["message"] = msg or _readable(text, 120)
            return r

        low = (text or "").lower()
        if "已签到" in low or "签到成功" in low:
            return {"ok": True, "already": "已签到" in low,
                    "credits": self._extract_credit(text, cfg),
                    "message": "已签到" if "已签到" in low else "签到成功",
                    "http": http, "code": 0}
        r["message"] = _readable(text, 120) or "未知响应"
        return r

    def checkin(self) -> dict:
        """每日签到（幂等）。返回 {ok, already, credits, message, http, code}

        ⚠️ 联想的 `/signadd` 有**服务端抖动**：同一个 _token、同一份 cookie、同一套
        请求头，只有约 **20%** 的请求能通过 Laravel 的 CSRF 校验，其余恒回
        `419 {"message":"CSRF token mismatch."}`（HTTP 200 之外的状态码）。
        这不是我们参数发错：2026-09 用真实 Chromium 调页面自带的 `signSubmit()`
        复现了同样的比例（4 次里 3 次 419），而且浏览器把数美 `deviceId` /
        `deviceToken` 都带上了照样失败。真实浏览器里**人手点签到也会时好时坏**。
        根因基本可以确定是对方后端（openresty + 多实例、会话不粘）：

        所以这里**重试到成功为止**。签到接口是幂等的（重复提交回
        `{"code":0,"msg":"用户已签到"}`），反复打没有副作用；已签到的情况下
        `signState == "1"`，在循环之前就短路返回了，一次 POST 都不会发。
        """
        ok, cfg, msg = self.sign_page()
        if not ok:
            return {"ok": False, "already": False, "credits": 0,
                    "message": msg, "http": 0, "code": -1}
        if self.signed_today(cfg):
            return {"ok": True, "already": True, "credits": 0,
                    "message": "今日已签到（幂等）", "http": 200, "code": 0}

        for i in range(SIGN_ATTEMPTS):
            if i:                            # 重试前重取签到页：换一个 _token 与后端实例
                ok2, cfg2, _ = self.sign_page()
                if ok2:
                    cfg = cfg2
            http, text = self._post_sign(cfg)
            if http == 419:                  # CSRF 抖动，换一次再打，不算失败
                continue
            return self._interpret(text, cfg, http)

        return {"ok": False, "already": False, "credits": 0, "http": 419, "code": -1,
                "message": f"联想服务端 CSRF 校验持续失败（已重试 {SIGN_ATTEMPTS} 次）；"
                           f"这是对方接口抖动，不是账号问题，稍后重试即可"}

    @staticmethod
    def _reward_msg(ledou: int, score: int) -> str:
        """签到成功文案。数字必须写出来——checkin.py / Swift 侧靠「本次获得 N」取数。"""
        parts = []
        if ledou:
            parts.append(f"{ledou} 乐豆")
        if score:
            parts.append(f"{score} 积分")
        if not parts:
            return "签到成功"
        return "签到成功，本次获得 " + "、".join(parts)

    def _user_info(self, attempts: int = 15) -> dict:
        """GET /signuserinfo —— 乐豆 / 积分 / 延保券余额。

        页面里的 `getUserInfo()` 就是打这个接口（`$('.ledou').html(res.ledou)`）。
        取到数据时的实测响应：`{"serviceAmount":77,"userCoins":1045,"ledou":"1.6万"}`

        ⚠️ 两点坑：
        1. `ledou` 是**站点自己四舍五入过的展示串**（"1.6万"），不是精确值；
           页面只暴露这一个口径，精确到个位的数拿不到。
        2. 这个接口同样吃 `/signadd` 那套后端抖动：未命中会话节点的请求回
           `{"res":"Must login"}`，而且 **HTTP 状态码仍是 200** —— 只看状态码会
           把「没拿到」误判成「拿到了」，所以必须按「有没有期望字段」判定并重试。

        只读接口、best-effort：失败返回空 dict，绝不影响签到状态的判定。
        """
        if self._op is None:                     # 直接调用的兜底
            self.sign_page()
        # 刻意**不**重建会话、只在同一个 self._op 上反复打：实测同一会话反复 GET 的
        # 命中率约 35%，而每次重建会话只有 ~10%。15 次足够把失败概率压到 ~0.1%。
        for _ in range(attempts):
            http, text = _sess_get(self._op, MCLUB + "/signuserinfo")
            if http != 200:
                continue
            try:
                obj = json.loads(text or "")
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and any(
                    k in obj for k in ("ledou", "userCoins", "serviceAmount")):
                return obj
        return {}

    def status(self) -> dict:
        """查询今日签到状态（无副作用）。返回 {ok, signed_today, nickname, credits, ...}

        `credits` 统一口径 = **账号当前可用余额（乐豆）**，取自 `/signuserinfo.ledou`。
        该字段是站点展示串（"1.6万"），展平后精度只到千位左右，所以同时给出
        `credits_text`（站点原文）与 `coins`/`warranty_days`，summary 里写原文，
        界面上不要把数字当成逐位精确值。
        """
        ok, cfg, msg = self.sign_page()
        base = {"ok": False, "signed_today": None, "nickname": "", "uid": "",
                "credits": 0, "credits_text": "", "coins": 0, "warranty_days": 0,
                "summary": "", "http": 0, "code": -1, "message": msg}
        if not ok:
            return base
        info = self._user_info()
        ledou_text = str(info.get("ledou") or "").strip()
        base.update(
            ok=True, http=200, code=0, message="",
            signed_today=self.signed_today(cfg),
            nickname=_cfg_nickname(cfg),
            uid=str(cfg.get("memberId") or cfg.get("lenovoId") or ""),
            credits=self._num_from_text(info.get("ledou")),
            credits_text=ledou_text,
            coins=self._to_int(info.get("userCoins")),
            warranty_days=self._to_int(info.get("serviceAmount")),
        )
        parts = []
        if base["nickname"]:
            parts.append(f"昵称 {base['nickname']}")
        if ledou_text:
            parts.append(f"乐豆 {ledou_text}")
        if base["coins"]:
            parts.append(f"积分 {base['coins']}")
        if base["warranty_days"]:
            parts.append(f"延保券 {base['warranty_days']} 天")
        base["summary"] = "，".join(parts)
        return base

    def probe(self) -> dict:
        """脱敏探测（无副作用）：验证登录态并回填昵称。"""
        ok, cfg, msg = self.sign_page()
        if not ok:
            return {"found": False, "healthy": False, "has_cookie": True, "nickname": "",
                    "uid": "", "message": msg, "reason": msg}
        return {"found": True, "healthy": True, "has_cookie": True,
                "nickname": _cfg_nickname(cfg),
                "uid": str(cfg.get("memberId") or cfg.get("lenovoId") or ""),
                "message": "", "reason": ""}

    @staticmethod
    def _extract_credit(text: str, cfg: dict) -> int:
        m = re.search(r"(\d+)\s*(?:乐豆|积分|延保)", text)
        if m:
            return int(m.group(1))
        return LenovoClient._to_int(cfg.get("point"))

    @staticmethod
    def _num_from_text(v) -> int:
        """把站点给的展示值展平成整数。

        联想 `/signuserinfo` 的 `ledou` 是 "1.6万" 这种中文缩写串（数字型则原样返回）。
        展开只是为了让 balance 可比较/可跨平台计数，**精度上限就是站点给的那位**
        （"1.6万" → 16000，真实值可能在 15500~16499 之间），所以 summary 里
        仍然写站点原文。
        """
        if isinstance(v, bool):
            return 0
        if isinstance(v, (int, float)):
            return int(v)
        s = str(v or "").strip()
        if not s:
            return 0
        # 用 search 而不是 match："约 2.5 万" / "1.6万+" 这类带前后缀的串也能正确展开
        m = re.search(r"([\d.]+)\s*(万|亿|[kKwW])?", s)
        if not m or not m.group(1):
            return 0
        try:
            n = float(m.group(1))
        except ValueError:
            return 0
        unit = m.group(2)
        if unit == "万" or unit in ("w", "W"):
            n *= 10000
        elif unit == "亿":
            n *= 100000000
        elif unit in ("k", "K"):
            n *= 1000
        return int(round(n))

    @staticmethod
    def _to_int(v) -> int:
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0


def login_and_save_cookie(account: str, password: str, name: str,
                          config_path: str | None = None) -> tuple:
    """账密自动登录：调 doLogin 抓取登录态，并跟进 signlist 页获取完整
    会话 cookie，写回 accounts.json 指定账号。

    返回 (是否成功, 描述)。密码仅用于本次登录请求，绝不落盘/打印。
    """
    if not account or not password:
        return False, "需要提供联想账号与密码"
    try:
        pwd_b64 = base64.b64encode(password.encode("utf-8")).decode("ascii")
    except Exception as e:  # noqa: BLE001
        return False, f"密码编码失败：{type(e).__name__}"
    http, text = _post_form(LOGIN, {
        "userName": account,
        "password": pwd_b64,
        "autoLogin": "true",
    })
    if http == -1:
        return False, "登录请求网络失败"
    if http not in (200, 302):
        return False, f"登录接口返回 HTTP {http}"
    try:
        body = json.loads(text or "{}")
    except json.JSONDecodeError:
        body = {}
    res_code = body.get("resCode") or body.get("code") if isinstance(body, dict) else None
    if isinstance(body, dict) and res_code not in ("200", 200, "0", 0):
        msg = body.get("result") or body.get("message") or "登录失败"
        return False, f"登录失败：{msg}"

    # 登录成功后携带会话 cookie 访问签到页，得到最终可用的登录态与完整 cookie
    cookie = _cookies_from_body(body)
    ok, cfg, msg = _lenovo_status_with_cookie(cookie)
    if not ok:
        return False, f"登录成功但签到页校验失败：{msg}"
    cookie = _merge_session_cookie(cookie)

    config = load_config(config_path)
    acc = find_account(config, name)
    if acc is None:
        return False, f"未找到账号：{name}，请先在账号管理中创建该账号"
    extra = {"nickname": _cfg_nickname(cfg),
             "uid": str(cfg.get("memberId") or cfg.get("lenovoId") or "")}
    if set_cookie(config, name, "lenovo", cookie, extra):
        return True, "账密登录成功，cookie 已更新到账号「%s」" % name
    return False, "cookie 写回失败"


def _cookies_from_body(body: dict) -> str:
    """从 doLogin 响应中拼装基础 cookie（尽力而为，缺失项不阻塞）。"""
    data = body.get("data") or {}
    parts = []
    if data.get("token"):
        parts.append(f"token={data['token']}")
    if data.get("userId"):
        parts.append(f"userId={data['userId']}")
    if data.get("account"):
        parts.append(f"account={data['account']}")
    return "; ".join(parts)


def _merge_session_cookie(cookie: str) -> str:
    """合并/去重 cookie 片段（登录响应中可能存在重复键），返回可直接落盘的 cookie 串。"""
    seen: dict[str, str] = {}
    for part in (cookie or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        seen[k.strip()] = v.strip()
    return "; ".join(f"{k}={v}" for k, v in seen.items())


def _lenovo_status_with_cookie(cookie: str) -> tuple:
    """用给定 cookie 访问签到页验证登录态，返回 (ok, config, message)。

    必须同时满足「解析到 $CONFIG」与「$CONFIG 是已登录态」——否则未登录页面
    （同样带 $CONFIG 与 token）会被误判成登录成功。
    """
    http, html = _get(SIGNLIST, cookie)
    if http in (401, 403):
        return False, {}, f"签到页返回 HTTP {http}"
    cfg = _parse_config(html)
    if not cfg:
        return False, {}, "签到页解析失败（可能未登录）"
    if not _is_logged_in(cfg):
        return False, cfg, "尚未登录（签到页未返回登录态）"
    return True, cfg, ""


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
        cookie = get_cookie(acc, "lenovo")
        has = bool(cookie)
        if not has:
            return {"found": False, "healthy": False, "has_cookie": False, "nickname": "",
                    "uid": "", "message": "账号未配置 cookie，请粘贴 mclub.lenovo.com.cn 的登录 cookie",
                    "reason": "账号未配置 cookie，请粘贴 mclub.lenovo.com.cn 的登录 cookie"}
        try:
            return LenovoClient(cookie).probe()
        except Exception as e:  # noqa: BLE001
            return {"found": False, "healthy": False, "has_cookie": True, "nickname": "",
                    "uid": "", "message": f"登录态验证失败：{type(e).__name__}",
                    "reason": f"登录态验证失败：{type(e).__name__}"}
    except Exception as e:  # noqa: BLE001
        return {"found": False, "healthy": False, "has_cookie": False, "nickname": "",
                "uid": "", "message": f"读取失败：{type(e).__name__}",
                "reason": f"读取失败：{type(e).__name__}"}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="联想智选每日签到客户端")
    parser.add_argument("--probe", action="store_true", help="探测账号登录态（输出脱敏 JSON）")
    parser.add_argument("--checkin", action="store_true", help="执行签到")
    parser.add_argument("--credits", action="store_true", help="查询账号状态/积分")
    parser.add_argument("--login-account", help="联想账号（账密自动登录抓 cookie）")
    parser.add_argument("--login-password", help="联想密码（仅用于本次登录请求）")
    parser.add_argument("--name", required=True, help="accounts.json 中的账号名")
    args = parser.parse_args()

    if args.login_account or args.login_password:
        ok, desc = login_and_save_cookie(args.login_account or "",
                                         args.login_password or "", args.name)
        print(desc)
        sys.exit(0 if ok else 1)

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
            state = "已签" if r.get("signed_today") else "未签"
            print(f"今日{state}，{'，'.join(x for x in [r.get('summary')] if x) or '状态正常'}")
        else:
            print(f"查询失败：{r.get('message')}（HTTP {r.get('http')}）")
            sys.exit(1)
    elif args.checkin:
        r = client.checkin()
        if r.get("ok"):
            mark = "今日已签到，无需重复" if r.get("already") else \
                (f"签到成功，本次获得 {r.get('credits')} 乐豆" if r.get("credits") else "签到成功")
            print(mark)
        else:
            print(r.get("message") or "签到失败")
            sys.exit(1)
    else:
        print("用法：--probe / --checkin / --credits（均需 --name）")


if __name__ == "__main__":
    main()
