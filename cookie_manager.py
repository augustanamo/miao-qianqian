#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用 Cookie 管理器（纯标准库）。

设计目标：
  1. 为 Bilibili / 联想智选 / 京东 等 Cookie 型平台提供统一的
     accounts.json 读写、Cookie 解析、脱敏、存在性检查能力，
     后续新增 Cookie 型平台可直接复用本模块。
  2. Cookie 等同账号密码：
     - 读取后仅在进程内存中使用，绝不写日志、绝不回显到 UI；
     - 落盘仅发生在「用户主动添加/更新账号」时，写入
       accounts.json 的 <platform>_auth.cookie 字段；
     - accounts.json 已被 .gitignore 排除，不会进入版本库。

约定：accounts.json 中 Cookie 型账号结构
    {
      "name": "bili-main",
      "app": "bilibili",
      "type": "bilibili",
      "enabled": true,
      "bilibili_auth": {
        "cookie": "SESSDATA=xxx; bili_jct=yyy",
        "nickname": "昵称",
        "uid": "12345",
        "saved_at": "2026-09-15 10:00:00",
        "updated_at": "2026-09-15 10:00:00"
      }
    }
其中 nickname / uid 等为「非敏感快照」，仅用于 UI 展示；
cookie 为敏感字段，读取后仅内存使用。
"""

import json
import os

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts.json")


class CookieError(RuntimeError):
    """Cookie 缺失、为空或格式不合法时抛出。"""


# ----------------------------------------------------------------------
# accounts.json 读写
# ----------------------------------------------------------------------
def load_config(path: str | None = None) -> dict:
    """读取 accounts.json；文件缺失/损坏时返回空结构。"""
    cfg_path = path or DEFAULT_CONFIG_PATH
    if not os.path.exists(cfg_path):
        return {"settings": {}, "accounts": []}
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        if isinstance(obj, dict):
            obj.setdefault("settings", {})
            obj.setdefault("accounts", [])
            return obj
    except (OSError, json.JSONDecodeError):
        pass
    return {"settings": {}, "accounts": []}


def save_config(config: dict, path: str | None = None) -> bool:
    """原子写回 accounts.json。"""
    cfg_path = path or DEFAULT_CONFIG_PATH
    try:
        tmp = cfg_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        os.replace(tmp, cfg_path)
        return True
    except OSError:
        return False


def find_account(config: dict, name: str) -> dict | None:
    """按账号名查找账号条目。"""
    for acc in config.get("accounts", []):
        if isinstance(acc, dict) and acc.get("name") == name:
            return acc
    return None


def auth_field(platform: str) -> str:
    """平台对应的 auth 字段名，如 bilibili -> bilibili_auth。"""
    return f"{platform}_auth"


# 少数平台的凭据不是 Cookie 而是另一种令牌，落盘字段名必须名副其实，
# 否则又会出现「标签写 Cookie、实际存的是 refresh_token」那种静默错配。
# aliyunpan：登录态是 refresh_token（存在浏览器 localStorage 里，不在 Cookie 中）。
# caimcloud：登录态是 authorization 授权码（App 抓包或网页 cookie 里的 authorization），
#            值是 `<authorization>#<手机号>` 两段——SSO 换 token 时两段都要用。
CREDENTIAL_FIELD = {
    "aliyunpan": "refresh_token",
    "caimcloud": "authorization",
}

# 凭据的中文名。界面文案必须跟字段说实话——「标签写 A、实际只吃 B」是最坏的一类
# 入口，用户会照着标签粘错东西，而且还不报错。
CREDENTIAL_LABEL = {
    "aliyunpan": "刷新令牌",
    "caimcloud": "授权码",
}


def credential_field(platform: str) -> str:
    """平台凭据在 <platform>_auth 里的字段名（默认 cookie）。"""
    return CREDENTIAL_FIELD.get(platform, "cookie")


def credential_label(platform: str) -> str:
    """凭据的中文名，用于界面文案（避免把 refresh_token 叫成 Cookie）。"""
    return CREDENTIAL_LABEL.get(platform, "Cookie")


# ----------------------------------------------------------------------
# Cookie / 凭据 读取、写入
# ----------------------------------------------------------------------
def get_cookie(acc: dict | None, platform: str, field: str | None = None) -> str:
    """从账号条目读取凭据（仅内存返回，绝不落盘/打印）。

    field 默认按 credential_field(platform) 取；若该字段为空，再回退读 "cookie"，
    这样历史上误存进 cookie 字段的账号不会突然读不到。
    """
    if not isinstance(acc, dict):
        return ""
    auth = acc.get(auth_field(platform)) or {}
    if not isinstance(auth, dict):
        return ""
    name = field or credential_field(platform)
    value = (auth.get(name) or "").strip()
    if not value and name != "cookie":
        value = (auth.get("cookie") or "").strip()
    return value


def require_cookie(acc: dict | None, platform: str, field: str | None = None) -> str:
    """读取凭据；缺失时抛 CookieError（携带可读提示）。"""
    value = get_cookie(acc, platform, field)
    if not value:
        raise CookieError(
            f"账号缺少 {platform} 的 {credential_label(platform)}，请先在账号管理中更新"
        )
    return value


def set_cookie(config: dict, name: str, platform: str, cookie: str,
               extra: dict | None = None, path: str | None = None) -> bool:
    """将 cookie 与非敏感快照写入 accounts.json 对应账号并保存。

    extra：可选的附加快照字段（如 nickname / uid），全部视为非敏感。
    path：指定写回路径（默认 accounts.json）；仅供脚本自测/隔离使用。
    返回是否保存成功；账号不存在时返回 False。
    """
    cookie = (cookie or "").strip()
    if not cookie:
        raise CookieError("凭据为空，未保存")
    acc = find_account(config, name)
    if acc is None:
        return False
    auth = dict(acc.get(auth_field(platform)) or {})
    # 字段名跟着平台走：aliyunpan 存 refresh_token，其余平台存 cookie。
    auth[credential_field(platform)] = cookie
    auth["updated_at"] = _now()
    if isinstance(extra, dict):
        for k, v in extra.items():
            if k in ("cookie", "refresh_token", "authorization", "updated_at"):
                continue
            if v is not None and str(v).strip():
                auth[k] = str(v).strip()
    acc["type"] = platform
    acc["app"] = platform
    acc[auth_field(platform)] = auth
    return save_config(config, path)


def update_credential(config: dict, name: str, platform: str, value: str,
                      field: str | None = None, extra: dict | None = None,
                      path: str | None = None) -> bool:
    """只更新凭据字段，不动 type / app / nickname 等其它字段。

    专门用于令牌轮换后的回写：阿里云盘每次刷新 access_token 都会同时下发
    **新的** refresh_token，旧的会失效，所以必须存回去，否则账号只能用一次。
    刻意不复用 set_cookie —— 后者会把 type/app 一并改写，回写过程不该动这些。
    """
    value = (value or "").strip()
    if not value:
        return False
    acc = find_account(config, name)
    if acc is None:
        return False
    key = auth_field(platform)
    auth = dict(acc.get(key) or {})
    auth[field or credential_field(platform)] = value
    auth["updated_at"] = _now()
    if isinstance(extra, dict):
        for k, v in extra.items():
            if k in ("cookie", "refresh_token", "authorization", "updated_at"):
                continue
            if v is not None and str(v).strip():
                auth[k] = str(v).strip()
    acc[key] = auth
    return save_config(config, path)


def _now() -> str:
    import datetime
    dt = datetime.datetime.now()
    return f"{dt.year}-{dt.month:02d}-{dt.day:02d} {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"


# ----------------------------------------------------------------------
# Cookie 解析 / 脱敏
# ----------------------------------------------------------------------
def parse_cookie(text: str) -> dict:
    """解析 "k=v; k2=v2; ..." 形式的 Cookie 字符串为字典（键大小写不敏感）。"""
    out: dict[str, str] = {}
    for part in (text or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        out[k.strip()] = v.strip()
    return out


def get_cookie_value(text: str, key: str) -> str:
    """按键名取 Cookie 值（键大小写不敏感）。"""
    low = key.lower()
    for k, v in parse_cookie(text).items():
        if k.lower() == low:
            return v
    return ""


def has_cookie_keys(text: str, *keys: str) -> bool:
    """判断 Cookie 是否包含全部指定键（如 SESSDATA / pt_key）。"""
    entries = parse_cookie(text)
    return all(any(k.lower() == kk.lower() for k in entries) for kk in keys)


def mask_cookie(text: str) -> str:
    """Cookie 脱敏展示：空 -> "(空)"；否则保留前 8 位后打码。"""
    text = (text or "").strip()
    if not text:
        return "(空)"
    if len(text) <= 12:
        return "******"
    return text[:8] + "******"


def cookie_summary(text: str) -> str:
    """凭据摘要：描述关键键位是否齐全（不含值）。"""
    text = (text or "").strip()
    if not text:
        return "Cookie 未配置"
    entries = parse_cookie(text)
    keys = sorted(entries.keys())
    if not keys:
        return "Cookie 格式无法解析"
    shown = "/".join(keys[:4])
    if len(keys) > 4:
        shown += f" 等 {len(keys)} 项"
    return f"Cookie {shown}"
