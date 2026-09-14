#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
多账号多软件自动签到工具 - 主执行脚本

读取 accounts.json 中配置的所有账号签到请求，依次重放并记录结果日志。
本脚本只负责"重放"你抓包得到的真实请求，不包含任何写死的接口地址。

用法：
    python3 checkin.py                  # 签到所有账号
    python3 checkin.py --only trae-1    # 只签到指定账号
    python3 checkin.py --dry-run        # 只预览请求，不真正发送

退出码：全部成功返回 0，任一账号失败返回 1（供定时任务感知）。
"""

import argparse
import datetime
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.json")
LOG_FILE = os.path.join(BASE_DIR, "logs", "checkin.log")

sys.path.insert(0, BASE_DIR)
from trae_api import TraeClient, TraeError  # noqa: E402
from workbuddy import WorkBuddyClient, WorkBuddyError, _read_token  # noqa: E402

DEFAULT_SETTINGS = {
    "timeout": 20,            # 单次请求超时（秒）
    "min_delay_seconds": 2,   # 账号间随机延迟下限
    "max_delay_seconds": 10,  # 账号间随机延迟上限
    "retry_times": 1,         # 单个请求失败后的重试次数
}

# 日志里对长敏感字段只保留前 8 个字符，防止 token/cookie 泄露进日志
_SENSITIVE_HEADERS = ("authorization", "cookie", "token", "x-token", "x-access-token")

# 定向脱敏：只打码“像凭证”的子串，绝不整句截断。
# 之前用 mask_sensitive(desc) 把整条中文描述也截成前 8 字符，
# 导致报错被吞成“签到失败：当前参******”，本次改为精准脱敏。
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]{10,}(?:\.[A-Za-z0-9_\-]+){1,2}")
_SENS_VAL_RE = re.compile(
    r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|cookie\s*[:=]\s*"
    r"|x-cloudide-session\s*[:=]\s*|x-token\s*[:=]\s*|x-access-token\s*[:=]\s*"
    r"|[\"']?(?:token|session|refresh_token|access_token)[\"']?\s*[:=]\s*)"
    r"[^ ,;\"'\t\n{}]{8,}"
)


def redact_secrets(text: str) -> str:
    """只对凭证类子串打码，保留可读的报错/积分信息原文。"""
    if not isinstance(text, str):
        return str(text)
    text = _SENS_VAL_RE.sub(lambda m: m.group(1) + "******", text)
    text = _JWT_RE.sub("[token]", text)
    return text


def mask_sensitive(value: str) -> str:
    if len(value) <= 12:
        return "******"
    return value[:8] + "******"


def log(msg: str) -> None:
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_accounts() -> dict:
    if not os.path.exists(ACCOUNTS_FILE):
        log(f"未找到账号配置文件：{ACCOUNTS_FILE}")
        sys.exit(1)
    with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_accounts(config: dict) -> None:
    """原子写回 accounts.json（仅更新内存中已变更的非敏感快照字段）。"""
    tmp = ACCOUNTS_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        os.replace(tmp, ACCOUNTS_FILE)
    except OSError as e:
        log(f"保存 accounts.json 失败：{e}")


def run_growth(only: str | None) -> int:
    """仅对 WorkBuddy 账号执行成长中心六步（不重复签到）。"""
    config = load_accounts()
    accounts = config.get("accounts", [])
    targets = [a for a in accounts
               if a.get("type") == "workbuddy"
               and ((only and a.get("name") == only) or (not only and a.get("enabled", True)))]
    if not targets:
        log("未找到启用的 WorkBuddy 账号")
        return 1
    fail = 0
    for acc in targets:
        name = acc.get("name", "?")
        try:
            token = _read_token()
            g = WorkBuddyClient(token).growth()
            acc["workbuddy_growth"] = {
                "date": datetime.datetime.now().strftime("%Y-%m-%d"),
                "summary": g.get("summary", ""),
                "credited": g.get("credited", 0),
                "energy": g.get("energy"),
                "streak_days": g.get("streak_days"),
            }
            save_accounts(config)
            if g.get("auth_lost"):
                fail += 1
                log(f"[FAIL] {name}(workbuddy) 成长中心：登录态已失效")
            else:
                log(f"[OK] {name}(workbuddy) 成长中心：{redact_secrets(g.get('summary') or '')}")
        except WorkBuddyError as e:
            fail += 1
            log(f"[FAIL] {name}(workbuddy) 成长中心：{redact_secrets(str(e))}")
    return 0 if fail == 0 else 1


def fill_template(text: str, today: str, today_iso: str) -> str:
    """替换请求 URL/body 中的日期占位符，兼容带格式的写法。"""
    if not isinstance(text, str):
        return json.dumps(text, ensure_ascii=False)
    replacements = {
        "{today}": today,            # 20260914
        "{today_iso}": today_iso,    # 2026-09-14
        "{today_cn}": datetime.datetime.now().strftime("%Y年%m月%d日"),
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text


def send_request(req_conf: dict, settings: dict) -> tuple:
    """发送单个签到请求，返回 (是否成功, 描述)。"""
    method = str(req_conf.get("method", "GET")).upper()
    url = req_conf["url"]
    headers = {str(k): str(v) for k, v in req_conf.get("headers", {}).items()}
    body = req_conf.get("body")

    today = datetime.datetime.now().strftime("%Y%m%d")
    today_iso = datetime.datetime.now().strftime("%Y-%m-%d")
    url = fill_template(url, today, today_iso)
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False)
    if isinstance(body, str):
        body = fill_template(body, today, today_iso)

    data = body.encode("utf-8") if body is not None else None
    if data is not None and "content-type" not in {k.lower() for k in headers}:
        headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    attempt = 0
    last_err = ""
    while attempt <= settings.get("retry_times", 0):
        attempt += 1
        try:
            with urllib.request.urlopen(req, timeout=settings.get("timeout", 20)) as resp:
                resp_body = resp.read().decode("utf-8", errors="replace")
                return True, f"HTTP {resp.status}, resp={resp_body[:200]}"
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            last_err = f"HTTP {e.code}, resp={err_body[:200] if err_body else '(空)'}"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
        if attempt <= settings.get("retry_times", 0):
            time.sleep(1 + random.random() * 2)

    return False, last_err


def run_trae_acc(acc: dict) -> tuple:
    """Trae 内置登录账号的签到：先读会话 Cookie 换 JWT，再调 claim 接口。

    返回 (是否成功, 描述)。描述固定展示 API 的错误码 code + 可读 message，
    该文案本身不含 token/session，仅在外层做定向凭证脱敏兜底。
    """
    auth = acc.get("trae_auth", {})
    session = (auth.get("session") or "").strip()
    if not session:
        return False, "缺少 trae_auth.session，请先用内置浏览器登录该账号（trae_login.py）"
    client = TraeClient(
        session=session,
        device_id=(auth.get("device_id") or "").strip(),
        session_token=(auth.get("token") or "").strip(),
    )
    res = client.checkin()
    http = res.get("http")
    if res.get("ok"):
        cred = res.get("credits", 0)
        extra = f"（今日已签到，累计 {cred} 积分）" if res.get("already_checked") else f"（HTTP {http}）"
        return True, f"签到成功，本次获得 {cred} 积分 {extra}"
    code = res.get("code")
    msg = res.get("message") or "未知错误"
    code_s = f"，错误码 {code}" if code not in (None, -1) else ""
    return False, f"签到失败：{msg}（HTTP {http}{code_s}）"


def run_workbuddy_acc(acc: dict) -> tuple:
    """WorkBuddy 账号签到：实时读取本机登录态 token（仅内存），
    先查今日状态，未签则调 daily-checkin（code=10001 幂等兜底）。
    签到成功后顺带执行成长中心六步（旅行/任务/补登/兑换/盲盒），
    并把非敏感快照写入 acc["workbuddy_growth"]（由调用方落盘）。

    token 绝不落盘、绝不写日志、绝不出现在返回描述中。
    返回 (是否成功, 描述)。
    """
    name = acc.get("name", "?")
    try:
        token = _read_token()
        client = WorkBuddyClient(token)
    except WorkBuddyError as e:
        return False, f"签到失败：{e}"

    def _append_growth(desc: str) -> str:
        try:
            g = client.growth()
        except Exception:  # noqa: BLE001
            return desc
        acc["workbuddy_growth"] = {
            "date": datetime.datetime.now().strftime("%Y-%m-%d"),
            "summary": g.get("summary", ""),
            "credited": g.get("credited", 0),
            "energy": g.get("energy"),
            "streak_days": g.get("streak_days"),
        }
        if g.get("auth_lost"):
            return desc + "；成长中心：登录态已失效，请重新登录 WorkBuddy 桌面端"
        if g.get("steps") or g.get("credited"):
            return desc + "；成长中心：" + g.get("summary", "")
        return desc

    try:
        st = client.status()
        if not st.get("ok"):
            if st.get("http") in (401, 403):
                return False, f"签到失败：{st.get('message')}"
            # 状态查询失败（网络/异常）不阻断，直接尝试签到，由 daily-checkin 幂等兜底
        elif st.get("today_checked_in"):
            return True, _append_growth(
                f"今日已签到，累计 {st.get('credit')} 积分"
                f"（连续第 {st.get('streak_days')} 天）"
            )
        res = client.checkin()
        if not res.get("ok"):
            return False, f"签到失败：{res.get('message')}（HTTP {res.get('http')}）"
        if res.get("already"):
            return True, _append_growth(
                f"今日已签到（幂等），累计 {res.get('credit')} 积分"
                f"（连续第 {res.get('streak_days')} 天）"
            )
        return True, _append_growth(
            f"签到成功，本次获得 {res.get('credit')} 积分"
            f"（连续第 {res.get('streak_days')} 天）"
        )
    except WorkBuddyError as e:
        return False, f"签到失败：{e}"
    except Exception as e:  # noqa: BLE001
        return False, f"签到失败：{type(e).__name__}: {e}"


def run_checkin(only: str | None, dry_run: bool) -> int:
    config = load_accounts()
    settings = {**DEFAULT_SETTINGS, **config.get("settings", {})}
    accounts = config.get("accounts", [])
    if not accounts:
        log("账号列表为空，请先用 curl_to_account.py 添加账号，或用内置浏览器登录。")
        return 1

    targets = [a for a in accounts
               if (only and a.get("name") == only) or (not only and a.get("enabled", True))]
    if only and not targets:
        log(f"未找到账号：{only}")
        return 1

    ok_count = 0
    fail_count = 0
    log(f"开始签到：共 {len(targets)} 个账号（dry_run={dry_run}）")

    for acc in targets:
        name = acc.get("name", "?")
        app = acc.get("app", "?")
        is_trae = acc.get("type") == "trae"
        is_wb = acc.get("type") == "workbuddy"

        if dry_run:
            if is_trae:
                log(f"[预览] {name}(trae) 内置登录模式：X-Cloudide-Session 换 JWT -> checkin_credits/claim")
            elif is_wb:
                log(f"[预览] {name}(workbuddy) 本机登录态：checkin-status -> daily-checkin")
            else:
                for r in acc.get("requests", []):
                    method = str(r.get("method", "GET")).upper()
                    log(f"[预览] {name}({app}) {method} {r.get('url')}")
            ok_count += 1
            if acc is not targets[-1]:
                time.sleep(random.uniform(settings["min_delay_seconds"], settings["max_delay_seconds"]))
            continue

        try:
            if is_trae:
                ok, desc = run_trae_acc(acc)
                shown = redact_secrets(desc)
                if ok:
                    ok_count += 1
                    log(f"[OK] {name}({app}) -> {shown}")
                else:
                    fail_count += 1
                    log(f"[FAIL] {name}({app}) -> {shown}")
            elif is_wb:
                ok, desc = run_workbuddy_acc(acc)
                shown = redact_secrets(desc)
                if ok:
                    ok_count += 1
                    log(f"[OK] {name}({app}) -> {shown}")
                else:
                    fail_count += 1
                    log(f"[FAIL] {name}({app}) -> {shown}")
                if "workbuddy_growth" in acc:
                    save_accounts(config)  # 落盘非敏感成长中心快照（不含任何 token）
            else:
                for r in acc.get("requests", []):
                    r_ok, r_desc = send_request(r, settings)
                    shown = redact_secrets(r_desc)
                    if r_ok:
                        ok_count += 1
                        log(f"[OK] {name}({app}) -> {shown}")
                    else:
                        fail_count += 1
                        log(f"[FAIL] {name}({app}) -> {shown}")
        except (TraeError, Exception) as e:  # noqa: BLE001
            fail_count += 1
            log(f"[FAIL] {name}({app}) 异常: {e}")
        # 账号间随机间隔，降低风控触发概率
        if acc is not targets[-1]:
            time.sleep(random.uniform(settings["min_delay_seconds"], settings["max_delay_seconds"]))

    log(f"签到结束：成功 {ok_count}，失败 {fail_count}")
    return 0 if fail_count == 0 else 1


def run_credits(only: str | None) -> int:
    """查询账号积分/余额并打印（Trae 查额度包；WorkBuddy 查本机登录态累计/连续天数）。"""
    config = load_accounts()
    accounts = config.get("accounts", [])
    targets = [a for a in accounts
               if (only and a.get("name") == only) or (not only and a.get("enabled", True))]
    if only and not targets:
        log(f"未找到账号：{only}")
        return 1
    if not targets:
        log("账号列表为空。")
        return 1

    fail = 0
    for acc in targets:
        name = acc.get("name", "?")
        app = acc.get("app", "?")
        if acc.get("type") == "workbuddy":
            try:
                token = _read_token()
                res = WorkBuddyClient(token).status()
                if not res.get("ok"):
                    log(f"[积分] {name}(workbuddy) 查询失败：{redact_secrets(str(res.get('message')))}（HTTP {res.get('http')}）")
                    fail += 1
                    continue
                checked = "已签" if res.get("today_checked_in") else "未签"
                log(
                    f"[积分] {name}(workbuddy) 今日{checked}，累计 {res.get('credit')} 积分，"
                    f"连续 {res.get('streak_days')} 天"
                )
            except WorkBuddyError as e:
                fail += 1
                log(f"[积分] {name}(workbuddy) 查询失败：{redact_secrets(str(e))}")
            continue
        if acc.get("type") != "trae":
            log(f"[积分] {name}({app}) 暂无公开积分/余额接口（WorkBuddy 需抓包签到，无法查询余额）")
            fail += 1
            continue
        auth = acc.get("trae_auth", {})
        try:
            client = TraeClient(
                session=(auth.get("session") or "").strip(),
                device_id=(auth.get("device_id") or "").strip(),
                session_token=(auth.get("token") or "").strip(),
            )
            res = client.credits()
            if not res.get("ok"):
                log(f"[积分] {name}(trae) 查询失败：{redact_secrets(str(res.get('message')))}（HTTP {res.get('http')}）")
                fail += 1
                continue
            packs = res.get("packs", [])
            detail = "；".join(
                f"{p.get('desc') or p.get('name') or '额度包'} 剩余 {p.get('remaining'):g}" for p in packs
            ) or "无额度包"
            log(
                f"[积分] {name}(trae) 总限额 {res.get('total_limit'):g}，已用 {res.get('total_used'):g}，"
                f"剩余 {res.get('remaining'):g} 积分。明细：{detail}"
            )
        except (TraeError, Exception) as e:  # noqa: BLE001
            fail += 1
            log(f"[积分] {name}(trae) 查询异常：{redact_secrets(str(e))}")
    return 0 if fail == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="多账号自动签到工具")
    parser.add_argument("--only", help="只处理指定账号 name")
    parser.add_argument("--dry-run", action="store_true", help="只预览不发送")
    parser.add_argument("--credits", action="store_true", help="查询账号积分/余额（不签到）")
    parser.add_argument("--growth", action="store_true", help="仅对 WorkBuddy 账号执行成长中心（不签到）")
    args = parser.parse_args()
    if args.credits:
        sys.exit(run_credits(args.only))
    if args.growth:
        sys.exit(run_growth(args.only))
    sys.exit(run_checkin(args.only, args.dry_run))


if __name__ == "__main__":
    main()
