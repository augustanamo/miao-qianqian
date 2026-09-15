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
STATE_FILE = os.path.join(BASE_DIR, "logs", "checkin_state.json")
_STATE_KEEP_DAYS = 14

sys.path.insert(0, BASE_DIR)
from trae_api import TraeClient, TraeError  # noqa: E402
from workbuddy import (  # noqa: E402
    WorkBuddyClient, WorkBuddyError,
    account_token, refresh_access_token,
)
from cookie_manager import CookieError  # noqa: E402
import bilibili as bilibili_mod  # noqa: E402,F401
import lenovo as lenovo_mod  # noqa: E402,F401
import jd as jd_mod  # noqa: E402,F401

# Cookie 型平台路由表：type -> 平台模块。模块需暴露
#   client_from_account(acc) -> 带 cookie 的客户端（缺失抛 CookieError）
#   client.checkin() -> {ok, already, credits, message, http}
#   client.status()  -> {ok, signed_today, nickname, credits, summary, http, message}
_COOKIE_PLATFORMS = {
    "bilibili": bilibili_mod,
    "lenovo": lenovo_mod,
    "jd": jd_mod,
}

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


# ---------- 本地签到台账 ----------
# 作用：同一天内重复执行签到时（手动点两次、定时任务与手动撞车、失败重试），
# 已经成功过的账号直接跳过，不再发一次请求。
# 各平台的"今日是否已签到"只读接口覆盖不全（Bilibili / 京东没有公开的只读接口），
# 台账是它们的通用兜底；有原生接口的（Trae / WorkBuddy / 联想）会先走原生预检。
# 台账只记录"是否成功"这类非敏感状态，**不含任何 token / cookie**。

def load_checkin_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_checkin_state(state: dict) -> None:
    """原子落盘，并只保留最近 _STATE_KEEP_DAYS 天，避免文件无限增长。"""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    for stale in sorted(state)[:-_STATE_KEEP_DAYS]:
        state.pop(stale, None)
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        log(f"保存签到台账失败：{e}")


def state_done_today(state: dict, date: str, name: str):
    """今天该账号是否已成功签到；是则返回台账记录，否则 None。"""
    rec = (state.get(date) or {}).get(name)
    return rec if isinstance(rec, dict) and rec.get("ok") else None


def state_record(state: dict, date: str, name: str, platform: str,
                 ok: bool, credits, message: str) -> None:
    state.setdefault(date, {})[name] = {
        "platform": platform,
        "ok": bool(ok),
        "credits": credits,
        "message": (message or "")[:160],
        "at": datetime.datetime.now().strftime("%H:%M:%S"),
    }


_CREDIT_IN_MSG = re.compile(r"(?:本次获得|累计|共)\s*([\d.]+)\s*(?:积分|经验|京豆|乐豆|硬币)")


def credit_from_message(msg: str):
    """从签到描述里抽一个可展示的积分数，抽不到返回 None（仅用于台账展示）。"""
    m = _CREDIT_IN_MSG.search(msg or "")
    try:
        return float(m.group(1)) if m else None
    except (TypeError, ValueError):
        return None


