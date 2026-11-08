#!/usr/bin/env python3
"""Generate Orion's brand SVGs (mark, small mark, light-paper mark, lockups) into os/theme/brand/svg/.

The mark is the black hole as a circle with its photon ring, bright on the side turning toward you, and Orion's
belt (three stars) drifting toward its edge. Lockup text is Unbounded, outlined to paths with fontTools so the
SVGs render the same everywhere without the font installed.

    python3 os/theme/brand/build.py        # then: node os/theme/brand/export.mjs  (PNG exports)
"""
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

HERE = Path(__file__).resolve().parent
FONTS = HERE.parent / "fonts"
OUT = HERE / "svg"

VOID, EVENT, EDGE = "#05070E", "#0B0F1E", "#262D50"
ACCRETION, PHOTON, STARLIGHT, EMBER = "#7C5CFF", "#00E5FF", "#D8DEFF", "#FFC66B"

C = 256  # centre of the 512 design grid
R = 100  # photon ring radius
# Orion's belt: Alnitak, Alnilam, Mintaka, the last one closest to the ring (and slightly stretched toward it)
BELT = [(432, 82, 11.5), (385, 123, 10.5), (340, 166, 9.5)]


def star(x, y, r, color, glow_id, sparkle=False, stretch=1.0):
    """A soft star: blurred glow, bright core, optional 4-point sparkle. `stretch` elongates it toward the hole."""
    import math

    ang = math.degrees(math.atan2(C - y, C - x))
    parts = [
        f'<g transform="translate({x} {y}) rotate({ang:.1f})">',
        f'<ellipse rx="{r * 2.3 * stretch:.1f}" ry="{r * 2.3:.1f}" fill="{color}" opacity="0.35" filter="url(#{glow_id})"/>',
        f'<ellipse rx="{r * stretch:.1f}" ry="{r:.1f}" fill="{color}"/>',
        f'<ellipse rx="{r * 0.55 * stretch:.1f}" ry="{r * 0.55:.1f}" fill="#FFFFFF"/>',
    ]
    parts.append("</g>")
    if sparkle:  # upright 4-point glint (not rotated with the stretch)
        L = r * 3.0
        w = r * 0.26
        parts.append(
            f'<path transform="translate({x} {y})" d="M{-L:.1f} 0 L0 {-w:.1f} L{L:.1f} 0 L0 {w:.1f}Z '
            f'M0 {-L:.1f} L{w:.1f} 0 L0 {L:.1f} L{-w:.1f} 0Z" fill="#FFFFFF" opacity="0.8"/>'
        )
    return "".join(parts)


def defs(prefix, light=False, small=False):
    if light:
        ring_stops = [(0, "#00A3C4", 1), (0.35, "#0090B8", 1), (0.75, ACCRETION, 0.9), (1, ACCRETION, 0.55)]
    else:
        ring_stops = [(0, "#F4FDFF", 1), (0.22, PHOTON, 1), (0.62, ACCRETION, 0.85), (1, ACCRETION, 0.32)]
    stops = "".join(f'<stop offset="{o}" stop-color="{c}" stop-opacity="{a}"/>' for o, c, a in ring_stops)
    blur = 3 if small else 6
    return f"""<defs>
<linearGradient id="{prefix}ring" x1="{C - R}" y1="{C}" x2="{C + R}" y2="{C}" gradientUnits="userSpaceOnUse">{stops}</linearGradient>
<radialGradient id="{prefix}halo" cx="{C}" cy="{C}" r="200" gradientUnits="userSpaceOnUse">
<stop offset="0.45" stop-color="{ACCRETION}" stop-opacity="0.55"/><stop offset="0.7" stop-color="{ACCRETION}" stop-opacity="0.16"/><stop offset="1" stop-color="{ACCRETION}" stop-opacity="0"/></radialGradient>
<radialGradient id="{prefix}bg" cx="{C}" cy="{C - 20}" r="380" gradientUnits="userSpaceOnUse">
<stop offset="0" stop-color="#151B3A"/><stop offset="0.55" stop-color="{EVENT}"/><stop offset="1" stop-color="{VOID}"/></radialGradient>
<filter id="{prefix}glow" x="-1" y="-1" width="3" height="3"><feGaussianBlur stdDeviation="{blur}"/></filter>
<filter id="{prefix}ringglow" x="-0.5" y="-0.5" width="2" height="2"><feGaussianBlur stdDeviation="{blur * 1.6}"/></filter>
</defs>"""


