#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
凭据型平台浏览器登录模块（Bilibili / 京东 / 联想智选 / 什么值得买 / 阿里云盘 / 中国移动云盘）

原理（仿 trae_login.py）：
  用 Playwright 打开系统显示窗口的 Chromium，用户扫码 / 账密登录后，
  脚本自动从浏览器上下文捕获登录凭据：
    - Bilibili   -> SESSDATA（及配套键，如 bili_jct / DedeUserID）
    - 京东       -> pt_key + pt_pin（HttpOnly）
    - 联想智选   -> mclub.lenovo.com.cn 会话 Cookie
    - 什么值得买 -> smzdm.com 域下的会话 Cookie
    - 阿里云盘   -> **不是 Cookie**：登录态在 localStorage 的 token 项里，
                    所以改用「在页面里执行 JS 取值」的方式读 refresh_token
    - 中国移动云盘 -> 同样是「页面里执行 JS 取值」：从 Vuex 状态里一次取出
                    authorization 与手机号，拼成 `authorization#手机号`
                    （签到要的就是这两段，缺手机号换不到登录态）
  捕获后立即用对应平台客户端做只读验证，通过即关窗并把凭据写回
  accounts.json 对应账号的 <platform>_auth 字段；字段名跟着平台走
  （cookie / refresh_token / authorization，见 cookie_manager.CREDENTIAL_FIELD），
  账号不存在时自动创建（enabled 由 --enabled 决定，同名重登保留原启用状态）。

  个别平台（阿里云盘）会在验证时**轮换凭据**，此时验证函数会在返回值第 5 项
  带回新凭据，调用方用它替换后再落盘——否则存下来的是已经作废的旧令牌。

  登录态判定是三态（见 PLATFORMS 上方的注释）：ok / pending / unknown。
  只有 pending（平台明确说「还没登录」）才会一直等；unknown（平台只读接口
  改版 / 风控 / 网络异常，判不了）连续出现 UNKNOWN_ACCEPT_STREAK 次就按
  「已捕获关键凭据」保存，避免平台的接口变动把用户永远卡在等待里。

  账号名称可留空（--name 省略）：此时按只读验证返回的昵称自动命名
  （如 bilibili-小明），取不到昵称则回退账号 ID，再兜底 <platform>-auto。

用法：
    python3 browser_login.py --platform bilibili --name bili-main [--timeout 600] [--enabled 1]
    python3 browser_login.py --platform bilibili              # 名称留空：自动读取昵称命名
    python3 browser_login.py --platform smzdm                 # 什么值得买
    python3 browser_login.py --platform aliyunpan             # 阿里云盘（读 localStorage）
    python3 browser_login.py --platform caimcloud             # 中国移动云盘（读 Vuex 状态）

依赖：pip install playwright && python -m playwright install chromium

