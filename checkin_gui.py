#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动签到助手 - macOS 图形界面（Tkinter/ttk 设计稿还原版）

界面按用户提供的设计稿重构（侧边栏/顶栏/三区块），后端与既有能力全部保留：
  - 左侧深黑导航栏 #0D0D0D（Logo AutoCheck + 01 签到 / 02 账号管理 / 03 设置 / 04 使用说明）
  - 顶部应用条（应用名 + 状态「自动签到运行中 · 下次 HH:MM」+ 手动签到）
  - 主区三区块：今日签到概览（深色卡片 + 积分状态卡片含近7天柱状图 tk.Canvas 自绘）
               账号签到任务表格（平台账号|最近签到|今日积分|状态|操作 + 筛选）
               自动签到开关（launchd 联动）+ 签到结果与异常提示卡片
  - 配色：主色 #0066FF / 成功 #34C759 / 失败 #FF3B30 / 侧边栏 #0D0D0D / 主区 #FFFFFF
          / 次文本 #666666 / 边框 #E0E0E0 / 8px 圆角（Tk 原生以近似圆角实现）

不可回退能力保持：Trae 内置浏览器登录（登录后列表稳定刷新 + _settle_refresh 兜底）、
签到错误完整展示（code+message，token/session/uid 经 redact_secrets 脱敏）、
积分状态查询（trae_api.credits / --credits）、cURL 添加账号、每日定时 launchd、
使用指南。既有的单事件队列 + 子进程回显结构沿用，仅重排版面与新增交互。

退出：本文件仅作图形界面入口，不承担签到/登录逻辑（业务都在 checkin.py /
trae_login.py / trae_api.py）。
"""

from __future__ import annotations

import datetime
import os
import queue
import re
import subprocess
import sys
import threading
import traceback

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

SELFTEST = os.environ.get("MARVIS_SELFTEST") == "1"
RUNNER = sys.executable or "/usr/bin/python3"

LAUNCHD_LABEL = "com.marvis.autocheckin"
LAUNCHD_PLIST = os.path.join(
    os.path.expanduser("~"), "Library", "LaunchAgents", LAUNCHD_LABEL + ".plist"
)
LOG_FILE = os.path.join(BASE_DIR, "logs", "checkin.log")

try:
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import messagebox, ttk

    HAVE_TK = True
except Exception:  # noqa: BLE001
    HAVE_TK = False

from checkin import redact_secrets  # noqa: E402
from curl_to_account import add_account, load_config, parse_curl, save_config  # noqa: E402
from trae_api import TraeClient  # noqa: E402

# ---------------- 设计稿配色 ----------------
COL_PRIMARY = "#0066FF"
COL_SUCCESS = "#34C759"
COL_ERROR = "#FF3B30"
COL_SIDEBAR = "#0D0D0D"
COL_SIDEBAR_CARD = "#191919"
COL_SIDEBAR_MUTED = "#8A8A8A"
COL_MAIN = "#FFFFFF"
COL_TEXT = "#000000"
COL_MUTED = "#666666"
COL_BORDER = "#E0E0E0"
COL_ROW = "#F5F5F5"
COL_BTN_DARK = "#111111"
COL_INACTIVE = "#9A9A9A"
COL_RED_BG = "#FEF1F0"
COL_RED_BORDER = "#FFD4D0"
COL_GREEN_BG = "#F0FAF4"
COL_GREEN_BORDER = "#CDEBCF"
COL_TRACK = "#E8ECEF"

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def _fmt_num(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "0"
    iv = int(f) if f == int(f) else f
    return f"{iv:,}"


def _now_str() -> str:
    dt = datetime.datetime.now()
    return (
        f"{dt.year}-{dt.month:02d}-{dt.day:02d} "
        f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"
    )


def parse_logs(log_file: str = LOG_FILE):
    """解析签到日志 -> {date: {"ok": {name: {time,credits,msg}}, "fail": {name: {time,msg}}}}"""
    out = {}
    if not os.path.exists(log_file):
        return out
    pat = re.compile(
        r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\] \[(OK|FAIL)\] (\S+)\((\w+)\)\s*->\s*(.*)$"
    )
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pat.match(line.strip())
                if not m:
                    continue
                date, t, okfail, name, app, msg = m.groups()
                day = out.setdefault(date, {"ok": {}, "fail": {}})
                cm = re.search(r"本次获得\s*([\d.]+)\s*积分", msg)
                credits = float(cm.group(1)) if cm else 0.0
                if okfail == "OK":
                    day["ok"][name] = {"time": t, "credits": credits, "msg": msg}
                else:
                    day["fail"][name] = {"time": t, "msg": msg}
    except Exception:  # noqa: BLE001
        pass
    return out


def plist_body(hour: int, minute: int, cmd: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-l</string>
        <string>-c</string>
        <string>{cmd}</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{hour}</integer>
        <key>Minute</key>
        <integer>{minute}</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{os.path.join(BASE_DIR, "logs", "launchd.log")}</string>
    <key>StandardErrorPath</key>
    <string>{os.path.join(BASE_DIR, "logs", "launchd.err.log")}</string>
</dict>
</plist>
"""


HELP_TEXT = """自动签到助手 · 使用指南

一、Trae 免抓包登录（推荐）
  「账号管理 → Trae 内置浏览器登录」→ 填账号名 → 点「打开登录浏览器」。
  在弹出的浏览器窗口里登录 Trae，程序会自动捕获登录态并保存。
  登录态约 14 天有效；期间每天自动签到会自动换新 Token，全程无需抓包。
  （首次使用需安装 Playwright 浏览器内核，见 README。）

二、抓包添加账号（WorkBuddy 等）
  1. 用 Proxyman / Charles 开启 HTTPS 解密并信任证书、开启系统代理。
  2. 在桌面客户端触发一次签到（WorkBuddy：左下角头像 → 领取今日礼包）。
  3. 找到 POST 请求（URL 常含 checkin/signin/daily 等）→ Copy as cURL。
  4. 在「账号管理 → 添加账号」粘贴 cURL，填账号名和软件名 → 保存。

三、签到
  「01 签到」页点顶栏「手动签到」或表格行内「签到 / 重试」按钮，
  实时查看每条请求结果与完整错误原因（含错误码 code + 可读 message）。
  签到结果会自动汇总到「签到结果与异常提示」卡片。

四、积分状态
  「01 签到」→「积分状态」卡片展示 Trae 账号累计积分总额、可用剩余、
  本周增量与近 7 天积分柱状图（数据来自签到日志与积分接口）。
  可在「03 设置」→「签到选项」里使用「仅签到账号」过滤。

五、每日自动签到
  「01 签到」→「自动签到」开关直接联动 macOS launchd 安装 / 卸载；
  「03 设置 → 每日定时」可精确选择每天执行的时间。支持随时卸载。

六、注意事项
  - 登录态过期请重新「Trae 内置浏览器登录」刷新。
  - accounts.json 内含登录凭据，请勿分享、勿提交代码仓库。
  - 自动签到属平台规则边缘行为，存在风控/封禁可能，请自行评估频率。
"""


# ---------------------------------------------------------------- 自定义控件
class Switch(tk.Canvas):
    """Toggle 开关（近似圆角轨道 + 滑块）"""

    def __init__(self, master, command=None, width=48, height=26, bg="#FFFFFF", **kw):
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, **kw)
        self._on = False
        self._cmd = command
        self.bind("<Button-1>", self._click)
        self._render()

    def _render(self):
        self.delete("all")
        w = int(self["width"])
        h = int(self["height"])
        r = h - 6
        track = COL_SUCCESS if self._on else COL_BORDER
        self.create_rectangle(2, (h - (h - 6)) // 2, w - 2, h - (h - 6) // 2,
                              fill=track, outline="")
        x = (w - r - 4) if self._on else 4
        self.create_rectangle(x + (h - 8) // 2, 4, x + (h - 8) // 2 + h - 8, h - 4,
                              fill="#FFFFFF", outline="")
        self.create_oval(x, 4, x + h - 8, h - 4, fill="#FFFFFF", outline="")

    def set(self, on: bool):
        on = bool(on)
        if on != self._on:
            self._on = on
            self._render()

    def get(self) -> bool:
        return self._on

    def _click(self, _e):
        self._on = not self._on
        self._render()
        if self._cmd:
            self._cmd(self._on)


class ProgressBar(tk.Canvas):
    """圆角进度条（绿色填充）"""

    def __init__(self, master, height=10, bg="#FFFFFF", **kw):
        super().__init__(master, height=height, bg=bg, highlightthickness=0, bd=0, **kw)
        self._ratio = 0.0
        self.bind("<Configure>", lambda e: self._render())

    def set_ratio(self, r):
        self._ratio = max(0.0, min(1.0, r))
        self._render()

    def _render(self):
        self.delete("all")
        w = max(self.winfo_width(), 12)
        h = int(self["height"])
        self.create_rectangle(1, 1, w - 1, h - 1, fill=COL_TRACK, outline="")
        fw = int((w - 2) * self._ratio)
        if fw > 2:
            self.create_rectangle(1, 1, fw + 1, h - 1, fill=COL_SUCCESS, outline="")


