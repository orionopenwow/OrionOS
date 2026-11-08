"""A small QR code encoder that draws an SVG, in pure Python (no network, no extra packages).

Used by the HUD's "Pair my phone" panel (phone_pairing_ui.py): the phone's camera reads the code and opens
orion://pair?u=...&c=... in Orion Mobile.

Byte mode only, all 40 versions, error correction L/M/Q/H, automatic mask choice. The steps follow the QR code
standard (ISO/IEC 18004) the same way Project Nayuki's "QR Code generator library" does (MIT licence,
https://www.nayuki.io/page/qr-code-generator-library); this file is a fresh, shortened write-up of those steps.

    svg = qr_svg.svg("orion://pair?u=http://192.168.1.20:9121&c=ABCD-...")
    rows = qr_svg.matrix("hello")            # list of rows of booleans (True = dark)
"""
from __future__ import annotations

from typing import Optional

# Error correction level -> (index into the tables, the two "format" bits)
_ECL = {"L": (0, 1), "M": (1, 0), "Q": (2, 3), "H": (3, 2)}

# Per version 1..40 (index 0 unused): error correction codewords per block, and number of blocks.
_ECC_PER_BLOCK = (
    (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30,
     30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28,
     28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30,
     30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30,
     30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
)
_NUM_BLOCKS = (
    (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18,
     19, 19, 20, 21, 22, 24, 25),
    (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31,
     33, 35, 37, 38, 40, 43, 45, 47, 49),
    (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40,
     43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48,
     51, 54, 57, 60, 63, 66, 70, 74, 77, 81),
)


# ===================================================================================== sizes
def _raw_modules(ver: int) -> int:
    """Data + error correction bits a version holds (everything that isn't a fixed pattern)."""
    n = (16 * ver + 128) * ver + 64
    if ver >= 2:
        na = ver // 7 + 2
        n -= (25 * na - 10) * na - 55
        if ver >= 7:
            n -= 36
    return n


def _data_codewords(ver: int, e: int) -> int:
    return _raw_modules(ver) // 8 - _ECC_PER_BLOCK[e][ver] * _NUM_BLOCKS[e][ver]


# ===================================================================================== Reed-Solomon over GF(256)
def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


# ===================================================================================== encoding
def _codewords(data: bytes, ver: int, e: int) -> list[int]:
    cap_bits = _data_codewords(ver, e) * 8
    bits: list[int] = []

    def put(val: int, n: int) -> None:
        bits.extend((val >> i) & 1 for i in reversed(range(n)))

    put(0b0100, 4)                                   # byte mode
    put(len(data), 8 if ver <= 9 else 16)
    for b in data:
        put(b, 8)
    put(0, min(4, cap_bits - len(bits)))             # terminator
    put(0, (-len(bits)) % 8)
    pad = 0xEC
    while len(bits) < cap_bits:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    out = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]

    # split into blocks, add error correction to each, then interleave
    nblocks, ecc_len = _NUM_BLOCKS[e][ver], _ECC_PER_BLOCK[e][ver]
    raw = _raw_modules(ver) // 8
    n_short = nblocks - raw % nblocks
    short_len = raw // nblocks
    div = _rs_divisor(ecc_len)
    blocks, k = [], 0
    for i in range(nblocks):
        dat = out[k:k + short_len - ecc_len + (0 if i < n_short else 1)]
        k += len(dat)
        ecc = _rs_remainder(dat, div)
        if i < n_short:
            dat = dat + [0]                          # placeholder so every block has the same length
        blocks.append(dat + ecc)
    result = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != short_len - ecc_len or j >= n_short:
                result.append(blk[i])
    return result


def _align_positions(ver: int, size: int) -> list[int]:
    if ver == 1:
        return []
    na = ver // 7 + 2
    step = (ver * 8 + na * 3 + 5) // (na * 4 - 4) * 2
    return [6] + sorted(size - 7 - i * step for i in range(na - 1))


