#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""什么值得买 每日签到客户端（纯标准库实现）。

接口来源与实测结论（2026-09-15 在本机逐条探测，**不含任何真实凭据**）：

  1. App 端（首选）
       POST https://user-api.smzdm.com/checkin
       需要 sign 签名，签名规则为公开协议：
         明文 = 按 key 字典序拼 "k=v&k=v…" + "&key=" + 固定密钥
         签名 = MD5(明文) 大写十六进制
       实测判别信号（用假 cookie 跑出来的，可靠性很高）：
         无 sign 参数        -> {"error_code":-1,"error_msg":"Sign param not enough"}
         错误签名 / 错误密钥  -> {"error_code":-1,"error_msg":"check Sign Fail"}
         正确签名 + 空 cookie -> {"error_code":"11111","error_msg":"请先登录","logout":"1"}
         正确签名 + 假 cookie -> {"error_code":"11111","error_msg":"貌似网络不太稳定…"}
         成功                -> {"error_code":"0", …}
       即：**"check Sign Fail" 说明是我们这边的签名定义过期了，不是用户的账号
       有问题**（归类"平台受限"）；"请先登录" / logout="1" 才是凭据失效。

  2. Web 端（兜底）
       GET  https://zhiyou.smzdm.com/user/checkin/jsonp_checkin
       只吃网页版 cookie。实测未登录返回 {"error_code":99,"error_msg":"请先登录"}，
       无 cookie 返回 {"error_code":1,"error_msg":{"public":"数据错误"}}。
       App cookie 能否在网页域生效并不保证，所以它只作为 App 端不可用时的退路，
       不作为主路径。

写这段代码时的取舍：App 端要签名、Web 端老接口随时可能下线，两条路都不算稳。
因此这里把「签名过期 / 活动下线 / 风控」统一归到 restricted=True（平台受限），
和「cookie 失效」严格分开——后者才需要用户去重新登录。

凭据安全：cookie 等同账号密码，读取后仅内存使用，绝不写日志、绝不回显到
stdout / UI。accounts.json 只保存用户主动粘贴的 cookie 与非敏感快照。

用法：
    from smzdm import SmzdmClient, client_from_account
    c = client_from_account(acc)
    print(c.status())
    print(c.checkin())
CLI：
    python3 smzdm.py --probe   --name <账号名>
    python3 smzdm.py --checkin --name <账号名>
    python3 smzdm.py --credits --name <账号名>