def fmt_num(v) -> str:
    """数字格式化：去掉无意义的小数尾巴（3.0 → 3，357.308 → 357.308）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "0"
    return f"{f:g}"


def is_already_msg(msg: str) -> bool:
    """描述是否表达"今日已签到、本次未重复领取"。日志与 UI 都按此判定。"""
    s = msg or ""
    return any(k in s for k in ("已签到", "无需重复", "跳过重复"))


def run_growth(only: str | None) -> int:
    """仅对 WorkBuddy 账号执行成长中心六步（不重复签到）。

    每个账号使用自身登录态（OAuth 多账号优先，旧版单账号回退本机），
    token 仅内存使用；401/403 且可续期时自动 refresh 并写回。
    """
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
            token = account_token(acc)
            client = _wb_client(acc, token)
            g = client.growth()
            if g.get("auth_lost") and _wb_refresh(acc):
                g = _wb_client(acc, account_token(acc)).growth()
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
                log(f"[FAIL] {name}(workbuddy) 成长中心：登录态已失效，请重新扫码登录（账号管理 → WorkBuddy）")
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
    """Trae 内置登录账号的签到：先查今日状态，未签才调 claim 接口。

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

    # 预检：能确证"今天已签到"就不再提交 claim，避免重复签到。
    # 判不出来（字段名不认识 / 网络异常）时静默跳过预检、照常签到，
    # 宁可多发一次请求，也绝不因为解析问题漏签。
    # 注：该接口的 credits 字段是"今日签到奖励"，不是账户可用余额，
    # 所以跳过文案里不带数字，避免被误读成累计积分。
    try:
        st = client.status()
        if st.get("known") and st.get("checked_in"):
            return True, "今日已签到，已跳过重复签到（未重复提交签到请求）"
    except TraeError:
        pass
    except Exception:  # noqa: BLE001
        pass

    res = client.checkin()
    http = res.get("http")
    code = res.get("code")
    if res.get("ok"):
        cred = res.get("credits") or 0
        if res.get("already_checked"):
            return True, "今日已签到，已跳过重复签到（平台返回幂等）"
        if cred:
            return True, f"签到成功，本次获得 {fmt_num(cred)} 积分（HTTP {http}）"
        # credits 为 0 时不要说"获得 0 积分"，那会让人以为签到没生效
        return True, f"签到成功（今日无新增积分，HTTP {http}）"
    msg = res.get("message") or "未知错误"
    code_s = f"，错误码 {code}" if code not in (None, -1) else ""
    return False, f"签到失败：{msg}（HTTP {http}{code_s}）"


def _wb_client(acc: dict, token: str) -> WorkBuddyClient:
    """按账号快照构建 WorkBuddy 客户端（带 uid/domain/enterprise_id）。"""
    auth = acc.get("workbuddy_auth") or {}
    if not isinstance(auth, dict):
        auth = {}
    return WorkBuddyClient(
        token,
        uid=(auth.get("uid") or "").strip(),
        domain=(auth.get("domain") or "").strip(),
        enterprise_id=(auth.get("enterprise_id") or "").strip(),
    )


def _wb_refresh(acc: dict) -> bool:
    """用账号 refreshToken 续期并更新 acc 内存快照（调用方负责落盘）。

    返回是否续期成功；账号无 refresh_token 或续期失败返回 False。
    """
    auth = acc.get("workbuddy_auth") or {}
    if not isinstance(auth, dict):
        return False
    refresh_token = (auth.get("refresh_token") or "").strip()
    if not refresh_token:
        return False
    r = refresh_access_token(refresh_token, (auth.get("domain") or "").strip())
    if not r.get("ok"):
        return False
    auth["access_token"] = r["access_token"]
    auth["refresh_token"] = r.get("refresh_token") or refresh_token
    if r.get("expires_at") is not None:
        auth["expires_at"] = r["expires_at"]
    if r.get("refresh_expires_at") is not None:
        auth["refresh_expires_at"] = r["refresh_expires_at"]
    if r.get("token_type"):
        auth["token_type"] = r["token_type"]
    acc["workbuddy_auth"] = auth
    acc["_wb_token_updated"] = True  # 供调用方检测后落盘 accounts.json
    return True