class _Grid:
    def __init__(self, ver: int) -> None:
        self.ver = ver
        self.size = ver * 4 + 17
        self.m = [[False] * self.size for _ in range(self.size)]
        self.fn = [[False] * self.size for _ in range(self.size)]

    def set_fn(self, x: int, y: int, dark: bool) -> None:
        self.m[y][x] = dark
        self.fn[y][x] = True

    def function_patterns(self) -> None:
        s = self.size
        for i in range(s):
            self.set_fn(6, i, i % 2 == 0)
            self.set_fn(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (s - 4, 3), (3, s - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < s and 0 <= y < s:
                        self.set_fn(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _align_positions(self.ver, s)
        last = len(pos) - 1
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_fn(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.format_bits(0, 0)       # reserve the areas; real bits are drawn after masking
        if self.ver >= 7:
            rem = self.ver
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.ver << 12 | rem
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = s - 11 + i % 3, i // 3
                self.set_fn(a, b, bit)
                self.set_fn(b, a, bit)

    def format_bits(self, ecl_bits: int, mask: int) -> None:
        data = ecl_bits << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1 == 1  # noqa: E731
        s = self.size
        for i in range(6):
            self.set_fn(8, i, bit(i))
        self.set_fn(8, 7, bit(6))
        self.set_fn(8, 8, bit(7))
        self.set_fn(7, 8, bit(8))
        for i in range(9, 15):
            self.set_fn(14 - i, 8, bit(i))
        for i in range(8):
            self.set_fn(s - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set_fn(8, s - 15 + i, bit(i))
        self.set_fn(8, s - 8, True)

    def place(self, data: list[int]) -> None:
        s, i, total = self.size, 0, len(data) * 8
        right = s - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(s):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = s - 1 - vert if upward else vert
                    if not self.fn[y][x] and i < total:
                        self.m[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        f = (
            lambda x, y: (x + y) % 2 == 0,
            lambda x, y: y % 2 == 0,
            lambda x, y: x % 3 == 0,
            lambda x, y: (x + y) % 3 == 0,
            lambda x, y: (x // 3 + y // 2) % 2 == 0,
            lambda x, y: x * y % 2 + x * y % 3 == 0,
            lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
            lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
        )[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.fn[y][x] and f(x, y):
                    self.m[y][x] = not self.m[y][x]

    def penalty(self) -> int:
        """The standard's four penalty rules: long runs, 2x2 blocks, finder look-alikes, dark/light balance."""
        s, m, score = self.size, self.m, 0
        lines = [row for row in m] + [[m[y][x] for y in range(s)] for x in range(s)]
        pat1 = [True, False, True, True, True, False, True, False, False, False, False]
        pat2 = pat1[::-1]
        for line in lines:
            run, prev = 0, None
            for v in line:
                if v == prev:
                    run += 1
                else:
                    if run >= 5:
                        score += run - 2
                    run, prev = 1, v
            if run >= 5:
                score += run - 2
            for i in range(len(line) - 10):
                seg = line[i:i + 11]
                if seg == pat1 or seg == pat2:
                    score += 40
        for y in range(s - 1):
            for x in range(s - 1):
                c = m[y][x]
                if c == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        dark = sum(sum(1 for v in row if v) for row in m)
        total = s * s
        k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
        return score + max(0, k) * 10


def matrix(text: str | bytes, ecl: str = "M", min_version: int = 1, mask: Optional[int] = None) -> list[list[bool]]:
    """The QR code for [text] as rows of booleans (True = dark), without the quiet zone."""
    data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    e, ecl_bits = _ECL[ecl.upper()]
    for ver in range(max(1, min_version), 41):
        cap = _data_codewords(ver, e) * 8
        need = 4 + (8 if ver <= 9 else 16) + 8 * len(data)
        if need <= cap:
            break
    else:
        raise ValueError("text too long for a QR code")
    words = _codewords(data, ver, e)
    best: Optional[_Grid] = None
    best_score = None
    for msk in ([mask] if mask is not None else range(8)):
        g = _Grid(ver)
        g.function_patterns()
        g.place(words)
        g.apply_mask(msk)
        g.format_bits(ecl_bits, msk)
        sc = g.penalty() if mask is None else 0
        if best is None or sc < best_score:
            best, best_score = g, sc
    assert best is not None
    return best.m


def svg(text: str | bytes, ecl: str = "M", border: int = 4, dark: str = "#05070E", light: str = "#FFFFFF") -> str:
    """A self-contained SVG (one path, crisp at any size). Dark on light: phone cameras read that best."""
    m = matrix(text, ecl)
    n = len(m) + border * 2
    parts = []
    for y, row in enumerate(m):
        x = 0
        while x < len(row):
            if row[x]:
                start = x
                while x < len(row) and row[x]:
                    x += 1
                parts.append(f"M{start + border},{y + border}h{x - start}v1h-{x - start}z")
            else:
                x += 1
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges" '
            f'role="img" aria-label="QR code">'
            f'<rect width="{n}" height="{n}" fill="{light}"/>'
            f'<path d="{"".join(parts)}" fill="{dark}"/></svg>')
