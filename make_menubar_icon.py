#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 assets/AppIcon.icns 生成「菜单栏专用图标」（透明底、挖空眼睛）。

为什么不能直接把 App 图标贴到菜单栏：
  App 图标是「白底 + 黑猫」的方形卡片，原样放进菜单栏就像贴了一小张纸片；
  而菜单栏图标该随系统明暗自动反色（NSImage.isTemplate），模板渲染只认
  **alpha 通道** —— 白底和白眼都会被当成不透明像素，一起涂成前景色，
  于是黑猫变成一块实心色块，眼睛也没了。

做法：把「亮度」直接当 alpha 用
  猫身是黑的  → 亮度低 → 不透明
  白底和白眼  → 亮度高 → 透明（眼睛因此成了挖空的洞）
  毛边灰阶    → 平滑过渡，保住抗锯齿
再交给 App 侧 `isTemplate = true` 渲染：
  浅色菜单栏 = 黑猫 + 白眼，深色菜单栏 = 白猫 + 深眼。

用法：python3 make_menubar_icon.py [输出目录]     # 默认 assets/menubar
输出：MenuBarIcon.png（18×18）、MenuBarIcon@2x.png（36×36）、preview-256.png（自查用）
"""
import os
import sys

import numpy as np
from PIL import Image

# 亮度 → alpha 的软阈值：<= DARK 全不透明，>= LIGHT 全透明，中间线性过渡
DARK, LIGHT = 110.0, 205.0
# 裁到内容包围盒后额外留的边距（占长边比例）：菜单栏图标不宜顶满
PAD_RATIO = 0.06
# 目标尺寸（pt）与对应像素：菜单栏图标标准 18pt
TARGETS = [("MenuBarIcon.png", 18), ("MenuBarIcon@2x.png", 36)]

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "assets", "AppIcon.icns")


def build(src: str, out_dir: str) -> int:
    if not os.path.exists(src):
        print(f"找不到源图标：{src}", file=sys.stderr)
        return 1

    im = Image.open(src).convert("RGBA")
    a = np.asarray(im, dtype=np.float32)
    lum = 0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]
    alpha = np.clip((LIGHT - lum) / (LIGHT - DARK), 0.0, 1.0) * (a[:, :, 3] / 255.0)

    ys, xs = np.nonzero(alpha > 0.12)
    if len(xs) == 0:
        print("图标里没有找到深色内容（整张都是白底？）", file=sys.stderr)
        return 1
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    side = max(x1 - x0 + 1, y1 - y0 + 1)
    pad = int(round(side * PAD_RATIO))
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    half = side / 2.0 + pad
    box = (int(round(cx - half)), int(round(cy - half)),
           int(round(cx + half)), int(round(cy + half)))

    mask = Image.fromarray((alpha * 255.0 + 0.5).astype(np.uint8), "L").crop(box)
    print(f"源图 {im.size[0]}×{im.size[1]}，猫的包围盒 {box}（{box[2]-box[0]}px 见方）")

    os.makedirs(out_dir, exist_ok=True)

    def rgba(px: int) -> Image.Image:
        out = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        out.putalpha(mask.resize((px, px), Image.LANCZOS))   # template 只取 alpha
        return out

    for name, px in TARGETS:
        p = os.path.join(out_dir, name)
        rgba(px).save(p)
        print(f"  写出 {p}  ({px}×{px})")

    # 自查用预览：同样是 RGBA（黑猫 + 透明底），不是灰度图 ——
    # 存成 "L" 的话读回来会是全不透明，拿它核对观感等于白看。
    p = os.path.join(out_dir, "preview-256.png")
    rgba(256).save(p)
    print(f"  写出 {p}（自查用，可不打包）")
    return 0


def main() -> int:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "assets", "menubar")
    return build(SRC, out_dir)


if __name__ == "__main__":
    sys.exit(main())
