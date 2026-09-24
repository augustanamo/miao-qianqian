#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
WorkBuddy 专用签到 API 客户端（仅标准库，无第三方依赖）

原理（参考开源 workbuddy-checkin 协议，本项目内以 Python 实现）：
  - WorkBuddy 桌面端会把当前登录态写入
      ~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info
    老版本里 j["auth"]["accessToken"] 是明文 Bearer JWT（等同账号密码），本模块读它即可。
  - ⚠️ **2026-09-24 起新版桌面端把敏感字段改成加密存储**：
      {"$wbEncrypted": 1, "envelope": "<base64 信封>"}
    加解密实现是 `app.asar/main/credential-protection.js` + 原生模块 `turing_sdk.node`
    （腾讯 TuringShield），密钥不落盘 —— 于是**本机登录态这条路对第三方不可读**。
    此时 `read_auth()` 会置 `encrypted=True` 并给出明确 reason；正确做法是改用
    `workbuddy_login.py` 的 OAuth 扫码登录（那条路不依赖桌面端）。
  - 本模块读取该文件（仅内存使用）后，调用腾讯官方接口：
      POST /v2/billing/meter/checkin-activity-status  查今日签到状态（**必须带 /v2**）
      POST /v2/billing/meter/daily-checkin            执行签到（幂等）
      POST /billing/meter/get-user-resource-summary   积分余额/档位聚合
      POST /billing/meter/get-user-resource-free-packages   免费/赠送额度包明细
      POST /billing/meter/get-user-resource-paid-packages   付费额度包明细
    header: Authorization: Bearer <token>，body: {} 或分页参数
  注意网关路由不一致：签到类在 /v2 下，资源类**不带** /v2（带 /v2 会 404）。

凭据安全：accessToken / refreshToken 只在函数内存中传递，
绝不写入日志、不回显到 stdout / UI，accounts.json 只保存非敏感快照字段。

用法：
    from workbuddy import WorkBuddyClient
    c = WorkBuddyClient(token="...", uid="...", domain="...")
    print(c.status())     # 签到状态：today_checked_in / streak_days / total_credits ...
    print(c.checkin())    # 签到：{ok, already, credit, streak_days}
    print(c.resources())  # 积分余额 + 逐包消费明细（{total, remain, used, packages})
