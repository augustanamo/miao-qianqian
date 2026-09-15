#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Trae 内置浏览器登录模块（基于 Playwright）

原理：
  打开系统显示窗口的内置浏览器进入 Trae 网页版，用户完成登录后，
  脚本自动捕获：
    - localStorage['Cloud-IDE-Token']   （JWT，约 8h，首登当天即可用）
    - HttpOnly Cookie: X-Cloudide-Session（约 14 天长效会话）
  并把账号写入 accounts.json（type=trae，后续由 checkin.py 走专用签到器）。

用法：
    python3 trae_login.py --name trae-1            # 开窗登录
    python3 trae_login.py                          # 名称留空：按账号 ID 自动命名
    python3 trae_login.py --name trae-2 --timeout 300
    python3 trae_login.py --list                   # 列出已保存的 Trae 账号（脱敏）

依赖：pip install playwright && python -m playwright install chromium
"""

import argparse
import datetime
import json
import os
import random
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.json")
STATE_DIR = os.path.join(BASE_DIR, ".browser_state")

# 尝试在这些页面站点完成登录（页面可自由切换/手动输入，脚本会监控所有页面）
LOGIN_URLS = [
    "https://www.trae.cn/dashboard#usage",
    "https://www.trae.cn/",
    "https://work.trae.cn/",
]

TOKEN_KEY = "Cloud-IDE-Token"
SESSION_COOKIE = "X-Cloudide-Session"

_log_lines = []


def _log(msg: str) -> None:
    line = f"[{datetime.datetime.now():%H:%M:%S}] {msg}"
    _log_lines.append(line)
    print(line, flush=True)


def mask(v: str) -> str:
    return (v[:8] + "******") if v and len(v) > 12 else ("******" if v else "(空)")


def load_config() -> dict:
    if os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"settings": {}, "accounts": []}


def save_config(cfg: dict) -> None:
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def pick_device_id(ctx) -> str:
    """从浏览器上下文里尽力读取 x-device-id 类 cookie；没有就随机生成 16 位数字。"""
    try:
        for c in ctx.cookies():
            if "device" in c["name"].lower() and c["name"].lower() != "x-device-id":
                continue
        for c in ctx.cookies():
            if c["name"].lower() == "x-device-id":
                v = c["value"]
                if v.isdigit():
                    return v
    except Exception:  # noqa: BLE001
        pass
    return str(random.randint(10**15, 10**16 - 1))


def parse_account_uid(jwt: str) -> str:
    """从 JWT payload 的 data.id 解析 16 位数字账号 ID（跨登录恒定，用于去重提示）。"""
    try:
        import base64
        parts = jwt.split(".")
        if len(parts) < 2:
            return ""
        payload = parts[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        uid = (data.get("data") or {}).get("id")
        return uid if isinstance(uid, str) else ""
    except Exception:  # noqa: BLE001
        return ""


def collect_token_and_session(ctx):
    """遍历所有页面/iframe 读取 localStorage token；并读取 HttpOnly 会话 Cookie。"""
    token = ""
    sessions = []
    try:
        for page in ctx.pages:
            try:
                t = page.evaluate("localStorage.getItem('%s')" % TOKEN_KEY)
                if t and len(t) >= 40:   # 真正的 JWT 远长于 40，短值视为失效/残留
                    token = t
            except Exception:  # noqa: BLE001
                pass
        for c in ctx.cookies():
            if c["name"] == SESSION_COOKIE and c["value"]:
                sessions.append(c["value"])
    except Exception:  # noqa: BLE001
        pass
    return token, sessions


def save_trae_account(name: str, session: str, token: str, device_id: str, uid: str = "", enabled: bool = True) -> None:
    cfg = load_config()
    accounts = cfg.setdefault("accounts", [])
    # 同账号（按 uid）已存在其他 name 时给出提示
    if uid:
        dup = next((a for a in accounts
                    if a.get("type") == "trae" and a.get("trae_auth", {}).get("uid") == uid
                    and a.get("name") != name), None)
        if dup:
            _log(f"提示：该 Trae 账号此前已绑定为「{dup.get('name')}」，本次保存为「{name}」，可能造成重复签到。"
                 f"如确属同一账号，建议删除其中一个。")
    entry = {
        "name": name,
        "app": "trae",
        "type": "trae",
        "trae_auth": {
            "session": session,
            "token": token,                      # 首登当天可直接用；过期后由 checkin 自动重换
            "device_id": device_id,
            "uid": uid,
            "saved_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    }
    idx = next((i for i, a in enumerate(accounts) if a.get("name") == name), None)
    if idx is None:
        entry["enabled"] = enabled               # 仅新增账号时按用户选择写入；同名重登保留原启用状态
        accounts.append(entry)
    else:
        entry["enabled"] = accounts[idx].get("enabled", True)
        accounts[idx] = entry
    save_config(cfg)
    _log(f"已保存账号「{name}」到 accounts.json")


def resolve_trae_name(name: str, uid: str) -> str:
    """确定最终账号名。

    Trae 的登录态里只有账号 ID（JWT payload 的 data.id），既无昵称字段，
    trae_api 也不提供用户资料接口，因此名称留空时用账号 ID 后 6 位命名。
    """
    name = (name or "").strip()
    if name:
        return name
    if uid:
        return f"trae-{uid[-6:]}"
    return "trae-" + datetime.datetime.now().strftime("%m%d%H%M")


def run_login(name: str, timeout_seconds: int, enabled: bool = True) -> int:
    name = name.strip()
    auto_name = not name
    if auto_name:
        _log("未指定账号名称：Trae 登录态不含昵称，将按账号 ID 自动命名。")

    os.makedirs(STATE_DIR, exist_ok=True)
    user_data_dir = os.path.join(STATE_DIR, name or "_auto")
    os.makedirs(user_data_dir, exist_ok=True)

    _log(f"正在打开内置浏览器（账号 {name or '（自动命名）'}）…请在弹出的浏览器窗口中登录 Trae。")
    _log("登录成功后本程序会自动识别并保存，无需手动操作。")
    _log("提示：登录期间可自由切换标签页/填写验证码，脚本会持续监控。")
    _log("若页面未自动跳转到登录页，可手动访问以下任一地址：")
    for u in LOGIN_URLS:
        _log("    " + u)

    deadline = time.time() + timeout_seconds
    token, session, device_id = "", "", ""

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir,
                headless=False,
                viewport={"width": 1280, "height": 860},
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = ctx.new_page()
            opened = False
            for u in LOGIN_URLS:
                try:
                    page.goto(u, wait_until="domcontentloaded", timeout=60000)
                    opened = True
                    if ctx.pages:
                        break
                except Exception as e:  # noqa: BLE001
                    _log(f"打开 {u} 失败（尝试下一个）：{e}")
            _log("已显示登录窗口（初始页面 " + (LOGIN_URLS[0] if opened else "打开失败，可手动输入上方地址") + "）。")
            _log("登录成功后本程序会自动识别并保存，请勿关闭本窗口直至提示完成。")

            wait_token_until = time.time() + 90   # 捕获到会话后，额外等待 Token 落盘的最长时间
            wait_first = True
            session = ""
            while time.time() < deadline:
                if not ctx.pages:
                    _log("浏览器窗口已全部关闭，取消登录。")
                    return 1
                token, sessions = collect_token_and_session(ctx)
                if sessions:
                    session = sessions[0]
                if session and wait_first:
                    _log("已捕获长效会话 Cookie（X-Cloudide-Session），继续等待页面写入 Token…")
                    wait_first = False
                if session and token:
                    break                            # 会话 + Token 都齐，完成
                if session and not token and time.time() >= wait_token_until:
                    _log("Token 未在预期时间写入，将以长效会话 Cookie 为准（可自动换取新 Token）。")
                    break
                time.sleep(2)

            if not session:
                _log(f"超时（{timeout_seconds}s）未检测到登录。请确认已在浏览器中登录 Trae，然后重试。")
                return 1

            device_id = pick_device_id(ctx)
            # 关闭浏览器（持久化会话状态，下次登录无需重复扫码等）
            ctx.close()
    except Exception as e:  # noqa: BLE001
        _log(f"内置浏览器异常：{e}")
        return 1

    # 若 localStorage 未捕获 Cloud-IDE-Token，用已捕获的长效会话 Cookie 主动换全新 JWT，
    # 避免 accounts.json 里 token 字段为空导致 UI 显示“Token (空)”。
    if not token and session:
        try:
            from trae_api import TraeClient
            _log("未捕获 Cloud-IDE-Token，正在用 X-Cloudide-Session 换取全新 JWT …")
            token = TraeClient(session=session, device_id=device_id).get_token()
            _log(f"JWT 换取成功：token={mask(token)}")
        except Exception as e:  # noqa: BLE001
            token = ""
            _log(f"JWT 换取失败（{e}），将仅保存长效会话 Cookie，token 暂为空。")

    uid = parse_account_uid(token) if token else ""
    final_name = resolve_trae_name(name, uid)
    if auto_name:
        _log(f"已自动命名账号：{final_name}"
             + ("" if uid else "（未能取到账号 ID，改用时间戳命名）"))
    save_trae_account(final_name, session, token, device_id, uid, enabled=enabled)
    _log(f"登录完成：session={mask(session)} device_id={device_id} uid={uid or '(未知)'}")
    _log("下一步：运行  python3 checkin.py --only %s  验证签到。" % final_name)
    return 0


def run_list() -> int:
    cfg = load_config()
    trae = [a for a in cfg.get("accounts", []) if a.get("type") == "trae"]
    if not trae:
        print("当前没有 Trae 内置登录账号。")
        return 0
    print("Trae 内置登录账号：")
    for a in trae:
        auth = a.get("trae_auth", {})
        print(f"  - {a.get('name')}  保存时间 {auth.get('saved_at','?')}  "
              f"session={mask(auth.get('session',''))}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Trae 内置浏览器登录")
    parser.add_argument("--name", default="",
                        help="账号名称（唯一），如 trae-1；留空时按账号 ID 自动命名")
    parser.add_argument("--timeout", type=int, default=600, help="等待登录超时秒数，默认 600")
    parser.add_argument("--enabled", type=int, default=1, choices=[0, 1],
                        help="新增账号是否加入自动签到（1=开启，0=关闭，默认开启；仅新建账号时生效）")
    parser.add_argument("--list", action="store_true", help="列出已保存的 Trae 账号")
    args = parser.parse_args()

    if args.list:
        return run_list()

    return run_login(args.name, args.timeout, enabled=bool(args.enabled))


if __name__ == "__main__":
    sys.exit(main())