凭据安全：凭据等同账号密码，读取后仅内存使用；落盘仅 accounts.json
（已被 .gitignore 排除）；日志 / UI 一律 mask 脱敏，绝不回显明文。
"""

import argparse
import atexit
import datetime
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.json")
# 浏览器 profile 的**持久**目录：只在「重新登录已有账号」时使用。
# 新增账号一律用一次性临时目录（见 _profile_dir）—— 持久 profile 里存着
# 上次登录的 Cookie，复用会让页面直接处于已登录态，新账号永远加不进来。
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

import browser_deps  # noqa: E402  缺 playwright 时给出能照着做的提示
from cookie_manager import (  # noqa: E402
    credential_field, find_account, load_config, mask_cookie, set_cookie,
)

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


def _credential_noun(platform: str) -> str:
    """该平台凭据的中文名。阿里云盘存的是 refresh_token，不能叫 Cookie。"""
    return "刷新令牌" if platform == "aliyunpan" else "Cookie"


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


def _verify_smzdm(cookie: str) -> tuple:
    """验证什么值得买登录态（只读）。返回 (state, nickname, uid, message, credential)。

    第 5 项是"实际应当落盘的凭据"：只有在校验过程中凭据被平台轮换过时才用得上。
    什么值得买不轮换 cookie，所以恒等于入参。
    """
    from smzdm import SmzdmClient
    if not cookie:
        return "pending", "", "", "尚未捕获什么值得买 Cookie", cookie
    try:
        st = SmzdmClient(cookie)._web_status()
    except Exception as e:  # noqa: BLE001
        return "unknown", "", "", f"登录态验证异常：{type(e).__name__}", cookie
    if st.get("ok"):
        return "ok", st.get("nickname", ""), st.get("uid", ""), "", cookie
    # 网页只读接口认不出来：可能是 App 抓包来的 Cookie（这很正常），也可能是
    # 真没登录。两种情况的只读信号完全一样，所以按 unknown 交给上层宽限放行，
    # 绝不在这里武断地判"登录失败"。
    return "unknown", "", "", st.get("message") or "网页端未识别到登录态", cookie


def _verify_aliyunpan(credential: str) -> tuple:
    """验证阿里云盘登录态。返回 (state, nickname, uid, message, credential)。

    阿里云盘的 refresh_token 在刷新时**可能被轮换**，所以这里必须把平台新下发的
    那一个作为"应落盘凭据"返回（第 5 项）；否则写进账号文件的是一个已经作废的
    旧令牌，账号只能用一次。
    """
    from aliyunpan import AliyunpanClient
    cred = (credential or "").strip()
    if not cred:
        return "pending", "", "", "尚未捕获阿里云盘刷新令牌（请先在页面完成登录）", cred
    if len(cred) < 20:
        return "pending", "", "", "读到 token 字段但内容不像有效令牌，继续等待", cred
    try:
        client = AliyunpanClient(cred, autosave=False)   # 这里不写盘，由调用方保存
        st = client.status()
    except Exception as e:  # noqa: BLE001
        return "unknown", "", "", f"登录态验证异常：{type(e).__name__}", cred
    if st.get("ok"):
        return "ok", st.get("nickname", ""), st.get("uid", ""), "", client.refresh_token
    msg = st.get("message") or "阿里云盘登录态验证失败"
    if "失效" in msg:
        return "pending", "", "", msg, cred
    return "unknown", "", "", msg, cred


def _verify_caimcloud(credential: str) -> tuple:
    """移动云盘登录态校验：换 jwtToken 后读签到状态（只读，无副作用）。

    凭据是 `authorization#手机号` 两段。只换取到 jwtToken 就说明 authorization
    有效；签到状态读不出来（对方字段改版）不该挡住登录，交给主循环的宽容逻辑。
    账号名只取手机号后四位，不把整号写进账号名。
    """
    from caimcloud import CaimCloudClient
    if not credential:
        return "pending", "", "", "尚未捕获移动云盘授权码", credential
    try:
        client = CaimCloudClient(credential)
        st = client.status()
    except Exception as e:  # noqa: BLE001
        return "unknown", "", "", f"登录态验证异常：{type(e).__name__}", credential
    tail = "".join(ch for ch in client.account if ch.isdigit())[-4:]
    if st.get("ok"):
        return "ok", "", tail, "", credential
    msg = st.get("message") or "移动云盘登录态验证失败"
    if "失效" in msg or "重新" in msg or "手机号" in msg:
        return "pending", "", "", msg, credential
    return "unknown", "", "", msg, credential


# 移动云盘的登录态是两段拼起来的：`authorization`（在 Cookie 里）**加**手机号
# （SSO 的 account 参数）。手机号只在页面状态里 —— 桌面版挂在 window.MCloudVM、
# 移动版挂在 window.VUEObj，都从各自的 `$store.state.auth.account` 取；
# authorization 优先在页面里试（非 HttpOnly 时读得到），读不到由调用方从
# Playwright 的 Cookie 里补（见 collect_cookie 的 pair 规则）。
_CAIM_SCRIPT = r"""(() => {
  let acct = '';
  const pick = (a) => {
    if (a && !acct) acct = a.account || a.accountPhone || a.completeAccount || '';
  };
  try { pick(window.VUEObj && window.VUEObj.$store && window.VUEObj.$store.state && window.VUEObj.$store.state.auth); } catch (e) {}
  try { pick(window.MCloudVM && window.MCloudVM.$store && window.MCloudVM.$store.state && window.MCloudVM.$store.state.auth); } catch (e) {}
  let authz = '';
  try {
    const a1 = window.VUEObj && window.VUEObj.$store && window.VUEObj.$store.state && window.VUEObj.$store.state.auth;
    if (a1 && a1.authorization) authz = a1.authorization;
  } catch (e) {}
  if (!authz) {
    try {
      const m = document.cookie.match(/(?:^|;\s*)authorization=([^;]+)/);
      if (m) authz = decodeURIComponent(m[1]);
    } catch (e) {}
  }
  return (authz || acct) ? (authz + '#' + acct) : '';
})()"""


# 平台配置：type -> (中文标签, 初始打开地址列表, 关键 Cookie 键, 抓取规则, 验证函数)
#
# 抓取规则（第 4 项）支持三种写法：
#   {"keys": [...]}     按键名白名单取 Cookie（Bilibili / 京东）
#   {"domains": (...)}  取指定域下的全部 Cookie（联想智选 / 什么值得买）
#   {"script": "..."}   在页面里执行 JS 取值（阿里云盘：登录态在 localStorage，
#                       根本不在 Cookie 里，靠 Cookie 抓取永远拿不到）
#
# 验证函数返回 4 元组 (state, nickname, uid, message)；若凭据在校验过程中被平台
# 轮换，可在第 5 项返回"应当落盘的新凭据"，调用方会用它替换原值。
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
    "smzdm": (
        "什么值得买",
        ["https://www.smzdm.com/user/login/", "https://www.smzdm.com/"],
        (),
        {"domains": ("smzdm.com",)},
        _verify_smzdm,
    ),
    "aliyunpan": (
        "阿里云盘",
        ["https://www.aliyundrive.com/sign/in", "https://www.aliyundrive.com/"],
        (),
        # 登录成功后阿里云盘会把凭据写进 localStorage 的 token 项（JSON，
        # 含 refresh_token）。这里只取 refresh_token，其余一律不碰。
        {"script": "JSON.parse(localStorage.getItem('token')||'{}').refresh_token || ''"},
        _verify_aliyunpan,
    ),
    "caimcloud": (
        "中国移动云盘",
        # 桌面版首页就是完整登录页（手机号 + 密码 + 验证码都在这页），
        # 而 /w/#/login 是 404 页，别用。移动版作为备选。
        ["https://yun.139.com/", "https://yun.139.com/m/#/main"],
        (),
        # 手机号从页面状态取；authorization 优先在页面里读，
        # 读不到（HttpOnly）时由 pair 规则从 Playwright 的 Cookie 里补。
        {"script": _CAIM_SCRIPT, "pair": ("authorization", "#")},
        _verify_caimcloud,
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


def _collect_via_script(ctx, script: str) -> str:
    """在页面上下文里执行 JS 取值（用于读 localStorage 里的 refresh_token）。

    逐个标签页试着取 —— 用户很可能把登录页开在新标签里。任何异常都吞掉，
    取不到就返回空串，让主循环继续等，不能因为一次 evaluate 失败就中断登录。
    """
    try:
        pages = list(ctx.pages)
    except Exception:  # noqa: BLE001  浏览器已退出
        return ""
    # 后开的标签页更可能是"刚登录完成"的那一个
    for page in reversed(pages):
        try:
            value = page.evaluate(script)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _cookie_value(ctx, name: str) -> str:
    """按名字取单个 Cookie 的值。

    用 Playwright 的 cookies() 而不是 document.cookie：HttpOnly 的项页面里
    读不到，这里拿得到（值只在内存里过一遍，不会进日志）。
    """
    try:
        for c in ctx.cookies():
            if c.get("name") == name:
                return c.get("value", "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def collect_cookie(ctx, platform: str) -> str:
    """从浏览器上下文按平台规则采集凭据。

    - keys    ：按键名白名单取（拼成 k=v; k2=v2）
    - domains ：取该域下全部 Cookie
    - script  ：在页面里执行 JS 取值（凭据不在 Cookie 里的平台，如阿里云盘）
    - script + pair：脚本结果约定为「<Cookie 字段值><分隔符><页面里取到的值>」，
      前半段为空时用 Cookie 补。移动云盘的 authorization 在 Cookie（可能是
      HttpOnly）、手机号在页面状态，两段缺一不可，靠这条规则拼齐。
    """
    label, _urls, _need, scope, _verify = PLATFORMS[platform]
    if scope.get("script"):
        value = _collect_via_script(ctx, scope["script"])
        pair = scope.get("pair")
        if pair:
            key, sep = pair
            head, _, tail = value.partition(sep)
            if not head:
                head = _cookie_value(ctx, key)
            return f"{head}{sep}{tail}" if head else ""
        return value
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
    """把浏览器登录得到的凭据写回 accounts.json 指定账号。

    账号不存在时自动创建（enabled 按参数）；同名账号更新凭据时保留原启用状态；
    全部复用 cookie_manager 的原子读写。字段名跟着平台走：多数平台写 cookie，
    阿里云盘写 refresh_token。
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
            f"{platform}_auth": {credential_field(platform): ""},
        }
        config.setdefault("accounts", []).append(entry)
    return set_cookie(config, name, platform, cookie, extra, path=config_path)