# ======================================================================
class AutoCheckinApp:
    def __init__(self, root: "tk.Tk"):
        self.root = root
        self.root.title("AutoCheck")
        self.root.geometry("1180x760")
        self.root.minsize(1000, 660)
        self.root.configure(bg=COL_MAIN)

        # 单一事件队列：全程复用，绝不在并发操作中重建（修复列表刷新竞态）
        self._q: "queue.Queue" = queue.Queue()
        self._signing = False
        self._login_running = False
        self._cred_busy = False

        # 展示状态
        self._logs = {}
        self._credit_data = {}          # name -> {total_limit, remaining, used}
        self._last_results = []         # [{name, ok, credits, reason}]
        self._run_lines = []            # 最近运行日志（查看详情）
        self._filter = "all"            # all / done / pending
        self._chart_days = 7            # 近7天 / 近30天
        self._accounts = []

        self._setup_fonts()
        self._setup_style()
        self._build_layout()
        self._build_pages()
        self._nav_select("sign")

        self._refresh_all(load_credits=True)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(120, self._poll)
        if SELFTEST:
            self.root.after(2600, self._on_close)

    # ---------------- 外观 ----------------
    def _setup_fonts(self):
        pf = "PingFang SC"
        self.f_title = tkfont.Font(family=pf, size=20, weight="bold")
        self.f_h2 = tkfont.Font(family=pf, size=16, weight="bold")
        self.f_h3 = tkfont.Font(family=pf, size=15, weight="bold")
        self.f_body = tkfont.Font(family=pf, size=13)
        self.f_body_b = tkfont.Font(family=pf, size=13, weight="bold")
        self.f_small = tkfont.Font(family=pf, size=11)
        self.f_tiny = tkfont.Font(family=pf, size=10)
        self.f_nav = tkfont.Font(family=pf, size=13)
        self.f_badge = tkfont.Font(family=pf, size=10, weight="bold")
        self.f_big = tkfont.Font(family=pf, size=30, weight="bold")
        self.f_big2 = tkfont.Font(family=pf, size=24, weight="bold")
        self.f_mono = tkfont.Font(family="Menlo", size=11)

    def _setup_style(self):
        self.style = ttk.Style(self.root)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        S = self.style
        S.configure(".", font=self.f_body)
        S.configure("TFrame", background=COL_MAIN)
        S.configure("TLabel", background=COL_MAIN, foreground=COL_TEXT)

        # 通用按钮
        S.configure("TButton", background="#FFFFFF", foreground=COL_TEXT,
                    bordercolor=COL_BORDER, lightcolor=COL_BORDER, darkcolor=COL_BORDER,
                    focuscolor="#FFFFFF", relief="flat", borderwidth=1,
                    padding=(12, 6), font=self.f_small)
        S.map("TButton", background=[("active", "#F2F4F7"), ("pressed", "#E8ECF1")])

        S.configure("Accent.TButton", background=COL_PRIMARY, foreground="#FFFFFF",
                    bordercolor=COL_PRIMARY, padding=(14, 7), font=self.f_body)
        S.map("Accent.TButton",
              background=[("active", "#0055D4"), ("pressed", "#0055D4"),
                          ("disabled", "#B9D1F5")],
              foreground=[("disabled", "#FFFFFF")])

        S.configure("Dark.TButton", background=COL_BTN_DARK, foreground="#FFFFFF",
                    bordercolor=COL_BTN_DARK, padding=(14, 7), font=self.f_body)
        S.map("Dark.TButton",
              background=[("active", "#2A2A2A"), ("pressed", "#000000"),
                          ("disabled", "#555555")],
              foreground=[("disabled", "#BBBBBB")])

        S.configure("Danger.TButton", background="#FFFFFF", foreground=COL_ERROR,
                    bordercolor="#FFD4D0", padding=(10, 5), font=self.f_small)
        S.map("Danger.TButton", background=[("active", "#FFF1F0"), ("pressed", "#FFE3E0")])

        S.configure("Ghost.TButton", background="#FFFFFF", foreground=COL_MUTED,
                    bordercolor=COL_BORDER, padding=(10, 5), font=self.f_small)
        S.map("Ghost.TButton", background=[("active", "#F5F7FA")])

        # 输入
        S.configure("TEntry", fieldbackground="#FFFFFF", foreground=COL_TEXT,
                    bordercolor=COL_BORDER, lightcolor=COL_BORDER, darkcolor=COL_BORDER,
                    padding=6)
        S.configure("TCombobox", fieldbackground="#FFFFFF", foreground=COL_TEXT)
        S.configure("TCheckbutton", background=COL_MAIN, foreground=COL_TEXT)

        # 表格（账号管理页）
        S.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF",
                    foreground=COL_TEXT, rowheight=32, borderwidth=0, font=self.f_body)
        S.configure("Treeview.Heading", background="#F7F9FB", foreground=COL_MUTED,
                    font=self.f_small, borderwidth=0, padding=(8, 8), relief="flat")
        S.map("Treeview", background=[("selected", "#E5F0FF")],
              foreground=[("selected", COL_TEXT)])

        # 滚动条
        S.configure("Vertical.TScrollbar", background="#DDDDDD", troughcolor="#FFFFFF",
                    borderwidth=0, arrowcolor="#FFFFFF", relief="flat")

    # ---------------- 布局 ----------------
    def _build_layout(self):
        self.shell = tk.Frame(self.root, bg=COL_MAIN)
        self.shell.pack(fill="both", expand=True)

        # 左侧深黑导航栏
        self.sidebar = tk.Frame(self.shell, bg=COL_SIDEBAR, width=216)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self._build_sidebar()

        # 右侧
        self.right = tk.Frame(self.shell, bg=COL_MAIN)
        self.right.pack(side="left", fill="both", expand=True)
        self._build_header()

        self.content = tk.Frame(self.right, bg=COL_MAIN)
        self.content.pack(fill="both", expand=True)

    def _build_sidebar(self):
        # Logo
        brand = tk.Frame(self.sidebar, bg=COL_SIDEBAR)
        brand.pack(fill="x", padx=20, pady=(26, 18))
        logo = tk.Canvas(brand, width=26, height=16, bg=COL_SIDEBAR,
                         highlightthickness=0, bd=0)
        logo.pack(side="left")
        logo.create_oval(2, 2, 11, 11, fill=COL_PRIMARY, outline="")
        logo.create_oval(11, 2, 20, 11, fill=COL_SUCCESS, outline="")
        logo.create_oval(6, 8, 15, 17, fill=COL_ERROR, outline="")
        tk.Label(brand, text="AutoCheck", bg=COL_SIDEBAR, fg="#FFFFFF",
                 font=tkfont.Font(family="PingFang SC", size=16, weight="bold")).pack(side="left", padx=(8, 0))

        # 导航菜单
        self.nav_btns = {}
        nav_items = [
            ("sign", "01", "签到"),
            ("account", "02", "账号管理"),
            ("settings", "03", "设置"),
            ("help", "04", "使用说明"),
        ]
        nav_wrap = tk.Frame(self.sidebar, bg=COL_SIDEBAR)
        nav_wrap.pack(fill="x", pady=(0, 18))
        for key, num, label in nav_items:
            b = tk.Frame(nav_wrap, bg=COL_SIDEBAR, cursor="hand2")
            b.pack(fill="x", padx=12, pady=2)
            num_l = tk.Label(b, text=num, bg=COL_SIDEBAR, fg=COL_SIDEBAR_MUTED,
                             font=self.f_tiny, width=3, anchor="w")
            num_l.pack(side="left", padx=(8, 6))
            txt = tk.Label(b, text=label, bg=COL_SIDEBAR, fg="#C6C6C6",
                           font=self.f_nav, anchor="w", padx=12, pady=10)
            txt.pack(side="left", fill="x", expand=True)
            b.bind("<Button-1>", lambda e, k=key: self._nav_select(k))
            txt.bind("<Button-1>", lambda e, k=key: self._nav_select(k))
            num_l.bind("<Button-1>", lambda e, k=key: self._nav_select(k))
            self.nav_btns[key] = (b, num_l, txt)

        # 今日进度卡片
        prog_card = tk.Frame(self.sidebar, bg=COL_SIDEBAR_CARD)
        prog_card.pack(fill="x", padx=14, pady=(0, 4))
        tk.Label(prog_card, text="今日进度", bg=COL_SIDEBAR_CARD, fg="#EDEDED",
                 font=self.f_body_b).pack(anchor="w", padx=14, pady=(12, 8))
        self.side_progress = ProgressBar(prog_card, height=8, bg=COL_SIDEBAR_CARD)
        self.side_progress.pack(fill="x", padx=14)
        self.side_progress_tip = tk.Label(prog_card, text="", bg=COL_SIDEBAR_CARD,
                                          fg=COL_SIDEBAR_MUTED, font=self.f_tiny,
                                          anchor="w")
        self.side_progress_tip.pack(anchor="w", padx=14, pady=(8, 12))

        # 底部本地账户 + 版本
        local = tk.Frame(self.sidebar, bg=COL_SIDEBAR_CARD)
        local.pack(fill="x", side="bottom", padx=14, pady=(0, 14))
        av = tk.Canvas(local, width=30, height=30, bg=COL_SIDEBAR_CARD,
                       highlightthickness=0, bd=0)
        av.pack(side="left", padx=12, pady=12)
        av.create_oval(2, 2, 28, 28, fill="#2A2A2A", outline="")
        av.create_oval(8, 7, 22, 21, fill="#555555", outline="")
        av.create_arc(8, 14, 22, 30, start=0, extent=180, fill="#555555", outline="")
        lf = tk.Frame(local, bg=COL_SIDEBAR_CARD)
        lf.pack(side="left", fill="x", expand=True, pady=12, padx=(0, 8))
        tk.Label(lf, text="本地账户", bg=COL_SIDEBAR_CARD, fg="#FFFFFF",
                 font=self.f_small).pack(anchor="w")
        tk.Label(lf, text="免费版 · v2.4.1", bg=COL_SIDEBAR_CARD, fg=COL_SIDEBAR_MUTED,
                 font=self.f_tiny).pack(anchor="w", pady=(2, 0))

    def _build_header(self):
        bar = tk.Frame(self.right, bg=COL_MAIN, height=56)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        bar.configure(highlightthickness=1, highlightbackground=COL_BORDER)
        tk.Label(bar, text="AutoCheck", bg=COL_MAIN, fg=COL_TEXT,
                 font=self.f_h2).pack(side="left", padx=(24, 0))

        right = tk.Frame(bar, bg=COL_MAIN)
        right.pack(side="right", padx=16)
        self.header_dot = tk.Canvas(right, width=10, height=10, bg=COL_MAIN,
                                    highlightthickness=0, bd=0)
        self.header_dot.pack(side="left", padx=(0, 6))
        self.header_status = tk.Label(right, text="就绪", bg=COL_MAIN, fg=COL_MUTED,
                                      font=self.f_small)
        self.header_status.pack(side="left", padx=(0, 14))
        ttk.Button(right, text="刷新数据", style="Ghost.TButton",
                   command=lambda: self._refresh_all(load_credits=True)).pack(side="left", padx=(0, 10))
        ttk.Button(right, text="⚡ 手动签到", style="Dark.TButton",
                   command=lambda: self._begin_sign(dry=False)).pack(side="left")

    def _nav_select(self, key: str):
        for k, (b, num_l, txt) in self.nav_btns.items():
            active = (k == key)
            b.configure(bg="#1C1C1C" if active else COL_SIDEBAR)
            num_l.configure(bg="#1C1C1C" if active else COL_SIDEBAR,
                            fg=COL_PRIMARY if active else COL_SIDEBAR_MUTED)
            txt.configure(bg="#1C1C1C" if active else COL_SIDEBAR,
                          fg="#FFFFFF" if active else "#C6C6C6")
        for k, page in getattr(self, "_pages", {}).items():
            if k == key:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()

    # ---------------- 页面骨架 ----------------
    def _build_pages(self):
        self._pages = {}
        for key, build in (("sign", self._page_sign), ("account", self._page_account),
                           ("settings", self._page_settings), ("help", self._page_help)):
            pg = tk.Frame(self.content, bg=COL_MAIN)
            build(pg)
            self._pages[key] = pg

    def _make_scroll(self, parent):
        """可滚动区域（Canvas + 内层 Frame）"""
        canvas = tk.Canvas(parent, bg=COL_MAIN, highlightthickness=0, bd=0)
        vs = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vs.set)
        inner = tk.Frame(canvas, bg=COL_MAIN)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _update_scrollregion(_e):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _update_width(_e):
            canvas.itemconfigure(win, width=_e.width)

        inner.bind("<Configure>", _update_scrollregion)
        canvas.bind("<Configure>", _update_width)

        def _wheel(e):
            if not e.delta:
                return
            canvas.yview_scroll(int(-e.delta / 120), "units")
            return "break"

        def _on_enter(_e):
            canvas.bind_all("<MouseWheel>", _wheel)

        def _on_leave(_e):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _on_enter)
        canvas.bind("<Leave>", _on_leave)
        return canvas, inner, vs

    @staticmethod
    def _page_header(parent, title, sub):
        tk.Label(parent, text=title, bg=COL_MAIN, fg=COL_TEXT,
                 font=tkfont.Font(family="PingFang SC", size=20, weight="bold")).pack(
            anchor="w", padx=24, pady=(20, 0))
        tk.Label(parent, text=sub, bg=COL_MAIN, fg=COL_MUTED,
                 font=tkfont.Font(family="PingFang SC", size=12)).pack(anchor="w", padx=24, pady=(3, 10))

    def _card(self, parent, dark=False, pad=18):
        """白色/深色卡片容器，外部浅灰底模拟轻阴影 + 1px 边框近似圆角"""
        outer_bg = "#F4F4F4" if not dark else COL_SIDEBAR
        border = COL_BORDER if not dark else "#242424"
        outer = tk.Frame(parent, bg=outer_bg)
        outer.configure(highlightthickness=1, highlightbackground=border,
                        highlightcolor=border)
        inner = tk.Frame(outer, bg=COL_MAIN if not dark else "#101010")
        inner.pack(fill="both", expand=True, padx=pad, pady=pad)
        return outer, inner

    # ----- 页面：01 签到 -----
    def _page_sign(self, parent):
        canvas, self.sign_inner, vs = self._make_scroll(parent)
        canvas.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")

        # 区块1：今日签到概览（两个卡片并排）
        row1 = tk.Frame(self.sign_inner, bg=COL_MAIN)
        row1.pack(fill="x", padx=24, pady=(4, 16))

        # 卡片A：今日签到（深色）
        cardA, A = self._card(row1, dark=True, pad=16)
        cardA.pack(side="left", fill="both", expand=True, padx=(0, 8))
        head = tk.Frame(A, bg="#101010")
        head.pack(fill="x")
        tk.Label(head, text="今日签到", bg="#101010", fg="#FFFFFF",
                 font=self.f_h2).pack(side="left")
        self.auto_tag = tk.Frame(head, bg="#1D2A1D")
        self.auto_tag.pack(side="right")
        self.auto_tag_dot = tk.Canvas(self.auto_tag, width=8, height=8, bg="#1D2A1D",
                                      highlightthickness=0, bd=0)
        self.auto_tag_dot.pack(side="left", padx=(8, 4), pady=4)
        self.auto_tag_text = tk.Label(self.auto_tag, text="自动签到已开启", bg="#1D2A1D",
                                      fg=COL_SUCCESS, font=self.f_tiny)
        self.auto_tag_text.pack(side="left", padx=(0, 8), pady=4)

        self.overview_sub = tk.Label(A, text="", bg="#101010", fg="#9A9A9A",
                                     font=self.f_small, anchor="w")
        self.overview_sub.pack(fill="x", pady=(6, 10))
        mid = tk.Frame(A, bg="#101010")
        mid.pack(fill="x")
        self.overview_big = tk.Label(mid, text="0 / 0", bg="#101010", fg="#FFFFFF",
                                     font=self.f_big)
        self.overview_big.pack(side="left")
        self.overview_note = tk.Label(mid, text="个账号今日已完成签到", bg="#101010",
                                      fg="#9A9A9A", font=self.f_small)
        self.overview_note.pack(side="left", padx=(10, 0), pady=(10, 0))
        self.overview_progress = ProgressBar(A, height=10, bg="#101010")
        self.overview_progress.pack(fill="x", pady=(8, 12))

        stats = tk.Frame(A, bg="#101010")
        stats.pack(fill="x")
        self.ov_stat = {}   # ('credit','total','streak')
        labels = [("credit", "+0"), ("total", "0"), ("streak", "0天")]
        titles = [("credit", "今日获积分"), ("total", "累计积分"), ("streak", "连续签到")]
        for i, (key, _) in enumerate(labels):
            col = tk.Frame(stats, bg="#101010")
            col.pack(side="left", expand=True, fill="x", padx=(0, 6))
            lb = tk.Label(col, text="0", bg="#101010", fg="#FFFFFF",
                          font=self.f_h3, anchor="w")
            lb.pack(anchor="w")
            tk.Label(col, text=titles[i][1], bg="#101010", fg="#9A9A9A",
                     font=self.f_tiny, anchor="w").pack(anchor="w", pady=(2, 0))
            self.ov_stat[key] = lb

        # 卡片B：积分状态（浅色）
        cardB, B = self._card(row1, dark=False, pad=16)
        cardB.pack(side="left", fill="both", expand=True, padx=(8, 0))
        headB = tk.Frame(B, bg=COL_MAIN)
        headB.pack(fill="x")
        tk.Label(headB, text="积分状态", bg=COL_MAIN, fg=COL_TEXT,
                 font=self.f_h2).pack(side="left")
        self.chart_btn = {}
        for days, label in ((7, "近7天"), (30, "近30天")):
            b = tk.Label(headB, text=label, bg="#F2F5F9" if days == 7 else COL_MAIN,
                         fg=COL_PRIMARY if days == 7 else COL_MUTED, font=self.f_tiny,
                         padx=8, pady=3, cursor="hand2")
            b.pack(side="right", padx=(4, 0))
            b.bind("<Button-1>", lambda e, d=days: self._set_chart_days(d))
            self.chart_btn[days] = b
        self.credit_big = tk.Label(B, text="0", bg=COL_MAIN, fg=COL_TEXT,
                                   font=self.f_big)
        self.credit_big.pack(anchor="w", pady=(10, 0))
        crow = tk.Frame(B, bg=COL_MAIN)
        crow.pack(fill="x")
        tk.Label(crow, text="累计积分总额", bg=COL_MAIN, fg=COL_MUTED,
                 font=self.f_small).pack(side="left")
        self.week_tag = tk.Label(crow, text="本周 +0", bg="#EAF6EE", fg=COL_SUCCESS,
                                 font=self.f_tiny, padx=8, pady=2)
        self.week_tag.pack(side="left", padx=(10, 0))
        self.credit_extra = tk.Label(B, text="可用剩余 0 · 已用 0", bg=COL_MAIN,
                                     fg=COL_MUTED, font=self.f_small)
        self.credit_extra.pack(anchor="w", pady=(3, 6))
        self.chart_canvas = tk.Canvas(B, height=130, bg=COL_MAIN, highlightthickness=0, bd=0)
        self.chart_canvas.pack(fill="x")
        self.chart_hint = tk.Label(B, text="", bg=COL_MAIN, fg=COL_SIDEBAR_MUTED,
                                   font=self.f_tiny, anchor="w")
        self.chart_hint.pack(fill="x", pady=(2, 0))

        # 区块2：账号签到任务
        self._build_task_block(self.sign_inner)

        # 区块3：自动签到 + 异常提示（两卡片）
        row3 = tk.Frame(self.sign_inner, bg=COL_MAIN)
        row3.pack(fill="x", padx=24, pady=(0, 24))
        self._build_auto_card(row3)
        self._build_result_card(row3)

    def _build_task_block(self, parent):
        outer, inner = self._card(parent, dark=False, pad=18)
        outer.pack(fill="x", padx=24, pady=(0, 16))
        head = tk.Frame(inner, bg=COL_MAIN)
        head.pack(fill="x")
        tk.Label(head, text="账号签到任务", bg=COL_MAIN, fg=COL_TEXT,
                 font=self.f_h2).pack(side="left")
        self.task_count_tag = tk.Label(head, text="0个账号", bg="#F2F5F9", fg=COL_MUTED,
                                       font=self.f_tiny, padx=8, pady=2)
        self.task_count_tag.pack(side="left", padx=(10, 0))
        # 筛选
        fw = tk.Frame(head, bg=COL_MAIN)
        fw.pack(side="right")
        self.filter_btns = {}
        for key, label_of in (("all", "全部"), ("done", "已完成"), ("pending", "待处理")):
            b = tk.Label(fw, text=label_of, bg="#F2F5F9", fg=COL_MUTED,
                         font=self.f_tiny, padx=10, pady=3, cursor="hand2")
            b.pack(side="left", padx=(4, 0))
            b.bind("<Button-1>", lambda e, k=key: self._set_filter(k))
            self.filter_btns[key] = (b, "0")

        # 表头
        hdr = tk.Frame(inner, bg="#F7F9FB")
        hdr.pack(fill="x", pady=(12, 4))
        cols = [("acc", "平台账号", 0.34), ("recent", "最近签到", 0.18),
                ("credit", "今日积分", 0.14), ("status", "状态", 0.16), ("op", "操作", 0.18)]
        self.task_cols = cols
        for key, title, _w in cols:
            tk.Label(hdr, text=title, bg="#F7F9FB", fg=COL_MUTED, font=self.f_small,
                     anchor="w").pack(side="left", fill="x", expand=True, padx=10)

        self.task_rows = tk.Frame(inner, bg=COL_MAIN)
        self.task_rows.pack(fill="x")
        self.task_empty = tk.Label(inner, text="", bg=COL_MAIN, fg=COL_SIDEBAR_MUTED,
                                   font=self.f_small, anchor="center")
        self.task_empty.pack(fill="x", pady=18)
        self.task_empty.pack_forget()

    def _build_auto_card(self, parent):
        outer, inner = self._card(parent, dark=False, pad=16)
        outer.pack(side="left", fill="both", expand=True, padx=(0, 8))
        head = tk.Frame(inner, bg=COL_MAIN)
        head.pack(fill="x")
        tk.Label(head, text="自动签到", bg=COL_MAIN, fg=COL_TEXT,
                 font=self.f_h2).pack(side="left")
        self.auto_switch = Switch(head, command=self._on_auto_toggle)
        self.auto_switch.pack(side="right")
        self.auto_desc = tk.Label(inner, text="每天 21:30 自动执行", bg=COL_MAIN,
                                  fg=COL_TEXT, font=self.f_body, anchor="w")
        self.auto_desc.pack(fill="x", pady=(12, 2))
        self.auto_note = tk.Label(inner, text="", bg=COL_MAIN, fg=COL_MUTED,
                                  font=self.f_small, anchor="w", justify="left",
                                  wraplength=340)
        self.auto_note.pack(fill="x")

    def _build_result_card(self, parent):
        outer, inner = self._card(parent, dark=False, pad=16)
        outer.pack(side="left", fill="both", expand=True, padx=(8, 0))
        head = tk.Frame(inner, bg=COL_MAIN)
        head.pack(fill="x")
        tk.Label(head, text="签到结果与异常提示", bg=COL_MAIN, fg=COL_TEXT,
                 font=self.f_h2).pack(side="left")
        self.result_count_tag = tk.Label(head, text="0条待处理", bg="#F2F5F9",
                                         fg=COL_MUTED, font=self.f_tiny, padx=8, pady=2)
        self.result_count_tag.pack(side="left", padx=(10, 0))
        self.result_box = tk.Frame(inner, bg=COL_MAIN)
        self.result_box.pack(fill="x", pady=(10, 0))

    # ----- 页面：02 账号管理 -----
    def _page_account(self, parent):
        self._page_header(parent, "账号管理", "添加 / 管理账号，支持抓包 cURL 与 Trae 内置浏览器登录")
        card, inner = self._card(parent)
        card.pack(fill="both", expand=True, padx=24, pady=(0, 20))

        self.acc_manage_tree = ttk.Treeview(
            inner, columns=("name", "app", "req", "auth"), show="headings", height=7)
        for c, t, w in (("name", "账号名", 210), ("app", "软件", 110),
                        ("req", "请求数", 90), ("auth", "登录方式", 200)):
            self.acc_manage_tree.heading(c, text=t)
            self.acc_manage_tree.column(c, width=w,
                                        anchor="center" if c == "req" else "w")
        vs = ttk.Scrollbar(inner, orient="vertical", command=self.acc_manage_tree.yview)
        self.acc_manage_tree.configure(yscrollcommand=vs.set)
        self.acc_manage_tree.pack(fill="both", expand=True)
        vs.pack(side="right", fill="y")

        ops = tk.Frame(inner, bg=COL_MAIN)
        ops.pack(fill="x", pady=(14, 4))
        ttk.Button(ops, text="添加账号（cURL）", style="Accent.TButton",
                   command=self.add_account_dialog).pack(side="left", padx=(0, 8))
        ttk.Button(ops, text="Trae 内置浏览器登录",
                   command=self.trae_login_dialog).pack(side="left", padx=(0, 8))
        ttk.Button(ops, text="查看详情", command=self.account_detail).pack(side="left", padx=(0, 8))
        ttk.Button(ops, text="删除选中", style="Danger.TButton",
                   command=self.delete_account).pack(side="left")
        ttk.Button(ops, text="刷新", style="Ghost.TButton",
                   command=lambda: self._refresh_all(load_credits=False)).pack(side="right")

        tip = ("提示：内置浏览器登录保存后，账号会自动出现在列表。"
               "若刚登录完列表未刷新，程序会每 0.7 秒自动重查直至账号可见，无需手动刷新。")
        tk.Label(inner, text=tip, bg=COL_MAIN, fg="#B45309", font=self.f_small,
                 anchor="w", justify="left", wraplength=680).pack(fill="x", pady=(6, 0))

    # ----- 页面：03 设置 -----
    def _page_settings(self, parent):
        self._page_header(parent, "设置", "每日定时任务（launchd）与签到偏好")

        # 卡片：每日定时
        card, inner = self._card(parent)
        card.pack(fill="x", padx=24, pady=(0, 16))
        tk.Label(inner, text="每日定时", bg=COL_MAIN, fg=COL_TEXT,
                 font=self.f_h2).pack(anchor="w")
        f1 = tk.Frame(inner, bg=COL_MAIN)
        f1.pack(fill="x", pady=(12, 8))
        tk.Label(f1, text="每天", bg=COL_MAIN, fg=COL_TEXT,
                 font=self.f_body).pack(side="left")
        self.timer_hour = ttk.Combobox(f1, values=[f"{h:02d}" for h in range(24)], width=5)
        self.timer_hour.set("21")
        self.timer_hour.pack(side="left", padx=6)
        tk.Label(f1, text="时", bg=COL_MAIN, fg=COL_TEXT).pack(side="left")
        self.timer_min = ttk.Combobox(f1, values=[f"{m:02d}" for m in range(60)], width=5)
        self.timer_min.set("30")
        self.timer_min.pack(side="left", padx=6)
        tk.Label(f1, text="分自动执行签到", bg=COL_MAIN, fg=COL_TEXT).pack(side="left")
        f2 = tk.Frame(inner, bg=COL_MAIN)
        f2.pack(fill="x", pady=(4, 10))
        ttk.Button(f2, text="安装定时任务", style="Accent.TButton",
                   command=self.install_timer).pack(side="left", padx=(0, 8))
        ttk.Button(f2, text="卸载定时任务", style="Danger.TButton",
                   command=self.uninstall_timer).pack(side="left", padx=(0, 8))
        ttk.Button(f2, text="查看状态", command=self.timer_status).pack(side="left")
        self.timer_info = tk.Label(inner, text="", bg=COL_MAIN, fg=COL_MUTED,
                                   font=self.f_small, justify="left", anchor="w",
                                   wraplength=720)
        self.timer_info.pack(fill="x", pady=(4, 0))

        # 卡片：签到选项
        card2, inner2 = self._card(parent)
        card2.pack(fill="x", padx=24, pady=(0, 20))
        tk.Label(inner2, text="签到选项", bg=COL_MAIN, fg=COL_TEXT,
                 font=self.f_h2).pack(anchor="w")
        f3 = tk.Frame(inner2, bg=COL_MAIN)
        f3.pack(fill="x", pady=(12, 6))
        tk.Label(f3, text="仅签到账号：", bg=COL_MAIN, fg=COL_MUTED,
                 font=self.f_small).pack(side="left")
        self.sign_only = tk.StringVar()
        ttk.Entry(f3, width=16, textvariable=self.sign_only).pack(side="left", padx=(4, 0))
        self.dry_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f3, text="预览模式（仅打印请求，不真正签到）",
                        variable=self.dry_var).pack(side="left", padx=(18, 0))
        f4 = tk.Frame(inner2, bg=COL_MAIN)
        f4.pack(fill="x", pady=(4, 6))
        ttk.Button(f4, text="预览请求", command=self.start_preview).pack(side="left", padx=(0, 8))
        ttk.Button(f4, text="立即签到（全部账号）", style="Accent.TButton",
                   command=lambda: self._begin_sign(dry=False)).pack(side="left")
        tk.Label(inner2, text="说明：自动签到开关在「01 签到」页底部，实时联动本页的定时任务与时间。",
                 bg=COL_MAIN, fg=COL_SIDEBAR_MUTED, font=self.f_tiny).pack(anchor="w", pady=(4, 0))

    # ----- 页面：04 使用说明 -----
    def _page_help(self, parent):
        self._page_header(parent, "使用说明", "常见用法与注意事项")
        card, inner = self._card(parent)
        card.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        t = tk.Text(inner, bg=COL_MAIN, fg=COL_TEXT, wrap="word", relief="flat",
                    highlightthickness=0, padx=6, pady=4, font=self.f_body,
                    state="disabled")
        t.pack(fill="both", expand=True)
        vs = ttk.Scrollbar(inner, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        t.configure(state="normal")
        t.insert("1.0", HELP_TEXT)
        t.configure(state="disabled")

    # ---------------- 数据与渲染 ----------------
    def _load_data(self):
        self._logs = parse_logs(LOG_FILE)
        self._accounts = self.load_config().get("accounts", [])

    def _refresh_all(self, load_credits=False):
        self._load_data()
        self._update_header()
        self._update_sidebar_progress()
        self._render_overview()
        self._render_task_table()
        self._rebuild_results_from_logs()
        self._render_results()
        self._update_auto_card()
        self._update_settings_timer()
        self._refresh_account_tree()
        if load_credits:
            self.refresh_credits()

    def load_config(self):
        return load_config()

    def _launchd_state(self):
        """读取 launchd 定时状态 -> {installed, hour, minute, next, delta}"""
        st = {"installed": False, "hour": 21, "minute": 30, "next": None, "delta": ""}
        try:
            if os.path.exists(LAUNCHD_PLIST):
                import plistlib
                with open(LAUNCHD_PLIST, "rb") as f:
                    d = plistlib.load(f)
                cal = d.get("StartCalendarInterval", {})
                st["hour"] = int(cal.get("Hour", 21))
                st["minute"] = int(cal.get("Minute", 30))
            out = subprocess.run(["launchctl", "list"], capture_output=True,
                                 text=True, timeout=8).stdout
            st["installed"] = LAUNCHD_LABEL in out
        except Exception:  # noqa: BLE001
            st["installed"] = os.path.exists(LAUNCHD_PLIST)
        if st["installed"]:
            now = datetime.datetime.now()
            nxt = now.replace(hour=st["hour"], minute=st["minute"],
                              second=0, microsecond=0)
            if nxt <= now:
                nxt += datetime.timedelta(days=1)
            st["next"] = nxt
            sec = int((nxt - now).total_seconds())
            st["delta"] = f"{sec // 3600} 小时 {(sec % 3600) // 60} 分"
        return st

    def _update_header(self):
        st = self._launchd_state()
        self.header_dot.delete("all")
        if st["installed"]:
            self.header_dot.create_oval(1, 1, 9, 9, fill=COL_SUCCESS, outline="")
            nxt = st["next"]
            self.header_status.configure(
                text=f"自动签到运行中 · 下次 {st['hour']:02d}:{st['minute']:02d}",
                fg=COL_MUTED)
        else:
            self.header_dot.create_oval(1, 1, 9, 9, fill="#C9C9C9", outline="")
            self.header_status.configure(text="自动签到未开启", fg=COL_MUTED)

    def _update_sidebar_progress(self):
        done = sum(1 for a in self._accounts if self._acc_status(a.get("name", ""))[0] == "done")
        total = len(self._accounts)
        if total:
            self.side_progress.set_ratio(done / total)
            self.side_progress_tip.configure(text=f"还有 {total - done} 个账号待签到")
        else:
            self.side_progress.set_ratio(0)
            self.side_progress_tip.configure(text="暂无账号")

    def _acc_status(self, name):
        """返回 (state, entry, date)；state: done / fail / pending"""
        today = datetime.date.today().strftime("%Y-%m-%d")
        logs = self._logs
        day = logs.get(today, {"ok": {}, "fail": {}})
        if name in day["ok"]:
            return "done", day["ok"][name], today
        if name in day["fail"]:
            return "fail", day["fail"][name], today
        for d in sorted(logs, reverse=True):
            dday = logs[d]
            for key, e in list(dday["ok"].items()) + list(dday["fail"].items()):
                if key == name:
                    return "pending", e, d
        return "pending", None, None

    def _streak_days(self):
        logs = self._logs
        if not logs:
            return 0
        dates = sorted(logs)
        today = datetime.date.today()
        yest = (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        start = yest if (dates[-1] == yest and today.strftime("%Y-%m-%d") not in logs) else today
        streak = 0
        cur = start
        while True:
            ds = cur.strftime("%Y-%m-%d")
            if ds in logs and logs[ds]["ok"]:
                streak += 1
                cur -= datetime.timedelta(days=1)
            else:
                break
        return streak

    def _week_credits(self):
        today = datetime.date.today()
        monday = today - datetime.timedelta(days=today.weekday())
        total = 0.0
        for ds, day in self._logs.items():
            try:
                d = datetime.datetime.strptime(ds, "%Y-%m-%d").date()
            except ValueError:
                continue
            if d >= monday:
                total += sum(e["credits"] for e in day["ok"].values())
        return total

    def _render_overview(self):
        today = datetime.date.today()
        today_s = today.strftime("%Y-%m-%d")
        day = self._logs.get(today_s, {"ok": {}, "fail": {}})
        done = len(day["ok"])
        fail = len(day["fail"])
        total = len(self._accounts)
        today_credits = sum(e["credits"] for e in day["ok"].values())
        cumu = sum(e["credits"] for d in self._logs.values()
                   for e in d["ok"].values())
        streak = self._streak_days()

        self.overview_big.configure(text=f"{done} / {total}")
        self.overview_note.configure(text="个账号今日已完成签到")
        self.overview_progress.set_ratio(done / total if total else 0.0)

        # 上次签到时间（全部记录中最近一次 OK/FAIL）
        last_time = "-"
        for ds in sorted(self._logs, reverse=True):
            for d in self._logs[ds].values():
                for e in d.values():
                    last_time = e["time"][:5]
                    break
            if last_time != "-":
                break
        dt = datetime.datetime.now()
        self.overview_sub.configure(
            text=f"{dt.year}年{dt.month:02d}月{dt.day:02d}日 {WEEKDAYS[dt.weekday()]} "
                 f"- 上次签到 {last_time} · 已连续签到 {streak} 天")
        self.ov_stat["credit"].configure(text=f"+{_fmt_num(today_credits)}")
        self.ov_stat["total"].configure(text=_fmt_num(cumu))
        self.ov_stat["streak"].configure(text=f"{streak}天")

        # 自动签到标签
        st = self._launchd_state()
        if st["installed"]:
            self.auto_tag.configure(bg="#1D2A1D")
            self.auto_tag_dot.configure(bg="#1D2A1D")
            self.auto_tag_dot.delete("all")
            self.auto_tag_dot.create_oval(1, 1, 7, 7, fill=COL_SUCCESS, outline="")
            self.auto_tag_text.configure(text="自动签到已开启", fg=COL_SUCCESS)
        else:
            self.auto_tag.configure(bg="#222222")
            self.auto_tag_dot.configure(bg="#222222")
            self.auto_tag_dot.delete("all")
            self.auto_tag_dot.create_oval(1, 1, 7, 7, fill="#8A8A8A", outline="")
            self.auto_tag_text.configure(text="自动签到未开启", fg="#8A8A8A")

        self._render_credit_card()
        self._render_chart()

    def _render_credit_card(self):
        tot_limit = sum(d.get("total_limit", 0) or 0 for d in self._credit_data.values())
        remain = sum(d.get("remaining", 0) or 0 for d in self._credit_data.values())
        used = sum(d.get("total_used", 0) or 0 for d in self._credit_data.values())
        week = self._week_credits()
        self.credit_big.configure(text=_fmt_num(tot_limit) if tot_limit else "0")
        self.week_tag.configure(text=f"本周 +{_fmt_num(week)}")
        self.credit_extra.configure(text=f"可用剩余 {_fmt_num(remain)} · 已用 {_fmt_num(used)}")

    def _render_chart(self):
        c = self.chart_canvas
        c.delete("all")
        days = self._chart_days
        w = max(c.winfo_width(), 300)
        h = 130
        today = datetime.date.today()
        series = []
        has_any = False
        for i in range(days - 1, -1, -1):
            d = today - datetime.timedelta(days=i)
            ds = d.strftime("%Y-%m-%d")
            dd = self._logs.get(ds, {"ok": {}, "fail": {}})
            v = sum(e["credits"] for e in dd["ok"].values())
            if v > 0:
                has_any = True
            series.append((d.strftime("%m/%d"), v))
        maxv = max((v for _, v in series), default=0) or 1
        pad_l, pad_r, pad_t, pad_b = 34, 8, 12, 22
        pw = w - pad_l - pad_r
        ph = h - pad_t - pad_b
        # Y 轴刻度
        for i in range(4):
            yv = maxv * i / 3
            y = h - pad_b - (ph * i / 3)
            c.create_line(pad_l, y, w - pad_r, y, fill="#F0F0F0")
            c.create_text(pad_l - 5, y, text=_fmt_num(yv), anchor="e",
                          font=self.f_tiny, fill="#999999")
        n = len(series)
        bw = pw / n
        for idx, (label, v) in enumerate(series):
            x0 = pad_l + idx * bw + bw * 0.22
            x1 = pad_l + (idx + 1) * bw - bw * 0.22
            bh = (v / maxv) * ph if v > 0 else 2
            color = COL_ERROR if idx == n - 1 else COL_PRIMARY
            c.create_rectangle(x0, h - pad_b - bh, x1, h - pad_b, fill=color, outline="")
            c.create_text(x0 + (x1 - x0) / 2, h - pad_b + 6, text=label,
                          font=self.f_tiny, fill="#999999")
        c.create_text(pad_l + (w - pad_l - pad_r) / 2, 6,
                      text="今日" if self._chart_days == 7 else f"近{self._chart_days}天",
                      font=self.f_tiny, fill="#BBBBBB")
        if not has_any:
            self.chart_hint.configure(
                text=f"近{self._chart_days}天暂无积分记录，柱状图基于签到日志逐日汇总展示。")
        else:
            self.chart_hint.configure(text="")

    def _set_chart_days(self, days):
        self._chart_days = days
        for k, b in self.chart_btn.items():
            b.configure(bg="#F2F5F9" if k == days else COL_MAIN,
                        fg=COL_PRIMARY if k == days else COL_MUTED)
        self._render_chart()

    # ---------------- 账号任务表格 ----------------
    def _set_filter(self, key):
        self._filter = key
        self._render_task_table()

    def _render_filter_btns(self):
        done = sum(1 for a in self._accounts
                   if self._acc_status(a.get("name", ""))[0] == "done")
        pending = len(self._accounts) - done
        counts = {"all": len(self._accounts), "done": done, "pending": pending}
        for key, (b, _old) in self.filter_btns.items():
            active = key == self._filter
            text = key
            if key == "all":
                text = f"全部{counts['all']}"
            elif key == "done":
                text = f"已完成{done}"
            else:
                text = f"待处理{pending}"
            b.configure(text=text, bg="#F2F5F9" if active else COL_MAIN,
                        fg=COL_PRIMARY if active else COL_MUTED)
            self.filter_btns[key] = (b, text)

    def _render_task_table(self):
        # 清空旧行
        for ch in self.task_rows.winfo_children():
            ch.destroy()
        self._render_filter_btns()
        self.task_count_tag.configure(text=f"{len(self._accounts)}个账号")

        rows = []
        for a in self._accounts:
            name = a.get("name", "?")
            app = a.get("app", "?")
            state, entry, date = self._acc_status(name)
            rows.append((a, state, entry, date))
        if self._filter == "done":
            rows = [r for r in rows if r[1] == "done"]
        elif self._filter == "pending":
            rows = [r for r in rows if r[1] != "done"]

        if not rows:
            self.task_empty.configure(
                text="暂无待展示账号" if not self._accounts else "当前筛选条件下没有账号，请调整筛选。")
            self.task_empty.pack(fill="x", pady=18)
            return
        self.task_empty.pack_forget()

        idx = 0
        for a, state, entry, date in rows:
            name = a.get("name", "?")
            app = a.get("app", "?")
            row = tk.Frame(self.task_rows, bg=COL_ROW if idx % 2 else COL_MAIN)
            row.pack(fill="x", pady=1)
            # 平台徽标
            badge = tk.Canvas(row, width=26, height=26,
                              bg=COL_ROW if idx % 2 else COL_MAIN,
                              highlightthickness=0, bd=0)
            badge.pack(side="left", padx=(10, 8), pady=4)
            bcolor = COL_PRIMARY if (a.get("type") == "trae" or app == "trae") else "#F59E0B"
            badge.create_oval(1, 1, 25, 25, fill=bcolor, outline="")
            badge.create_text(13, 13, text=name[:1].upper(), fill="#FFFFFF",
                              font=self.f_badge)
            # 平台账号
            acc_cell = tk.Frame(row, bg=COL_ROW if idx % 2 else COL_MAIN)
            acc_cell.pack(side="left", fill="x", expand=True,
                          padx=4, pady=6)
            tk.Label(acc_cell, text=name, bg=COL_ROW if idx % 2 else COL_MAIN,
                     fg=COL_TEXT, font=self.f_body_b, anchor="w").pack(anchor="w")
            tk.Label(acc_cell, text=app, bg=COL_ROW if idx % 2 else COL_MAIN,
                     fg=COL_MUTED, font=self.f_tiny, anchor="w").pack(anchor="w")
            # 最近签到
            if state in ("done", "fail") and entry:
                recent = "今天 " + entry["time"][:5]
            elif entry:
                recent = f"{date[5:]} {entry['time'][:5]}"
            else:
                recent = "未签到"
            self._cell(row, idx, recent)
            # 今日积分
            if state == "done" and entry:
                cred = f"+{_fmt_num(entry.get('credits', 0))}"
            else:
                cred = "0"
            self._cell(row, idx, cred)
            # 状态
            if state == "done":
                stext, scol = "已完成", COL_SUCCESS
            elif state == "fail":
                stext, scol = "签到失败", COL_ERROR
            else:
                stext, scol = "待签到", COL_INACTIVE
            self._cell(row, idx, stext, color=scol, bold=True)
            # 操作
            op = tk.Frame(row, bg=COL_ROW if idx % 2 else COL_MAIN)
            op.pack(side="left", fill="x", expand=True, padx=4, pady=6)
            if state == "done":
                b = tk.Button(op, text="已签到", state="disabled", disabledforeground="#B0B0B0",
                              bg="#F0F2F5", relief="flat", font=self.f_small, padx=10, pady=3)
                b.pack(anchor="w")
            elif state == "fail":
                b = tk.Button(op, text="重试", fg=COL_ERROR, bg=COL_MAIN, relief="solid",
                              bd=1, highlightthickness=0, font=self.f_small, padx=10, pady=3,
                              cursor="hand2", command=lambda n=name: self._retry_account(n))
                b.pack(anchor="w")
            else:
                b = tk.Button(op, text="签到", fg="#FFFFFF", bg=COL_PRIMARY, relief="flat",
                              font=self.f_small, padx=10, pady=3, cursor="hand2",
                              command=lambda n=name: self._retry_account(n))
                b.pack(anchor="w")
            idx += 1

    @staticmethod
    def _cell(row, idx, text, color=None, bold=False):
        del idx
        f = tkfont.Font(family="PingFang SC", size=12, weight="bold" if bold else "normal")
        tk.Label(row, text=text, bg=row["bg"], fg=color or COL_TEXT, font=f,
                 anchor="w").pack(side="left", fill="x", expand=True, padx=4, pady=6)

    # ---------------- 结果与异常卡片 ----------------
    def _render_results(self):
        for ch in self.result_box.winfo_children():
            ch.destroy()
        fail_items = [r for r in self._last_results if not r.get("ok")]
        ok_items = [r for r in self._last_results if r.get("ok")]
        ok_total = sum(r.get("credits", 0) for r in ok_items)
        n_fail = len(fail_items)
        self.result_count_tag.configure(text=f"{n_fail}条待处理" if n_fail else "无待处理")

        if not self._last_results:
            tk.Label(self.result_box, text="暂无签到记录，点击「手动签到」或表格内「签到」后这里会展示结果。",
                     bg=COL_MAIN, fg=COL_SIDEBAR_MUTED, font=self.f_small,
                     anchor="w", justify="left", wraplength=360).pack(fill="x")
            return

        # 失败项：红色警告框
        for r in fail_items:
            box = tk.Frame(self.result_box, bg=COL_RED_BG)
            box.configure(highlightthickness=1, highlightbackground=COL_RED_BORDER)
            box.pack(fill="x", pady=(0, 8))
            tk.Label(box, text="⚠", bg=COL_RED_BG, fg=COL_ERROR, font=self.f_h2).pack(
                side="left", padx=(12, 8), pady=8)
            tf = tk.Frame(box, bg=COL_RED_BG)
            tf.pack(side="left", fill="x", expand=True, pady=8)
            tk.Label(tf, text=f"{r.get('app','')} · {r.get('name','')} 签到失败",
                     bg=COL_RED_BG, fg=COL_TEXT, font=self.f_body_b, anchor="w").pack(anchor="w")
            reason = redact_secrets(str(r.get("reason", "")))
            tk.Label(tf, text=reason, bg=COL_RED_BG, fg=COL_MUTED, font=self.f_tiny,
                     anchor="w", justify="left", wraplength=260).pack(anchor="w", pady=(2, 0))
            tk.Button(box, text="立即处理", bg=COL_ERROR, fg="#FFFFFF", relief="flat",
                      font=self.f_tiny, padx=10, pady=3, cursor="hand2",
                      command=lambda n=r.get("name"): self._retry_account(n)).pack(
                side="right", padx=12, pady=8)

        # 成功项：绿色框
        if ok_items:
            names = "、".join(f"{r.get('app','')}·{r.get('name','')}" for r in ok_items)
            box = tk.Frame(self.result_box, bg=COL_GREEN_BG)
            box.configure(highlightthickness=1, highlightbackground=COL_GREEN_BORDER)
            box.pack(fill="x", pady=(0, 4))
            tk.Label(box, text="✓", bg=COL_GREEN_BG, fg=COL_SUCCESS, font=self.f_h2).pack(
                side="left", padx=(12, 8), pady=8)
            tf = tk.Frame(box, bg=COL_GREEN_BG)
            tf.pack(side="left", fill="x", expand=True, pady=8)
            tk.Label(tf, text="签到成功", bg=COL_GREEN_BG, fg=COL_TEXT,
                     font=self.f_body_b, anchor="w").pack(anchor="w")
            tk.Label(tf, text=f"{names} · 本次共获得 {_fmt_num(ok_total)} 积分",
                     bg=COL_GREEN_BG, fg=COL_MUTED, font=self.f_tiny,
                     anchor="w", justify="left", wraplength=280).pack(anchor="w", pady=(2, 0))
            tk.Button(box, text="查看详情", bg=COL_SUCCESS, fg="#FFFFFF", relief="flat",
                      font=self.f_tiny, padx=10, pady=3, cursor="hand2",
                      command=self._show_run_detail).pack(side="right", padx=12, pady=8)

    def _show_run_detail(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("签到结果详情")
        dlg.configure(bg=COL_MAIN)
        dlg.geometry("680x460")
        dlg.transient(self.root)
        t = tk.Text(dlg, bg="#0E1526", fg="#DFE7F2", wrap="word", relief="flat",
                    font=self.f_mono, padx=12, pady=10, state="disabled")
        t.pack(fill="both", expand=True, padx=10, pady=10)
        vs = ttk.Scrollbar(dlg, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=vs.set)
        vs.place(relx=1.0, rely=0.0, relheight=1.0, anchor="ne")
        t.configure(state="normal")
        content = "\n".join(self._run_lines[-400:]) or "（暂无日志）"
        t.insert("1.0", redact_secrets(content) + "\n")
        t.configure(state="disabled")

    # ---------------- 自动签到开关（launchd 联动） ----------------
    def _update_auto_card(self):
        st = self._launchd_state()
        self.auto_switch.set(st["installed"])
        if st["installed"]:
            self.auto_desc.configure(
                text=f"每天 {st['hour']:02d}:{st['minute']:02d} 自动执行 · 距下次签到 {st['delta']}")
            self.auto_note.configure(text="✓ 任务计划已生效，将在后台自动运行",
                                     fg=COL_SUCCESS)
        else:
            self.auto_desc.configure(text="自动签到未开启")
            self.auto_note.configure(text="开启后由 macOS launchd 每天定时自动执行签到。",
                                     fg=COL_MUTED)

    def _update_settings_timer(self):
        st = self._launchd_state()
        try:
            self.timer_hour.set(f"{st['hour']:02d}")
            self.timer_min.set(f"{st['minute']:02d}")
        except Exception:  # noqa: BLE001
            pass
        if st["installed"]:
            self.timer_info.configure(
                text=f"当前已安装：每天 {st['hour']:02d}:{st['minute']:02d} 自动签到。"
                     f"卸载后不会删除账号数据，仅停用自动任务。")
        else:
            self.timer_info.configure(
                text="当前未安装定时任务。选择时间后点「安装定时任务」即可，"
                     "或直接回「01 签到」页用自动签到开关一键开启。")

    def _on_auto_toggle(self, on):
        if on:
            # 读取当前配置时间（默认 21:30），若未安装则安装
            st = self._launchd_state()
            self.install_timer_at(st["hour"], st["minute"])
        else:
            self.uninstall_timer(silent=True)
        self._refresh_all(load_credits=False)
        self.append_log("[定时] 自动签到开关已更新。")

    def _all_launchd(self):
        entries = []
        try:
            out = subprocess.run(["launchctl", "list"], capture_output=True,
                                 text=True, timeout=8)
            for line in out.stdout.splitlines():
                if LAUNCHD_LABEL in line:
                    parts = line.split()
                    entries.append(parts)
        except Exception:  # noqa: BLE001
            pass
        return entries

    def install_timer(self):
        try:
            hour = int(self.timer_hour.get())
            minute = int(self.timer_min.get())
        except ValueError:
            messagebox.showwarning("提示", "请选择有效时间。")
            return
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            messagebox.showwarning("提示", "时间超范围。")
            return
        self.install_timer_at(hour, minute)

    def install_timer_at(self, hour, minute):
        try:
            os.makedirs(os.path.dirname(LAUNCHD_PLIST), exist_ok=True)
            with open(LAUNCHD_PLIST, "w", encoding="utf-8") as f:
                f.write(plist_body(hour, minute,
                                   f"cd {BASE_DIR} && {RUNNER} {BASE_DIR}/checkin.py >> {BASE_DIR}/logs/launchd.log 2>&1"))
        except OSError as e:
            messagebox.showerror("错误", f"写入 plist 失败：{e}")
            return
        try:
            subprocess.run(["launchctl", "unload", LAUNCHD_PLIST],
                           capture_output=True, text=True)
        except Exception:  # noqa: BLE001
            pass
        r = subprocess.run(["launchctl", "load", LAUNCHD_PLIST],
                           capture_output=True, text=True)
        if r.returncode == 0:
            self.append_log(f"[定时] 已安装每日 {hour:02d}:{minute:02d} 自动签到。")
            messagebox.showinfo("成功", f"已安装每日 {hour:02d}:{minute:02d} 自动签到。")
        else:
            self.append_log(f"[定时] 安装失败：{r.stderr.strip()}")
            messagebox.showerror("失败", f"launchctl load 失败：{r.stderr.strip()}")

    def uninstall_timer(self, silent=False):
        subprocess.run(["launchctl", "unload", LAUNCHD_PLIST],
                       capture_output=True, text=True)
        try:
            if os.path.exists(LAUNCHD_PLIST):
                os.remove(LAUNCHD_PLIST)
        except OSError:
            pass
        if not silent:
            self.append_log("[定时] 已卸载定时任务。")
            messagebox.showinfo("完成", "已卸载每日自动签到。")

    def timer_status(self):
        entries = self._all_launchd()
        if entries:
            self.append_log("[定时] launchd 中已注册该定时任务。")
            messagebox.showinfo("定时状态",
                                "定时任务已注册（launchd 中可见 com.marvis.autocheckin）。")
        else:
            self.append_log("[定时] 当前未注册定时任务。")
            messagebox.showinfo("定时状态", "当前未安装每日自动签到。")

    # ---------------- 账号管理 ----------------
    def _refresh_account_tree(self):
        if getattr(self, "acc_manage_tree", None) is None:
            return
        if not self.acc_manage_tree.winfo_exists():
            return
        self.acc_manage_tree.delete(*self.acc_manage_tree.get_children())
        for a in self._accounts:
            name = a.get("name", "?")
            app = a.get("app", "?")
            req = str(len(a.get("requests", [])))
            auth = "Trae 登录" if a.get("type") == "trae" else "抓包 cURL"
            self.acc_manage_tree.insert("", "end", iid=name,
                                        values=(name, app, req, auth))

    def _selected_name(self):
        sel = self.acc_manage_tree.selection()
        if not sel:
            return None
        vals = self.acc_manage_tree.item(sel[0], "values")
        return vals[0] if vals else None

    def delete_account(self):
        name = self._selected_name()
        if not name:
            messagebox.showinfo("提示", "请先在列表中选中要删除的账号。")
            return
        if not messagebox.askyesno("删除账号", f"确定删除账号「{name}」吗？\n（仅删除配置，不影响已登录状态）"):
            return
        cfg = self.load_config()
        before = len(cfg.get("accounts", []))
        cfg["accounts"] = [a for a in cfg.get("accounts", [])
                           if a.get("name") != name]
        save_config(cfg)
        self.append_log(f"[配置] 已删除账号「{name}」（{before} -> {len(cfg['accounts'])}）")
        self._refresh_all(load_credits=False)

    def account_detail(self):
        name = self._selected_name()
        if not name:
            messagebox.showinfo("提示", "请先选中要查看的账号。")
            return
        cfg = self.load_config()
        acc = next((a for a in cfg.get("accounts", []) if a.get("name") == name), None)
        if not acc:
            return
        lines = [f"账号：{acc.get('name')}", f"软件：{acc.get('app')}",
                 f"类型：{'Trae 登录' if acc.get('type') == 'trae' else '抓包 cURL'}"]
        auth = acc.get("trae_auth", {})
        if auth:
            lines += ["", "登录态：", f"  uid      = {auth.get('uid', '') or '未知'}",
                      f"  saved_at = {auth.get('saved_at', '') or '未知'}",
                      "  session / token 已保存（为避免泄露不在界面显示原文）"]
        reqs = acc.get("requests", [])
        if reqs:
            lines += ["", f"请求数：{len(reqs)}"]
            for i, r in enumerate(reqs[:10], 1):
                lines.append(f"  {i}. {str(r.get('method', 'GET')).upper()} {r.get('url')}")
        messagebox.showinfo(f"账号详情 - {name}", "\n".join(lines))

    # ---------------- 添加账号 ----------------
    def add_account_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("添加账号（粘贴 cURL）")
        dlg.configure(bg=COL_MAIN)
        dlg.geometry("640x560")
        dlg.transient(self.root)

        tk.Label(dlg, text="账号名：", bg=COL_MAIN, fg=COL_TEXT).pack(anchor="w", padx=14, pady=(14, 2))
        name_var = tk.StringVar()
        tk.Entry(dlg, textvariable=name_var, width=40, font=self.f_body).pack(anchor="w", padx=14)
        tk.Label(dlg, text="软件名（如 trae / workbuddy）：", bg=COL_MAIN, fg=COL_TEXT).pack(anchor="w", padx=14, pady=(10, 2))
        app_var = tk.StringVar(value="trae")
        tk.Entry(dlg, textvariable=app_var, width=20, font=self.f_body).pack(anchor="w", padx=14)
        tk.Label(dlg, text="粘贴抓包得到的 cURL：", bg=COL_MAIN, fg=COL_TEXT).pack(anchor="w", padx=14, pady=(10, 2))
        txt = tk.Text(dlg, height=14, font=self.f_mono, wrap="word", bg="#FBFCFE",
                      relief="solid", bd=1, highlightthickness=0)
        txt.pack(fill="both", expand=True, padx=14, pady=(0, 10))

        def do_add():
            name = name_var.get().strip()
            app = app_var.get().strip() or "trae"
            curl = txt.get("1.0", "end").strip()
            if not name:
                messagebox.showwarning("提示", "请输入账号名。")
                return
            if not curl:
                messagebox.showwarning("提示", "请粘贴 cURL。")
                return
            try:
                req = parse_curl(curl)
            except ValueError as e:
                messagebox.showerror("解析失败", f"无法解析该 cURL：\n{e}")
                return
            cfg = self.load_config()
            dup = next((a for a in cfg.get("accounts", [])
                        if a.get("name") == name), None)
            if dup and not messagebox.askyesno("覆盖", f"已存在同名账号「{name}」，是否覆盖？"):
                return
            add_account(cfg, name, app, req, replace=True)
            save_config(cfg)
            self.append_log(f"[配置] 已添加账号「{name}（{app}）」，requests={len(req)} 条")
            self._refresh_all(load_credits=False)
            dlg.destroy()

        bar = tk.Frame(dlg, bg=COL_MAIN)
        bar.pack(fill="x", padx=14, pady=(0, 14))
        ttk.Button(bar, text="保存", style="Accent.TButton", command=do_add).pack(side="right")
        ttk.Button(bar, text="取消", command=dlg.destroy).pack(side="right", padx=(0, 8))

    # ---------------- Trae 内置浏览器登录 ----------------
    def trae_login_dialog(self):
        if self._login_running or self._signing:
            messagebox.showinfo("提示", "已有任务进行中，请稍候。")
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("Trae 内置浏览器登录")
        dlg.configure(bg=COL_MAIN)
        dlg.geometry("460x220")
        dlg.transient(self.root)
        tk.Label(dlg, text="给这个 Trae 账号起个名字：", bg=COL_MAIN, fg=COL_TEXT,
                 font=self.f_body).pack(anchor="w", padx=16, pady=(22, 6))
        name_var = tk.StringVar()
        tk.Entry(dlg, textvariable=name_var, width=28, font=self.f_body).pack(anchor="w", padx=16)
        tf = tk.Label(dlg, text="输入账号名后点击下方按钮，会弹出内置浏览器窗口。\n"
                                "在窗口里登录 Trae，程序会自动捕获登录态并保存。",
                      bg=COL_MAIN, fg=COL_MUTED, font=self.f_small, justify="left")
        tf.pack(anchor="w", padx=16, pady=(14, 0))

        def go():
            name = name_var.get().strip()
            dlg.destroy()
            if not name:
                messagebox.showwarning("提示", "请先输入账号名。")
                return
            self.start_trae_login(name)

        bar = tk.Frame(dlg, bg=COL_MAIN)
        bar.pack(fill="x", padx=16, pady=(16, 16))
        ttk.Button(bar, text="打开登录浏览器", style="Accent.TButton", command=go).pack(side="right")
        ttk.Button(bar, text="取消", command=dlg.destroy).pack(side="right", padx=(0, 8))

    def start_trae_login(self, name):
        if self._login_running:
            self.append_log("[Trae] 已有登录任务进行中，请稍候。")
            return
        if self._signing:
            self.append_log("[Trae] 签到进行中，请完成后再登录。")
            return
        self._login_running = True
        self.header_status.configure(text="正在打开内置浏览器（登录中…）", fg=COL_MUTED)
        self.root.configure(cursor="watch")
        self.append_log(f"[Trae] 启动内置浏览器登录（账号 {name}），请在弹出窗口内完成登录…")
        threading.Thread(target=self._trae_login_worker, args=(name,), daemon=True).start()

    def _trae_login_worker(self, name):
        q = self._q
        script = os.path.join(BASE_DIR, "trae_login.py")
        cmd = [RUNNER, script, "--name", name]
        try:
            p = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1, cwd=BASE_DIR)
        except Exception as e:  # noqa: BLE001
            q.put(("log", f"[FAIL] 无法启动登录子进程：{e}"))
            q.put(("login_done", (name, 1)))
            return
        for line in p.stdout:
            if line:
                q.put(("log", line.rstrip("\n")))
        p.wait()
        q.put(("login_done", (name, p.returncode)))

    def _on_login_done(self, name, code):
        self._login_running = False
        self.header_status.configure(text="就绪", fg=COL_MUTED)
        self.root.configure(cursor="")
        self._refresh_all(load_credits=False)
        # 竞态兜底：登录子进程把账号写入 accounts.json 与 GUI 读取之间
        # 偶尔存在延迟，这里每 0.7 秒重查一次，直到新账号可见（上限 ~3.5s）。
        self._settle_refresh(name, 0)
        self.append_log(f"[Trae] 登录子进程退出（exit={code}），已触发账号列表刷新。")

    def _settle_refresh(self, name, tries):
        try:
            present = any(a.get("name") == name
                          for a in self.load_config().get("accounts", []))
        except Exception:  # noqa: BLE001
            present = False
        self._refresh_all(load_credits=False)
        if present:
            if tries > 0:
                self.append_log(f"[Trae] 已捕获到新账号「{name}」，列表已刷新。")
            return
        if tries >= 4:
            self.append_log("[Trae] 暂未在 accounts.json 中识别到新账号，请检查浏览器内登录是否完成。")
            return
        self.root.after(700, self._settle_refresh, name, tries + 1)

    # ---------------- 签到 ----------------
    def start_sign(self):
        self._begin_sign(bool(self.dry_var.get()))

    def start_preview(self):
        self._begin_sign(True)

    def _retry_account(self, name):
        self.sign_only.set(name)
        self._begin_sign(dry=False, only=name)

    def _begin_sign(self, dry, only=None):
        if self._signing:
            self.append_log("[提示] 签到正在执行中，请稍候。")
            return
        if self._login_running:
            self.append_log("[提示] Trae 登录进行中，请完成后再签到。")
            return
        self._signing = True
        self.header_status.configure(
            text=f"签到进行中（账号 {'仅' + only if only else '全部'}）…", fg=COL_PRIMARY)
        self.root.configure(cursor="watch")
        only = only or self.sign_only.get().strip() or None
        self.append_log("[提示] 开始签到…" + (f"（仅账号 {only}）" if only else ""))
        threading.Thread(target=self._sign_worker, args=(only, dry), daemon=True).start()

    def _sign_worker(self, only, dry):
        q = self._q
        cmd = [RUNNER, os.path.join(BASE_DIR, "checkin.py")]
        if only:
            cmd += ["--only", only]
        if dry:
            cmd += ["--dry-run"]
        try:
            p = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1, cwd=BASE_DIR)
        except Exception as e:  # noqa: BLE001
            q.put(("log", f"[FAIL] 无法启动签到进程：{e}"))
            q.put(("sign_done", 1))
            return
        for line in p.stdout:
            if line:
                q.put(("log", line.rstrip("\n")))
        p.wait()
        q.put(("sign_done", p.returncode))

    def _on_sign_done(self, code):
        self._signing = False
        self.header_status.configure(text=f"签到结束（exit={code}）", fg=COL_MUTED)
        self.root.configure(cursor="")
        self._load_data()
        self._rebuild_results_from_logs()
        self._refresh_all(load_credits=True)

    def _rebuild_results_from_logs(self):
        """根据今天的日志重建结果卡片（上个运行结果）"""
        today = datetime.date.today().strftime("%Y-%m-%d")
        day = self._logs.get(today, {"ok": {}, "fail": {}})
        results = []
        for name, e in day["fail"].items():
            acc = next((a for a in self._accounts if a.get("name") == name), {})
            results.append({"name": name, "app": acc.get("app", "?"),
                            "ok": False,
                            "reason": e["msg"].split("：", 1)[-1] if "：" in e["msg"] else e["msg"]})
        for name, e in day["ok"].items():
            acc = next((a for a in self._accounts if a.get("name") == name), {})
            results.append({"name": name, "app": acc.get("app", "?"),
                            "ok": True, "credits": e.get("credits", 0),
                            "reason": e["msg"]})
        self._last_results = results

    # ---------------- 积分状态 ----------------
    def refresh_credits(self):
        if self._cred_busy:
            return
        accounts = self._accounts
        if not accounts:
            return
        self._cred_busy = True
        self.header_status.configure(text="正在查询积分…", fg=COL_PRIMARY)
        threading.Thread(target=self._credits_worker,
                         args=([a for a in accounts],), daemon=True).start()

    def _credits_worker(self, accounts):
        q = self._q
        for acc in accounts:
            name = acc.get("name", "?")
            app = acc.get("app", "?")
            if acc.get("type") != "trae":
                continue
            try:
                auth = acc.get("trae_auth", {})
                client = TraeClient(
                    session=(auth.get("session") or "").strip(),
                    device_id=(auth.get("device_id") or "").strip(),
                    session_token=(auth.get("token") or "").strip())
                res = client.credits()
                if res.get("ok"):
                    q.put(("credits_update", (name, {
                        "ok": True,
                        "remaining": res.get("remaining"),
                        "total_limit": res.get("total_limit"),
                        "total_used": res.get("total_used"),
                    })))
                else:
                    q.put(("credits_update", (name, {"error": str(res.get('message'))})))
            except Exception as e:  # noqa: BLE001
                q.put(("credits_update", (name, {"error": str(e)})))
        q.put(("credits_done", None))

    def _on_credits_update(self, name, data):
        if "error" in data:
            self._credit_data.pop(name, None)
        else:
            self._credit_data[name] = data
        self._render_credit_card()

    def _on_credits_done(self):
        self._cred_busy = False
        self.header_status.configure(text="就绪", fg=COL_MUTED)
        self._render_credit_card()
        self.append_log("[积分] 积分查询完成。")

    # ---------------- 日志 / 状态 ----------------
    def _classify(self, line):
        if "[OK]" in line or "签到成功" in line:
            return "ok"
        if "[FAIL]" in line or "失败" in line:
            return "fail"
        if "[Trae]" in line:
            return "trae"
        return "info"

    def append_log(self, line):
        line = redact_secrets(str(line))
        self._run_lines.append(line)
        if len(self._run_lines) > 3000:
            self._run_lines = self._run_lines[-2000:]

    # ---------------- 事件循环 ----------------
    def _poll(self):
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "log":
                    self.append_log(payload)
                elif kind == "sign_done":
                    self._on_sign_done(payload)
                elif kind == "login_done":
                    self._on_login_done(*payload)
                elif kind == "credits_update":
                    self._on_credits_update(*payload)
                elif kind == "credits_done":
                    self._on_credits_done()
        except queue.Empty:
            pass
        except Exception:  # noqa: BLE001
            self.append_log(f"[FAIL] 内部异常：{traceback.format_exc()[-400:]}")
        try:
            self._poll_job = self.root.after(150, self._poll)
        except Exception:  # noqa: BLE001
            pass

    def _on_close(self):
        try:
            self.header_status.configure(text="退出中…")
        except Exception:  # noqa: BLE001
            pass
        try:
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    if not HAVE_TK:
        print("当前环境无法创建图形界面（缺少 Tk），请安装 Python with Tk 后重试。")
        sys.exit(1)
    root = tk.Tk()
    app = AutoCheckinApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