"""

import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import subtasks

BASE = "https://copilot.tencent.com"
TIMEOUT = 30
_UA = "WorkBuddyCheckin/1.0 (macOS; AutoCheck)"

# 新版明文登录态路径（WorkBuddy v5.3.8+，macOS）
AUTH_REL = os.path.join(
    "Library", "Application Support", "CodeBuddyExtension",
    "Data", "Public", "auth", "workbuddy-desktop.info",
)


# ----------------------------------------------------------------------
# 计费接口（积分余额 / 消费明细）
#
# 端点全部从 WorkBuddy 客户端 app.asar 里扒出来，与官网同源：
#   POST /billing/meter/get-user-resource-summary        余额/档位聚合，无业务参数
#   POST /billing/meter/get-user-resource-free-packages  免费/赠送包明细（分页）
#   POST /billing/meter/get-user-resource-paid-packages  付费包明细（分页）
#   POST /billing/meter/get-enterprise-user-usage        企业版月度额度
#
# 网关路由有个坑：**签到类接口在 /v2 下，资源类接口不带 /v2**
# （实测 /v2/billing/meter/get-user-resource-free-packages 直接 404 Route Not Found）。
# ----------------------------------------------------------------------
V2 = "/v2"

# 套餐代码（客户端 CommodityCode 枚举原样抄下来，别手改）
COMMODITY_CODE = {
    "free": "TCACA_code_001_PqouKr6QWV",
    "proMon": "TCACA_code_002_AkiJS3ZHF5",
    "proYear": "TCACA_code_003_FAnt7lcmRT",
    "proMonPlus": "TCACA_code_005_maRGyrHhw1",
    "gift": "TCACA_code_006_DbXS0lrypC",
    "activity": "TCACA_code_007_nzdH5h4Nl0",
    "freeMon": "TCACA_code_008_cfWoLwvjU4",
    "extra": "TCACA_code_009_0XmEQc2xOf",
    "youth": "TCACA_code_023_4xbGhMrE6q",
    "advanced": "TCACA_code_026_BaESVICNoi",
    "flagship": "TCACA_code_027_0FCGVA6vSa",
    "bonus28": "TCACA_code_028_NtpWi0jzXs",
    "bonus29": "TCACA_code_029_6wCGEWquYy",
    "bonus30": "TCACA_code_030_BjSt89qTvr",
    "freeMonIntl": "TCACA_code_035_ArVxJcGDsm",
    "extraIntl": "TCACA_code_036_lupO5WgNdG",
    "bonusIntl": "TCACA_code_037_WxOD3MpI2o",
    "extra38": "TCACA_code_038_OhvqZtiPKr",
    "proTrialMon": "TCACA_code_039_BxUvNc8NxS",
    "proTrialYear": "TCACA_code_040_mi9rCYg46x",
}
# 付费包码集：付费订阅档 + 加量包（不含免费月包，它归 api3）
PAID_PACKAGE_CODES = [COMMODITY_CODE[k] for k in
                      ("proMon", "proMonPlus", "proYear", "youth", "advanced",
                       "flagship", "extra", "extra38", "extraIntl")]
# 免费包码集：免费月包 / 体验包 / 试用切片包 / 运营赠送 / 权益赠送 / 版本赠送
FREE_PACKAGE_CODES = [COMMODITY_CODE[k] for k in
                      ("free", "freeMon", "freeMonIntl", "gift", "proTrialMon",
                       "proTrialYear", "activity", "bonus28", "bonusIntl",
                       "bonus29", "bonus30")]

# 摘要接口（api1）只回 PackageCode、不回名字，这里给短名兜底；
# 明细接口（api2/api3）会回 PackageName，优先用它的。
PACKAGE_SHORT_NAME = {
    COMMODITY_CODE["free"]: "免费版",
    COMMODITY_CODE["freeMon"]: "个人体验版",
    COMMODITY_CODE["freeMonIntl"]: "免费月包",
    COMMODITY_CODE["gift"]: "Pro 试用包",
    COMMODITY_CODE["activity"]: "运营活动赠送包",
    COMMODITY_CODE["proMon"]: "Pro 包月",
    COMMODITY_CODE["proMonPlus"]: "Pro 包月（加量）",
    COMMODITY_CODE["proYear"]: "Pro 包年",
    COMMODITY_CODE["youth"]: "青春版",
    COMMODITY_CODE["advanced"]: "进阶版",
    COMMODITY_CODE["flagship"]: "旗舰版",
    COMMODITY_CODE["extra"]: "加量包",
    COMMODITY_CODE["extra38"]: "加量包",
    COMMODITY_CODE["extraIntl"]: "加量包",
    COMMODITY_CODE["bonus28"]: "版本赠送包",
    COMMODITY_CODE["bonus29"]: "版本赠送包",
    COMMODITY_CODE["bonus30"]: "版本赠送包",
    COMMODITY_CODE["bonusIntl"]: "版本赠送包",
    COMMODITY_CODE["proTrialMon"]: "Pro 试用（月）",
    COMMODITY_CODE["proTrialYear"]: "Pro 试用（年）",
}

CAPACITY_TYPE_SLICE = 4        # 切片包（按日切额度）的 CapacityType
ACCOUNT_STATUS_VALID = 0       # 包状态：有效
ACCOUNT_STATUS_USED_UP = 3     # 包状态：已用完
_PAGE_SIZE = 200
_MAX_PAGES = 10


def fmt_num(v) -> str:
    """积分展示：整数不带小数点，小数最多两位。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "0"
    return ("%d" % round(f)) if abs(f - round(f)) < 0.005 else ("%.2f" % f)