def resolve_mode(mode: str, name: str) -> str:
    """把 mode 归一成 "add" / "relogin"：显式指定则原样返回，`auto` 按老规则推断
    （给了名字当"更新已有账号"，留空当"新增"）。

    单独抽出来是为了能直接单测 —— 这个判断决定了浏览器用哪个 profile，
    判错就会重现"新增账号读到旧账号登录态"的那个 bug。
    """
    if mode in ("add", "relogin"):
        return mode
    return "relogin" if name else "add"


def _profile_dir(platform: str, name: str, mode: str) -> tuple[str, bool]:
    """决定本次登录用哪个浏览器 profile，返回 (目录, 是否一次性)。

    这里踩过一个真实的坑：早先所有登录都按 `<平台>/<账号名 or _auto>` 持久化，
    于是**第二次新增同平台账号**时目录和上一次完全相同 —— profile 里躺着的
    还是上次那个账号的 Cookie，页面一打开就是已登录态，脚本第一轮轮询就抓到
    旧账号的凭据、判定成功、关窗、按旧昵称写盘。用户看到的是"登录完成，账号已保存"，
    实际上"新增"退化成"更新旧账号"，第二个账号永远加不上。

    所以按动作区分：
    - add（新增）：一次性临时目录。必须是**全新会话**，用户才可能登录另一个账号。
    - relogin（重新登录已有账号）：沿用该账号自己的持久目录，保留设备身份
      （少填一次验证码），语义上也确实是"更新这个账号"。
    """
    if mode == "add":
        return tempfile.mkdtemp(prefix=f"miaoeqian-{platform}-"), True
    os.makedirs(STATE_DIR, exist_ok=True)
    # relogin 一定带账号名（调用方传的是已有账号），留空只是 CLI 误用时的兜底
    path = os.path.join(STATE_DIR, platform, name or "_auto")
    os.makedirs(path, exist_ok=True)
    return path, False


