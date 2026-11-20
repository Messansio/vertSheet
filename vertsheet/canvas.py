"""Backend independent drawing surface + SVG and PDF writers.

All coordinates are in PostScript points with the origin at the top-left
corner of the page and y growing downwards. The PDF writer is self contained
(no third-party libraries) and uses the 14 standard PDF fonts.
"""
from __future__ import annotations

import math
import zlib
from typing import List, Sequence
from xml.sax.saxutils import escape

from . import glyphs as G

FONTS = {
    "sans": ("Helvetica", "Helvetica, Arial, sans-serif", "normal", "normal"),
    "sans-bold": ("Helvetica-Bold", "Helvetica, Arial, sans-serif", "bold", "normal"),
    "serif": ("Times-Roman", "'Times New Roman', Times, serif", "normal", "normal"),
    "serif-italic": ("Times-Italic", "'Times New Roman', Times, serif", "normal", "italic"),
    "serif-bold": ("Times-Bold", "'Times New Roman', Times, serif", "bold", "normal"),
    "serif-bold-italic": ("Times-BoldItalic", "'Times New Roman', Times, serif", "bold", "italic"),
}

# Approximate advance widths (per 1000 em) for layout / anchoring.
_NARROW = set("iljtfI.,;:!|'ìí")
_WIDE = set("mwMW@%")


def text_width(s: str, font: str, size: float) -> float:
    serif = font.startswith("serif")
    total = 0
    for ch in s:
        if ch == " ":
            w = 250 if serif else 278
        elif ch.isdigit():
            w = 500 if serif else 556
        elif ch in _NARROW:
            w = 280 if serif else 250
        elif ch in _WIDE:
            w = 830 if serif else 860
        elif ch.isupper():
            w = 690 if serif else 690
        elif ch in "rsz":
            w = 390 if serif else 480
        else:
            w = 480 if serif else 540
        if "bold" in font:
            w *= 1.05
        total += w
    return total * size / 1000.0


class Page:
    def __init__(self, width: float, height: float):
        self.width = width
        self.height = height
        self.ops: List[tuple] = []

    # primitives -------------------------------------------------------
    def line(self, x1, y1, x2, y2, width=0.5, color=(0, 0, 0), cap="butt", dash=None):
        self.ops.append(("line", x1, y1, x2, y2, width, color, cap, dash))

    def rect(self, x, y, w, h, color=(0, 0, 0)):
        if w < 0:
            x, w = x + w, -w
        if h < 0:
            y, h = y + h, -h
        self.ops.append(("rect", x, y, w, h, color))

    def path(self, cmds, fill=True, stroke_width=None, evenodd=False, color=(0, 0, 0), cap="round"):
        self.ops.append(("path", list(cmds), fill, stroke_width, evenodd, color, cap))

    def text(self, x, y, s, size=10, font="serif", anchor="start", angle=0.0, color=(0, 0, 0)):
        if s:
            self.ops.append(("text", x, y, s, size, font, anchor, angle, color))

    # helpers ----------------------------------------------------------
    def shapes(self, shapes, tf, scale, color=(0, 0, 0)):
        for shape in shapes:
            kind, cmds = shape[0], shape[1]
            mapped = []
            for c in cmds:
                if c[0] == "Z":
                    mapped.append(("Z",))
                    continue
                pts = []
                for i in range(1, len(c), 2):
                    pts.extend(tf(c[i], c[i + 1]))
                mapped.append((c[0], *pts))
            if kind == "fill":
                self.path(mapped, fill=True, evenodd=shape[2], color=color)
            else:
                self.path(mapped, fill=False, stroke_width=shape[2] * scale, color=color)

    def glyph(self, name, cx, cy, s, mode="rot", color=(0, 0, 0)):
        self.shapes(G.glyph(name), G.transform(mode, cx, cy, s), s, color)

    def lens(self, p0, p1, side, height, thick, color=(0, 0, 0)):
        """Filled curved band (tie / slur) bulging towards ``side`` (+1 right, -1 left)."""
        (x0, y0), (x1, y1) = p0, p1
        dx, dy = x1 - x0, y1 - y0
        L = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / L, dx / L
        if nx * side < 0 or (abs(nx) < 1e-9 and ny * side < 0):
            nx, ny = -nx, -ny
        k = 4.0 / 3.0

        def ctrl(t, h):
            return (x0 + dx * t + nx * h * k, y0 + dy * t + ny * h * k)

        o1, o2 = ctrl(0.25, height), ctrl(0.75, height)
        i1, i2 = ctrl(0.25, height - thick), ctrl(0.75, height - thick)
        self.path([("M", x0, y0), ("C", *o1, *o2, x1, y1), ("C", *i2, *i1, x0, y0), ("Z",)],
                  fill=True, color=color)