def run_workbuddy_acc(acc: dict) -> tuple:
    """WorkBuddy 账号签到：优先使用账号自身登录态（OAuth 多账号），
    旧版单账号自动回退本机登录态文件（兼容路径）。token 仅内存使用。

    先查今日状态，未签则调 daily-checkin（code=10001 幂等兜底）。
    签到成功后顺带执行成长中心六步，并把非敏感快照写入
    acc["workbuddy_growth"]（由调用方落盘）。
    401/403 且账号有 refresh_token 时自动续期并重试一次；
    续期失败/无续期凭证时给出中文可读提示（请重新扫码登录）。

    token 绝不落盘、绝不写日志、绝不出现在返回描述中。
    返回 (是否成功, 描述)。
    """
    name = acc.get("name", "?")
    try:
        token = account_token(acc)
        client = _wb_client(acc, token)
    except WorkBuddyError as e:
        return False, f"签到失败：{e}"

    def _auth_fail(msg: str) -> str:  # noqa: ARG001
        return ("签到失败：登录态已失效（HTTP 401/403），请重新扫码登录"
                "（账号管理 → WorkBuddy → 扫码登录添加账号）")

    def _rebuild_after_refresh() -> bool:
        if not _wb_refresh(acc):
            return False
        nonlocal client
        client = _wb_client(acc, account_token(acc))
        return True

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
            return desc + "；成长中心：登录态已失效，请重新扫码登录"
        if g.get("steps") or g.get("credited"):
            return desc + "；成长中心：" + g.get("summary", "")
        return desc

    try:
        st = client.status()
        if st.get("http") in (401, 403) and _rebuild_after_refresh():
            st = client.status()
        if st.get("http") in (401, 403):
            return False, _auth_fail(st.get("message") or "令牌已过期或无权限")
        if not st.get("ok"):
            # 状态查询失败（网络/异常）不阻断，直接尝试签到，由 daily-checkin 幂等兜底
            pass
        elif st.get("today_checked_in"):
            return True, _append_growth(
                f"今日已签到，累计 {st.get('credit')} 积分"
                f"（连续第 {st.get('streak_days')} 天）"
            )
        res = client.checkin()
        if res.get("http") in (401, 403) and _rebuild_after_refresh():
            res = client.checkin()
        if res.get("http") in (401, 403):
            return False, _auth_fail(res.get("message") or "令牌已过期或无权限")
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


def run_cookie_acc(acc: dict) -> tuple:
    """Cookie 型平台（Bilibili / 联想智选 / 京东）签到统一入口。

    通过 _COOKIE_PLATFORMS 路由到各平台单文件客户端（cookie 仅内存持有，
    模块内部绝不打印/落盘 cookie 明文）。

    返回 (是否成功, 描述, 是否平台受限)。

    第三项 restricted=True 表示"平台侧不可用"（活动下线 / 接口迁移 / 风控），
    必须与"cookie 失效、网络错误"区别对待：前者不该催用户去重登，也不该在
    界面上显示成红色的"签到失败"。
    """
    ptype = acc.get("type")
    mod = _COOKIE_PLATFORMS.get(ptype)
    if mod is None:
        return False, f"不支持的平台类型：{ptype}", False
    try:
        client = mod.client_from_account(acc)
        res = client.checkin()
    except CookieError as e:
        return False, f"签到失败：{e}", False
    except Exception as e:  # noqa: BLE001
        return False, f"签到失败：{type(e).__name__}: {e}", False

    restricted = bool(res.get("restricted"))
    msg = str(res.get("message") or "").strip()
    if res.get("ok"):
        if res.get("already"):
            # 平台自己的文案更准（含"已签到"判定），优先用；
            # is_already_msg / Swift looksAlreadySigned 都靠"已签到"识别跳过。
            return True, msg or "今日已签到（幂等）", False
        cred = res.get("credits")
        cred_s = (f"，本次获得 {cred:g} 积分"
                  if isinstance(cred, (int, float)) and cred and "本次获得" not in msg else "")
        if msg:
            return True, msg + cred_s, False
        return True, f"签到成功{cred_s}", False

    http = res.get("http")
    http_s = f"（HTTP {http}）" if http else ""
    if not msg:
        msg = "未知错误"
    if restricted:
        return False, msg + http_s, True
    return False, f"签到失败：{msg}{http_s}", False


def _report_result(name: str, app: str, ok: bool, desc: str, restricted: bool = False) -> str:
    """统一的签到结果上报：[OK] / [受限] / [FAIL]。

    [受限] 表示平台侧不可用（活动下线 / 接口迁移 / 风控），与 [FAIL]
    （cookie 失效、网络错误等需要用户处理的问题）区分开——否则用户会
    看到一个红色的"签到失败"，跑去反复重新登录一个本来好的账号。
    桌面端 ParsedLogs 会把 [受限] 解析成第三种状态。
    """
    shown = redact_secrets(desc)
    if ok:
        log(f"[OK] {name}({app}) -> {shown}")
    elif restricted:
        log(f"[受限] {name}({app}) -> {shown}")
    else:
        log(f"[FAIL] {name}({app}) -> {shown}")
    return shown


