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
import re
import sys
import time
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


# ----------------------------------------------------------------------
# 多账号支持（OAuth 扫码登录态存于 accounts.json 的 workbuddy_auth 字段）
# 约定：access_token / refresh_token 等同账号密码，仅写入 accounts.json
#       （已被 .gitignore 排除），仅内存使用，绝不写日志、绝不回显明文。
# ----------------------------------------------------------------------
ACCOUNTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts.json")
OAUTH_BASE = "https://www.codebuddy.cn"
OAUTH_PREFIX = "/v2/plugin"
_JWT_PAT = re.compile(r"eyJ[A-Za-z0-9_\-]{10,}(?:\.[A-Za-z0-9_\-]+){1,2}")


def _redact(text: str) -> str:
    """对描述文本中的 JWT 形态子串打码，防止意外回显 token。"""
    return _JWT_PAT.sub("[token]", str(text))


def mask_token(value: str) -> str:
    """Token 脱敏展示：空 -> "(空)"；否则保留前 8 位后打码。"""
    value = (value or "").strip()
    if not value:
        return "(空)"
    if len(value) <= 12:
        return "******"
    return value[:8] + "******"


def _to_ms(v):
    """统一为绝对毫秒时间戳；非法值返回 None。兼容相对秒/绝对秒/毫秒。"""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f < 100_000:
        return int(time.time() * 1000 + f * 1000)   # 相对秒
    if f < 100_000_000_000:
        return int(f * 1000)                         # 绝对秒
    return int(f)                                     # 绝对毫秒


def _jwt_exp(token: str):
    """解析 JWT exp（返回毫秒时间戳），解析失败返回 None。"""
    try:
        parts = (token or "").split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        import base64
        data = base64.urlsafe_b64decode(payload)
        obj = json.loads(data)
        exp = obj.get("exp")
        if exp is None:
            return None
        exp = float(exp)
        return exp if exp > 10_000_000_000 else exp * 1000
    except Exception:  # noqa: BLE001
        return None


def load_workbuddy_accounts(config_path: str | None = None) -> list:
    """从 accounts.json 读取所有 workbuddy 账号（token 仅内存返回）。

    返回列表，每项：
      {name, enabled, uid, nickname, domain, enterprise_id, source,
       token, refresh_token, expires_at(ms), refresh_expires_at(ms)}
    source: "oauth"（accounts.json 内 token）或 "local"（本机登录态回退）。
    """
    from cookie_manager import load_config
    cfg = load_config(config_path)
    out = []
    for acc in cfg.get("accounts", []):
        if not isinstance(acc, dict) or acc.get("type") != "workbuddy":
            continue
        auth = acc.get("workbuddy_auth") or {}
        if not isinstance(auth, dict):
            auth = {}
        token = (auth.get("access_token") or "").strip()
        out.append({
            "name": acc.get("name", "?"),
            "enabled": bool(acc.get("enabled", True)),
            "uid": (auth.get("uid") or "").strip(),
            "nickname": (auth.get("nickname") or "").strip(),
            "domain": (auth.get("domain") or "").strip(),
            "enterprise_id": (auth.get("enterprise_id") or "").strip(),
            "source": "oauth" if token else "local",
            "token": token,
            "refresh_token": (auth.get("refresh_token") or "").strip(),
            "expires_at": _to_ms(auth.get("expires_at")),
            "refresh_expires_at": _to_ms(auth.get("refresh_expires_at")),
        })
    return out


def account_token(acc: dict) -> str:
    """取账号 accessToken（仅内存返回，绝不落盘/打印）。

    优先 accounts.json 内 workbuddy_auth.access_token（OAuth 多账号）；
    无则回退本机登录态文件（旧版单账号路径，保持兼容）。
    两者均不可用时抛 WorkBuddyError（中文可读提示）。
    """
    if isinstance(acc, dict):
        auth = acc.get("workbuddy_auth") or {}
        if isinstance(auth, dict):
            t = (auth.get("access_token") or "").strip()
            if t:
                return t
    return _read_token()  # 回退本机登录态


def _now_str() -> str:
    import datetime
    dt = datetime.datetime.now()
    return f"{dt.year}-{dt.month:02d}-{dt.day:02d} {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"


