#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""WorkBuddy 多账号扫码登录（OAuth device flow，仅参考开源协议思路）。

流程：
  1) POST https://www.codebuddy.cn/v2/plugin/auth/state?platform=workbuddy
     获取 state + authUrl（授权链接）；
  2) 用内置 Chromium（Playwright，与 trae_login / browser_login 同一套）打开
     authUrl 并等待扫码；device flow 不依赖持久登录态，故每次都是全新的
     干净 profile，用完即弃。Playwright 不可用时自动回退系统默认浏览器；
  3) 轮询 GET .../v2/plugin/auth/token?state= 获取 accessToken/refreshToken；
  4) GET .../v2/plugin/login/account?state= 获取 uid/nickname/enterpriseId 等；
  5) 写回 accounts.json 对应 workbuddy 账号的 workbuddy_auth 字段。

凭据安全：accessToken/refreshToken 等同账号密码，仅写入 accounts.json
（已被 .gitignore 排除），stdout/日志一律脱敏，绝不回显明文。

用法：
    python3 workbuddy_login.py --name workbuddy-2 --enabled 1
    python3 workbuddy_login.py --name workbuddy-2 --browser system    # 用系统浏览器
    python3 workbuddy_login.py --name workbuddy-2 --manual --timeout 30
退出码：0=成功；1=失败/超时/参数错误；2=用户取消（窗口被关或 Ctrl+C）。
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from workbuddy import _UA, _loads_body, _resp_msg  # noqa: E402
from cookie_manager import load_config, save_config, find_account  # noqa: E402

OAUTH_BASE = "https://www.codebuddy.cn"
OAUTH_PREFIX = "/v2/plugin"
PLATFORM = "workbuddy"
DEFAULT_TIMEOUT_SECONDS = 600
POLL_INTERVAL = 3


def _http_json(method: str, url: str, headers: dict | None = None,
               body: str | None = None, timeout: int = 30) -> tuple:
    data = body.encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return -1, json.dumps({"error": str(e)})


def _dig(obj, key):
    """在可能被 data/result 信封包裹的响应里查找字段。"""
    if isinstance(obj, dict):
        if obj.get(key) is not None:
            return obj[key]
        for k in ("data", "result", "resp"):
            if isinstance(obj.get(k), dict):
                v = _dig(obj[k], key)
                if v is not None:
                    return v
    return None


def _mask(v) -> str:
    v = (v or "").strip()
    if not v:
        return "(空)"
    if len(v) <= 12:
        return "******"
    return v[:8] + "******"


def _now() -> str:
    import datetime
    dt = datetime.datetime.now()
    return f"{dt.year}-{dt.month:02d}-{dt.day:02d} {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"


def _to_abs_ms(v):
    """统一为绝对毫秒时间戳（兼容相对秒/绝对秒/毫秒）。"""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f < 100_000:
        return int(time.time() * 1000 + f * 1000)
    if f < 100_000_000_000:
        return int(f * 1000)
    return int(f)


def start_oauth() -> tuple:
    """发起 OAuth 登录，返回 (state, authUrl)。失败抛 RuntimeError。"""
    url = f"{OAUTH_BASE}{OAUTH_PREFIX}/auth/state?platform={PLATFORM}"
    status, text = _http_json("POST", url, headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": _UA,
    }, body="{}")
    body = _loads_body(text)
    if status != 200:
        raise RuntimeError(f"获取授权链接失败（HTTP {status}）：{_resp_msg(body) or '(空)'}")
    state = _dig(body, "state")
    auth_url = _dig(body, "authUrl")
    if not state or not auth_url:
        raise RuntimeError("授权接口未返回 state/authUrl")
    return str(state), str(auth_url)


class BrowserClosed(RuntimeError):
    """用户关闭了内置浏览器窗口（等同取消登录）。"""


def _fetch_token_once(state: str) -> dict | None:
    """单次查询授权结果：已授权返回 token 快照，尚未完成返回 None。"""
    url = f"{OAUTH_BASE}{OAUTH_PREFIX}/auth/token?state={state}"
    status, text = _http_json("GET", url, headers={"Accept": "application/json"})
    if status != 200:
        return None
    body = _loads_body(text)
    access = _dig(body, "accessToken") or _dig(body, "access_token")
    if not access:
        return None
    return {
        "access_token": str(access),
        "refresh_token": str(_dig(body, "refreshToken") or _dig(body, "refresh_token") or ""),
        "domain": str(_dig(body, "domain") or ""),
        "expires_at": _to_abs_ms(_dig(body, "expiresAt") or _dig(body, "expiresIn")),
        "refresh_expires_at": _to_abs_ms(_dig(body, "refreshExpiresAt") or _dig(body, "refreshExpiresIn")),
        "token_type": str(_dig(body, "tokenType") or _dig(body, "token_type") or ""),
    }


