#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Cookie 型平台浏览器登录模块（Bilibili / 联想智选 / 京东）

原理（仿 trae_login.py）：
  用 Playwright 打开系统显示窗口的 Chromium，用户扫码 / 账密登录后，
  脚本自动从浏览器上下文捕获登录 Cookie：
    - Bilibili   -> SESSDATA（及配套键，如 bili_jct / DedeUserID）
    - 京东       -> pt_key + pt_pin（HttpOnly）
    - 联想智选   -> mclub.lenovo.com.cn 会话 Cookie
  捕获后立即用对应平台客户端做只读验证，通过即关窗并把 Cookie 写回
  accounts.json 对应账号的 <platform>_auth.cookie 字段；账号不存在时
  自动创建（enabled 由 --enabled 决定，同名重登保留原启用状态）。

  登录态判定是三态（见 PLATFORMS 上方的注释）：ok / pending / unknown。
  只有 pending（平台明确说「还没登录」）才会一直等；unknown（平台只读接口
  改版 / 风控 / 网络异常，判不了）连续出现 UNKNOWN_ACCEPT_STREAK 次就按
  「已捕获关键 Cookie」保存，避免平台的接口变动把用户永远卡在等待里。

  账号名称可留空（--name 省略）：此时按只读验证返回的昵称自动命名
  （如 bilibili-小明），取不到昵称则回退账号 ID，再兜底 <platform>-auto。

用法：
    python3 browser_login.py --platform bilibili --name bili-main [--timeout 600] [--enabled 1]
    python3 browser_login.py --platform bilibili              # 名称留空：自动读取昵称命名
    python3 browser_login.py --platform jd                    # 同上，自动命名
    python3 browser_login.py --platform lenovo  --name lenovo-main

依赖：pip install playwright && python -m playwright install chromium

