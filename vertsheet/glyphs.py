"""Vector music glyphs.

Every glyph is described in *normal* (horizontal) notation coordinates, in
staff-space units: ``gx`` grows to the right (forward in time) and ``gy``
grows upwards (higher pitch). The engraver maps them onto the vertical page
with either a 90° clockwise rotation (time runs down, pitch rises to the
right) or an upright mapping, see :func:`transform`.

A glyph is a list of shapes:
    ("fill",   commands, evenodd)
    ("stroke", commands, width)
where ``commands`` is a list of ("M", x, y) / ("L", x, y) /
("C", x1, y1, x2, y2, x, y) / ("Z",) tuples.
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Callable, Dict, List, Tuple

K = 0.5522847498

Shape = tuple
Cmds = List[tuple]


def ellipse(cx: float, cy: float, a: float, b: float, angle_deg: float = 0.0) -> Cmds:
    t = math.radians(angle_deg)
    c, s = math.cos(t), math.sin(t)

    def P(x, y):
        return (cx + x * c - y * s, cy + x * s + y * c)

    return [("M", *P(a, 0)),
            ("C", *P(a, K * b), *P(K * a, b), *P(0, b)),
            ("C", *P(-K * a, b), *P(-a, K * b), *P(-a, 0)),
            ("C", *P(-a, -K * b), *P(-K * a, -b), *P(0, -b)),
            ("C", *P(K * a, -b), *P(a, -K * b), *P(a, 0)),
            ("Z",)]


def circle(cx, cy, r) -> Cmds:
    return ellipse(cx, cy, r, r)


def poly(points, close=True) -> Cmds:
    cmds = [("M", *points[0])] + [("L", *p) for p in points[1:]]
    if close:
        cmds.append(("Z",))
    return cmds


def _bar(x0, x1, yc, rise, thick) -> Cmds:
    """Slanted thick bar (used by sharps and naturals)."""
    h = thick / 2
    return poly([(x0, yc - rise / 2 - h), (x1, yc + rise / 2 - h),
                 (x1, yc + rise / 2 + h), (x0, yc - rise / 2 + h)])


# ---------------------------------------------------------------- glyphs

def _sharp():
    return [("stroke", [("M", -0.22, -1.25), ("L", -0.22, 1.05)], 0.12),
            ("stroke", [("M", 0.22, -1.05), ("L", 0.22, 1.25)], 0.12),
            ("fill", _bar(-0.5, 0.5, -0.38, 0.25, 0.2), False),
            ("fill", _bar(-0.5, 0.5, 0.38, 0.25, 0.2), False)]


def _flat(dx=0.0):
    return [("stroke", [("M", -0.3 + dx, -0.48), ("L", -0.3 + dx, 1.9)], 0.12),
            ("stroke", [("M", -0.3 + dx, -0.42), ("C", 0.3 + dx, -0.05, 0.68 + dx, 0.38, 0.38 + dx, 0.58),
                        ("C", 0.16 + dx, 0.7, -0.08 + dx, 0.56, -0.3 + dx, 0.28)], 0.17)]


def _natural():
    return [("stroke", [("M", -0.25, -0.7), ("L", -0.25, 1.3)], 0.12),
            ("stroke", [("M", 0.25, -1.3), ("L", 0.25, 0.7)], 0.12),
            ("fill", _bar(-0.25, 0.25, 0.4, 0.15, 0.2), False),
            ("fill", _bar(-0.25, 0.25, -0.4, 0.15, 0.2), False)]


def _double_sharp():
    return [("stroke", [("M", -0.38, -0.38), ("L", 0.38, 0.38)], 0.13),
            ("stroke", [("M", -0.38, 0.38), ("L", 0.38, -0.38)], 0.13),
            ("fill", poly([(-0.48, 0.48), (-0.2, 0.48), (-0.48, 0.2)]), False),
            ("fill", poly([(0.48, 0.48), (0.2, 0.48), (0.48, 0.2)]), False),
            ("fill", poly([(-0.48, -0.48), (-0.2, -0.48), (-0.48, -0.2)]), False),
            ("fill", poly([(0.48, -0.48), (0.2, -0.48), (0.48, -0.2)]), False)]


def _double_flat():
    return _flat(-0.42) + _flat(0.42)


def _clef_g():
    path = [("M", 0.12, -0.28),
            ("C", 0.62, -0.25, 0.66, 0.46, 0.1, 0.5),
            ("C", -0.55, 0.52, -0.72, -0.5, -0.08, -0.86),
            ("C", 0.62, -1.18, 1.18, -0.3, 0.86, 0.55),
            ("C", 0.62, 1.25, -0.22, 1.6, -0.3, 2.45),
            ("C", -0.4, 3.35, 0.12, 4.05, 0.38, 4.25),
            ("C", 0.72, 3.8, 0.66, 3.0, 0.12, 2.6),
            ("C", -0.1, 2.42, -0.05, 2.2, 0.0, 1.85),
            ("L", 0.36, -1.85),
            ("C", 0.42, -2.45, -0.28, -2.58, -0.46, -2.12)]
    return [("stroke", path, 0.17), ("fill", circle(-0.36, -2.0, 0.27), False)]


def _clef_f():
    path = [("M", -0.7, 0.05), ("C", -0.58, 0.92, 0.95, 1.05, 1.05, 0.0),
            ("C", 1.12, -1.1, 0.2, -1.9, -1.0, -2.6)]
    return [("stroke", path, 0.22), ("fill", circle(-0.62, 0.0, 0.31), False),
            ("fill", circle(1.48, 0.5, 0.14), False), ("fill", circle(1.48, -0.5, 0.14), False)]


def _clef_c():
    upper = [("M", -0.28, 0.05), ("C", 0.05, 0.3, 0.15, 0.9, 0.3, 1.3),
             ("C", 0.5, 1.95, 1.32, 2.05, 1.32, 1.3), ("C", 1.32, 0.8, 0.82, 0.8, 0.76, 1.15)]
    lower = [(c[0],) + tuple(v if i % 2 == 0 else -v for i, v in enumerate(c[1:])) for c in upper]
    return [("fill", poly([(-1.0, -2.0), (-0.6, -2.0), (-0.6, 2.0), (-1.0, 2.0)]), False),
            ("fill", poly([(-0.46, -2.0), (-0.34, -2.0), (-0.34, 2.0), (-0.46, 2.0)]), False),
            ("stroke", upper, 0.2), ("stroke", lower, 0.2)]


def _rest_whole():
    return [("fill", poly([(-0.6, 0.5), (0.6, 0.5), (0.6, 1.0), (-0.6, 1.0)]), False)]


def _rest_half():
    return [("fill", poly([(-0.6, 0.0), (0.6, 0.0), (0.6, 0.5), (-0.6, 0.5)]), False)]


def _rest_breve():
    return [("fill", poly([(-0.25, 0.0), (0.25, 0.0), (0.25, 1.0), (-0.25, 1.0)]), False)]


def _rest_quarter():
    return [("stroke", [("M", -0.12, 1.45), ("L", 0.3, 0.8)], 0.16),
            ("stroke", [("M", 0.3, 0.8), ("L", -0.18, 0.18)], 0.36),
            ("stroke", [("M", -0.18, 0.18), ("L", 0.3, -0.48)], 0.16),
            ("stroke", [("M", 0.3, -0.48), ("C", -0.25, -0.62, -0.36, -1.0, 0.0, -1.35)], 0.2)]


def _rest_flagged(n: int):
    shapes = []
    top_x, top_y = 0.42, 0.55
    slope = 0.29
    bottom_y = -1.0 - 0.85 * (n - 1)
    shapes.append(("stroke", [("M", top_x, top_y), ("L", top_x - slope * (top_y - bottom_y), bottom_y)], 0.13))
    for k in range(n):
        yk = top_y - 0.85 * k
        xs = top_x - slope * (top_y - yk)
        shapes.append(("fill", circle(xs - 0.68, yk - 0.12, 0.2), False))
        shapes.append(("stroke", [("M", xs - 0.66, yk - 0.28),
                                  ("C", xs - 0.36, yk - 0.4, xs - 0.12, yk - 0.2, xs, yk)], 0.12))
    return shapes


def _flag(n: int, up: bool):
    s = -1 if up else 1   # flags hang towards the notehead
    shapes = []
    for k in range(n):
        y0 = s * 0.78 * k
        shapes.append(("stroke", [("M", 0.0, y0),
                                  ("C", 0.08, y0 + s * 0.62, 0.95, y0 + s * 0.92, 0.78, y0 + s * 2.0)], 0.16))
    return shapes


def _fermata():
    outer = [("M", -1.0, 0.0), ("C", -1.0, 1.4, 1.0, 1.4, 1.0, 0.0),
             ("L", 0.92, 0.0), ("C", 0.9, 1.1, -0.9, 1.1, -0.92, 0.0), ("Z",)]
    return [("fill", outer, False), ("fill", circle(0.0, 0.22, 0.17), False)]


def _accent():
    return [("stroke", [("M", -0.6, 0.36), ("L", 0.6, 0.0), ("L", -0.6, -0.36)], 0.12)]


def _marcato():
    return [("fill", poly([(-0.45, 0.0), (0.0, 1.0), (0.45, 0.0), (0.25, 0.0), (0.0, 0.62), (-0.25, 0.0)]), False)]


def _staccato():
    return [("fill", circle(0.0, 0.0, 0.17), False)]


def _staccatissimo():
    return [("fill", poly([(-0.14, 0.45), (0.14, 0.45), (0.0, -0.4)]), False)]


def _tenuto():
    return [("fill", poly([(-0.6, -0.07), (0.6, -0.07), (0.6, 0.07), (-0.6, 0.07)]), False)]


def _mordent(inverted: bool):
    pts = [(-0.9, -0.1), (-0.5, 0.35), (-0.15, -0.1), (0.2, 0.35), (0.55, -0.1), (0.9, 0.35)]
    shapes = [("stroke", poly(pts, close=False), 0.16)]
    if not inverted:
        shapes.append(("stroke", [("M", 0.0, -0.4), ("L", 0.0, 0.65)], 0.12))
    return shapes


def _turn():
    return [("stroke", [("M", -0.9, 0.0), ("C", -0.9, 0.6, -0.2, 0.6, 0.0, 0.0),
                        ("C", 0.2, -0.6, 0.9, -0.6, 0.9, 0.0)], 0.18),
            ("fill", circle(-0.82, -0.08, 0.14), False), ("fill", circle(0.82, 0.08, 0.14), False)]


GLYPH_BUILDERS: Dict[str, Callable[[], list]] = {
    "sharp": _sharp, "flat": _flat, "natural": _natural,
    "double-sharp": _double_sharp, "sharp-sharp": _double_sharp, "flat-flat": _double_flat,
    "clef_g": _clef_g, "clef_f": _clef_f, "clef_c": _clef_c,
    "rest_breve": _rest_breve, "rest_whole": _rest_whole, "rest_half": _rest_half,
    "rest_quarter": _rest_quarter,
    "rest_eighth": lambda: _rest_flagged(1), "rest_16th": lambda: _rest_flagged(2),
    "rest_32nd": lambda: _rest_flagged(3), "rest_64th": lambda: _rest_flagged(4),
    "rest_128th": lambda: _rest_flagged(5),
    "fermata": _fermata, "accent": _accent, "strong-accent": _marcato, "staccato": _staccato,
    "staccatissimo": _staccatissimo, "spiccato": _staccatissimo, "tenuto": _tenuto,
    "detached-legato": lambda: _tenuto() + [("fill", circle(0, -0.4, 0.15), False)],
    "mordent": lambda: _mordent(False), "inverted-mordent": lambda: _mordent(True),
    "turn": _turn, "inverted-turn": _turn,
}
for _n in range(1, 7):
    GLYPH_BUILDERS[f"flag_up_{_n}"] = (lambda n=_n: _flag(n, True))
    GLYPH_BUILDERS[f"flag_down_{_n}"] = (lambda n=_n: _flag(n, False))

ACCIDENTAL_GLYPH = {
    "sharp": "sharp", "flat": "flat", "natural": "natural", "double-sharp": "double-sharp",
    "sharp-sharp": "double-sharp", "flat-flat": "flat-flat", "double-flat": "flat-flat",
    "natural-sharp": "sharp", "natural-flat": "flat",
}

REST_GLYPH = {"maxima": "rest_breve", "long": "rest_breve", "breve": "rest_breve",
              "whole": "rest_whole", "half": "rest_half", "quarter": "rest_quarter",
              "eighth": "rest_eighth", "16th": "rest_16th", "32nd": "rest_32nd",
              "64th": "rest_64th", "128th": "rest_128th", "256th": "rest_128th"}


@lru_cache(maxsize=None)
def glyph(name: str) -> tuple:
    return tuple(GLYPH_BUILDERS[name]())


@lru_cache(maxsize=None)
def bbox(name: str) -> Tuple[float, float, float, float]:
    xs, ys = [], []
    for shape in glyph(name):
        w = shape[2] / 2 if shape[0] == "stroke" else 0.0
        for c in shape[1]:
            pts = c[1:]
            for i in range(0, len(pts), 2):
                xs += [pts[i] - w, pts[i] + w]
                ys += [pts[i + 1] - w, pts[i + 1] + w]
    return (min(xs), min(ys), max(xs), max(ys))


def transform(mode: str, cx: float, cy: float, s: float):
    """Map glyph coordinates onto the page.

    ``rot``     90° clockwise rotation: time (gx) -> +y, pitch (gy) -> +x
    ``upright`` normal reading orientation: gx -> +x, gy -> -y
    """
    if mode == "rot":
        return lambda gx, gy: (cx + gy * s, cy + gx * s)
    return lambda gx, gy: (cx + gx * s, cy - gy * s)


def time_extent(name: str, mode: str, scale: float = 1.0) -> Tuple[float, float]:
    """Extent of the glyph along the page y axis (time) relative to its anchor."""
    x0, y0, x1, y1 = bbox(name)
    if mode == "rot":
        return x0 * scale, x1 * scale
    return -y1 * scale, -y0 * scale


def pitch_extent(name: str, mode: str, scale: float = 1.0) -> Tuple[float, float]:
    """Extent of the glyph along the page x axis (pitch) relative to its anchor."""
    x0, y0, x1, y1 = bbox(name)
    if mode == "rot":
        return y0 * scale, y1 * scale
    return x0 * scale, x1 * scale


def notehead_shapes(kind: str) -> list:
    """Noteheads in normal coordinates centred on (0, 0)."""
    if kind == "whole":
        outer = ellipse(0, 0, 0.74, 0.5)
        inner = ellipse(0, 0, 0.34, 0.27, 62)
        return [("fill", outer + inner, True)]
    if kind == "breve":
        outer = ellipse(0, 0, 0.74, 0.5)
        inner = ellipse(0, 0, 0.34, 0.27, 62)
        bars = [("stroke", [("M", -0.92, -0.6), ("L", -0.92, 0.6)], 0.1),
                ("stroke", [("M", 0.92, -0.6), ("L", 0.92, 0.6)], 0.1)]
        return [("fill", outer + inner, True)] + bars
    if kind == "half":
        outer = ellipse(0, 0, 0.59, 0.44, 20)
        inner = ellipse(0, 0, 0.5, 0.2, 35)
        return [("fill", outer + inner, True)]
    if kind == "x":
        return [("stroke", [("M", -0.45, -0.42), ("L", 0.45, 0.42)], 0.13),
                ("stroke", [("M", -0.45, 0.42), ("L", 0.45, -0.42)], 0.13)]
    if kind == "diamond":
        return [("stroke", poly([(-0.55, 0), (0, 0.45), (0.55, 0), (0, -0.45)]), 0.12)]
    return [("fill", ellipse(0, 0, 0.59, 0.44, 20), False)]


HEAD_HALF_TIME = {"whole": 0.74, "breve": 0.95}  # half extent along time; default 0.59
