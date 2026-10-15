"""QR codes for calls to action: a link as a scannable picture, with no extra library.

Byte mode, error correction level M, versions 1 to 10 (links up to about 200
characters), following ISO/IEC 18004: Reed-Solomon codewords over GF(256),
block interleaving, the eight data masks scored by the standard penalty rules,
format and version information. Tests decode every code with OpenCV's reader.
"""
from __future__ import annotations

from pathlib import Path

# level M, versions 1..10
ECC_PER_BLOCK = (None, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26)
BLOCKS = (None, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5)
FORMAT_M = 0
MASKS = (
    lambda x, y: (x + y) % 2,
    lambda x, y: y % 2,
    lambda x, y: x % 3,
    lambda x, y: (x + y) % 3,
    lambda x, y: (x // 3 + y // 2) % 2,
    lambda x, y: x * y % 2 + x * y % 3,
    lambda x, y: (x * y % 2 + x * y % 3) % 2,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2,
)


def _raw_modules(ver: int) -> int:
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        n = ver // 7 + 2
        result -= (25 * n - 10) * n - 55
        if ver >= 7:
            result -= 36
    return result


def _data_codewords(ver: int) -> int:
    return _raw_modules(ver) // 8 - ECC_PER_BLOCK[ver] * BLOCKS[ver]


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list, divisor: list) -> list:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _codewords(text: str):
    data = text.encode("utf-8")
    for ver in range(1, 11):
        count_bits = 8 if ver < 10 else 16
        capacity = _data_codewords(ver) * 8
        if 4 + count_bits + 8 * len(data) <= capacity:
            break
    else:
        raise ValueError("Use a shorter link (up to about 200 characters) for the QR code.")
    bits = []

    def put(value, n):
        bits.extend((value >> i) & 1 for i in reversed(range(n)))
    put(0b0100, 4)
    put(len(data), count_bits)
    for b in data:
        put(b, 8)
    put(0, min(4, capacity - len(bits)))
    put(0, -len(bits) % 8)
    pad = 0xEC
    while len(bits) < capacity:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    # error correction blocks, interleaved
    nblocks, ecclen = BLOCKS[ver], ECC_PER_BLOCK[ver]
    raw = _raw_modules(ver) // 8
    nshort = nblocks - raw % nblocks
    shortlen = raw // nblocks
    div = _rs_divisor(ecclen)
    blocks, k = [], 0
    for i in range(nblocks):
        dat = words[k:k + shortlen - ecclen + (0 if i < nshort else 1)]
        k += len(dat)
        ecc = _rs_remainder(dat, div)
        if i < nshort:
            dat.append(0)
        blocks.append(dat + ecc)
    out = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != shortlen - ecclen or j >= nshort:
                out.append(blk[i])
    return ver, out


class _Grid:
    def __init__(self, ver: int):
        self.ver, self.size = ver, ver * 4 + 17
        self.m = [[False] * self.size for _ in range(self.size)]
        self.fn = [[False] * self.size for _ in range(self.size)]

    def set_fn(self, x, y, dark):
        self.m[y][x], self.fn[y][x] = dark, True

    def align_positions(self):
        if self.ver == 1:
            return []
        n = self.ver // 7 + 2
        step = (self.ver * 4 + n * 2 + 1) // (n * 2 - 2) * 2
        return list(reversed([self.size - 7 - i * step for i in range(n - 1)] + [6]))

    def function_patterns(self):
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
        pos = self.align_positions()
        n = len(pos)
        for i in range(n):
            for j in range(n):
                if (i, j) in ((0, 0), (0, n - 1), (n - 1, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_fn(pos[i] + dx, pos[j] + dy, max(abs(dx), abs(dy)) != 1)
        self.format_bits(0)
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

    def format_bits(self, mask):
        data = FORMAT_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1 == 1
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

    def place(self, words):
        i, s = 0, self.size
        right = s - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(s):
                for j in range(2):
                    x = right - j
                    upward = (right + 1) & 2 == 0
                    y = s - 1 - vert if upward else vert
                    if not self.fn[y][x] and i < len(words) * 8:
                        self.m[y][x] = (words[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def mask(self, k):
        f = MASKS[k]
        for y in range(self.size):
            for x in range(self.size):
                if not self.fn[y][x] and f(x, y) == 0:
                    self.m[y][x] = not self.m[y][x]

    def penalty(self) -> int:
        s, m, score = self.size, self.m, 0
        lines = [row for row in m] + [[m[y][x] for y in range(s)] for x in range(s)]
        for line in lines:
            run, prev = 0, None
            for v in line:
                run = run + 1 if v == prev else 1
                prev = v
                if run == 5:
                    score += 3
                elif run > 5:
                    score += 1
            # finder-like 1:1:3:1:1 patterns with four light modules on a side
            text = "".join("1" if v else "0" for v in line)
            score += 40 * (text.count("10111010000") + text.count("00001011101"))
        for y in range(s - 1):
            for x in range(s - 1):
                if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        dark = sum(v for row in m for v in row)
        total = s * s
        score += 10 * ((abs(dark * 20 - total * 10) + total - 1) // total - 1)
        return score


def matrix(text: str) -> list:
    """The QR code for `text` as rows of booleans (True = dark), without the quiet zone."""
    ver, words = _codewords(text)
    best = None
    for k in range(8):
        g = _Grid(ver)
        g.function_patterns()
        g.place(words)
        g.mask(k)
        g.format_bits(k)
        score = g.penalty()
        if best is None or score < best[0]:
            best = (score, g)
    return best[1].m


def png(text: str, dest, *, scale: int = 12, border: int = 4, dark=(20, 20, 20), light=(255, 255, 255)) -> Path:
    from PIL import Image, ImageDraw
    m = matrix(text)
    n = len(m)
    size = (n + 2 * border) * scale
    img = Image.new("RGB", (size, size), light)
    draw = ImageDraw.Draw(img)
    for y, row in enumerate(m):
        for x, on in enumerate(row):
            if on:
                x0, y0 = (x + border) * scale, (y + border) * scale
                draw.rectangle([x0, y0, x0 + scale - 1, y0 + scale - 1], fill=dark)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)
    return dest