凭据安全：Cookie 等同账号密码，读取后仅内存使用；落盘仅 accounts.json
（已被 .gitignore 排除）；日志 / UI 一律 mask 脱敏，绝不回显明文。
"""

import argparse
import datetime
import json
import os
import sys
import time
import urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.json")
STATE_DIR = os.path.join(BASE_DIR, ".browser_state_cookie")

# 「已抓到关键 Cookie 但只读接口判不了」连续多少次后按成功保存。
# 目的：第三方接口改版时不要让用户对着浏览器干等 10 分钟。
UNKNOWN_ACCEPT_STREAK = 3
# 已抓到关键 Cookie（need_keys 齐了）后，最多再等多久去做只读复核。
# 京东的只读接口本身不稳定（同一请求一次 200/code3、一次 403），
# 若只按 unknown 计数，遇到「一直 pending」也会无限等，故再加一道时间上限。
CAPTURE_GRACE_SECONDS = 120.0
# 等待期间的进度日志间隔（秒），避免看起来像卡死
PROGRESS_LOG_EVERY = 20.0

from cookie_manager import find_account, load_config, mask_cookie, set_cookie  # noqa: E402

_log_lines: list[str] = []


def _log(msg: str) -> None:
    line = f"[{datetime.datetime.now():%H:%M:%S}] {msg}"
    _log_lines.append(line)
    print(line, flush=True)


# ----------------------------------------------------------------------
# 平台配置：打开地址 / 关键 Cookie / 只读验证
#
# 验证函数统一返回 4 元组 (state, nickname, uid, message)：
#   "ok"       只读接口明确表示已登录      -> 立即保存
#   "pending"  明确表示还没登录           -> 继续等（用户可能还在输密码/扫码）
#   "unknown"  接口变更 / 网络异常 / 判不了 -> 计入 unknown 次数，连续多次后
#                                          按「已捕获关键 Cookie」保存
#
# ⚠️ 为什么必须有 "unknown" 这一档：平台的只读接口随时会失效（京东的
# passport 接口 2026-09 就改成了返回首页 HTML）。如果「校验不通过就死等」，
# 用户登录成功了浏览器也会一直挂在那儿等到超时——正是要修的问题。
# ----------------------------------------------------------------------
def _verify_bilibili(cookie: str) -> tuple:
    """验证 Bilibili 登录态。返回 (state, nickname, uid, message)。"""
    from bilibili import BilibiliClient
    if not cookie:
        return "pending", "", "", "尚未捕获 Bilibili Cookie"
    try:
        nav = BilibiliClient(cookie).nav()
    except Exception as e:  # noqa: BLE001
        return "unknown", "", "", f"登录态验证异常：{type(e).__name__}"
    if nav.get("ok") and nav.get("is_login"):
        return "ok", nav.get("nickname", ""), nav.get("uid", ""), ""
    if nav.get("ok"):
        return "pending", "", "", nav.get("message") or "尚未登录，请在弹出的窗口中完成登录"
    # 接口请求本身失败（网络/风控）→ 判不了，不要当成「未登录」死等
    return "unknown", "", "", nav.get("message") or "登录态接口无响应"


def _cookie_map(cookie: str) -> dict:
    """cookie 串 -> {名字: 值}（值的空白去掉）。"""
    out: dict[str, str] = {}
    for part in (cookie or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k.strip():
            out[k.strip()] = v.strip()
    return out


def _mask_pin(pin: str) -> str:
    """pt_pin 可能是手机号，兜底命名时只留尾 4 位，避免手机号出现在界面上。"""
    s = (pin or "").strip()
    if not s:
        return ""
    if s.isdigit() and len(s) >= 7:
        return "****" + s[-4:]
    return s


def _jd_pin(cookie: str) -> str:
    """从 cookie 里取 pt_pin（京东登录名，URL 编码）并解码。"""
    raw = _cookie_map(cookie).get("pt_pin", "")
    if not raw:
        return ""
    try:
        return urllib.parse.unquote(raw)
    except Exception:  # noqa: BLE001
        return raw


def _verify_jd(cookie: str) -> tuple:
    """验证京东登录态。返回 (state, nickname, uid, message)。

    只读接口：api.m.jd.com signBeanIndex（未登录 {"code":"3","errorMessage":"用户未登录"}）。
    pt_key/pt_pin 只在登录成功后由京东下发，因此它们齐了基本就等于登录成功；
    只读接口判不了时按 unknown 处理，不会卡住流程。
    """
    from jd import probe_login
    cmap = _cookie_map(cookie)
    if not cookie:
        return "pending", "", "", "尚未捕获京东 Cookie"
    if not cmap.get("pt_key") or not cmap.get("pt_pin"):
        return "pending", "", "", "尚未拿到京东登录 Cookie（需要 pt_key 与 pt_pin）"
    uid = _mask_pin(_jd_pin(cookie))
    try:
        p = probe_login(cookie)
    except Exception as e:  # noqa: BLE001
        return "unknown", "", uid, f"登录态验证异常：{type(e).__name__}"
    if p["state"] == "ok":
        return "ok", p["nickname"], uid, ""
    if p["state"] == "invalid":
        return "pending", "", uid, "京东侧仍返回未登录，请确认已完成登录"
    return "unknown", "", uid, "京东只读接口未能判定登录态"


def _verify_lenovo(cookie: str) -> tuple:
    """验证联想 mclub 登录态。返回 (state, nickname, uid, message)。"""
    from lenovo import _cfg_nickname, _lenovo_status_with_cookie
    if not cookie:
        return "pending", "", "", "尚未捕获联想 Cookie"
    try:
        ok, cfg, msg = _lenovo_status_with_cookie(cookie)
    except Exception as e:  # noqa: BLE001
        return "unknown", "", "", f"登录态验证异常：{type(e).__name__}"
    if ok:
        return "ok", _cfg_nickname(cfg), str(cfg.get("memberId") or cfg.get("lenovoId") or ""), ""
    if cfg:
        # 能解析到 $CONFIG 但里面还是未登录态 -> 确实还没登录，继续等
        return "pending", "", "", msg
    # 整页解析失败：多半是页面结构变了，不是用户没登录
    return "unknown", "", "", msg or "签到页无法解析"


# 平台配置：type -> (中文标签, 初始打开地址列表, 关键 Cookie 键, 抓取键白名单/域过滤, 验证函数)
PLATFORMS: dict[str, tuple] = {
    "bilibili": (
        "Bilibili",
        ["https://passport.bilibili.com/login", "https://www.bilibili.com/"],
        ("SESSDATA",),
        {"keys": ["SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5",
                  "sid", "buvid3", "buvid4", "b_nut", "CURRENT_FNVAL"]},
        _verify_bilibili,
    ),
    "jd": (
        "京东",
        ["https://plogin.m.jd.com/login/login?appid=300&returnurl=https%3A%2F%2Fhome.m.jd.com%2FmyJd%2FnewMyJd.action",
         "https://passport.jd.com/new/login.aspx"],
        ("pt_key", "pt_pin"),
        {"keys": ["pt_key", "pt_pin"]},
        _verify_jd,
    ),
    "lenovo": (
        "联想智选",
        ["https://mclub.lenovo.com.cn/signlist/", "https://reg.lenovo.com.cn/login"],
        (),
        {"domains": ("mclub.lenovo.com.cn", "lenovo.com.cn")},
        _verify_lenovo,
    ),
}


def clean_name(raw: str) -> str:
    """把昵称清洗成可做账号名的形式（去空白与路径分隔符，限制长度）。"""
    s = "".join(ch for ch in (raw or "") if not ch.isspace())
    for ch in ("/", "\\", ":", "\n", "\t"):
        s = s.replace(ch, "-")
    return s.strip("-")[:24]


def resolve_name(platform: str, name: str, nickname: str = "", uid: str = "") -> str:
    """确定最终账号名：显式指定则原样使用，留空时按「昵称 → uid → auto」自动取。

    昵称来自各平台的只读登录态校验（Bilibili / 京东 / 联想都能取到），
    因此留空时通常能直接得到与用户认知一致的账号名。
    """
    name = (name or "").strip()
    if name:
        return name
    base = clean_name(nickname) or clean_name(uid)
    return f"{platform}-{base}" if base else f"{platform}-auto"


def collect_cookie(ctx, platform: str) -> str:
    """从浏览器上下文按平台规则采集登录 Cookie 串（k=v; k2=v2）。"""
    label, _urls, _need, scope, _verify = PLATFORMS[platform]
    keys = scope.get("keys") or []
    domains = scope.get("domains") or ()
    entries: dict[str, str] = {}
    try:
        for c in ctx.cookies():
            name = c.get("name", "")
            domain = (c.get("domain") or "").lstrip(".")
            if keys:
                if name in keys:
                    entries[name] = c.get("value", "")
            elif domains:
                if any(domain == d or domain.endswith("." + d) for d in domains):
                    entries[name] = c.get("value", "")
    except Exception:  # noqa: BLE001
        pass
    if keys:
        return "; ".join(f"{k}={entries[k]}" for k in keys if k in entries)
    return "; ".join(f"{k}={v}" for k, v in entries.items())


def save_cookie_account(name: str, platform: str, cookie: str,
                        extra: dict | None = None, enabled: bool = True,
                        config_path: str | None = None) -> bool:
    """把浏览器登录得到的 Cookie 写回 accounts.json 指定账号。

    账号不存在时自动创建（enabled 按参数）；同名账号更新 Cookie 时保留
    原启用状态；全部复用 cookie_manager 的原子读写。
    """
    config = load_config(config_path)
    acc = find_account(config, name)
    if acc is None:
        label, _urls, _need, _scope, _verify = PLATFORMS[platform]
        entry = {
            "name": name,
            "app": label,
            "type": platform,
            "enabled": bool(enabled),
            f"{platform}_auth": {"cookie": ""},
        }
        config.setdefault("accounts", []).append(entry)
    return set_cookie(config, name, platform, cookie, extra, path=config_path)


def run_browser_login(platform: str, name: str, timeout_seconds: int,
                      enabled: bool = True) -> int:
    if platform not in PLATFORMS:
        _log(f"未知平台：{platform}，可选：{', '.join(PLATFORMS)}")
        return 1
    label, login_urls, need_keys, _scope, verify = PLATFORMS[platform]

    name = clean_name(name)
    auto_name = not name
    if auto_name:
        _log("未指定账号名称：登录成功后将自动读取该账号的真实昵称来命名。")

    os.makedirs(STATE_DIR, exist_ok=True)
    # 名称留空时先用共享的 _auto 目录承载浏览器状态
    user_data_dir = os.path.join(STATE_DIR, platform, name or "_auto")
    os.makedirs(user_data_dir, exist_ok=True)

    _log(f"正在打开内置浏览器（{label} / {name or '（自动命名）'}）…请在弹出窗口中扫码或账密登录。")
    _log("登录成功后本程序会自动识别并保存 Cookie，无需手动复制粘贴。")
    _log("提示：登录期间可自由切换标签页 / 填写验证码，脚本会持续监控。")
    _log("若页面未自动跳转登录页，可手动访问：")
    for u in login_urls:
        _log("    " + u)

    deadline = time.time() + timeout_seconds
    cookie = ""
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
            for u in login_urls:
                try:
                    page.goto(u, wait_until="domcontentloaded", timeout=60000)
                    opened = True
                    break           # 打开第一个能用的地址即可，其余只是备选
                except Exception as e:  # noqa: BLE001
                    _log(f"打开 {u} 失败（尝试下一个）：{e}")
            _log("已显示登录窗口" + ("" if opened else "（初始页面打开失败，可手动输入上方地址）") + "。")
            _log("登录成功后本程序会自动识别并保存，请勿关闭本窗口直至提示完成。")

            verified_ok = False
            nickname, uid = "", ""
            unknown_streak = 0
            captured_since = None
            started = time.time()
            last_log = started
            while time.time() < deadline:
                try:
                    if not ctx.pages:
                        _log("浏览器窗口已全部关闭，已取消登录。")
                        return 1
                except Exception:  # noqa: BLE001  浏览器进程已退出（连接断开）
                    _log("浏览器窗口已全部关闭，已取消登录。")
                    return 1

                cookie = collect_cookie(ctx, platform)
                if need_keys:
                    # 必须「有值」才算捕到：游客态也可能出现 pt_key= 这样的空值，
                    # 只比键名会把「还没登录」误判成「登录过了」，宽限期就会提前开跑。
                    cmap = _cookie_map(cookie)
                    missing = [k for k in need_keys if not cmap.get(k)]
                    if missing:
                        time.sleep(2)
                        continue
                    # 关键 Cookie 齐了 = 平台那边确实登录过了，开始计时
                    if captured_since is None:
                        captured_since = time.time()
                if not cookie:
                    time.sleep(2)
                    continue

                state, nickname, uid, msg = verify(cookie)
                if state == "ok":
                    verified_ok = True
                    break
                if state == "unknown":
                    # 关键 Cookie 已在手，但平台的只读接口判不了（接口改版/风控/网络）。
                    # 连续若干次后按成功处理，绝不把用户永远按在等待里。
                    unknown_streak += 1
                    if unknown_streak >= UNKNOWN_ACCEPT_STREAK:
                        _log(f"已 {unknown_streak} 次无法用平台只读接口判定登录态（{msg or '无响应'}）。")
                        _log("关键 Cookie 已捕获，按登录成功保存；稍后可在账号管理页用"
                             "「凭证健康检查」复核。")
                        verified_ok = True
                        break
                else:
                    unknown_streak = 0

                now = time.time()
                # 宽限期兜底：抓到关键 Cookie 后仍长时间判不出结果就直接放行
                if (captured_since is not None
                        and now - captured_since >= CAPTURE_GRACE_SECONDS):
                    _log(f"已捕获 {label} 关键 Cookie，但只读复核持续未通过"
                         f"（{msg or '无响应'}），已等待 {int(now - captured_since)}s。")
                    _log("按登录成功保存；若账号实际不可用，请在账号管理页用"
                         "「凭证健康检查」查看原因后重新登录。")
                    verified_ok = True
                    break

                if now - last_log >= PROGRESS_LOG_EVERY:
                    last_log = now
                    _log(f"仍在等待 {label} 登录完成…已等待 {int(now - started)}s"
                         + (f"（{msg}）" if msg else ""))

                time.sleep(6)

            if not verified_ok:
                _log(f"超时（{timeout_seconds}s）未检测到登录完成。")
                _log("请确认已在浏览器中登录 " + label + " 后重试；"
                     "若已在页面上登录成功却仍无反应，多半是该平台的只读校验接口发生变化，"
                     "可改用「凭据导入」粘贴 Cookie，或运行 "
                     f"python3 browser_login.py --platform {platform} 复现并把日志发我。")
                return 1

            _log(f"登录成功：已捕获 {label} Cookie（{mask_cookie(cookie)}）")
            # 显式关窗：持久化本次会话状态，下次登录无需重复扫码
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass
        # with 退出兜底：无论走哪条 return，浏览器都会被关掉
    except Exception as e:  # noqa: BLE001
        _log(f"内置浏览器异常：{e}")
        return 1

    extra = {}
    if nickname:
        extra["nickname"] = nickname
    if uid:
        extra["uid"] = uid

    final_name = resolve_name(platform, name, nickname, uid)
    if auto_name:
        _log(f"已自动读取账号名称：{final_name}"
             + ("" if nickname else "（未能取到昵称，改用账号 ID 命名）"))

    if not save_cookie_account(final_name, platform, cookie, extra, enabled=enabled):
        _log("Cookie 写回 accounts.json 失败（请检查账号文件权限）")
        return 1
    _log(f"已保存账号「{final_name}」到 accounts.json（昵称 {nickname or '(未取到)'}，uid {uid or '(未取到)'}）")
    _log(f"下一步：运行  python3 checkin.py --only {final_name}  验证签到。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Cookie 型平台浏览器登录（Bilibili/联想智选/京东）")
    parser.add_argument("--platform", required=True, choices=list(PLATFORMS.keys()),
                        help="平台：bilibili / lenovo / jd")
    parser.add_argument("--name", default="",
                        help="账号名称（唯一），如 bili-main；留空时自动读取该账号真实昵称命名")
    parser.add_argument("--timeout", type=int, default=600, help="等待登录超时秒数，默认 600")
    parser.add_argument("--enabled", type=int, default=1, choices=[0, 1],
                        help="新增账号是否加入自动签到（1=开启，0=关闭；仅新建账号时生效）")
    args = parser.parse_args()
    return run_browser_login(args.platform, args.name, args.timeout,
                             enabled=bool(args.enabled))


if __name__ == "__main__":
    sys.exit(main())
