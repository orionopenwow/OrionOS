#!/usr/bin/env python3
"""Small boot-chain pictures for OrionOS, drawn from code (no downloaded art). Re-run after changing them:

    python3 os/theme/boot/make-assets.py

Writes
  os/image/files/usr/share/plymouth/themes/orion/  star.png, star-cyan.png, orbit.png   (vault prompt)
  os/image/grub-theme/                             background.png, mark.png, select_*.png (GRUB menu)
Needs Pillow and numpy. The outputs are committed, so the image build doesn't need either.
"""
from __future__ import annotations

import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
PLY = os.path.join(REPO, "os/image/files/usr/share/plymouth/themes/orion")
GRUB = os.path.join(REPO, "os/image/grub-theme")

VOID = (5, 7, 14)
ACCRETION = (124, 92, 255)
PHOTON = (0, 229, 255)
STARLIGHT = (216, 222, 255)


def soft_star(size: int, color, core: float, glint: float) -> Image.Image:
    """A soft round star with a faint four-point glint (RGBA, premultiplied look)."""
    s = size * 4
    y, x = np.mgrid[0:s, 0:s].astype(np.float32)
    c = (s - 1) / 2
    r = np.hypot(x - c, y - c) / (s / 2)
    a = np.exp(-(r / core) ** 2) + 0.35 * np.exp(-(r / (core * 2.6)) ** 2)
    cross = np.exp(-(np.abs(x - c) / (s * 0.018)) ** 2) * np.exp(-(np.abs(y - c) / (s * glint)) ** 2)
    cross += np.exp(-(np.abs(y - c) / (s * 0.018)) ** 2) * np.exp(-(np.abs(x - c) / (s * glint)) ** 2)
    a = np.clip(a + 0.5 * cross, 0, 1) * (r < 1)
    rgb = np.zeros((s, s, 3), np.float32)
    white = np.clip(np.exp(-(r / (core * 0.6)) ** 2), 0, 1)[..., None]
    rgb[:] = np.array(color, np.float32) * (1 - white) + 255 * white
    img = Image.fromarray(np.dstack([rgb, a * 255]).astype(np.uint8), "RGBA")
    return img.resize((size, size), Image.LANCZOS)


def orbit(m: int = 720) -> Image.Image:
    """The near edge of an ellipse around the hole (rx 0.80, ry 0.45 of the short side), fading at the ends.
    orion.script puts the image's bottom (minus 3 px) at the ellipse's lowest point."""
    rx, ry, half = 0.80 * m, 0.45 * m, 0.40 * m
    k = 4
    w, h = int(2 * half) + 8, int(ry - ry * math.sqrt(1 - (half / rx) ** 2)) + 14
    big = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    bottom = (h - 3) * k
    pts = []
    for i in range(-400, 401):
        dx = half * i / 400
        pts.append(((w / 2 + dx) * k, bottom - (ry - ry * math.sqrt(1 - (dx / rx) ** 2)) * k, abs(i) / 400))
    for (x0, y0, t0), (x1, y1, _) in zip(pts, pts[1:]):
        a = int(255 * (1 - t0 ** 2.2) * 0.9)
        d.line([(x0, y0), (x1, y1)], fill=ACCRETION + (a,), width=int(1.6 * k))
    glow = big.filter(ImageFilter.GaussianBlur(4 * k))
    out = Image.alpha_composite(glow, big)
    return out.resize((w, h), Image.LANCZOS)


def starfield(w: int, h: int, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3), np.float32)
    img[:] = np.array(VOID, np.float32) * 0.6
    # a faint violet haze where the hole sits (behind the mark)
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = w / 2, h * 0.27
    r = np.hypot((x - cx) / w, (y - cy) / h)
    img += np.array(ACCRETION, np.float32)[None, None, :] * (0.10 * np.exp(-(r / 0.22) ** 2))[..., None]
    img += np.array(PHOTON, np.float32)[None, None, :] * (0.025 * np.exp(-(r / 0.12) ** 2))[..., None]
    tints = np.array([(255, 255, 255), STARLIGHT, (200, 190, 255), (170, 240, 255), (255, 236, 220)], np.float32)
    n = int(w * h / 900)
    xs, ys = rng.uniform(0, w, n), rng.uniform(0, h, n)
    z = rng.random(n) ** 2.4
    for xi, yi, zi in zip(xs, ys, z):
        rad = 0.6 + zi * 1.8
        bri = 0.25 + zi * 0.75
        col = tints[rng.integers(0, len(tints))]
        x0, x1 = int(max(0, xi - 4)), int(min(w, xi + 5))
        y0, y1 = int(max(0, yi - 4)), int(min(h, yi + 5))
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        a = np.exp(-(((xx - xi) ** 2 + (yy - yi) ** 2) / (rad ** 2)))[..., None] * bri
        img[y0:y1, x0:x1] += col * a
    return np.clip(img, 0, 255).astype(np.uint8)


