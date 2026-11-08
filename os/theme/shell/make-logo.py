#!/usr/bin/env python3
"""The small ANSI black hole that `orion` (and fastfetch, when installed) shows next to the system info.

    python3 os/theme/shell/make-logo.py     -> os/theme/shell/orion-logo.ansi  (truecolor half blocks, 22x9 cells)
"""
import math
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
COLS, ROWS = 22, 9             # each cell = 2 vertical pixels (about square)
PX_W, PX_H = COLS, ROWS * 2
ACC = np.array([124, 92, 255], float)
PHO = np.array([0, 229, 255], float)
STAR = np.array([216, 222, 255], float)


def pixel(x: float, y: float):
    """RGBA of the mini hole at (x, y) in -1..1 (y down), with a tilted disk passing in front of the shadow."""
    r = math.hypot(x, y)
    a = math.atan2(y, x)
    col = np.zeros(3)
    alpha = 0.0
    # tilted disk (ellipse ry = 0.32 rx)
    dy = y / 0.34
    dr = math.hypot(x, dy)
    if 0.42 < dr < 1.0:
        k = (dr - 0.42) / 0.58
        arms = 0.55 + 0.45 * math.cos(2 * math.atan2(dy, x) - 5 * math.log(dr))
        dens = (1 - k) ** 0.8 * arms
        dop = 0.75 + 0.35 * (-x)                      # brighter on the side coming toward you (left)
        c = PHO * (1 - k) + ACC * k
        col += c * dens * dop * 1.25
        alpha = max(alpha, min(1, dens * 2.2))
    # shadow and photon ring, drawn over the far half of the disk only
    front = y > 0 and 0.42 < dr < 1.0
    if r < 0.27 and not front:
        return np.zeros(3), 1.0
    ring = math.exp(-((r - 0.34) / 0.05) ** 2) * (0.65 + 0.35 * math.cos(a - math.pi))
    if not front:
        col += (STAR * 0.6 + PHO * 0.4) * ring
        alpha = max(alpha, min(1, ring * 1.4))
    halo = math.exp(-((r - 0.45) / 0.14) ** 2) * 0.35
    col += ACC * halo
    alpha = max(alpha, halo)
    return np.clip(col, 0, 255), alpha


def main() -> None:
    img = np.zeros((PX_H, PX_W, 4))
    for j in range(PX_H):
        for i in range(PX_W):
            acc_c, acc_a = np.zeros(3), 0.0
            for sy in (0.25, 0.75):                    # 2x2 supersampling
                for sx in (0.25, 0.75):
                    x = (i + sx) / PX_W * 2 - 1
                    y = ((j + sy) / PX_H * 2 - 1) * PX_H / PX_W
                    c, a = pixel(x * 0.92, y * 0.92)
                    acc_c += c * a
                    acc_a += a
            img[j, i, 3] = acc_a / 4
            img[j, i, :3] = acc_c / max(acc_a, 1e-6)
    esc = "\x1b["
    lines = []
    for row in range(ROWS):
        s = ""
        for i in range(COLS):
            top, bot = img[row * 2, i], img[row * 2 + 1, i]
            tv, bv = top[3] > 0.10, bot[3] > 0.10
            t = (top[:3] * min(1, top[3] * 1.3)).astype(int)
            b = (bot[:3] * min(1, bot[3] * 1.3)).astype(int)
            if tv and bv:
                s += f"{esc}38;2;{t[0]};{t[1]};{t[2]}m{esc}48;2;{b[0]};{b[1]};{b[2]}m▀"
            elif tv:
                s += f"{esc}49m{esc}38;2;{t[0]};{t[1]};{t[2]}m▀"
            elif bv:
                s += f"{esc}49m{esc}38;2;{b[0]};{b[1]};{b[2]}m▄"
            else:
                s += f"{esc}0m "
        lines.append(s + f"{esc}0m")
    with open(os.path.join(HERE, "orion-logo.ansi"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
