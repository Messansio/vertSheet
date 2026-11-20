"""Draws a :class:`~vertsheet.canvas.Page` display list on a Tkinter canvas.

Used by the graphical interface for the live preview. Bezier curves are
flattened into polylines; filled paths with holes (even-odd glyphs such as
open noteheads) are drawn as one polygon so Tk's even-odd fill keeps the hole.
"""
from __future__ import annotations

import math
from typing import List

FONT_MAP = {
    "sans": ("Arial", ""),
    "sans-bold": ("Arial", "bold"),
    "serif": ("Times New Roman", ""),
    "serif-italic": ("Times New Roman", "italic"),
    "serif-bold": ("Times New Roman", "bold"),
    "serif-bold-italic": ("Times New Roman", "bold italic"),
}
CAPS = {"butt": "butt", "round": "round", "square": "projecting"}
ANCHORS = {"start": "w", "middle": "center", "end": "e"}


def _hex(c) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v * 255)))) for v in c)


def _subpaths(cmds, s: float, ox: float, oy: float) -> List[List[float]]:
    """Flattened screen-space subpaths: lists of x0, y0, x1, y1, ..."""
    out: List[List[float]] = []
    cur: List[float] = []
    x = y = 0.0
    start = None
    for c in cmds:
        op = c[0]
        if op == "M":
            if len(cur) >= 4:
                out.append(cur)
            x, y = c[1], c[2]
            start = (x, y)
            cur = [ox + x * s, oy + y * s]
        elif op == "L":
            x, y = c[1], c[2]
            cur += [ox + x * s, oy + y * s]
        elif op == "C":
            x1, y1, x2, y2, x3, y3 = c[1:7]
            length = (math.hypot(x1 - x, y1 - y) + math.hypot(x2 - x1, y2 - y1) +
                      math.hypot(x3 - x2, y3 - y2)) * s
            n = max(2, min(24, int(length / 3) + 1))
            for i in range(1, n + 1):
                t = i / n
                u = 1 - t
                a, b, cc, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
                px = a * x + b * x1 + cc * x2 + d * x3
                py = a * y + b * y1 + cc * y2 + d * y3
                cur += [ox + px * s, oy + py * s]
            x, y = x3, y3
        elif op == "Z":
            if start is not None:
                cur += [ox + start[0] * s, oy + start[1] * s]
                x, y = start
    if len(cur) >= 4:
        out.append(cur)
    return out


def draw_page(canvas, page, scale: float, ox: float = 0.0, oy: float = 0.0,
              tag: str = "page") -> int:
    """Draw ``page`` at ``scale`` pixels per point with its top-left corner at
    (ox, oy). Returns the number of canvas items created."""
    s = scale
    n = 0
    fonts = {}
    for op in page.ops:
        kind = op[0]
        if kind == "line":
            _, x1, y1, x2, y2, width, color, cap, dash = op
            kw = {}
            if dash:
                kw["dash"] = tuple(max(1, int(round(d * s))) for d in dash)
            canvas.create_line(ox + x1 * s, oy + y1 * s, ox + x2 * s, oy + y2 * s,
                               width=max(1.0, width * s), fill=_hex(color),
                               capstyle=CAPS.get(cap, "butt"), tags=tag, **kw)
            n += 1
        elif kind == "rect":
            _, x, y, w, h, color = op
            col = _hex(color)
            x0, y0 = ox + x * s, oy + y * s
            x1, y1 = x0 + max(w * s, 1.0), y0 + max(h * s, 1.0)
            canvas.create_rectangle(x0, y0, x1, y1, fill=col, outline="", tags=tag)
            n += 1
        elif kind == "path":
            _, cmds, fill, sw, evenodd, color, cap = op
            subs = _subpaths(cmds, s, ox, oy)
            if not subs:
                continue
            col = _hex(color)
            if fill:
                polys = [sum(subs, [])] if evenodd and len(subs) > 1 else subs
                for pts in polys:
                    if len(pts) >= 6:
                        canvas.create_polygon(pts, fill=col, outline="", tags=tag)
                        n += 1
                    else:
                        canvas.create_line(pts, fill=col, width=1, tags=tag)
                        n += 1
            else:
                for pts in subs:
                    canvas.create_line(pts, fill=col, width=max(1.0, (sw or 0.5) * s),
                                       capstyle=CAPS.get(cap, "round"), joinstyle="round", tags=tag)
                    n += 1
        elif kind == "text":
            _, x, y, text, size, font, anchor, angle, color = op
            px = max(1, int(round(size * s)))
            family, style = FONT_MAP.get(font, FONT_MAP["serif"])
            key = (family, px, style)
            if key not in fonts:
                fonts[key] = (family, -px, style) if style else (family, -px)
            a = math.radians(angle)
            # move the anchor from the baseline to the middle of the x-height
            up = 0.32 * size * s
            tx = ox + x * s + math.sin(a) * up
            ty = oy + y * s - math.cos(a) * up
            canvas.create_text(tx, ty, text=text, font=fonts[key], fill=_hex(color),
                               anchor=ANCHORS.get(anchor, "w"), angle=-angle, tags=tag)
            n += 1
    return n


def fit_scale(page_w: float, page_h: float, view_w: float, view_h: float,
              margin: float = 24.0, mode: str = "page") -> float:
    """Scale (pixels per point) that fits a page into a view."""
    sw = (view_w - 2 * margin) / page_w
    sh = (view_h - 2 * margin) / page_h
    return max(0.05, sw if mode == "width" else min(sw, sh))