def mark_body(prefix, light=False, small=False):
    """The mark on the 512 grid, without background."""
    ring_w = 26 if small else 9
    out = []
    if not light:
        out.append(f'<circle cx="{C}" cy="{C}" r="200" fill="url(#{prefix}halo)"/>')
        out.append(
            f'<circle cx="{C}" cy="{C}" r="{R}" fill="none" stroke="url(#{prefix}ring)" stroke-width="{ring_w * 2.6}" '
            f'opacity="0.55" filter="url(#{prefix}ringglow)"/>'
        )
    # event horizon
    out.append(f'<circle cx="{C}" cy="{C}" r="{R - ring_w / 2 + 0.5}" fill="{VOID if light else "#000000"}"/>')
    # faint secondary ring just outside (dropped at small sizes)
    if not small:
        out.append(
            f'<circle cx="{C}" cy="{C}" r="{R + 15}" fill="none" stroke="{ACCRETION}" stroke-width="2.2" '
            f'stroke-opacity="{0.55 if light else 0.4}"/>'
        )
    # photon ring, bright on the left (approaching side)
    out.append(f'<circle cx="{C}" cy="{C}" r="{R}" fill="none" stroke="url(#{prefix}ring)" stroke-width="{ring_w}"/>')
    # Orion's belt
    star_col = EDGE if light else STARLIGHT
    for i, (x, y, r) in enumerate(BELT):
        rr = r * (2.3 if small else 1.0)
        if light:
            out.append(f'<circle cx="{x}" cy="{y}" r="{rr}" fill="{star_col}"/>')
        else:
            out.append(star(x, y, rr, star_col, f"{prefix}glow", sparkle=(i == 0 and not small), stretch=1.35 if i == 2 else 1.0))
    return "\n".join(out)


def svg(w, h, body, view=None):
    vb = view or f"0 0 {w} {h}"
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="{vb}">\n{body}\n</svg>\n'