def poll_token(state: str, timeout_seconds: int, tick=None) -> dict:
    """轮询授权结果，返回 token 快照（相对秒已换算为绝对毫秒）。

    tick：可选回调，每轮询一次调用一次。内置浏览器模式用它驱动 Playwright
    事件循环——否则窗口会卡住不响应任何点击。回调抛出的异常会中断轮询。
    """
    deadline = time.time() + timeout_seconds
    while True:
        if time.time() > deadline:
            raise TimeoutError(f"等待扫码超时（{timeout_seconds} 秒），请重新发起登录")
        snap = _fetch_token_once(state)
        if snap:
            return snap
        # 未完成扫码 / 网络抖动：继续轮询
        if tick is not None:
            tick()
        else:
            time.sleep(POLL_INTERVAL)


def open_in_embedded_browser(auth_url: str, state: str, timeout_seconds: int) -> dict:
    """用内置 Chromium 打开授权页并等待授权完成，返回 token 快照。

    与 trae_login / browser_login 共用同一套 Playwright 内置浏览器，不再占用
    用户日常浏览器。device flow 不依赖持久登录态，因此每次都用全新的干净
    profile（用完即弃），不会残留个人上网痕迹。

    Playwright 未安装或启动失败时抛异常，由调用方决定是否回退系统浏览器。
    """
    from playwright.sync_api import sync_playwright   # 未安装时抛 ImportError

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            ctx = browser.new_context(viewport={"width": 1180, "height": 820})
            page = ctx.new_page()
            page.goto(auth_url, wait_until="domcontentloaded", timeout=60000)
            print("[LOG] 已在内置浏览器打开授权页，请扫码并在页面中确认登录。", flush=True)
            print("[LOG] 登录成功后会自动保存账号，请勿提前关闭该窗口。", flush=True)

            def tick() -> None:
                try:
                    pages = ctx.pages
                except Exception:  # noqa: BLE001  浏览器进程已退出
                    raise BrowserClosed("浏览器窗口已关闭，已取消登录。") from None
                live = next((pg for pg in pages if not pg.is_closed()), None)
                if live is None:
                    raise BrowserClosed("浏览器窗口已关闭，已取消登录。")
                try:
                    live.wait_for_timeout(int(POLL_INTERVAL * 1000))
                except Exception:  # noqa: BLE001  句柄失效：退化为短暂等待
                    time.sleep(1)

            return poll_token(state, timeout_seconds, tick=tick)
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass


def fetch_account(state: str, access_token: str, domain: str = "") -> dict:
    """拉取账号 uid/nickname 等非敏感信息（用于自动回填昵称）。"""
    headers = {
        "Authorization": "Bearer " + access_token,
        "Accept": "application/json",
        "User-Agent": _UA,
    }
    if domain:
        headers["X-Domain"] = domain
    url = f"{OAUTH_BASE}{OAUTH_PREFIX}/login/account?state={state}"
    status, text = _http_json("GET", url, headers=headers)
    body = _loads_body(text)
    if status != 200:
        raise RuntimeError(f"获取账号信息失败（HTTP {status}）：{_resp_msg(body) or '(空)'}")
    return {
        "uid": str(_dig(body, "uid") or _dig(body, "accountId") or _dig(body, "user_id") or ""),
        "nickname": str(_dig(body, "nickname") or _dig(body, "name") or ""),
        "email": str(_dig(body, "email") or ""),
        "enterprise_id": str(_dig(body, "enterpriseId") or _dig(body, "enterprise_id") or ""),
        "domain": str(_dig(body, "domain") or domain),
    }