def mark(size: int = 160) -> Image.Image:
    """The Orion mark: a black hole with its photon ring, brighter on one side, and Orion's belt (three stars)
    drifting toward its edge. (Provisional: art-core's brand mark replaces it when it lands.)"""
    k = 4
    s = size * k
    y, x = np.mgrid[0:s, 0:s].astype(np.float32)
    c = s / 2
    dx, dy = (x - c) / (s / 2), (y - c) / (s / 2)
    r = np.hypot(dx, dy)
    ang = np.arctan2(dy, dx)
    R = 0.46
    ring = np.exp(-((r - R) / 0.035) ** 2) * (0.55 + 0.45 * np.cos(ang - math.pi * 0.85))
    halo = np.exp(-((r - R) / 0.16) ** 2) * 0.35 * (0.6 + 0.4 * np.cos(ang - math.pi * 0.85))
    rgb = (np.array(PHOTON, np.float32) * 0.55 + 255 * 0.45) * ring[..., None]
    rgb += np.array(ACCRETION, np.float32) * halo[..., None]
    alpha = np.clip(ring + halo, 0, 1)
    inside = r < R - 0.02
    alpha = np.where(inside, 1.0, alpha)
    rgb = np.where(inside[..., None], 0.0, rgb)
    img = Image.fromarray(np.dstack([np.clip(rgb, 0, 255), alpha * 255]).astype(np.uint8), "RGBA")
    # the belt: three stars in a slanted line, toward the upper right edge
    for i, (bx, by, bs) in enumerate([(0.70, 0.30, 0.060), (0.80, 0.215, 0.052), (0.90, 0.13, 0.068)]):
        st = soft_star(int(s * bs * 2.4), STARLIGHT if i != 1 else (200, 245, 255), 0.32, 0.30)
        img.alpha_composite(st, (int(s * bx - st.width / 2), int(s * by - st.height / 2)))
    return img.resize((size, size), Image.LANCZOS)


def select_pixmaps() -> dict[str, Image.Image]:
    """9-slice pieces for GRUB's highlighted menu entry: a violet glass pill (only w/c/e are needed)."""
    h, k = 42, 4
    rad = h // 2
    full = Image.new("RGBA", (rad * 2 * k + 8 * k, h * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(full)
    d.rounded_rectangle([0, 0, full.width - 1, full.height - 1], radius=rad * k,
                        fill=ACCRETION + (150,), outline=(157, 133, 255, 235), width=2 * k)
    full = full.resize((full.width // k, h), Image.LANCZOS)
    return {
        "select_w.png": full.crop((0, 0, rad, h)),
        "select_c.png": full.crop((rad, 0, rad + 8, h)),
        "select_e.png": full.crop((full.width - rad, 0, full.width, h)),
    }


def main() -> None:
    os.makedirs(GRUB, exist_ok=True)
    soft_star(15, STARLIGHT, 0.22, 0.42).save(os.path.join(PLY, "star.png"), optimize=True)
    soft_star(21, PHOTON, 0.22, 0.48).save(os.path.join(PLY, "star-cyan.png"), optimize=True)
    orbit().save(os.path.join(PLY, "orbit.png"), optimize=True)
    # GRUB reads 8-bit RGB / RGBA PNGs (no palette), so these stay true-colour.
    Image.fromarray(starfield(1920, 1080), "RGB").save(os.path.join(GRUB, "background.png"), optimize=True)
    mark(160).save(os.path.join(GRUB, "mark.png"), optimize=True)
    for name, im in select_pixmaps().items():
        im.save(os.path.join(GRUB, name), optimize=True)
    print("assets written:", PLY, GRUB)


if __name__ == "__main__":
    main()
