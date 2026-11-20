"""Draws the vertSheet icon and writes assets/vertsheet.ico + PNG sizes.

Pure Python (no imaging library): a tiny anti-aliased rasterizer renders the
motif - a pair of vertical staves with noteheads, a barline across the pair
and the amber divider line - on a blue rounded square.

    python tools/make_icon.py [output_dir]
"""
from __future__ import annotations

import math
import os
import struct
import sys
import zlib
from typing import Callable, List, Tuple

Color = Tuple[float, float, float]
TOP = (0.27, 0.47, 0.90)
BOTTOM = (0.11, 0.22, 0.56)
WHITE = (1.0, 1.0, 1.0)
AMBER = (1.0, 0.77, 0.24)


class Raster:
    """RGBA canvas with premultiplied float pixels; shapes in unit coordinates."""

    def __init__(self, n: int):
        self.n = n
        self.px = [[0.0, 0.0, 0.0, 0.0] for _ in range(n * n)]

    def _blend(self, i: int, color: Color, a: float) -> None:
        if a <= 0:
            return
        p = self.px[i]
        k = 1.0 - a
        p[0] = color[0] * a + p[0] * k
        p[1] = color[1] * a + p[1] * k
        p[2] = color[2] * a + p[2] * k
        p[3] = a + p[3] * k

    def rect(self, x0: float, y0: float, x1: float, y1: float, color: Color, alpha: float = 1.0) -> None:
        """Axis-aligned rectangle with exact area coverage."""
        n = self.n
        X0, Y0, X1, Y1 = x0 * n, y0 * n, x1 * n, y1 * n
        for py in range(max(0, int(Y0)), min(n, int(math.ceil(Y1)))):
            cy = min(Y1, py + 1) - max(Y0, py)
            if cy <= 0:
                continue
            for px in range(max(0, int(X0)), min(n, int(math.ceil(X1)))):
                cx = min(X1, px + 1) - max(X0, px)
                if cx > 0:
                    self._blend(py * n + px, color, alpha * cx * cy)

    def shape(self, inside: Callable[[float, float], bool], bbox, color, alpha: float = 1.0,
              ss: int = 4, color_at: Callable[[float], Color] = None) -> None:
        """Arbitrary shape by supersampling inside its bounding box."""
        n = self.n
        x0, y0, x1, y1 = bbox
        offs = [(i + 0.5) / ss for i in range(ss)]
        for py in range(max(0, int(y0 * n)), min(n, int(math.ceil(y1 * n)))):
            col = color_at((py + 0.5) / n) if color_at else color
            for px in range(max(0, int(x0 * n)), min(n, int(math.ceil(x1 * n)))):
                hits = 0
                for oy in offs:
                    for ox in offs:
                        if inside((px + ox) / n, (py + oy) / n):
                            hits += 1
                if hits:
                    self._blend(py * n + px, col, alpha * hits / (ss * ss))

    def rounded_rect(self, x0, y0, x1, y1, r, color_at: Callable[[float], Color]) -> None:
        def inside(x, y):
            cx = min(max(x, x0 + r), x1 - r)
            cy = min(max(y, y0 + r), y1 - r)
            return (x - cx) ** 2 + (y - cy) ** 2 <= r * r and x0 <= x <= x1 and y0 <= y <= y1
        self.shape(inside, (x0, y0, x1, y1), None, 1.0, ss=4, color_at=color_at)

    def ellipse(self, cx, cy, rx, ry, angle_deg, color: Color) -> None:
        a = math.radians(angle_deg)
        ca, sa = math.cos(a), math.sin(a)

        def inside(x, y):
            dx, dy = x - cx, y - cy
            u = dx * ca + dy * sa
            v = -dx * sa + dy * ca
            return (u / rx) ** 2 + (v / ry) ** 2 <= 1.0
        m = max(rx, ry)
        self.shape(inside, (cx - m, cy - m, cx + m, cy + m), color, 1.0, ss=4)

    def rgba_rows(self) -> List[bytes]:
        rows = []
        n = self.n
        for y in range(n):
            row = bytearray()
            for x in range(n):
                r, g, b, a = self.px[y * n + x]
                if a > 0:
                    r, g, b = r / a, g / a, b / a
                row += bytes((_b(r), _b(g), _b(b), _b(a)))
            rows.append(bytes(row))
        return rows