"""

import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from cookie_manager import CookieError, find_account, get_cookie, load_config

APP_API = "https://user-api.smzdm.com"
WEB_API = "https://zhiyou.smzdm.com"
TIMEOUT = 20

# 签名密钥与客户端版本号来自公开的抓包模板（App v10.4.26）。
# 若平台改版，这里会失效——表现为 "check Sign Fail"，届时归为"平台受限"。
SIGN_KEY = "apr1$AwP!wRRT$gJ/q.X24poeBInlUJC"
APP_VERSION = "10.4.26"
APP_UA = (f"smzdm_android_V{APP_VERSION} rv:866 "
          f"(Redmi Note 3;Android10.0;zh)smzdmapp")
WEB_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 SmzdmCheckin/1.0")

# 登录态失败的关键词。命中即认为 cookie 确实失效（需要用户重新登录），
# 而不是"平台受限"。刻意不收"账号异常"这类词——那更像风控，不是没登录。
_LOGIN_FAIL_WORDS = ("请先登录", "没有登录", "未登录", "登录已过期")

# 参与签名的固定参数（顺序无关，_sign 会按字典序重排）
_SIGNED_KEYS = ("weixin", "basic_v", "f", "v", "time")


class SmzdmError(RuntimeError):
    pass


def _read_body(resp) -> str:
    return resp.read().decode("utf-8", errors="replace")


def _parse_json(text: str):
    try:
        return json.loads(text or "{}")
    except json.JSONDecodeError:
        return {"error_code": -1, "error_msg": "响应解析失败",
                "raw": (text or "")[:200]}


def _code_str(v) -> str:
    """error_code 在不同端有时是数字有时是字符串，统一成字符串比较。"""
    if v is None:
        return ""
    return str(v).strip()


def _msg_str(v) -> str:
    """error_msg 可能是字符串，也可能是 {"public": "数据错误"} 这种对象。"""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        for k in ("public", "app", "msg"):
            if isinstance(v.get(k), str) and v[k]:
                return v[k]
        return "；".join(str(x) for x in v.values() if isinstance(x, (str, int)))
    return "" if v is None else str(v)


def _pick(d, *keys):
    """从响应里取第一个存在的字段：先看 data 层，再看顶层。

    实测各版本什么值得买把 daily_num / cgold / pre_re_silver 放在哪一层
    并不固定（抓包模板是直接对整段文本做正则），所以两层都找。
    """
    if not isinstance(d, dict):
        return None
    data = d.get("data")
    for src in (data if isinstance(data, dict) else {}, d):
        for k in keys:
            if isinstance(src, dict) and src.get(k) not in (None, ""):
                return src.get(k)
    return None


def _to_num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _to_int(v):
    n = _to_num(v)
    return int(n) if n is not None else None


def client_from_account(acc: dict) -> "SmzdmClient":
    """从账号条目构造客户端；cookie 缺失时抛 CookieError。"""
    from cookie_manager import require_cookie
    cookie = require_cookie(acc, "smzdm")
    return SmzdmClient(cookie)


class SmzdmClient:
    """什么值得买每日签到客户端。cookie 由调用方传入（仅内存持有）。"""

    def __init__(self, cookie: str):
        self.cookie = (cookie or "").strip()
        if not self.cookie:
            raise CookieError("缺少什么值得买 cookie，请先登录网页版后粘贴")

    # ---------------- 签名 / 请求 ----------------
    @staticmethod
    def _sign(params: dict) -> str:
        """按公开协议算 sign：字典序拼参 + 固定 key，MD5 取大写。"""
        base = "&".join(f"{k}={params[k]}" for k in sorted(params))
        raw = f"{base}&key={SIGN_KEY}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()

    def _app_post(self, path: str, params: dict) -> tuple:
        """App 端 POST：自动补 time 与 sign，返回 (http, body)。"""
        p = dict(params)
        p["time"] = str(int(time.time() * 1000))
        # 参与签名的参数集合固定为这五个；_sign 内部会再按字典序排一次。
        signed = {k: p[k] for k in _SIGNED_KEYS if k in p}
        body = "&".join(f"{k}={signed[k]}" for k in _SIGNED_KEYS if k in signed)
        body += "&sign=" + self._sign(signed)
        req = urllib.request.Request(APP_API + path, data=body.encode("utf-8"),
                                     headers={
            "User-Agent": APP_UA,
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": self.cookie,
            "Referer": "https://www.smzdm.com/",
            "Accept": "application/json, text/plain, */*",
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.status, _parse_json(_read_body(resp))
        except urllib.error.HTTPError as e:
            return e.code, _parse_json(e.read().decode("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001
            return -1, {"error_code": -1, "error_msg": f"网络错误：{type(e).__name__}"}

    def _web_get(self, path: str) -> tuple:
        """Web 端 GET（兜底路径）。返回 (http, body)。"""
        req = urllib.request.Request(WEB_API + path, headers={
            "User-Agent": WEB_UA,
            "Cookie": self.cookie,
            "Referer": "https://www.smzdm.com/",
            "Accept": "application/json, text/plain, */*",
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.status, _parse_json(_read_body(resp))
        except urllib.error.HTTPError as e:
            return e.code, _parse_json(e.read().decode("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001
            return -1, {"error_code": -1, "error_msg": f"网络错误：{type(e).__name__}"}

    # ---------------- 结果归类 ----------------
    @staticmethod
    def _classify(body: dict) -> tuple:
        """把响应归类为 (kind, message)。

        kind: "ok" | "login"（凭据失效）| "restricted"（平台受限/风控/签名过期）
        """
        code = _code_str(body.get("error_code"))
        msg = _msg_str(body.get("error_msg"))
        logout = _code_str(body.get("logout"))

        if code == "0":
            return "ok", ""
        # 凭据失效：平台明确让我们去登录
        if logout == "1" or any(w in msg for w in _LOGIN_FAIL_WORDS):
            return "login", msg or "登录态已失效"
        # 我们这边的签名/密钥过期 —— 不是用户账号的问题
        if "sign" in msg.lower() and ("fail" in msg.lower() or "错误" in msg):
            return "restricted", ("什么值得买签名校验未通过，接口定义可能已变更，"
                                  "请更新 smzdm.py 的签名参数")
        if code == "99":
            return "login", msg or "登录态已失效"
        # 其余（含 11111 风控、"数据错误"）一律归为平台受限，避免误报成失败
        return "restricted", msg or f"平台返回异常（error_code={code or '?'}）"

    # ---------------- 状态查询（只读） ----------------
    def _web_status(self) -> dict:
        """网页端只读查询：昵称 / 金币 / 连续签到天数。"""
        http, body = self._web_get("/user/info/jsonp_get_current")
        r = {"http": http, "ok": False, "nickname": "", "uid": "", "credits": None,
             "signed_today": None, "daily_num": None, "message": ""}
        if not isinstance(body, dict):
            r["message"] = f"HTTP {http}"
            return r
        sid = _to_int(body.get("smzdm_id"))
        r["uid"] = str(sid) if sid else ""
        r["nickname"] = str(body.get("nickname") or body.get("username") or "")
        r["credits"] = _to_num(body.get("point") if body.get("point") is not None
                               else body.get("gold"))
        if sid:
            r["ok"] = True
        else:
            # App Cookie 不被网页端接受也会是 0。如实说明，但别让用户以为账号坏了。
            r["message"] = ("网页端未识别到登录态（若你粘贴的是 App Cookie，"
                            "这不影响签到，可忽略）")
        return r

    def status(self) -> dict:
        """查询账号状态（只读，无副作用）。

        返回 {ok, signed_today, nickname, uid, credits, summary, http, message}

        注意：signed_today 恒为 None —— 什么值得买没有公开的"今日是否已签"
        只读接口，必须调签到本身才能知道（幂等，不会重复发奖）。这里如实返回
        None，让上层显示"登录态正常"而不是编一个假的签到状态。
        """
        w = self._web_status()
        r = {
            "http": w["http"],
            "ok": w["ok"],
            "signed_today": None,
            "nickname": w["nickname"],
            "uid": w["uid"],
            "credits": w["credits"],
            "summary": "",
            "message": w["message"],
        }
        if w["ok"]:
            parts = []
            if w["credits"] is not None:
                parts.append(f"金币 {w['credits']:g}")
            if w["nickname"]:
                parts.append(f"昵称 {w['nickname']}")
            r["summary"] = "，".join(parts)
        return r

    # ---------------- 签到 ----------------
    def checkin(self) -> dict:
        """执行每日签到（幂等）。

        返回 {ok, already, credits, message, http, code, restricted, daily, via}

        via: "app" = 走 App 端签名接口；"web" = 退到网页端老接口。
        """
        r = {
            "http": 0, "code": "", "message": "", "ok": False, "already": False,
            "credits": 0.0, "restricted": False, "daily": {}, "via": "app",
        }

        # ---- 主路径：App 端 ----
        http, body = self._app_post("/checkin", {
            "weixin": "1", "basic_v": "0", "f": "android", "v": APP_VERSION,
        })
        r["http"] = http
        if isinstance(body, dict):
            r["code"] = _code_str(body.get("error_code"))
        kind, msg = self._classify(body if isinstance(body, dict) else {})

        if kind == "ok":
            return self._fill_ok(r, body, already=False)

        if kind == "login":
            # App 端说没登录。App cookie 与网页 cookie 不一定互通，
            # 所以再用网页端复核一次，避免把"换了个端的 cookie"误判成失效。
            w_http, w_body = self._web_get("/user/checkin/jsonp_checkin")
            w_kind, w_msg = self._classify(w_body if isinstance(w_body, dict) else {})
            if w_kind == "ok":
                r["via"] = "web"
                r["http"] = w_http
                r["code"] = _code_str(w_body.get("error_code"))
                return self._fill_ok(r, w_body, already=False)
            r["message"] = ("什么值得买登录态已失效，请重新登录后更新 Cookie"
                            f"（App：{msg or '未登录'}；网页：{w_msg or '未登录'}）")
            return r

        if kind == "restricted":
            # 风控 / 签名过期 / 端点变更：再用网页端试一次，能签上就是签上了
            w_http, w_body = self._web_get("/user/checkin/jsonp_checkin")
            w_kind, _ = self._classify(w_body if isinstance(w_body, dict) else {})
            if w_kind == "ok":
                r["via"] = "web"
                r["http"] = w_http
                r["code"] = _code_str(w_body.get("error_code"))
                return self._fill_ok(r, w_body, already=False)
            r["restricted"] = True
            r["message"] = msg
            return r

        r["restricted"] = True
        r["message"] = msg or "未知响应"
        return r

    def _fill_ok(self, r: dict, body: dict, already: bool) -> dict:
        """把成功响应里的天数 / 金币 / 碎银子填进结果。"""
        daily_num = _to_int(_pick(body, "daily_num", "checkin_num", "day"))
        cgold = _to_num(_pick(body, "cgold", "gold", "point"))
        silver = _pick(body, "pre_re_silver", "silver")

        r["ok"] = True
        r["already"] = already
        # 什么值得买的收益是金币/碎银子，不是统一"积分"；放 daily 交给上层展示，
        # credits 只填数值型的金币，避免污染其它平台的"积分"语义。
        r["credits"] = cgold if cgold is not None else 0.0
        r["daily"] = {"daily_num": daily_num, "cgold": cgold, "silver": silver}

        bits = []
        if daily_num is not None:
            bits.append(f"连续签到 {daily_num} 天")
        if cgold is not None:
            bits.append(f"金币 {cgold:g}")
        if silver not in (None, ""):
            bits.append(f"碎银子 {silver}")
        tail = ("；" + "，".join(bits)) if bits else ""

        if already:
            r["message"] = "今日已签到（幂等，未重复领取）" + tail
        else:
            r["message"] = "签到成功" + tail
        return r

    # ---------------- 探测（供 Swift 侧一键添加/刷新） ----------------
    def probe(self) -> dict:
        """探测登录态。

        这里刻意**用签到接口本身**来判定，理由：
          - 我们签到走的就是这两条路，别的只读接口通不通不代表签到能成功
            （京东/联想都踩过"只读接口判不了"的坑）；
          - 签到幂等，同一天重复调用不会重复发奖；
          - 只有它能准确区分「cookie 失效」和「我们签名过期」。
        副作用是"顺带完成当日签到"，对用户是好事，不算越权。
        """
        res = self.checkin()
        if res.get("ok"):
            daily = res.get("daily") or {}
            return {
                "found": True, "healthy": True, "has_cookie": True,
                "nickname": "", "uid": "",
                "credits": daily.get("cgold") or 0,
                "message": "", "reason": "",
            }
        reason = res.get("message") or "登录态验证失败"
        return {
            "found": True, "healthy": False, "has_cookie": True,
            "nickname": "", "uid": "", "credits": 0,
            "message": reason, "reason": reason,
        }


# ---------- CLI ----------
def _load_acc(name: str) -> dict:
    acc = find_account(load_config(), name)
    if acc is None:
        sys.stderr.write(f"未找到账号：{name}\n")
        sys.exit(2)
    return acc


def _probe(acc: dict) -> dict:
    """脱敏探测 JSON（不含 cookie 明文），供给 Swift 侧。"""
    try:
        cookie = get_cookie(acc, "smzdm")
        if not cookie:
            reason = "账号未配置 Cookie，请登录网页版后粘贴后重试"
            return {"found": False, "healthy": False, "has_cookie": False,
                    "nickname": "", "uid": "", "credits": 0,
                    "message": reason, "reason": reason}
        return SmzdmClient(cookie).probe()
    except Exception as e:  # noqa: BLE001
        reason = f"读取失败：{type(e).__name__}"
        return {"found": False, "healthy": False, "has_cookie": False,
                "nickname": "", "uid": "", "credits": 0,
                "message": reason, "reason": reason}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="什么值得买 每日签到客户端")
    parser.add_argument("--probe", action="store_true", help="探测账号登录态（输出脱敏 JSON）")
    parser.add_argument("--checkin", action="store_true", help="执行签到")
    parser.add_argument("--credits", action="store_true", help="查询账号状态")
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
            print(f"登录态正常，{r.get('summary') or '（接口未返回余额）'}")
        else:
            print(f"查询失败：{r.get('message')}（HTTP {r.get('http')}）")
            sys.exit(1)
    elif args.checkin:
        r = client.checkin()
        if r.get("ok"):
            print(f"{r.get('message')}（路径 {r.get('via')}）")
        elif r.get("restricted"):
            print(f"平台受限：{r.get('message')}（HTTP {r.get('http')}）")
            sys.exit(3)
        else:
            print(f"签到失败：{r.get('message')}（HTTP {r.get('http')}）")
            sys.exit(1)
    else:
        print("用法：--probe / --checkin / --credits（均需 --name）")


if __name__ == "__main__":
    main()