def save_account(config_path: str | None, name: str, enabled: bool,
                 tokens: dict, account: dict, source: str = "oauth") -> bool:
    """写回 accounts.json（原子写）。同名保留原启用状态，新账号按 enabled。"""
    cfg = load_config(config_path)
    arr = cfg.get("accounts", [])
    auth = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "expires_at": tokens["expires_at"],
        "refresh_expires_at": tokens["refresh_expires_at"],
        "token_type": tokens["token_type"],
        "domain": account.get("domain") or "",
        "uid": account.get("uid") or "",
        "nickname": account.get("nickname") or "",
        "email": account.get("email") or "",
        "enterprise_id": account.get("enterprise_id") or "",
        "saved_at": _now(),
        "source": source,
    }
    auth = {k: v for k, v in auth.items() if v not in (None, "")}
    acc = find_account(cfg, name)
    if acc is not None:
        old_auth = acc.get("workbuddy_auth")
        if not isinstance(old_auth, dict):
            old_auth = {}
        old_auth.update(auth)
        acc["workbuddy_auth"] = old_auth
        acc["type"] = "workbuddy"
        acc["app"] = "workbuddy"
    else:
        arr.append({
            "name": name,
            "app": "workbuddy",
            "type": "workbuddy",
            "enabled": enabled,
            "workbuddy_auth": auth,
        })
    cfg["accounts"] = arr
    return save_config(cfg, config_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="WorkBuddy 多账号扫码登录（OAuth device flow）")
    parser.add_argument("--name", default="", help="账号名称；留空时自动以昵称命名（workbuddy-<昵称>）")
    parser.add_argument("--enabled", default="1", help="1=加入自动签到（默认），0=仅保存不签到")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS,
                        help=f"扫码等待秒数（默认 {DEFAULT_TIMEOUT_SECONDS}）")
    parser.add_argument("--manual", action="store_true", help="只打印授权 URL，不自动打开浏览器")
    parser.add_argument("--browser", default="auto", choices=["auto", "embedded", "system"],
                        help="登录窗口：auto=优先内置浏览器、不可用时回退系统浏览器（默认）；"
                             "embedded=只用内置浏览器；system=只用系统默认浏览器")
    parser.add_argument("--config", default=None, help="accounts.json 路径（默认项目目录）")
    args = parser.parse_args()

    name = args.name.strip()
    enabled = args.enabled.strip() not in ("0", "false", "False")
    timeout_seconds = max(10, args.timeout)

    print(f"[LOG] 正在发起 WorkBuddy OAuth 登录（账号「{name or '自动命名'}」）…", flush=True)
    try:
        state, auth_url = start_oauth()
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "reason": str(e)}, ensure_ascii=False), flush=True)
        return 1

    print(f"[URL] {auth_url}", flush=True)

    # --manual 保持旧语义：只打印 URL，由用户自行粘贴到任意浏览器
    mode = "manual" if args.manual else args.browser
    tokens = None

    if mode in ("auto", "embedded"):
        try:
            tokens = open_in_embedded_browser(auth_url, state, timeout_seconds)
        except BrowserClosed as e:
            print(json.dumps({"ok": False, "reason": str(e)}, ensure_ascii=False), flush=True)
            return 2
        except TimeoutError as e:
            print(json.dumps({"ok": False, "reason": str(e)}, ensure_ascii=False), flush=True)
            return 1
        except KeyboardInterrupt:
            print(json.dumps({"ok": False, "reason": "用户取消登录"}, ensure_ascii=False), flush=True)
            return 2
        except Exception as e:  # noqa: BLE001
            if mode == "embedded":
                print(json.dumps({"ok": False, "reason": f"内置浏览器不可用：{e}"},
                                 ensure_ascii=False), flush=True)
                return 1
            print(f"[LOG] 内置浏览器不可用（{type(e).__name__}: {e}），回退系统默认浏览器。",
                  flush=True)

    if tokens is None:
        if mode == "manual":
            print("[LOG] 请手动复制上方 URL 到浏览器访问并完成登录", flush=True)
        else:
            try:
                webbrowser.open(auth_url)
                print("[LOG] 已在系统默认浏览器打开授权页，请完成扫码/确认（如需取消可按 Ctrl+C）", flush=True)
            except Exception:  # noqa: BLE001
                print("[LOG] 自动打开浏览器失败，请手动复制上方 URL 到浏览器访问", flush=True)
        try:
            tokens = poll_token(state, timeout_seconds)
        except KeyboardInterrupt:
            print(json.dumps({"ok": False, "reason": "用户取消登录"}, ensure_ascii=False), flush=True)
            return 2
        except TimeoutError as e:
            print(json.dumps({"ok": False, "reason": str(e)}, ensure_ascii=False), flush=True)
            return 1
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"ok": False, "reason": f"等待授权失败：{e}"}, ensure_ascii=False), flush=True)
            return 1

    try:
        account = fetch_account(state, tokens["access_token"], tokens.get("domain", ""))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "reason": str(e)}, ensure_ascii=False), flush=True)
        return 1

    # 名称留空时自动以昵称命名（自动回填昵称），兜底 workbuddy-oauth
    if not name:
        base = (account.get("nickname") or "").strip() or "oauth"
        name = f"workbuddy-{base}"
    print(f"[LOG] 登录成功：{account.get('nickname') or account.get('uid') or '?'}（token 已脱敏保存）", flush=True)

    if not save_account(args.config, name, enabled, tokens, account, source="oauth"):
        print(json.dumps({"ok": False, "reason": "写入 accounts.json 失败"}, ensure_ascii=False), flush=True)
        return 1

    print(json.dumps({
        "ok": True,
        "name": name,
        "uid": account["uid"],
        "nickname": account["nickname"],
        "enabled": enabled,
        "expires_at": tokens["expires_at"],
        "token_mask": _mask(tokens["access_token"]),
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
