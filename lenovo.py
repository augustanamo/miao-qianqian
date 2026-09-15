#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""联想智选每日签到客户端（纯标准库实现）。

接口思路参考开源项目协议（lenovo-sign / mclub 签到），代码为本项目内
独立实现，不照搬任何仓库源码：
  - GET https://mclub.lenovo.com.cn/signlist/
       签到页（需登录 cookie），页面内嵌 var $CONFIG = {...}，
       含 signState（今日是否已签）、rowKey（签到提交凭据）、memberSource
  - POST https://mclub.lenovo.com.cn/signadd
       执行签到（当天已签时不调用，幂等）
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

    # ---- 签到页与 $CONFIG ----
    def sign_page(self) -> tuple:
        """拉取签到页 HTML 并解析 $CONFIG。返回 (ok, config, message)。"""
        http, html = _get(SIGNLIST, self.cookie)
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

    def _submit(self, cfg: dict) -> tuple:
        """构造 signadd 提交参数：优先用页面提供的 rowKey/memberSource/token。"""
        data = {}
        for key in ("rowKey", "memberSource", "token", "memberId"):
            v = cfg.get(key)
            if v and str(v).strip() not in ("None", "undefined", "null"):
                data[key] = str(v).strip()
        if "memberSource" not in data:
            data["memberSource"] = "LenovoClubAndroid"
        if not data.get("rowKey") and not data.get("token"):
            return -1, "页面缺少签到凭据（rowKey/token），请重新获取 cookie"
        return _post_form(SIGNADD, data, self.cookie)

    def checkin(self) -> dict:
        """每日签到（幂等）。返回 {ok, already, credits, message, http, code}"""
        ok, cfg, msg = self.sign_page()
        if not ok:
            return {"ok": False, "already": False, "credits": 0,
                    "message": msg, "http": 0, "code": -1}
        if self.signed_today(cfg):
            return {"ok": True, "already": True, "credits": 0,
                    "message": "今日已签到（幂等）", "http": 200, "code": 0}
        http, text = self._submit(cfg)
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
        low = (text or "").lower()
        if "已签到" in low or "签到成功" in low:
            # 无论结构如何，页面已明确表达签到结果
            return {"ok": True, "already": "已签到" in low,
                    "credits": self._extract_credit(text, cfg),
                    "message": "已签到" if "已签到" in low else "签到成功",
                    "http": http, "code": 0}
        msg = body.get("message") or body.get("msg") or text[:80] if body else (text or "")[:80]
        if isinstance(body, dict) and str(body.get("code")) in ("0", "200"):
            return {"ok": True, "already": False,
                    "credits": self._extract_credit(text, cfg),
                    "message": "签到成功", "http": http, "code": 0}
        # 不要在这里再写一次"签到失败："前缀——run_cookie_acc 统一加，
        # 否则日志会出现"签到失败：签到失败：xxx"的双重前缀。
        r["message"] = msg or "未知响应"
        return r

    def status(self) -> dict:
        """查询今日签到状态（无副作用）。返回 {ok, signed_today, nickname, credits, ...}"""
        ok, cfg, msg = self.sign_page()
        base = {"ok": False, "signed_today": None, "nickname": "", "uid": "",
                "credits": 0, "summary": "", "http": 0, "code": -1, "message": msg}
        if not ok:
            return base
        base.update(
            ok=True, http=200, code=0, message="",
            signed_today=self.signed_today(cfg),
            nickname=_cfg_nickname(cfg),
            uid=str(cfg.get("memberId") or cfg.get("lenovoId") or ""),
            credits=self._to_int(cfg.get("point")),
        )
        parts = []
        if base["nickname"]:
            parts.append(f"昵称 {base['nickname']}")
        if base["credits"]:
            parts.append(f"乐豆/积分 {base['credits']}")
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
