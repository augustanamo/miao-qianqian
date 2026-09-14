#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行时探测：检测当前 Python 能否真正创建 Tk 窗口（含 macOS 15 系统 Tk 8.5 崩溃问题）。
返回 0 表示可用并打印版本，否则返回 1。供 App 启动器挑选可用的解释器。
"""
import sys
import tkinter

try:
    root = tkinter.Tk()
    root.withdraw()
    patch = root.tk.call("info", "patchlevel")
    root.update_idletasks()
    root.destroy()
except Exception as e:  # noqa: BLE001
    print("BAD", e, file=sys.stderr)
    sys.exit(1)
print("OK", patch)
sys.exit(0)