def save_oauth_tokens(config_path: str | None, name: str, tokens: dict) -> bool:
    """把 OAuth token 快照写回 accounts.json 对应账号（原子写，自动建号）。

    tokens 字段：access_token/refresh_token/expires_at/refresh_expires_at/
                 token_type/domain/uid/nickname/email/enterprise_id/source。
    同名账号保留原启用状态与历史非敏感快照；新账号默认启用。
    """
    from cookie_manager import load_config, save_config
    cfg = load_config(config_path)
    arr = cfg.get("accounts", [])
    auth = {
        "access_token": (tokens.get("access_token") or "").strip(),
        "refresh_token": (tokens.get("refresh_token") or "").strip(),
        "expires_at": tokens.get("expires_at"),
        "refresh_expires_at": tokens.get("refresh_expires_at"),
        "token_type": tokens.get("token_type") or "",
        "domain": tokens.get("domain") or "",
        "uid": tokens.get("uid") or "",
        "nickname": tokens.get("nickname") or "",
        "email": tokens.get("email") or "",
        "enterprise_id": tokens.get("enterprise_id") or "",
        "saved_at": _now_str(),
        "source": tokens.get("source") or "oauth",
    }
    auth = {k: v for k, v in auth.items() if v not in (None, "")}
    idx = next((i for i, a in enumerate(arr)
                if isinstance(a, dict) and a.get("name") == name), None)
    if idx is not None:
        old = arr[idx]
        if not isinstance(old.get("workbuddy_auth"), dict):
            old["workbuddy_auth"] = {}
        old["workbuddy_auth"].update(auth)
        old["type"] = "workbuddy"
        old["app"] = "workbuddy"
        arr[idx] = old
    else:
        arr.append({
            "name": name,
            "app": "workbuddy",
            "type": "workbuddy",
            "enabled": True,
            "workbuddy_auth": auth,
        })
    cfg["accounts"] = arr
    return save_config(cfg, config_path)


def refresh_access_token(refresh_token: str, domain: str = "") -> dict:
    """用 refreshToken 换新 token（OAuth device flow 协议，仅标准库）。

    返回 dict：{ok, http, message, access_token, refresh_token,
                expires_at(ms), refresh_expires_at(ms), token_type, domain}
    """
    headers = {
        "Authorization": "Bearer " + (refresh_token or "").strip(),
        "X-Refresh-Token": (refresh_token or "").strip(),
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": _UA,
    }
    if domain:
        headers["X-Domain"] = domain
    req = urllib.request.Request(
        OAUTH_BASE + OAUTH_PREFIX + "/auth/token/refresh",
        data=b"{}", headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            status, text = resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "http": -1, "message": f"网络异常：{e}"}
    body = _loads_body(text)
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    if status in (401, 403):
        return {"ok": False, "http": status, "message": "refreshToken 已失效，请重新扫码登录"}
    if status != 200:
        return {"ok": False, "http": status, "message": _resp_msg(body) or f"HTTP {status}"}
    token = data.get("accessToken") or data.get("access_token") or ""
    if not token:
        return {"ok": False, "http": status, "message": "刷新响应缺少 accessToken"}
    new_refresh = data.get("refreshToken") or data.get("refresh_token") or (refresh_token or "")
    return {
        "ok": True,
        "http": status,
        "message": "",
        "access_token": token,
        "refresh_token": new_refresh,
        "expires_at": _to_ms(data.get("expiresAt") or data.get("expiresIn")),
        "refresh_expires_at": _to_ms(data.get("refreshExpiresAt") or data.get("refreshExpiresIn")),
        "token_type": data.get("tokenType") or data.get("token_type") or "",
        "domain": data.get("domain") or domain,
    }


def checkin_account(entry: dict, config_path: str | None = None,
                    growth: bool = True) -> dict:
    """单账号签到（幂等）+ 可选成长中心。token 仅内存，返回脱敏结果。

    签到/查状态遇 401/403 时，若账号有 refresh_token 自动续期并写回，
    续期失败或无可续期凭证时给出中文可读提示。
    """
    name = entry["name"]
    result = {"name": name, "source": entry["source"], "ok": False, "desc": ""}

    def _build() -> WorkBuddyClient:
        return WorkBuddyClient(
            entry["token"],
            uid=entry.get("uid") or "",
            domain=entry.get("domain") or "",
            enterprise_id=entry.get("enterprise_id") or "",
        )

    try:
        client = _build()
    except WorkBuddyError as e:
        result["desc"] = f"签到失败：{e}"
        return result

    def _refresh_once() -> bool:
        """尝试用 refreshToken 续期并写回；返回是否成功。"""
        if not (entry.get("refresh_token") or "").strip():
            return False
        r = refresh_access_token(entry["refresh_token"], entry.get("domain") or "")
        if not r.get("ok"):
            return False
        entry["token"] = r["access_token"]
        entry["refresh_token"] = r.get("refresh_token") or entry["refresh_token"]
        entry["expires_at"] = r.get("expires_at")
        entry["refresh_expires_at"] = r.get("refresh_expires_at")
        if config_path:
            save_oauth_tokens(config_path, name, {
                "access_token": entry["token"],
                "refresh_token": entry["refresh_token"],
                "expires_at": entry["expires_at"],
                "refresh_expires_at": entry["refresh_expires_at"],
                "token_type": r.get("token_type", ""),
                "domain": r.get("domain") or entry.get("domain") or "",
                "uid": entry.get("uid", ""),
                "nickname": entry.get("nickname", ""),
                "source": entry.get("source", "oauth"),
            })
        return True

    growth_info = ""

    def _run_growth() -> None:
        nonlocal growth_info
        try:
            g = client.growth()
            if g.get("auth_lost"):
                growth_info = "成长中心：登录态已失效，请重新扫码登录"
            elif g.get("steps") or g.get("credited"):
                growth_info = "成长中心：" + g.get("summary", "")
        except Exception as e:  # noqa: BLE001
            growth_info = f"成长中心异常：{type(e).__name__}: {e}"

    def _auth_fail(msg: str) -> str:  # noqa: ARG001
        return "签到失败：登录态已失效（HTTP 401/403），请重新扫码登录（账号管理 → WorkBuddy → 扫码登录）"

    try:
        st = client.status()
        if st.get("http") in (401, 403) and _refresh_once():
            client = _build()
            st = client.status()
        if st.get("http") in (401, 403):
            result["desc"] = _auth_fail(st.get("message") or "令牌已过期或无权限")
            return result
        if st.get("ok") and st.get("today_checked_in"):
            result["ok"] = True
            result["desc"] = (f"今日已签到，累计 {st.get('credit')} 积分"
                              f"（连续第 {st.get('streak_days')} 天）")
            if growth:
                _run_growth()
            if growth_info:
                result["desc"] += "；" + growth_info
            return result
        res = client.checkin()
        if res.get("http") in (401, 403) and _refresh_once():
            client = _build()
            res = client.checkin()
        if res.get("http") in (401, 403):
            result["desc"] = _auth_fail(res.get("message") or "令牌已过期或无权限")
            return result
        if res.get("ok"):
            result["ok"] = True
            result["desc"] = (("今日已签到（幂等）" if res.get("already") else "签到成功")
                              + f"，累计 {res.get('credit')} 积分"
                              + f"（连续第 {res.get('streak_days')} 天）")
            if growth:
                _run_growth()
            if growth_info:
                result["desc"] += "；" + growth_info
            return result
        result["desc"] = f"签到失败：{res.get('message')}（HTTP {res.get('http')}）"
    except WorkBuddyError as e:
        result["desc"] = f"签到失败：{e}"
    except Exception as e:  # noqa: BLE001
        result["desc"] = f"签到失败：{type(e).__name__}: {e}"
    result["desc"] = _redact(result["desc"])
    return result