def run_checkin(only: str | None, dry_run: bool, force: bool = False) -> int:
    """执行签到。

    force=True 时忽略本地签到台账，强制重新发起签到请求（用于"我就是想再签一次"
    或怀疑台账与平台实际状态不一致时的排查）。
    """
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
    restricted_count = 0
    skipped_count = 0
    today_date = datetime.datetime.now().strftime("%Y-%m-%d")
    # 签到台账只在真正签到（非预览）时读写
    state = {} if dry_run else load_checkin_state()
    log(f"开始签到：共 {len(targets)} 个账号（dry_run={dry_run}{'，强制重签' if force else ''}）")

    for acc in targets:
        name = acc.get("name", "?")
        app = acc.get("app", "?")
        is_trae = acc.get("type") == "trae"
        is_wb = acc.get("type") == "workbuddy"
        is_cookie = acc.get("type") in _COOKIE_PLATFORMS

        if dry_run:
            if is_trae:
                log(f"[预览] {name}(trae) 内置登录模式：X-Cloudide-Session 换 JWT -> checkin_credits/claim")
            elif is_wb:
                log(f"[预览] {name}(workbuddy) 本机登录态：checkin-status -> daily-checkin")
            elif is_cookie:
                log(f"[预览] {name}({app}) Cookie 登录：{acc.get('type')} 官方签到接口（cookie 不落盘、不回显）")
            else:
                for r in acc.get("requests", []):
                    method = str(r.get("method", "GET")).upper()
                    log(f"[预览] {name}({app}) {method} {r.get('url')}")
            ok_count += 1
            if acc is not targets[-1]:
                time.sleep(random.uniform(settings["min_delay_seconds"], settings["max_delay_seconds"]))
            continue

        # 今天已成功签到过的账号直接跳过，不再发一次请求。
        # 手动连点、定时任务与手动撞车、失败重试这几种场景都会命中这里。
        if not dry_run and not force:
            rec = state_done_today(state, today_date, name)
            if rec is not None:
                ok_count += 1
                skipped_count += 1
                log(f"[OK] {name}({app}) -> 今日已签到，已跳过重复签到（{rec.get('at', '')} 已完成）")
                continue

        try:
            if is_trae:
                ok, desc = run_trae_acc(acc)
                ok_count += 1 if ok else 0
                fail_count += 0 if ok else 1
                _report_result(name, app, ok, desc)
                state_record(state, today_date, name, str(acc.get("type") or app),
                             ok, credit_from_message(desc), desc)
                save_checkin_state(state)
            elif is_wb:
                ok, desc = run_workbuddy_acc(acc)
                ok_count += 1 if ok else 0
                fail_count += 0 if ok else 1
                _report_result(name, app, ok, desc)
                if "workbuddy_growth" in acc or acc.get("_wb_token_updated"):
                    save_accounts(config)  # 落盘非敏感成长中心快照 / OAuth 续期后的新 token（token 仅写入 accounts.json）
                state_record(state, today_date, name, str(acc.get("type") or app),
                             ok, credit_from_message(desc), desc)
                save_checkin_state(state)
            elif is_cookie:
                ok, desc, restricted = run_cookie_acc(acc)
                if ok:
                    ok_count += 1
                elif restricted:
                    restricted_count += 1
                else:
                    fail_count += 1
                _report_result(name, app, ok, desc, restricted)
                state_record(state, today_date, name, str(acc.get("type") or app),
                             ok, credit_from_message(desc), desc)
                save_checkin_state(state)
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

    log(f"签到结束：成功 {ok_count}（其中 {skipped_count} 个已签到自动跳过）"
        + (f"，平台受限 {restricted_count}" if restricted_count else "")
        + f"，失败 {fail_count}")
    return 0 if fail_count == 0 else 1


