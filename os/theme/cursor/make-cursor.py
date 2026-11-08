#!/usr/bin/env python3
"""The Orion cursor theme: a Starlight arrow, and a busy cursor that is a tiny spinning black hole disk.

    python3 os/theme/cursor/make-cursor.py      -> os/theme/cursor/Orion/  (needs Pillow, numpy, xcursorgen)

Only the arrow and the two busy cursors are drawn; every other shape (hand, text beam, resize arrows) comes from
Adwaita through index.theme's Inherits. Original drawings made by this script; licence CC0.
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "Orion")
SIZES = (24, 32, 48)
FRAMES = 12
DELAY_MS = 55
ALIASES = {
    "default": "left_ptr", "arrow": "left_ptr", "top_left_arrow": "left_ptr",
    "wait": "watch",
    "progress": "left_ptr_watch", "half-busy": "left_ptr_watch",
    "08e8e1c95fe2fc01f976f1e063a24ccd": "left_ptr_watch", "3ecb610c1bf2410f44200f48c40d3599": "left_ptr_watch",
}

STARLIGHT = (216, 222, 255, 255)
VOID = (5, 7, 14, 255)
ACCRETION = (124, 92, 255)
PHOTON = (0, 229, 255)

# arrow outline in a 32-unit box (tip at 1,1)
ARROW = [(1.5, 1.5), (1.5, 23.5), (7.0, 18.2), (10.6, 26.6), (14.2, 25.0), (10.7, 16.9), (18.3, 16.9)]


def arrow(size: int) -> Image.Image:
    k = 8
    s = size * k
    sc = s / 32
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(x * sc, y * sc) for x, y in ARROW]
    d.polygon(pts, fill=VOID)
    d.line(pts + [pts[0]], fill=VOID, width=int(2.6 * sc), joint="curve")
    # Starlight body, inset by the outline
    body = Image.new("L", (s, s), 0)
    ImageDraw.Draw(body).polygon(pts, fill=255)
    body = body.filter(ImageFilter.MinFilter(int(2.2 * sc) | 1))
    fill = Image.new("RGBA", (s, s), STARLIGHT)
    img.paste(fill, (0, 0), body)
    # a faint violet glow along the tip edge
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(glow).line(pts[:2], fill=ACCRETION + (110,), width=int(1.2 * sc))
    img = Image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(sc)), img)
    return img.resize((size, size), Image.LANCZOS)


def disk(size: int, frame: int) -> Image.Image:
    """A tiny black hole: dark centre, photon ring, a two-armed disk turning (inner faster)."""
    k = 4
    s = size * k
    y, x = np.mgrid[0:s, 0:s].astype(np.float32)
    c = (s - 1) / 2
    dx, dy = (x - c) / (s / 2), (y - c) / (s / 2)
    r = np.hypot(dx, dy)
    a = np.arctan2(dy, dx)
    turn = 2 * math.pi * frame / FRAMES
    # inner gas turns once per loop, outer gas half as fast (Keplerian-ish), and the loop stays seamless
    wind = 3.2 * np.log(np.maximum(r, 0.05))
    w = np.clip((r - 0.4) / 0.45, 0, 1)
    arms = 0.5 + 0.5 * ((1 - w) * np.cos(2 * (a + turn) - wind) + w * np.cos(2 * a + turn - wind))
    band = np.clip((r - 0.34) / 0.12, 0, 1) * np.clip((0.95 - r) / 0.25, 0, 1)
    dens = band * (0.35 + 0.65 * arms)
    hot = np.clip(1 - (r - 0.34) / 0.5, 0, 1)
    rgb = (np.array(PHOTON) * hot[..., None] + np.array(ACCRETION) * (1 - hot[..., None])) * dens[..., None] * 1.2
    ring = np.exp(-((r - 0.33) / 0.05) ** 2) * (0.7 + 0.3 * np.cos(a + turn))
    rgb += 255 * ring[..., None] * 0.9
    alpha = np.clip(dens * 1.1 + ring, 0, 1)
    hole = r < 0.30
    alpha = np.where(hole, 1.0, alpha) * (r < 1)
    rgb = np.where(hole[..., None], np.array(VOID[:3], np.float32), rgb)
    # a thin dark rim so it reads on light windows too
    rim = (np.abs(r - 0.97) < 0.04)
    alpha = np.maximum(alpha, rim * 0.5)
    img = Image.fromarray(np.dstack([np.clip(rgb, 0, 255), alpha * 255]).astype(np.uint8), "RGBA")
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    if not shutil.which("xcursorgen"):
        raise SystemExit("xcursorgen is missing (apt install x11-apps)")
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    cur = os.path.join(OUT, "cursors")
    os.makedirs(cur)
    with tempfile.TemporaryDirectory() as tmp:
        def gen(name: str, lines: list[str]) -> None:
            cfg = os.path.join(tmp, name + ".cfg")
            with open(cfg, "w") as f:
                f.write("\n".join(lines) + "\n")
            subprocess.run(["xcursorgen", cfg, os.path.join(cur, name)], check=True, cwd=tmp)

        lines = []
        for sz in SIZES:
            p = f"arrow-{sz}.png"
            arrow(sz).save(os.path.join(tmp, p))
            lines.append(f"{sz} {round(1.5 * sz / 32)} {round(1.5 * sz / 32)} {p}")
        gen("left_ptr", lines)

        wait, prog = [], []
        for sz in SIZES:
            for fr in range(FRAMES):
                p = f"disk-{sz}-{fr:02d}.png"
                disk(sz, fr).save(os.path.join(tmp, p))
                wait.append(f"{sz} {sz // 2} {sz // 2} {p} {DELAY_MS}")
                # progress: the arrow with the little disk at its lower right
                comb = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
                comb.alpha_composite(arrow(sz))
                d = disk(int(sz * 0.55), fr)
                comb.alpha_composite(d, (sz - d.width, sz - d.height))
                q = f"prog-{sz}-{fr:02d}.png"
                comb.save(os.path.join(tmp, q))
                prog.append(f"{sz} {round(1.5 * sz / 32)} {round(1.5 * sz / 32)} {q} {DELAY_MS}")
        gen("watch", wait)
        gen("left_ptr_watch", prog)
        # a preview strip for people (and for checking it)
        strip = Image.new("RGBA", (48 * (FRAMES + 2), 48), (11, 15, 30, 255))
        strip.alpha_composite(arrow(48), (0, 0))
        for fr in range(FRAMES):
            strip.alpha_composite(disk(48, fr), (48 * (fr + 1), 0))
        strip.save(os.path.join(HERE, "preview.png"))

    # The alias names (default, wait, progress, ...) are made as links when the theme is installed
    # (os/image/art-boot.sh reads aliases.txt): no symlinks in git, so a Windows checkout keeps them working.
    with open(os.path.join(OUT, "aliases.txt"), "w") as f:
        f.write("# alias target\n" + "".join(f"{a} {t}\n" for a, t in ALIASES.items()))
    with open(os.path.join(OUT, "index.theme"), "w") as f:
        f.write("[Icon Theme]\nName=Orion\nComment=Orion cursors: a Starlight arrow and a tiny spinning black hole\n"
                "Inherits=Adwaita\n")
    with open(os.path.join(OUT, "cursor.theme"), "w") as f:
        f.write("[Icon Theme]\nName=Orion\nInherits=Adwaita\n")
    print("cursor theme:", OUT)


if __name__ == "__main__":
    main()