def _b(v: float) -> int:
    return max(0, min(255, int(round(v * 255))))


def _mix(a: Color, b: Color, t: float) -> Color:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def draw_icon(n: int) -> Raster:
    r = Raster(n)
    small = n <= 24
    m = 0.0 if small else 0.035
    r.rounded_rect(m, m, 1 - m, 1 - m, 0.2 if small else 0.21,
                   lambda y: _mix(TOP, BOTTOM, min(1.0, max(0.0, (y - m) / (1 - 2 * m)))))
    lines = 3 if small else 5
    lw = max(1.35 / n, 0.016)
    top, bot = (0.2, 0.8) if small else (0.17, 0.83)
    groups = [(0.2, 0.42), (0.52, 0.74)] if small else [(0.19, 0.41), (0.5, 0.72)]
    for gx0, gx1 in groups:
        for i in range(lines):
            x = gx0 + (gx1 - gx0) * i / (lines - 1)
            r.rect(x - lw / 2, top, x + lw / 2, bot, WHITE, 0.92)
    # barline across the pair
    bw = max(1.2 / n, 0.016)
    r.rect(groups[0][0] - lw / 2, 0.5 - bw / 2, groups[1][1] + lw / 2, 0.5 + bw / 2, WHITE, 0.95)
    # divider after the pair
    if n >= 24:
        dw = max(1.6 / n, 0.026)
        r.rect(0.84 - dw / 2, top, 0.84 + dw / 2, bot, AMBER, 1.0)
    # noteheads
    if small:
        notes = [(0.31, 0.33), (0.63, 0.67)]
        rx, ry = 0.095, 0.07
    else:
        notes = [(0.3, 0.31), (0.24, 0.68), (0.66, 0.26), (0.58, 0.41), (0.67, 0.71)]
        rx, ry = 0.066, 0.048
    for cx, cy in notes:
        r.ellipse(cx, cy, rx, ry, -24, AMBER)
    return r


# ---------------------------------------------------------------- encoders

def png_bytes(r: Raster) -> bytes:
    n = r.n
    raw = b"".join(b"\x00" + row for row in r.rgba_rows())

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def dib_bytes(r: Raster) -> bytes:
    """32-bit BMP (DIB) icon image: BGRA bottom-up + 1-bit AND mask."""
    n = r.n
    rows = r.rgba_rows()
    header = struct.pack("<IiiHHIIiiII", 40, n, 2 * n, 1, 32, 0, 0, 0, 0, 0, 0)
    xor = bytearray()
    for row in reversed(rows):
        for i in range(0, len(row), 4):
            rr, gg, bb, aa = row[i:i + 4]
            xor += bytes((bb, gg, rr, aa))
    mask_row = ((n + 31) // 32) * 4
    mask = bytearray()
    for row in reversed(rows):
        bits = bytearray(mask_row)
        for x in range(n):
            if row[4 * x + 3] == 0:
                bits[x // 8] |= 0x80 >> (x % 8)
        mask += bits
    return header + bytes(xor) + bytes(mask)


def ico_bytes(images: List[Tuple[int, bytes]]) -> bytes:
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    body = b""
    for n, data in images:
        dim = 0 if n >= 256 else n
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        body += data
    return out + body


def build(out_dir: str) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)
    rasters = {n: draw_icon(n) for n in (16, 20, 24, 32, 40, 48, 64, 128, 256)}
    entries = []
    for n in (16, 20, 24, 32, 40, 48, 64):
        entries.append((n, dib_bytes(rasters[n])))
    for n in (128, 256):
        entries.append((n, png_bytes(rasters[n])))
    written = []
    ico = os.path.join(out_dir, "vertsheet.ico")
    with open(ico, "wb") as fh:
        fh.write(ico_bytes(entries))
    written.append(ico)
    for n in (32, 64, 128, 256):
        p = os.path.join(out_dir, f"vertsheet-{n}.png")
        with open(p, "wb") as fh:
            fh.write(png_bytes(rasters[n]))
        written.append(p)
    return written


if __name__ == "__main__":
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "assets")
    for path in build(target):
        print("wrote", path)
