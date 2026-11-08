#!/usr/bin/env python3
"""Regenerate the "Orion Glass" GTK 3/4 theme from GTK's own Adwaita-dark (Art Update §4, §7).

    python3 os/theme/gtk/build-orion-glass.py [--gtk3 FILE] [--gtk4 FILE]

Without arguments it extracts Adwaita-dark from the installed libgtk (needs `gresource`, package libglib2.0-bin):
  GTK 3: resource:///org/gtk/libgtk/theme/Adwaita/gtk-contained-dark.css   (libgtk-3.so.0)
  GTK 4: resource:///org/gtk/libgtk/theme/Default/Default-dark.css          (libgtk-4.so.1)
The committed files were made from Ubuntu 24.04's GTK 3.24.41 and GTK 4.14.5.

Every colour in Adwaita-dark is mapped onto the Orion palette (greys -> Void/Event/Edge/Starlight by lightness,
blue accent -> Accretion violet, green -> aurora, orange -> ember, red -> rose), asset URLs are pointed back at
GTK's built-in resources, and orion-*.css (hand-written: the see-through glass, rounding, belt-dot window buttons,
Thunar) is layered on top. Orion-Glass-Solid is the opaque twin used under plain Sway (no blur).
Licence: derived from GTK's Adwaita theme, LGPL-2.1-or-later (see LICENSE in each theme folder).
"""
import argparse
import colorsys
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ARCH_LIB = "/usr/lib/x86_64-linux-gnu"
SOURCES = {
    "gtk-3.0": (f"{ARCH_LIB}/libgtk-3.so.0", "/org/gtk/libgtk/theme/Adwaita/gtk-contained-dark.css",
                "resource:///org/gtk/libgtk/theme/Adwaita/"),
    "gtk-4.0": (f"{ARCH_LIB}/libgtk-4.so.1", "/org/gtk/libgtk/theme/Default/Default-dark.css",
                "resource:///org/gtk/libgtk/theme/Default/"),
}


def hexrgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def ramp(stops, v):
    """stops: [(position, (r,g,b)), ...] sorted; piecewise-linear lookup."""
    if v <= stops[0][0]:
        return stops[0][1]
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if v <= p1:
            return lerp(c0, c1, (v - p0) / (p1 - p0))
    return stops[-1][1]


# Adwaita-dark lightness -> Orion colour. Adwaita's borders (#1b1b1b) are darker than its surfaces; Orion's are a
# thin light edge (Edge #262D50), hence the bump between 0.05 and 0.135.
GREYS = [
    (0.000, hexrgb("#02030A")), (0.045, hexrgb("#03040B")),
    (0.060, hexrgb("#1A1F3A")), (0.106, hexrgb("#262D50")), (0.125, hexrgb("#1E2440")),
    (0.135, hexrgb("#0C1022")), (0.150, hexrgb("#070A14")), (0.177, hexrgb("#080B17")),
    (0.196, hexrgb("#151A30")), (0.208, hexrgb("#0B0F1E")), (0.216, hexrgb("#171C36")),
    (0.250, hexrgb("#1E2546")), (0.330, hexrgb("#3A4170")), (0.360, hexrgb("#4A5280")),
    (0.570, hexrgb("#8A90B8")), (0.840, hexrgb("#C3C9EE")), (0.930, hexrgb("#D8DEFF")),
    (1.000, hexrgb("#FFFFFF")),
]
# Adwaita's blue accent (#15539e, L 0.35) -> Accretion #7C5CFF and its lighter/darker steps.
VIOLET = [
    (0.00, hexrgb("#05030F")), (0.05, hexrgb("#0E0826")), (0.15, hexrgb("#2A1D6E")), (0.25, hexrgb("#5B3FD9")),
    (0.35, hexrgb("#7C5CFF")), (0.45, hexrgb("#8E72FF")), (0.55, hexrgb("#A38FFF")), (0.70, hexrgb("#C2B3FF")),
    (0.90, hexrgb("#E6E0FF")), (1.00, hexrgb("#FFFFFF")),
]


def accent(base_hex, anchor_l, l):
    """Same hue/saturation as the palette colour, lightness scaled like Adwaita's step."""
    r, g, b = hexrgb(base_hex)
    h, bl, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    nl = max(0.02, min(0.97, bl * (l / anchor_l))) if l < anchor_l else bl + (1 - bl) * (l - anchor_l) / max(1e-6, 1 - anchor_l)
    rr, gg, bb = colorsys.hls_to_rgb(h, nl, s)
    return (round(rr * 255), round(gg * 255), round(bb * 255))


def map_rgb(rgb):
    r, g, b = rgb
    if (r, g, b) == (255, 255, 255):
        return rgb
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    hue = h * 360
    if s < 0.12 or l < 0.02:
        return ramp(GREYS, l)
    if 195 <= hue <= 235:                      # Adwaita blue -> Orion violet
        return ramp(VIOLET, l)
    if 80 <= hue < 170:                        # success green -> aurora mint
        return accent("#5CFFB0", 0.31, l)
    if 20 <= hue < 60:                         # warning orange -> ember (the only warm colour)
        return accent("#FFC66B", 0.48, l)
    if hue < 20 or hue >= 330:                 # error red -> rose
        return accent("#FF5C8A", 0.40, l)
    return ramp(VIOLET, l)


def fmt(rgb):
    return "#%02X%02X%02X" % rgb


def recolour(css):
    def hx(m):
        v = m.group(1)
        if len(v) == 3:
            v = "".join(c * 2 for c in v)
        return fmt(map_rgb(hexrgb(v)))

    def rgba(m):
        r, g, b, a = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
        if (r, g, b) == (0, 0, 0):
            nr = (2, 3, 10)
        elif (r, g, b) == (255, 255, 255):
            nr = (255, 255, 255)
        else:
            nr = map_rgb((r, g, b))
        return "rgba(%d, %d, %d, %s)" % (nr + (a,))

    css = re.sub(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b(?![-\w])", hx, css)
    css = re.sub(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", rgba, css)
    css = re.sub(r"\bcolor: white;", "color: #FFFFFF;", css)
    return css


def extract(lib, path):
    return subprocess.run(["gresource", "extract", lib, path], check=True, capture_output=True, text=True).stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gtk3")
    ap.add_argument("--gtk4")
    args = ap.parse_args()
    given = {"gtk-3.0": args.gtk3, "gtk-4.0": args.gtk4}
    theme = os.path.join(HERE, "Orion-Glass")
    for ver, (lib, res, res_base) in SOURCES.items():
        src = open(given[ver]).read() if given[ver] else extract(lib, res)
        out = recolour(src)
        # Assets (check marks, sliders...) stay GTK's built-in ones.
        out = re.sub(r'url\("assets/', f'url("{res_base}assets/', out)
        head = (f"/* Orion Glass: GTK's Adwaita-dark ({ver}) recoloured onto the Orion palette by build-orion-glass.py.\n"
                " * Generated file: edit orion-glass.css / build-orion-glass.py instead. LGPL-2.1-or-later (from GTK). */\n")
        d = os.path.join(theme, ver)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "adwaita-orion.css"), "w") as f:
            f.write(head + out)
        print(f"{ver}: {len(out) // 1024} KiB")


if __name__ == "__main__":
    sys.exit(main())