def find_same_account(platform: str, name: str, uid: str,
                      config_path: str | None = None) -> tuple[str, str]:
    """在已有账号里找与本次登录**身份相同**的那个，返回 (账号名, 判据)。

    判据优先级：uid 相同 > 最终账号名相同；没找到返回 ("", "")。

    为什么需要这一步：写盘是按"最终名字"走的（`set_cookie` 按名找账号），
    如果用户这次登录的其实就是已有账号，凭据会被静默刷到那个账号身上，
    而界面提示"账号已保存" —— 用户以为加上了新账号，实际什么都没加。
    """
    try:
        config = load_config(config_path)
    except Exception:  # noqa: BLE001
        return "", ""
    uid = (uid or "").strip()
    for acc in config.get("accounts", []):
        if acc.get("type") != platform:
            continue
        acc_name = str(acc.get("name") or "")
        auth = acc.get(f"{platform}_auth") or {}
        if uid and str(auth.get("uid") or "").strip() == uid:
            return acc_name, "uid"
        if name and acc_name == name:
            return acc_name, "name"
    return "", ""


def run_browser_login(platform: str, name: str, timeout_seconds: int,
                      enabled: bool = True, mode: str = "auto") -> int:
    """打开内置浏览器完成一次登录，成功返回 0，重复账号返回 3，其余失败返回 1。

    mode：
      "add"     新增账号 —— 一次性干净会话；若这次登录的其实是已有账号则拒绝写盘
      "relogin" 更新已有账号的凭据 —— 沿用该账号自己的持久 profile
      "auto"    未指定时按老规则推断：给了名字当 relogin，留空当 add
    """
    if platform not in PLATFORMS:
        _log(f"未知平台：{platform}，可选：{', '.join(PLATFORMS)}")
        return 1
    label, login_urls, need_keys, _scope, verify = PLATFORMS[platform]

    name = clean_name(name)
    auto_name = not name
    mode = resolve_mode(mode, name)
    if auto_name:
        _log("未指定账号名称：登录成功后将自动读取该账号的真实昵称来命名。")

    user_data_dir, ephemeral = _profile_dir(platform, name, mode)
    if mode == "add":
        _log("本次新增使用全新浏览器会话（不读取任何历史登录态），可放心登录要新增的账号。")

    _log(f"正在打开内置浏览器（{label} / {name or '（自动命名）'}）…请在弹出窗口中扫码或账密登录。")
    _log("登录成功后本程序会自动识别并保存 Cookie，无需手动复制粘贴。")
    _log("提示：登录期间可自由切换标签页 / 填写验证码，脚本会持续监控。")
    _log("若页面未自动跳转登录页，可手动访问：")
    for u in login_urls:
        _log("    " + u)

    if ephemeral:
        # 一次性会话：无论怎么离开（成功 / 取消 / 异常 / 超时）都要删掉临时 profile。
        # 用 atexit 而不是 try/finally —— 下面有多处 return，包起来要整体重排缩进，
        # 而改缩进这种大动作最容易引入静默错误。
        atexit.register(shutil.rmtree, user_data_dir, True)

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

                res = verify(cookie)
                state, nickname, uid, msg = res[0], res[1], res[2], res[3]
                # 第 5 项（可选）= 校验过程中被平台轮换过的新凭据。
                # 阿里云盘刷新时可能换发 refresh_token，必须用新的那个去落盘，
                # 否则存下来的是已经作废的旧令牌。
                if len(res) > 4 and res[4]:
                    cookie = res[4]
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
                     "可改用面板里的「手动粘贴」渠道，或运行 "
                     f"python3 browser_login.py --platform {platform} 复现并把日志发我。")
                return 1

            _log(f"登录成功：已捕获 {label} {_credential_noun(platform)}"
                 f"（{mask_cookie(cookie)}）")
            # 显式关窗：持久化本次会话状态，下次登录无需重复扫码
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass
        # with 退出兜底：无论走哪条 return，浏览器都会被关掉
    except Exception as e:  # noqa: BLE001
        _log(f"内置浏览器异常：{e}")
        # 缺 playwright 时原始信息只有 "No module named 'playwright'"，
        # 用户看到的是"点了没反应、窗口不弹"，所以额外给一句照做就行的指引。
        for line in browser_deps.explain(e):
            _log(line)
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

    # 新增动作下，如果这次登录的其实是**本机已有的账号**，就不要静默写盘 ——
    # 否则界面会提示"账号已保存"，而用户要加的新账号一个也没加上
    # （凭据只是被刷到了旧账号身上）。只在能识别身份（uid 或昵称）时判定：
    # 两个都取不到时身份本来就无从区分，放行并提示，不把用户卡死在这。
    if mode == "add":
        if uid or nickname:
            dup, why = find_same_account(platform, final_name, uid)
            if dup:
                _log(f"这次登录的还是本机已有的账号「{dup}」"
                     f"（判据：{'账号 ID' if why == 'uid' else '账号名'}），没有新增账号。")
                _log("要新增另一个账号：请重新打开登录窗口，登录那个账号；"
                     "若它的昵称与已有账号相同，请在「账号名称」里填一个不同的名字"
                     "（同名会互相覆盖，无法共存）。")
                _log(f"若只是想更新「{dup}」的凭据：请用它右侧的「重新登录」入口。")
                return 3
        else:
            _log("本次未能识别登录账号的身份（平台只读接口没给出昵称/ID），"
                 "无法判断是否与已有账号重复；若列表里没出现新账号，请改用「重新登录」更新它。")

    if not save_cookie_account(final_name, platform, cookie, extra, enabled=enabled):
        _log("Cookie 写回 accounts.json 失败（请检查账号文件权限）")
        return 1
    _log(f"已保存账号「{final_name}」到 accounts.json（昵称 {nickname or '(未取到)'}，uid {uid or '(未取到)'}）")
    _log(f"下一步：运行  python3 checkin.py --only {final_name}  验证签到。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="凭据型平台浏览器登录（Bilibili/京东/联想智选/什么值得买/阿里云盘）")
    parser.add_argument("--platform", required=True, choices=list(PLATFORMS.keys()),
                        help="平台：" + " / ".join(PLATFORMS.keys()))
    parser.add_argument("--name", default="",
                        help="账号名称（唯一），如 bili-main；留空时自动读取该账号真实昵称命名")
    parser.add_argument("--timeout", type=int, default=600, help="等待登录超时秒数，默认 600")
    parser.add_argument("--enabled", type=int, default=1, choices=[0, 1],
                        help="新增账号是否加入自动签到（1=开启，0=关闭；仅新建账号时生效）")
    parser.add_argument("--mode", default="auto", choices=["auto", "add", "relogin"],
                        help="add=新增账号（全新浏览器会话；若登录的是已有账号则拒绝并返回 3）／"
                             "relogin=更新已有账号凭据（沿用该账号自己的 profile）；"
                             "默认 auto：给了 --name 当 relogin，留空当 add")
    args = parser.parse_args()
    return run_browser_login(args.platform, args.name, args.timeout,
                             enabled=bool(args.enabled), mode=args.mode)


if __name__ == "__main__":
    sys.exit(main())
