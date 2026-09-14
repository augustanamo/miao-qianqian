#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 AutoCheckin 应用图标（绿色圆角方块 + 白色对勾），输出 .icns
用法：python3 make_icon.py <输出.icns路径>
"""
import math
import os
import struct
import subprocess
import sys
import tempfile
import zlib

GREEN = (46, 160, 84)


def _chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def write_png(path, size, rgba):
    raw = b""
    for y in range(size):
        raw += b"\x00" + bytes(rgba[y * size * 4:(y + 1) * size * 4])
    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += _chunk(b"IDAT", zlib.compress(raw, 9))
    png += _chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def make_icon(size):
    px = bytearray(size * size * 4)
    edge = max(2, int(size * 0.085))          # 圆角半径
    cx1, cy1 = edge, edge
    cx2, cy2 = size - edge, edge
    cx3, cy3 = edge, size - edge
    cx4, cy4 = size - edge, size - edge
    r2 = edge * edge
    # 对勾折线三点
    p1 = (size * 0.30, size * 0.52)
    p2 = (size * 0.43, size * 0.66)
    p3 = (size * 0.73, size * 0.34)
    half_t = max(1.0, size * 0.032)
    aa = 1.0  # 抗锯齿宽度(像素)

    def dist_seg(q, a, b):
        ax, ay = a[0] - q[0], a[1] - q[1]
        bx, by = b[0] - q[0], b[1] - q[1]
        ab2 = (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 or 1e-9
        t = max(0.0, min(1.0, -(ax * bx + ay * by) / ab2))
        sx, sy = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        return math.hypot(q[0] - sx, q[1] - sy)

    for y in range(size):
        yf = y + 0.5
        for x in range(size):
            xf = x + 0.5
            inside_rect = (
                (edge <= xf <= size - edge) or (edge <= yf <= size - edge)
            )
            in_corner = False
            if not inside_rect:
                for ccx, ccy in ((cx1, cy1), (cx2, cy2), (cx3, cy3), (cx4, cy4)):
                    if (xf - ccx) ** 2 + (yf - ccy) ** 2 <= r2:
                        in_corner = True
                        break
            if not (inside_rect or in_corner):
                continue  # 透明
            # 背景：圆角区域绿色
            base_a = 255
            # 粗略抗锯齿：对边缘像素半透明
            if inside_rect is False and in_corner:
                nearest = min(math.hypot(xf - ccx, yf - ccy)
                              for ccx, ccy in ((cx1, cy1), (cx2, cy2), (cx3, cy3), (cx4, cy4)))
                if nearest > edge - aa:
                    base_a = min(255, int(255 * max(0.0, (edge - nearest) / aa + 1.0)))
            d_check = min(dist_seg((xf, yf), p1, p2), dist_seg((xf, yf), p2, p3))
            if d_check <= half_t:
                # 对勾白色
                cr, cg, cb = 255, 255, 255
                alpha = 255
                if d_check > half_t - aa:
                    alpha = min(255, int(255 * max(0.0, (half_t - d_check) / aa + 1.0)))
                alpha = min(alpha, base_a)
            else:
                cr, cg, cb = GREEN
                alpha = base_a
            idx = (y * size + x) * 4
            px[idx] = cr
            px[idx + 1] = cg
            px[idx + 2] = cb
            px[idx + 3] = alpha
    return bytes(px)


def main():
    if len(sys.argv) < 2:
        print("用法：python3 make_icon.py <输出.icns>")
        return 1
    out_icns = sys.argv[1]
    tmp = tempfile.mkdtemp(prefix="autocheckin_iconset")
    iconset = os.path.join(tmp, "AppIcon.iconset")
    os.makedirs(iconset, exist_ok=True)
    specs = [(16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
             (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
             (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
             (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
             (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png")]
    for size, name in specs:
        write_png(os.path.join(iconset, name), size, make_icon(size))
    r = subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out_icns],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("iconutil 失败：", r.stderr, file=sys.stderr)
        return 1
    print("图标已生成：", out_icns)
    return 0


if __name__ == "__main__":
    sys.exit(main())