# ======================================================================
# SVG
# ======================================================================

def _f(v: float) -> str:
    s = f"{v:.2f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _rgb(c) -> str:
    return "#%02x%02x%02x" % tuple(int(round(v * 255)) for v in c)


def _svg_path_d(cmds) -> str:
    out = []
    for c in cmds:
        if c[0] == "Z":
            out.append("Z")
        else:
            out.append(c[0] + " ".join(_f(v) for v in c[1:]))
    return " ".join(out)


def page_to_svg(page: Page, background: str = "#ffffff") -> str:
    w, h = page.width, page.height
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{_f(w)}pt" height="{_f(h)}pt" '
             f'viewBox="0 0 {_f(w)} {_f(h)}">',
             f'<rect width="100%" height="100%" fill="{background}"/>']
    for op in page.ops:
        kind = op[0]
        if kind == "line":
            _, x1, y1, x2, y2, width, color, cap, dash = op
            extra = f' stroke-dasharray="{" ".join(_f(d) for d in dash)}"' if dash else ""
            parts.append(f'<line x1="{_f(x1)}" y1="{_f(y1)}" x2="{_f(x2)}" y2="{_f(y2)}" '
                         f'stroke="{_rgb(color)}" stroke-width="{_f(width)}" stroke-linecap="{cap}"{extra}/>')
        elif kind == "rect":
            _, x, y, rw, rh, color = op
            parts.append(f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(rw)}" height="{_f(rh)}" fill="{_rgb(color)}"/>')
        elif kind == "path":
            _, cmds, fill, sw, evenodd, color, cap = op
            d = _svg_path_d(cmds)
            if fill:
                rule = ' fill-rule="evenodd"' if evenodd else ""
                parts.append(f'<path d="{d}" fill="{_rgb(color)}"{rule}/>')
            else:
                parts.append(f'<path d="{d}" fill="none" stroke="{_rgb(color)}" stroke-width="{_f(sw)}" '
                             f'stroke-linecap="{cap}" stroke-linejoin="round"/>')
        elif kind == "text":
            _, x, y, s, size, font, anchor, angle, color = op
            _, family, weight, style = FONTS.get(font, FONTS["serif"])
            ta = {"start": "start", "middle": "middle", "end": "end"}[anchor]
            tr = f' transform="rotate({_f(angle)} {_f(x)} {_f(y)})"' if angle else ""
            parts.append(f'<text x="{_f(x)}" y="{_f(y)}" font-family="{escape(family)}" font-size="{_f(size)}" '
                         f'font-weight="{weight}" font-style="{style}" text-anchor="{ta}" '
                         f'fill="{_rgb(color)}"{tr}>{escape(s)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


# ======================================================================
# PDF
# ======================================================================

def _pdf_str(s: str) -> bytes:
    raw = s.encode("cp1252", errors="replace")
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def _p(v: float) -> str:
    return _f(v)


class PDFWriter:
    def __init__(self, title: str = "", author: str = ""):
        self.pages: List[Page] = []
        self.title = title
        self.author = author

    def add(self, page: Page) -> None:
        self.pages.append(page)

    def _content(self, page: Page, fonts: dict) -> bytes:
        H = page.height
        out: List[str] = [f"1 0 0 -1 0 {_p(H)} cm", "1 J 1 j"]

        def color(c, stroke):
            return (f"{_p(c[0])} {_p(c[1])} {_p(c[2])} " + ("RG" if stroke else "rg"))

        for op in page.ops:
            kind = op[0]
            if kind == "line":
                _, x1, y1, x2, y2, width, col, cap, dash = op
                capn = {"butt": 0, "round": 1, "square": 2}[cap]
                d = f"[{' '.join(_p(v) for v in dash)}] 0 d" if dash else "[] 0 d"
                out.append(f"q {color(col, True)} {_p(width)} w {capn} J {d} "
                           f"{_p(x1)} {_p(y1)} m {_p(x2)} {_p(y2)} l S Q")
            elif kind == "rect":
                _, x, y, w, h, col = op
                out.append(f"q {color(col, False)} {_p(x)} {_p(y)} {_p(w)} {_p(h)} re f Q")
            elif kind == "path":
                _, cmds, fill, sw, evenodd, col, cap = op
                seg = []
                for c in cmds:
                    if c[0] == "M":
                        seg.append(f"{_p(c[1])} {_p(c[2])} m")
                    elif c[0] == "L":
                        seg.append(f"{_p(c[1])} {_p(c[2])} l")
                    elif c[0] == "C":
                        seg.append(" ".join(_p(v) for v in c[1:]) + " c")
                    elif c[0] == "Z":
                        seg.append("h")
                if fill:
                    out.append(f"q {color(col, False)} " + " ".join(seg) + (" f*" if evenodd else " f") + " Q")
                else:
                    capn = {"butt": 0, "round": 1, "square": 2}[cap]
                    out.append(f"q {color(col, True)} {_p(sw)} w {capn} J " + " ".join(seg) + " S Q")
            elif kind == "text":
                _, x, y, s, size, font, anchor, angle, col = op
                base = FONTS.get(font, FONTS["serif"])[0]
                fid = fonts.setdefault(base, f"F{len(fonts) + 1}")
                t = math.radians(angle)
                c, sn = math.cos(t), math.sin(t)
                w = text_width(s, font, size)
                shift = {"start": 0.0, "middle": w / 2, "end": w}[anchor]
                tx, ty = x - c * shift, y - sn * shift
                out.append(f"q {color(col, False)} BT /{fid} {_p(size)} Tf "
                           f"{_p(c)} {_p(sn)} {_p(sn)} {_p(-c)} {_p(tx)} {_p(ty)} Tm "
                           + _pdf_str(s).decode("latin-1") + " Tj ET Q")
        return "\n".join(out).encode("latin-1")

    def save(self, path: str) -> None:
        fonts: dict = {}
        contents = [zlib.compress(self._content(p, fonts)) for p in self.pages]
        # object numbering: 1 catalog, 2 pages, 3 info, fonts, then page + content pairs
        font_ids = {}
        next_id = 4
        for base, fid in fonts.items():
            font_ids[fid] = next_id
            next_id += 1
        page_ids = []
        for _ in self.pages:
            page_ids.append((next_id, next_id + 1))
            next_id += 2
        font_res = " ".join(f"/{fid} {oid} 0 R" for fid, oid in font_ids.items())
        objects = {}
        objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
        kids = " ".join(f"{pid} 0 R" for pid, _ in page_ids)
        objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(self.pages)} >>".encode()
        objects[3] = (b"<< /Producer (vertSheet) /Title " + _pdf_str(self.title or "vertSheet") +
                      (b" /Author " + _pdf_str(self.author) if self.author else b"") + b" >>")
        for base, fid in fonts.items():
            objects[font_ids[fid]] = (f"<< /Type /Font /Subtype /Type1 /BaseFont /{base} "
                                      f"/Encoding /WinAnsiEncoding >>").encode()
        for (pid, cid), page, data in zip(page_ids, self.pages, contents):
            objects[pid] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_p(page.width)} {_p(page.height)}] "
                            f"/Resources << /Font << {font_res} >> >> /Contents {cid} 0 R >>").encode()
            objects[cid] = f"<< /Length {len(data)} /Filter /FlateDecode >>\nstream\n".encode() + data + b"\nendstream"
        buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = {}
        for oid in range(1, next_id):
            offsets[oid] = len(buf)
            buf += f"{oid} 0 obj\n".encode() + objects[oid] + b"\nendobj\n"
        xref = len(buf)
        buf += f"xref\n0 {next_id}\n0000000000 65535 f \n".encode()
        for oid in range(1, next_id):
            buf += f"{offsets[oid]:010d} 00000 n \n".encode()
        buf += f"trailer\n<< /Size {next_id} /Root 1 0 R /Info 3 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        with open(path, "wb") as fh:
            fh.write(buf)


def pages_to_html(pages: Sequence[Page], title: str) -> str:
    body = "\n".join(f'<div class="page">{page_to_svg(p)}</div>' for p in pages)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title or 'vertSheet')}</title>
<style>
  :root {{ --bg: #e9e9ec; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg: #1d1d20; }} }}
  body {{ margin: 0; background: var(--bg); font-family: system-ui, sans-serif; }}
  .page {{ margin: 24px auto; width: fit-content; box-shadow: 0 2px 12px rgba(0,0,0,.25); background: #fff; }}
  .page svg {{ display: block; max-width: 100vw; height: auto; }}
  @media print {{
    body {{ background: none; }}
    .page {{ margin: 0; box-shadow: none; page-break-after: always; }}
    .page svg {{ width: 100%; height: auto; }}
  }}
</style></head><body>
{body}
</body></html>
"""