def _fnum(v) -> float:
    """客户端 toCount 同口径：非有限数或 <=0 一律记 0。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if f > 0 else 0.0


def _slice_period_range(now=None) -> dict:
    """切片包的"当日窗口"，与官网 getCnPlan 的 startOf('D')/endOf('D') 同口径。"""
    d = now or datetime.datetime.now()
    return {
        "SlicePeriodStartTime": d.strftime("%Y-%m-%d 00:00:00"),
        "SlicePeriodEndTime": d.strftime("%Y-%m-%d 23:59:59"),
    }


def _capacity_of(raw: dict) -> tuple:
    """解析一个资源包的 (总量, 剩余, 已用, 是否当日口径)。

    切片包优先读当期切片明细，这时数字是**当日**口径；
    缺明细时回落周期额度（后端对国内体验版这类切片包只下发
    CycleCapacity*Precise、不返回 SlicePeriodUsageDetails，
    硬读切片会让额度整行显示成 0，客户端 #105166 就是这个坑），
    此时数字是**周期**口径 —— 必须如实标出来，别让界面把周期值写成"今日剩余"。
    """
    if int(raw.get("CapacityType") or 0) == CAPACITY_TYPE_SLICE:
        detail = raw.get("SlicePeriodUsageDetails") or []
        if detail and isinstance(detail[0], dict):
            s = detail[0]
            total = _fnum(s.get("SlicePeriodCapacitySizePrecise"))
            left = _fnum(s.get("SlicePeriodCapacityRemainPrecise"))
            return total, left, max(0.0, total - left), True
    total = _fnum(raw.get("CycleCapacitySizePrecise"))
    left = _fnum(raw.get("CycleCapacityRemainPrecise"))
    return total, left, max(0.0, total - left), False


def _package_view(raw: dict, group: str) -> dict:
    """把 api2/api3 的一个 Accounts 条目归一成展示用结构（不含任何凭据）。"""
    total, left, used, daily = _capacity_of(raw)
    code = str(raw.get("PackageCode") or "")
    ctype = int(raw.get("CapacityType") or 0)
    return {
        "package_code": code,
        "name": (raw.get("PackageName")
                 or PACKAGE_SHORT_NAME.get(code)
                 or raw.get("SubProductName")
                 or "额度包"),
        "group": group,
        "total": total,
        "remain": left,
        "used": used,
        "unit": raw.get("CapacityUnit") or "credits",
        # 数字确实是"当日"口径时才为 True（见 _capacity_of 的说明）
        "slice": daily,
        "capacity_type": ctype,
        "cycle_start": raw.get("CycleStartTime") or "",
        "cycle_end": raw.get("CycleEndTime") or "",
        "resource_id": str(raw.get("ResourceId") or ""),
    }


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


def is_encrypted_field(v) -> bool:
    """是不是桌面端的 at-rest 加密字段。

    新版桌面端把敏感字段写成 `{"$wbEncrypted": 1, "envelope": "<base64>"}`
    （实现见 `app.asar/main/credential-protection.js`，真实加解密在原生模块
    `turing_sdk.node` 里，密钥不落盘）。所以这种字段**不是**"用户没登录"，
    而是"登录了但对我们不可读"—— 两者要给完全不同的提示。
    """
    return (isinstance(v, dict) and v.get("$wbEncrypted") == 1
            and isinstance(v.get("envelope"), str))


def plain_str(v) -> str:
    """只接受明文字符串。加密信封一律当空，**绝不把 envelope 透出去**。"""
    return v if isinstance(v, str) else ""


def read_auth() -> dict:
    """读取本机 WorkBuddy 登录态，返回脱敏安全视图（不含 token 明文）。

    返回 dict：
      {found, has_token, encrypted, uid, nickname, uin, domain, enterprise_id,
       expires_at, reason}
    found=False 时 reason 说明失败原因（用于 UI 提示）。
    """
    out = {
        "found": False,
        "has_token": False,
        "encrypted": False,
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
    raw_token = auth.get("accessToken")
    out["found"] = True
    out["has_token"] = bool(plain_str(raw_token).strip())
    out["uid"] = plain_str(acct.get("uid"))
    out["nickname"] = plain_str(acct.get("nickname"))
    out["uin"] = plain_str(acct.get("uin"))
    out["domain"] = plain_str(auth.get("domain"))
    out["enterprise_id"] = plain_str(acct.get("enterpriseId"))
    exp = auth.get("expiresAt") or auth.get("expiresIn")
    out["expires_at"] = str(exp) if exp is not None else ""

    # 桌面端升级后把凭据改成加密存储。必须**如实说**：原来那句"请重新登录桌面端"
    # 会让用户去重登一遍，而重登完仍然是加密的 —— 症状一模一样，白折腾。
    out["encrypted"] = is_encrypted_field(raw_token)
    if out["encrypted"]:
        out["reason"] = ("WorkBuddy 桌面端把登录凭据改成了加密存储（密钥在原生模块里，不落盘），"
                         "本工具读不到明文 accessToken。请改用「扫码登录」添加 WorkBuddy 账号。")
    elif not out["has_token"]:
        out["reason"] = "本机 WorkBuddy 登录态中缺少 accessToken，请先打开 WorkBuddy 桌面端登录"
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
    token = plain_str((j.get("auth") or {}).get("accessToken"))
    if not token.strip():
        # 走到这儿说明读盘之后又变成了非明文（探测与读取之间被桌面端改写）
        raise WorkBuddyError("WorkBuddy 登录态缺少明文 accessToken（可能是桌面端加密字段）")
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
            result["desc"] = (f"今日已签到，本活动累计获得 "
                              f"{fmt_num(st.get('total_credits'))} 积分"
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
            result["desc"] = (("今日已签到（幂等）" if res.get("already")
                               else f"签到成功，本次获得 {fmt_num(res.get('credit'))} 积分")
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
            # 「本机登录态」来源的账号（accounts.json 未存 token）：凭据只有桌面端有，
            # 所以实时读一次；读不到就签不了。
            try:
                e["token"] = _read_token()
            except WorkBuddyError as exc:
                # 把**真实原因**带出去，不要写死一句"请先扫码登录"：
                # 桌面端把凭据加密之后，"请先打开桌面端登录"这类老话会把用户引到死路
                # （重登桌面端症状一模一样）。exc 里已经说明了真实原因与出路（扫码登录）。
                results.append({
                    "name": e["name"],
                    "source": e["source"],
                    "ok": False,
                    "desc": f"签到失败：账号无登录态（本机登录态来源）——{exc}",
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

    def _billing(self, path: str, payload: dict | None = None,
                 method: str = "POST", extra: dict | None = None) -> tuple:
        """打一个计费/活动接口，返回 (http_status, body_dict)。

        统一带上客户端在用的身份头（uid / 企业 / 域，缺了服务端会直接拒）。
        token 只出现在请求头里，绝不落盘、绝不进日志。
        """
        headers = {
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Language": "zh",
            "User-Agent": _UA,
        }
        if self.uid:
            headers["X-User-Id"] = self.uid
        if self.enterprise_id:
            headers["X-Enterprise-Id"] = self.enterprise_id
            headers["X-Tenant-Id"] = self.enterprise_id
        if self.domain:
            headers["X-Domain"] = self.domain
        if extra:
            headers.update(extra)
        data = (json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
                if method == "POST" else None)
        req = urllib.request.Request(BASE + path, data=data,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.status, _loads_body(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            return e.code, _loads_body(e.read().decode("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001
            return -1, {"message": "%s: %s" % (type(e).__name__, e)}

    @staticmethod
    def _payload(http: int, body: dict) -> tuple:
        """拆 {code, msg, data} 信封，返回 (code, message, data)。"""
        code = body.get("code")
        code = code if isinstance(code, int) else -1
        message = (body.get("msg") or body.get("message")
                   or body.get("error_msg") or ("HTTP %s" % http))
        data = body.get("data")
        return code, str(message), (data if isinstance(data, dict) else {})

    # 签到状态：客户端当前用的是 /v2 那条。老路径 /billing/meter/checkin-status
    # 仍在，但实测它长期回 active=false / total_credits=0，已不是真实活动态，只作兜底。
    CHECKIN_STATUS_PATHS = (
        V2 + "/billing/meter/checkin-activity-status",
        "/billing/meter/checkin-status",
    )
    # 签到动作：两条都通，优先客户端在用的 /v2。
    CHECKIN_PATHS = (
        V2 + "/billing/meter/daily-checkin",
        "/billing/meter/daily-checkin",
    )

    @staticmethod
    def _status_view(http: int, body: dict, code: int, message: str, data: dict) -> dict:
        """把签到状态回包归一成固定字段集（缺失一律补 0/空，不留 None）。"""
        total = data.get("total_credits")
        return {
            "http": http,
            "code": code,
            "message": message,
            "ok": (http == 200 and code == 0),
            "active": bool(data.get("active", False)),
            "today_checked_in": bool(data.get("today_checked_in", False)),
            "streak_days": int(_fnum(data.get("streak_days"))),
            "daily_credit": _fnum(data.get("daily_credit")),
            "today_credit": _fnum(data.get("today_credit")),
            # 「本活动累计获得」，不是账户可用余额 —— 余额走 resources()["remain"]
            "total_credits": _fnum(total),
            # 兼容旧字段名：老调用方读的是 credit，挂同一个值，别让它踩空
            "credit": _fnum(total),
            "week_checkin_days": int(_fnum(data.get("week_checkin_days"))),
            "checkin_dates": data.get("checkin_dates") or [],
            "activity_name": data.get("activity_name") or "",
            "theme_name": data.get("theme_name") or "",
            "season": int(_fnum(data.get("season"))),
            "start_time": data.get("start_time") or "",
            "end_time": data.get("end_time") or "",
            "claim_button_text": data.get("claim_button_text") or "",
            "raw": body,
        }

    def checkin(self) -> dict:
        """执行每日签到。幂等：code=10001（今日已签到）视为成功。

        返回 dict：{ok, http, code, message, already, credit, streak_days,
                    is_streak_day}
        credit 是**本次签到获得**的积分，不是账户余额。
        """
        if not self.token:
            raise WorkBuddyError("缺少 accessToken")
        last = None
        for path in self.CHECKIN_PATHS:
            http, body = self._billing(path)
            code, message, data = self._payload(http, body)
            r = {
                "http": http, "code": code, "message": message, "ok": False,
                "already": False, "credit": 0.0, "streak_days": 0,
                "is_streak_day": False, "raw": body,
            }
            if http in (401, 403):
                r["message"] = (f"令牌已过期或无权限（HTTP {http}），"
                                f"请打开 WorkBuddy 桌面端刷新登录态")
                return r
            # 回包把 credit / streak_days 放在顶层，也有版本塞进 data，两处都读
            for src in (data, body):
                if r["credit"] == 0.0 and src.get("credit") is not None:
                    r["credit"] = _fnum(src.get("credit"))
                if not r["streak_days"] and src.get("streak_days") is not None:
                    r["streak_days"] = int(_fnum(src.get("streak_days")))
                if src.get("is_streak_day") is not None:
                    r["is_streak_day"] = bool(src.get("is_streak_day"))
            if http == 200 and code == 0:
                r["ok"] = True
                return r
            if code == 10001:
                # 幂等：今日已签到，视为成功，不重复领取
                r["ok"] = True
                r["already"] = True
                r["message"] = message or "今日已签到（幂等）"
                return r
            last = r
        return last

    def status(self) -> dict:
        """查询今日签到状态（只读，无副作用）。

        走客户端当前在用的 /v2/billing/meter/checkin-activity-status；
        老路径只作兜底（见 CHECKIN_STATUS_PATHS 的注释）。

        返回 dict：{ok, http, code, message, active, today_checked_in,
                    streak_days, daily_credit, today_credit, total_credits,
                    credit, week_checkin_days, checkin_dates, activity_name,
                    theme_name, season, start_time, end_time, claim_button_text}
        """
        if not self.token:
            raise WorkBuddyError("缺少 accessToken")
        last = None
        for path in self.CHECKIN_STATUS_PATHS:
            http, body = self._billing(path)
            code, message, data = self._payload(http, body)
            view = self._status_view(http, body, code, message, data)
            if http in (401, 403):
                view["message"] = (f"令牌已过期或无权限（HTTP {http}），"
                                   f"请打开 WorkBuddy 桌面端刷新登录态")
                return view
            last = view
            if view["ok"]:
                return view
        return last

    # ------------------------------------------------------------------ #
    # 积分余额 / 消费明细                                                  #
    # ------------------------------------------------------------------ #
    BILLING_SUMMARY = "/billing/meter/get-user-resource-summary"
    BILLING_FREE = "/billing/meter/get-user-resource-free-packages"
    BILLING_PAID = "/billing/meter/get-user-resource-paid-packages"

    def _resource_summary(self) -> dict:
        """余额/档位聚合（无业务参数）。"""
        http, body = self._billing(self.BILLING_SUMMARY)
        code, message, data = self._payload(http, body)
        return {"http": http, "code": code, "message": message, "data": data}

    def _resource_packages(self, group: str) -> dict:
        """逐页取全一路资源包明细（最多 _MAX_PAGES 页，超出必须显式告警）。"""
        path = self.BILLING_FREE if group == "free" else self.BILLING_PAID
        codes = FREE_PACKAGE_CODES if group == "free" else PAID_PACKAGE_CODES
        extra = {"NeedRenewInfo": True} if group == "paid" else _slice_period_range()
        collected, reported, note = [], 0, ""
        for page in range(1, _MAX_PAGES + 1):
            http, body = self._billing(path, {
                "PageNumber": page,
                "PageSize": _PAGE_SIZE,
                "PackageCodes": codes,
                "Status": [ACCOUNT_STATUS_VALID, ACCOUNT_STATUS_USED_UP],
                **extra,
            })
            code, message, data = self._payload(http, body)
            if http in (401, 403):
                return {"http": http, "code": code, "message": "登录态已失效",
                        "packages": [], "reported": 0, "note": ""}
            if http != 200 or code != 0:
                return {"http": http, "code": code, "message": message,
                        "packages": [], "reported": 0, "note": ""}
            accounts = [a for a in (data.get("Accounts") or []) if isinstance(a, dict)]
            collected.extend(_package_view(a, group) for a in accounts)
            reported = int(_fnum(data.get("TotalCount"))) or reported
            if len(accounts) < _PAGE_SIZE or (reported and len(collected) >= reported):
                break
        if reported > len(collected):
            # 权益赠送包（每日签到等）会无上限累积，截断时明细与合计都会偏小，不能静默
            note = "明细分页截断：只取到 %s/%s 条，合计偏小" % (len(collected), reported)
        return {"http": 200, "code": 0, "message": "", "packages": collected,
                "reported": reported, "note": note}

    def resources(self) -> dict:
        """查询积分余额与消费明细（只读，无副作用）。

        三路并发对应客户端的三个计费接口（与官网同源）：
          summary（api1） → 余额/档位聚合，**合计以它为准**
          free（api3）/ paid（api2） → 逐包明细：名称、周期、总量/已用/剩余

        口径说明：服务端只按**资源包周期**给已用量，逐次会话的 token 消耗是
        客户端本地算的、不落库，所以"消费明细"的最细粒度就是这个包级视图。

        返回 dict：
          {ok, http, message, unit, total, remain, used,
           is_paid_user, subscription_package_code, packages, sources_failed}
        """
        if not self.token:
            raise WorkBuddyError("缺少 accessToken")
        out = {
            "ok": False, "http": 0, "message": "", "unit": "credits",
            "total": 0.0, "remain": 0.0, "used": 0.0,
            "is_paid_user": False, "subscription_package_code": "",
            "packages": [], "sources_failed": [],
        }

        def _job_summary() -> dict:
            r = self._resource_summary()
            r["source"] = "summary"
            return r

        def _job_packages(group: str) -> dict:
            r = self._resource_packages(group)
            r["source"] = group
            return r

        got: dict = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(_job_summary),
                       pool.submit(_job_packages, "free"),
                       pool.submit(_job_packages, "paid")]
            for fut in as_completed(futures):
                try:
                    r = fut.result()
                except Exception as e:  # noqa: BLE001
                    out["sources_failed"].append("%s: %s" % (type(e).__name__, e))
                    continue
                got[r["source"]] = r

        # 登录态失效是全局性的：任一路回 401/403 就别拼半个结果给用户看
        for r in got.values():
            if r.get("http") in (401, 403):
                out["http"] = r["http"]
                out["message"] = ("令牌已过期或无权限（HTTP %s），"
                                  "请重新登录 WorkBuddy" % r["http"])
                return out

        packages, notes = [], []
        for group in ("free", "paid"):
            r = got.get(group)
            if r is None or r.get("http") != 200:
                out["sources_failed"].append(group)
                continue
            packages.extend(r.get("packages") or [])
            if r.get("note"):
                notes.append(r["note"])

        # 同一资源包可能同时落在摘要与明细里，按 ResourceId 去重
        seen, deduped = set(), []
        for p in packages:
            key = p.get("resource_id") or (p.get("package_code"), p.get("cycle_start"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(p)
        out["packages"] = deduped
        if deduped:
            out["unit"] = deduped[0].get("unit") or "credits"

        s = got.get("summary")
        if s is not None and s.get("http") == 200 and s.get("code") == 0:
            data = s.get("data") or {}
            out["is_paid_user"] = bool(data.get("IsPaidUser"))
            out["subscription_package_code"] = data.get("SubscriptionPackageCode") or ""
            total = remain = used = 0.0
            for item in data.get("Packages") or []:
                if not isinstance(item, dict):
                    continue
                total += _fnum(item.get("CycleTotalCapacity"))
                remain += _fnum(item.get("CycleRemainCapacity"))
                used += _fnum(item.get("CycleUsedCapacity"))
            out["total"], out["remain"], out["used"] = total, remain, used
            out["http"] = 200
            out["ok"] = True
        elif deduped:
            # 摘要挂了就靠明细兜底：切片包是"当日"口径，混进合计会失真，排除掉
            cycle = [p for p in deduped if not p.get("slice")]
            out["total"] = sum(p["total"] for p in cycle)
            out["remain"] = sum(p["remain"] for p in cycle)
            out["used"] = sum(p["used"] for p in cycle)
            out["http"] = 200
            out["ok"] = True
            notes.append("汇总接口不可用，合计由明细求和（不含按日切片包）")
        else:
            out["message"] = (s or {}).get("message") or "未取到额度信息"
        if notes:
            out["message"] = "；".join(notes)
        return out

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
            # 用 note 而不是 msg：旅行中属于"进行态"，不是收益也不是失败，
            # 按纪律不该进日志；但图标要保住橙色，靠的是 note 以
            # running_prefix（"Buddy 旅行中"）开头 —— 见 growth() 里的 _exec。
            return True, "", 0, "Buddy 旅行中（%s），回来后领礼物" % loc
        if state == "idle":
            if daily_limit:
                return True, "", 0, "今日旅行名额已用完（明天可再派）"
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
        # state 不是 arrived/traveling/idle：可能是服务端新增了状态，也可能
        # 是字段名变了。把读到的原值摆出来，免得它静默变成一个没有解释的灰图标。
        return True, "", 0, "旅行状态未识别（state=%s）" % (state or "未读到")

    def _growth_tasks(self) -> tuple:
        """任务：未领取的任务先领取（进度从领取起计）；已完成且已领取的领奖。"""
        code, body = self._growth_request("/tasks")
        if code in (401, 403):
            return False, "登录态已失效（HTTP %s）" % code, 0
        if code != 200:
            return False, "查任务列表失败（HTTP %s）" % code, 0
        raw = _dig(body, "tasks")
        tasks = raw if isinstance(raw, list) else []
        if not tasks:
            # 顺手把"读到几条"写进原因：字段名若被服务端改掉，这里会露出 0 条，
            # 一眼就能区分"真的没任务"和"我们没解析出来"。
            return True, "", 0, "没有可领的任务奖励（从服务端读到 0 条任务）"
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
        if not msgs:
            return True, "", 0, "任务都已领取或还没完成（共读到 %s 条）" % len(tasks)
        return True, "；".join(msgs), got

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
        if cards <= 0:
            # 第 4 项 note 只在 idle 时进 tooltip：不写日志（否则每天都要说一句
            # "没有补登卡"，把日志淹掉），但悬停时能回答"这个图标为什么是灰的"。
            return True, "", 0, "没有补登卡可用"
        if not (isinstance(dates, list) and dates):
            return True, "", 0, "补登卡 %s 张，但当前连签没有中断、没有可补的日期" % cards
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
        got, msgs, seen = 0, [], []
        for tier, label, days in (("starter", "入门", 7),
                                  ("advanced", "进阶", 14),
                                  ("legendary", "巅峰", 28)):
            status_ = _dig(body, tier + "_status")
            # 记下读到的**原始状态**：idle 时写进 tooltip。既回答"为什么这个图标是灰的"，
            # 也让"字段名到底读没读到"在界面上一眼可验 —— 服务端给的是 locked/claimed，
            # 若这里恒显示"未读到"，就说明我们对 /redeem/summary 的字段名猜错了。
            seen.append("%s %s" % (label, status_ or "未读到"))
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
        if not msgs:
            return True, "", 0, "三档都没有可领的（" + "、".join(seen) + "）"
        return True, "；".join(msgs), got

    def _growth_lottery(self, max_draws: int = 1) -> tuple:
        """抽奖/开盲盒：有机会才抽，每轮最多 max_draws 次。"""
        code, body = self._growth_request("/lottery/chances")
        if code in (401, 403):
            return False, "登录态已失效（HTTP %s）" % code, 0
        if code != 200:
            return False, "查抽奖机会失败（HTTP %s）" % code, 0
        chances = int(_dig(body, "balance") or 0)
        if chances <= 0:
            return True, "", 0, "今天没有抽奖机会（机会由签到与成长任务发放）"
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
            return True, "", 0, "能量还不够开一次盲盒（当前可开 0 次）"
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
                    steps, summary, tasks}

        `steps` 是给日志看的短句，`tasks` 是给桌面端任务表画图标的逐项状态。
        两个都要给：短句回答"今天值不值得说一句"（安静是默认），图标回答"这六步
        各自什么状态"。少任何一边，另一边就得被迫说谎。
        """
        steps, tasks, credited, auth_lost = [], [], 0, False

        def _exec(key: str, label: str, fn,
                  running_prefix: str | None = None,
                  idle_prefix: str | None = None) -> None:
            nonlocal credited, auth_lost
            try:
                r = fn()
                # 步骤函数可以多返回一项 `note`（第 4 项）：**只进 tooltip，不进日志**。
                # 为什么不让它走 msg：msg 会被 append 进 steps，而 steps 进日志；
                # 项目的纪律是"只有本次有收益和真失败才出声"，天天把"今天没有可领的"
                # 写进日志就把日志淹了。所以原因只进 detail（悬停才看）。
                # note 还兼一个作用：以 running_prefix 开头时判为 running，这样
                # "Buddy 正在旅行"这类**进行态**既能保住橙色图标、又不必写日志。
                ok, msg, got = r[0], r[1], r[2]
                note = r[3] if len(r) > 3 else ""
                if msg:
                    steps.append(msg)
                if got:
                    credited += int(got)
                # 登录态失效要看**文案本身**，不能只看 ok：`_growth_tasks` 中途撞到
                # 401 时仍按"查询跑通了"返回 ok=True，只认 ok 会把"token 已死"漏判
                # 成成长中心一切正常。
                if "登录态已失效" in (msg or ""):
                    auth_lost = True
                state = subtasks.from_step(ok, msg, running_prefix, idle_prefix)
                if (state == subtasks.IDLE and not msg and note
                        and running_prefix and note.startswith(running_prefix)):
                    state = subtasks.RUNNING
                tasks.append(subtasks.task(key, label, state, msg or note))
            except Exception as e:  # noqa: BLE001
                detail = "%s异常：%s: %s" % (label, type(e).__name__, e)
                steps.append(detail)
                # 界面上这一步的图标会变红，日志里就必须有对应的原因，不能只留个空
                tasks.append(subtasks.task(key, label, subtasks.FAIL, detail))

        # key 是机器标识（Swift 靠它选图标，改了等于改契约），label 是 tooltip 里的任务名。
        # 只有旅行这一步需要 running_prefix：它有一种"既非成功也非失败"的进行态，
        # 靠 note 以这个前缀开头来认（见 _exec）。idle 的原因一律走 note 的第 4 项，
        # 不再借 idle_prefix —— 那条路要求 msg 非空，会把稳态字句带进日志。
        _exec("travel", "Buddy 旅行", self._growth_travel,
              running_prefix="Buddy 旅行中")
        _exec("tasks", "任务奖励", self._growth_tasks)
        _exec("makeup", "补登", lambda: self._growth_makeup(max_per_run=makeup_max))
        _exec("redeem", "连登兑换", self._growth_redeem)
        _exec("lottery", "抽盲盒", self._growth_lottery)
        _exec("buddy_box", "能量盲盒", self._growth_buddy_box)

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
            # 六步各自的逐项状态，桌面端任务表「今日任务」列按此渲染图标
            "tasks": tasks,
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
    parser.add_argument("--credits", action="store_true", help="查询本机登录态签到状态/活动积分（自检）")
    parser.add_argument("--resources", action="store_true", help="查询本机登录态积分余额与消费明细（自检）")
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

    try:
        token = _read_token()
    except WorkBuddyError as e:
        # 自检入口依赖"本机登录态文件"；多账号请用 --list-accounts / --checkin-all
        print(f"未找到可用的本机 WorkBuddy 登录态：{e}")
        sys.exit(2)
    client = WorkBuddyClient(token)
    if args.growth:
        r = client.growth()
        print(r["summary"])
        if r.get("auth_lost"):
            print("成长中心中止：登录态已失效")
        return
    if args.resources:
        r = client.resources()
        if not r.get("ok"):
            print(f"查询失败：{r.get('message')}（HTTP {r.get('http')}）")
            return
        print(f"剩余 {fmt_num(r['remain'])} / 总量 {fmt_num(r['total'])}，"
              f"已用 {fmt_num(r['used'])}（{r['unit']}）")
        for p in r["packages"]:
            span = (f"，周期 {p['cycle_start']} ~ {p['cycle_end']}"
                    if p.get("cycle_end") else "")
            print(f"  · {p['name']}（{p['group']}，"
                  f"{'当日' if p.get('slice') else '周期'}）"
                  f"剩余 {fmt_num(p['remain'])} / {fmt_num(p['total'])}，"
                  f"已用 {fmt_num(p['used'])}{span}")
        if r.get("message"):
            print(f"  注：{r['message']}")
        return
    if args.credits:
        r = client.status()
        if r.get("ok"):
            print(
                f"活动={r.get('activity_name') or '-'} "
                f"今日已签到={r.get('today_checked_in')} "
                f"本活动累计获得={fmt_num(r.get('total_credits'))} 积分 "
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