_PLATFORM_LABEL = {
    "trae": "Trae",
    "workbuddy": "WorkBuddy",
    "bilibili": "Bilibili",
    "lenovo": "联想智选",
    "jd": "京东",
}
# 各平台"积分"的实际单位（口径不同，不能简单相加，界面上需标明）
_PLATFORM_UNIT = {
    "trae": "积分",
    "workbuddy": "积分",
    "bilibili": "硬币",
    "lenovo": "乐豆",
    "jd": "京豆",
}


def _num_or_none(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _positive_num(v, zero_ok: bool = False):
    """转成数字；<=0 或无法解析时返回 None（用于"这个平台没给出余额"）。"""
    f = _num_or_none(v)
    if f is None:
        return None
    if f > 0:
        return f
    return 0.0 if zero_ok else None


def _int_or_none(v):
    f = _num_or_none(v)
    return int(f) if f is not None else None


def _credit_record(name: str, app: str, ptype: str, ok: bool, balance=None,
                   unit: str | None = None, used=None, limit=None, streak=None,
                   state: str = "", summary: str = "", message: str = "") -> dict:
    """单个账号的积分记录（机读口径，供桌面端直接渲染）。"""
    return {
        "name": name,
        "app": app,
        "type": ptype,
        "label": _PLATFORM_LABEL.get(ptype, app),
        "unit": unit or _PLATFORM_UNIT.get(ptype, "积分"),
        "ok": bool(ok),
        "balance": balance,
        "used": used,
        "limit": limit,
        "streak_days": streak,
        "state": state,
        "summary": summary,
        "message": message,
    }


def _emit_credits_json(items: list) -> None:
    """打印机读汇总行。用 print 而非 log()：不加时间戳、不写入 checkin.log，
    保证日志解析（ParsedLogs）不会把这一行当成签到记录。"""
    balances = [i["balance"] for i in items if isinstance(i["balance"], (int, float))]
    payload = {
        "date": datetime.datetime.now().strftime("%Y-%m-%d"),
        "total": sum(balances),
        "counted": len(balances),
        "items": items,
    }
    try:
        print("[CREDITS_JSON] " + json.dumps(payload, ensure_ascii=False), flush=True)
    except OSError:
        pass


def run_credits(only: str | None, as_json: bool = False) -> int:
    """查询账号积分/余额并打印（Trae 查额度包；WorkBuddy 查累计积分/连续天数）。

    各平台"积分"口径不同，统一按"账号当前可用余额"归一到一个 balance 字段：
      Trae        → 额度包剩余积分
      WorkBuddy   → checkin-status 的累计积分（接口未给出时留空）
      Bilibili    → 硬币
      联想智选     → 乐豆
      京东        → 无公开余额接口
    各平台单位不同，unit 字段标明实际单位；金额混合汇总仅作"点数合计"理解。

    as_json=True 时额外在 stdout 打印一行 `[CREDITS_JSON] {...}`，
    供桌面端直接解析（避免在 Swift 侧用正则去啃中文文案）。
    """
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
    items: list = []
    for acc in targets:
        name = acc.get("name", "?")
        app = acc.get("app", "?")
        ptype = str(acc.get("type") or "")
        if ptype == "workbuddy":
            try:
                token = account_token(acc)
                res = _wb_client(acc, token).status()
                if not res.get("ok"):
                    if res.get("http") in (401, 403):
                        msg = "登录态已失效，请重新扫码登录（账号管理 → WorkBuddy → 扫码登录添加账号）"
                    else:
                        msg = f"{redact_secrets(str(res.get('message')))}（HTTP {res.get('http')}）"
                    log(f"[积分] {name}(workbuddy) 查询失败：{msg}")
                    items.append(_credit_record(name, app, ptype, False, message=msg))
                    fail += 1
                    continue
                checked = "已签" if res.get("today_checked_in") else "未签"
                streak = res.get("streak_days")
                log(
                    f"[积分] {name}(workbuddy) 今日{checked}，累计 {res.get('credit')} 积分，"
                    f"连续 {streak} 天"
                )
                items.append(_credit_record(
                    name, app, ptype, True,
                    balance=_positive_num(res.get("credit")),
                    streak=_int_or_none(streak),
                    state=("今日已签" if res.get("today_checked_in") else "今日未签"),
                    summary=f"今日{checked}，连续 {streak} 天",
                    message="接口未返回累计积分" if not _positive_num(res.get("credit")) else "",
                ))
            except WorkBuddyError as e:
                msg = redact_secrets(str(e))
                fail += 1
                log(f"[积分] {name}(workbuddy) 查询失败：{msg}")
                items.append(_credit_record(name, app, ptype, False, message=msg))
            continue
        if ptype in _COOKIE_PLATFORMS:
            mod = _COOKIE_PLATFORMS[ptype]
            try:
                client = mod.client_from_account(acc)
                res = client.status()
                if not res.get("ok"):
                    msg = f"{redact_secrets(str(res.get('message')) or '')}（HTTP {res.get('http')}）"
                    log(f"[积分] {name}({app}) 查询失败：{msg}")
                    items.append(_credit_record(name, app, ptype, False, message=msg))
                    fail += 1
                    continue
                if res.get("signed_today") is True:
                    state = "今日已签名"
                elif res.get("signed_today") is False:
                    state = "今日未签名"
                else:
                    state = "登录态正常"
                summary = res.get("summary") or ""
                log(f"[积分] {name}({app}) {state}" + (f"，{summary}" if summary else ""))
                items.append(_credit_record(
                    name, app, ptype, True,
                    balance=_positive_num(res.get("credits")),
                    state=state,
                    summary=summary,
                    message="该平台无公开余额接口" if _positive_num(res.get("credits")) is None else "",
                ))
            except CookieError as e:
                fail += 1
                log(f"[积分] {name}({app}) 查询失败：{e}")
                items.append(_credit_record(name, app, ptype, False, message=str(e)))
            except Exception as e:  # noqa: BLE001
                msg = redact_secrets(str(e))
                fail += 1
                log(f"[积分] {name}({app}) 查询失败：{msg}")
                items.append(_credit_record(name, app, ptype, False, message=msg))
            continue
        if ptype != "trae":
            msg = "暂无公开积分/余额接口"
            log(f"[积分] {name}({app}) {msg}（WorkBuddy 需抓包签到，无法查询余额）")
            items.append(_credit_record(name, app, ptype, False, message=msg))
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
                msg = f"{redact_secrets(str(res.get('message')))}（HTTP {res.get('http')}）"
                log(f"[积分] {name}(trae) 查询失败：{msg}")
                items.append(_credit_record(name, app, ptype, False, message=msg))
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
            items.append(_credit_record(
                name, app, ptype, True,
                balance=_positive_num(res.get("remaining"), zero_ok=True),
                used=_num_or_none(res.get("total_used")),
                limit=_num_or_none(res.get("total_limit")),
                summary=f"总限额 {res.get('total_limit'):g}，已用 {res.get('total_used'):g}",
            ))
        except (TraeError, Exception) as e:  # noqa: BLE001
            msg = redact_secrets(str(e))
            fail += 1
            log(f"[积分] {name}(trae) 查询异常：{msg}")
            items.append(_credit_record(name, app, ptype, False, message=msg))

    if as_json:
        _emit_credits_json(items)
    return 0 if fail == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="多账号自动签到工具")
    parser.add_argument("--only", help="只处理指定账号 name")
    parser.add_argument("--dry-run", action="store_true", help="只预览不发送")
    parser.add_argument("--credits", action="store_true", help="查询账号积分/余额（不签到）")
    parser.add_argument("--json", action="store_true",
                        help="配合 --credits：额外在 stdout 输出一行机读 JSON 汇总")
    parser.add_argument("--growth", action="store_true", help="仅对 WorkBuddy 账号执行成长中心（不签到）")
    parser.add_argument("--force", action="store_true",
                        help="忽略本地签到台账，强制重新签到（默认今天已签到的账号会跳过）")
    args = parser.parse_args()
    if args.credits:
        sys.exit(run_credits(args.only, as_json=args.json))
    if args.growth:
        sys.exit(run_growth(args.only))
    sys.exit(run_checkin(args.only, args.dry_run, force=args.force))


if __name__ == "__main__":
    main()