def icon(prefix="i", small=False, radius=112):
    """App icon: the mark on a deep-space rounded square."""
    bg = f'<rect width="512" height="512" rx="{radius}" fill="url(#{prefix}bg)"/>'
    dust = ""
    if not small:
        pts = [(70, 120, 1.6), (110, 400, 1.2), (190, 70, 1.0), (460, 300, 1.4), (420, 450, 1.1), (60, 280, 1.0),
               (300, 470, 1.3), (480, 190, 0.9), (150, 210, 0.8)]
        dust = "".join(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{STARLIGHT}" opacity="0.7"/>' for x, y, r in pts)
    # the belt weights the mark to the top right, so the icon nudges it down-left to sit optically centred
    body = f'<g transform="translate(-16 16)">{mark_body(prefix, small=small)}</g>'
    return svg(512, 512, defs(prefix, small=small) + bg + dust + body)


# ------------------------------------------------------------------ text outlines
class Outliner:
    def __init__(self, path):
        self.font = TTFont(path)
        self.gs = self.font.getGlyphSet()
        self.cmap = self.font.getBestCmap()
        self.upm = self.font["head"].unitsPerEm
        self.kern = self._kerning()

    def _kerning(self):
        # simple pair kerning from GPOS PairPos format 1 (enough for lowercase "orion os")
        pairs = {}
        if "GPOS" not in self.font:
            return pairs
        for lookup in self.font["GPOS"].table.LookupList.Lookup:
            for st in lookup.SubTable:
                st = getattr(st, "ExtSubTable", st)
                if getattr(st, "LookupType", None) != 2 and type(st).__name__ != "PairPos":
                    continue
                if getattr(st, "Format", 0) == 1:
                    for i, first in enumerate(st.Coverage.glyphs):
                        for pvr in st.PairSet[i].PairValueRecord:
                            v = getattr(pvr.Value1, "XAdvance", 0) if pvr.Value1 else 0
                            if v:
                                pairs[(first, pvr.SecondGlyph)] = v
        return pairs

    def text(self, s, size, x, baseline, tracking=0.0, fill=STARLIGHT):
        scale = size / self.upm
        paths = []
        pen_x = x
        prev = None
        for ch in s:
            g = self.cmap.get(ord(ch))
            if g is None:
                continue
            if prev:
                pen_x += self.kern.get((prev, g), 0) * scale
            if ch != " ":
                sp = SVGPathPen(self.gs)
                self.gs[g].draw(TransformPen(sp, (scale, 0, 0, -scale, pen_x, baseline)))
                paths.append(sp.getCommands())
            pen_x += self.gs[g].width * scale + tracking * size
            prev = g
        d = " ".join(p for p in paths if p)
        return f'<path d="{d}" fill="{fill}"/>', pen_x - tracking * size

    def cap_height(self, size):
        os2 = self.font["OS/2"]
        h = getattr(os2, "sxHeight", 0) or self.upm * 0.5
        return h * size / self.upm


def lockup(word, suffix, light=False, prefix="l"):
    """Horizontal lockup: mark, then the wordmark in Unbounded (x-height centred on the mark)."""
    semi = Outliner(FONTS / "Unbounded-SemiBold.ttf")
    reg = Outliner(FONTS / "Unbounded-Regular.ttf")
    mark_h = 512
    size = 230
    x0 = 470
    xh = semi.cap_height(size)
    baseline = C + xh / 2
    fg = VOID if light else STARLIGHT
    p1, end = semi.text(word, size, x0, baseline, tracking=0.02, fill=fg)
    body = [defs(prefix, light=light), mark_body(prefix, light=light), p1]
    if suffix:
        p2, end = reg.text(" " + suffix, size, end, baseline, tracking=0.02, fill=ACCRETION if not light else "#5B3FE0")
        body.append(p2)
    width = int(end + 60)
    return svg(width, mark_h, "\n".join(body))


def stacked(word, light=False, prefix="s"):
    """Stacked lockup (splash / boot): mark above, wordmark below."""
    semi = Outliner(FONTS / "Unbounded-SemiBold.ttf")
    size = 120
    p, end = semi.text(word, size, 0, 0, tracking=0.04, fill=VOID if light else STARLIGHT)
    w = end
    body = defs(prefix, light=light) + mark_body(prefix, light=light)
    text = f'<g transform="translate({C - w / 2:.1f} 640)">{p}</g>'
    return svg(512, 700, body + text)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    files = {
        "orion-icon.svg": icon("i"),
        "orion-icon-small.svg": icon("k", small=True, radius=96),
        "orion-mark.svg": svg(512, 512, defs("m") + mark_body("m")),
        "orion-mark-small.svg": svg(512, 512, defs("n", small=True) + mark_body("n", small=True)),
        "orion-mark-light.svg": svg(512, 512, defs("p", light=True) + mark_body("p", light=True)),
        "orion-lockup.svg": lockup("orion", None),
        "orion-os-lockup.svg": lockup("orion", "os", prefix="o"),
        "orion-lockup-light.svg": lockup("orion", None, light=True, prefix="q"),
        "orion-os-lockup-light.svg": lockup("orion", "os", light=True, prefix="r"),
        "orion-stacked.svg": stacked("orion"),
    }
    for name, content in files.items():
        (OUT / name).write_text(content)
        print("wrote", OUT / name)


if __name__ == "__main__":
    main()