def list_accounts_json(config_path: str | None = None) -> str:
    """多账号脱敏列表（不含 token 明文）。"""
    entries = load_workbuddy_accounts(config_path)
    now_ms = int(time.time() * 1000)
    out = []
    for e in entries:
        exp = e["expires_at"]
        state = ("expired" if exp and exp < now_ms
                 else "valid" if exp else "unknown")
        out.append({
            "name": e["name"],
            "enabled": e["enabled"],
            "uid": e["uid"],
            "nickname": e["nickname"],
            "source": e["source"],
            "expires_at": exp,
            "state": state,
            "token_mask": mask_token(e["token"]) if e["token"] else "(空)",
        })
    return json.dumps({"count": len(out), "accounts": out},
                      ensure_ascii=False, indent=2)


def checkin_all_json(config_path: str | None = None, growth: bool = True) -> tuple:
    """多账号归并签到，返回 (JSON 字符串, 退出码)。结果与描述均脱敏。"""
    entries = load_workbuddy_accounts(config_path)
    results = []
    for e in entries:
        if not e["enabled"]:
            continue
        if not e["token"]:
            # 本机登录态回退账号（旧版单账号路径，accounts.json 未存 token）：
            # 实时读取本机登录态文件签到，保持兼容；本机也无登录态时给出提示。
            try:
                e["token"] = _read_token()
            except WorkBuddyError:
                results.append({
                    "name": e["name"],
                    "source": e["source"],
                    "ok": False,
                    "desc": "签到失败：账号无登录态，请先扫码登录（账号管理 → WorkBuddy → 扫码登录）",
                })
                continue
        r = checkin_account(e, config_path=config_path, growth=growth)
        r["desc"] = _redact(r["desc"])
        results.append(r)
    ok = sum(1 for r in results if r["ok"])
    payload = {
        "count": len(results),
        "success": ok,
        "failed": len(results) - ok,
        "results": results,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2), (0 if ok == len(results) else 1)


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
    parser = argparse.ArgumentParser(description="WorkBuddy 登录态探测 / 签到自检 / 多账号管理")
    parser.add_argument("--probe", action="store_true", help="探测本机 WorkBuddy 登录态（输出脱敏 JSON）")
    parser.add_argument("--credits", action="store_true", help="查询本机登录态签到状态/积分（自检）")
    parser.add_argument("--checkin", action="store_true", help="执行本机登录态签到（自检）")
    parser.add_argument("--growth", action="store_true", help="执行本机登录态成长中心六步（自检）")
    parser.add_argument("--list-accounts", action="store_true", help="列出 WorkBuddy 多账号（脱敏 JSON）")
    parser.add_argument("--checkin-all", action="store_true", help="多账号签到（按账号归并输出 JSON，脱敏）")
    parser.add_argument("--config", default=None, help="accounts.json 路径（默认项目目录）")
    parser.add_argument("--no-growth", action="store_true", help="--checkin-all 时跳过成长中心")
    args = parser.parse_args()

    if args.list_accounts:
        print(list_accounts_json(args.config))
        return
    if args.checkin_all:
        text, code = checkin_all_json(args.config, growth=not args.no_growth)
        print(text)
        sys.exit(code)
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
