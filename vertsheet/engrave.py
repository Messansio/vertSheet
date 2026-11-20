"""The vertical engraver.

Turns laid-out columns into drawing operations on :class:`~vertsheet.canvas.Page`
objects. Drawing happens in four passes so that later elements can avoid the
earlier ones:

  A. staves, headers, barlines, noteheads, accidentals, dots, rests,
     unbeamed stems and flags, articulations
  B. beams (and their stems)
  C. ties, slurs, tuplets, fermatas, ornaments, fingerings
  D. dynamics, hairpins, words, tempo, pedal, 8va lines, voltas, measure
     numbers (placed in free space with a simple collision search)
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from . import glyphs as G
from .analyze import Analyzer, Options, HEAD_HALF
from .canvas import Page, text_width
from .layout import Column, Layout, SMALL_CLEF, clef_extent, key_step, time_sig_height, y_at
from .model import Clef, Event, HANDS, Measure, Score

BLACK = (0, 0, 0)
GREY = (0.35, 0.35, 0.38)
LIGHT = (0.55, 0.55, 0.6)
# collision boxes that texts may overlap as a last resort (everything else -
# texts, marks, lines, headers - is never overprinted while a slot exists)
NOTE_KINDS = {"chord", "rest"}


class Engraver:
    def __init__(self, score: Score, opts: Options, layout: Layout, analyzer: Analyzer):
        self.score = score
        self.opts = opts
        self.layout = layout
        self.analyzer = analyzer
        self.mode = opts.symbols
        self.sp = layout.sp
        W, H = opts.page_size
        self.pages = [Page(W, H) for _ in range(layout.n_pages)]
        self.columns = layout.columns

    # ------------------------------------------------------------------ utils
    def page(self, col: Column) -> Page:
        return self.pages[col.page]

    def box(self, col: Column, x0, y0, x1, y1, hand=None, kind="", ev=None):
        b = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), hand, kind, ev)
        col.boxes.append(b)
        return b

    def symbol_text(self, pg: Page, s: str, cx: float, cy: float, size: float, font: str,
                    color=BLACK, anchor="middle", rot: Optional[bool] = None) -> Tuple[float, float, float, float]:
        """Text that follows the notation orientation; returns its bounding box."""
        w = text_width(s, font, size)
        if rot is None:
            rot = self.mode == "rot"
        if rot:
            bx = cx - 0.3 * size
            if anchor == "middle":
                pg.text(bx, cy, s, size, font, "middle", 90, color)
                return (bx - 0.22 * size, cy - w / 2, bx + 0.75 * size, cy + w / 2)
            pg.text(bx, cy, s, size, font, "start", 90, color)
            return (bx - 0.22 * size, cy, bx + 0.75 * size, cy + w)
        if anchor == "middle":
            pg.text(cx, cy + 0.33 * size, s, size, font, "middle", 0, color)
            return (cx - w / 2, cy - 0.45 * size, cx + w / 2, cy + 0.4 * size)
        pg.text(cx - w / 2, cy + 0.66 * size, s, size, font, "start", 0, color)
        return (cx - w / 2, cy - 0.1 * size, cx + w / 2, cy + 0.85 * size)

    def text_dims(self, s: str, font: str, size: float, rot: Optional[bool] = None) -> Tuple[float, float]:
        """(extent along x, extent along y) of symbol text."""
        w = text_width(s, font, size)
        if rot is None:
            rot = self.mode == "rot"
        return (0.97 * size, w) if rot else (w, 0.95 * size)

    def zone(self, col: Column, name: str) -> Tuple[float, float]:
        sp = self.sp
        if name == "gap":
            return col.lh_x0 + 4 * sp + 0.25 * sp, col.rh_x0 - 0.25 * sp
        if name == "rh-out":
            return col.rh_x0 + 4 * sp + 0.35 * sp, col.x1 - 0.35 * sp
        return col.x0 + 0.35 * sp, col.lh_x0 - 0.35 * sp

    def free_x(self, col: Column, zone: str, y0: float, y1: float, w: float,
               notes_too: bool = True) -> Optional[float]:
        """Centre x of a free slot ``w`` wide in ``zone`` over [y0, y1], or None.

        With ``notes_too=False`` only texts, lines and marks count as obstacles
        (the slot may cross stems and noteheads)."""
        zx0, zx1 = self.zone(col, zone)
        occ = sorted((b[0], b[2]) for b in col.boxes
                     if b[3] > y0 and b[1] < y1 and b[2] > zx0 and b[0] < zx1
                     and (notes_too or b[5] not in NOTE_KINDS))
        free = []
        cur = zx0
        for a, b in occ:
            if a > cur:
                free.append((cur, a))
            cur = max(cur, b)
        if cur < zx1:
            free.append((cur, zx1))
        fits = [(a, b) for a, b in free if b - a >= w]
        if not fits:
            return None
        zc = (zx0 + zx1) / 2
        if zone == "gap":
            best = min(fits, key=lambda ab: abs(min(max(zc, ab[0] + w / 2), ab[1] - w / 2) - zc))
            return min(max(zc, best[0] + w / 2), best[1] - w / 2)
        if zone == "rh-out":
            a, _ = fits[0]
            return a + w / 2
        _, b = fits[-1]
        return b - w / 2

    def place(self, col: Column, zone: str, y0: float, length: float, w: float,
              movable: bool = True) -> Tuple[float, float]:
        """(centre x, start y) for a text or mark that runs ``length`` down the
        column from ``y0``.

        The mark is kept inside the column. When its zone is full it slides
        down past the texts already placed there (``movable`` marks only),
        and only as a last resort does it overlap stems and noteheads; two
        texts are never printed on top of each other unless no slot exists."""
        sp = self.sp
        limit = col.end_y - 0.2 * sp
        y0 = max(min(y0, limit - length), col.system_y)
        # candidate start positions: the original one, then just after each
        # text / line / mark below it - first close by, then anywhere lower
        near: List[float] = []
        far: List[float] = []
        if movable:
            zx0, zx1 = self.zone(col, zone)
            window = max(4.0 * sp, length)
            for b in col.boxes:
                if b[5] in NOTE_KINDS or b[3] <= y0 or b[2] <= zx0 or b[0] >= zx1:
                    continue
                y = b[3] + 0.25 * sp
                if y + length <= limit:
                    (near if y - y0 <= window else far).append(y)
        for group in ([y0] + sorted(set(near)), sorted(set(far))):
            for notes_too in (True, False):
                for y in group:
                    cx = self.free_x(col, zone, y, y + length, w, notes_too)
                    if cx is not None:
                        return cx, y
        return self.find_x(col, zone, y0, y0 + length, w), y0

    def find_x(self, col: Column, zone: str, y0: float, y1: float, w: float) -> float:
        cx = self.free_x(col, zone, y0, y1, w)
        if cx is not None:
            return cx
        zx0, zx1 = self.zone(col, zone)
        occ = [(b[0], b[2]) for b in col.boxes
               if b[3] > y0 and b[1] < y1 and b[2] > zx0 and b[0] < zx1]
        zc = (zx0 + zx1) / 2
        if zone == "rh-out":
            return (max(b for _, b in occ) if occ else zx0) + w / 2 + 0.2 * self.sp
        if zone == "lh-out":
            return (min(a for a, _ in occ) if occ else zx1) - w / 2 - 0.2 * self.sp
        return zc

    # ------------------------------------------------------------------ run
    def run(self) -> List[Page]:
        self.page_furniture()
        for col in self.columns:
            self.draw_column(col)
        for grp in self.analyzer.groups:
            self.draw_beam_group(grp)
        self.unbeamed_fallback()
        self.draw_tuplets()
        self.draw_ties()
        self.draw_slurs()
        self.draw_event_marks()
        self.draw_directions()
        self.draw_endings()
        self.draw_measure_numbers()
        if self.opts.debug:
            for col in self.columns:
                pg = self.page(col)
                for b in col.boxes:
                    pg.line(b[0], b[1], b[2], b[1], 0.2, (1, 0, 0))
                    pg.line(b[0], b[3], b[2], b[3], 0.2, (1, 0, 0))
                    pg.line(b[0], b[1], b[0], b[3], 0.2, (1, 0, 0))
                    pg.line(b[2], b[1], b[2], b[3], 0.2, (1, 0, 0))
        return self.pages

    # ------------------------------------------------------------------ pages
    def page_furniture(self) -> None:
        o = self.opts
        W, H = o.page_size
        title = o.title if o.title is not None else self.score.title
        composer = o.composer if o.composer is not None else self.score.composer
        n = len(self.pages)
        for pi, pg in enumerate(self.pages):
            if pi == 0:
                y = o.margin_top
                if title:
                    pg.text(W / 2, y + 14, title, 19, "serif-bold", "middle")
                    y += 24
                if self.score.subtitle:
                    pg.text(W / 2, y + 6, self.score.subtitle, 11, "serif-italic", "middle")
                    y += 14
                if composer:
                    pg.text(W - o.margin_side, y + 6, composer, 10, "serif", "end")
            else:
                if title:
                    pg.text(W / 2, o.margin_top - 16, title, 8.5, "serif-italic", "middle", color=GREY)
            pg.text(W / 2, H - o.margin_bottom + 20, f"{pi + 1} / {n}", 8.5, "sans", "middle", color=GREY)
        # dividers between / after the pairs of columns
        for pi, pg in enumerate(self.pages):
            cols = [c for c in self.columns if c.page == pi]
            if not cols or o.divider == "none":
                continue
            top = cols[0].y_top - self.layout.label_h + 0.2 * self.sp
            bottom = H - o.margin_bottom + 6
            count = o.pairs if o.divider == "after" else len(cols) - 1
            if o.divider == "after":
                count = len(cols)
            for k in range(count):
                x = o.margin_side + (k + 1) * self.layout.pair_w
                pg.line(x, top, x, bottom, 0.9, GREY)
        if o.labels:
            for col in self.columns:
                pg = self.page(col)
                ly = col.y_top - 0.9 * self.sp
                lh_label, rh_label = self.opts.hand_labels
                pg.text(col.x("LH", 4), ly, lh_label, 1.7 * self.sp, "sans-bold", "middle", color=GREY)
                pg.text(col.x("RH", 4), ly, rh_label, 1.7 * self.sp, "sans-bold", "middle", color=GREY)

    # ------------------------------------------------------------------ pass A
    def draw_column(self, col: Column) -> None:
        pg = self.page(col)
        sp = self.sp
        y0, y1 = col.system_y, col.end_y
        for hand in HANDS:
            for i in range(5):
                x = col.x(hand, 2 * i)
                pg.line(x, y0, x, y1, 0.11 * sp)
            self.box(col, col.x(hand, 0) - 0.05 * sp, y0, col.x(hand, 8) + 0.05 * sp, y1, hand, "staff")
        pg.line(col.lh_x0, y0, col.rh_x0 + 4 * sp, y0, 0.2 * sp)
        self.brace(pg, col.lh_x0, col.rh_x0 + 4 * sp, y0 - 0.35 * sp, 1.25 * sp)
        self.draw_header(col)
        for m in col.measures:
            self.draw_measure(col, m)

    def brace(self, pg: Page, xa: float, xb: float, yb: float, d: float) -> None:
        xm = (xa + xb) / 2
        t = 0.42 * self.sp
        for sgn, x_end in ((1, xa), (-1, xb)):
            c1 = (x_end, yb - d * 1.15)
            c2 = (xm, yb + d * 0.2)
            c1i = (x_end, yb - d * 1.15 + t)
            c2i = (xm, yb + d * 0.2 + t)
            pg.path([("M", x_end, yb), ("C", *c1, *c2, xm, yb - d), ("C", *c2i, *c1i, x_end, yb), ("Z",)])
            _ = sgn

    def clef_glyph(self, pg: Page, col: Column, hand: str, clef: Clef, top: float, scale: float = 1.0) -> float:
        sp = self.sp
        name = clef.glyph
        t0, t1 = clef_extent(clef, self.mode, scale)
        cy = top - t0 * sp
        if self.mode == "rot":
            cx = col.x(hand, clef.anchor_pos)
            pg.glyph(name, cx, cy, sp * scale, "rot")
        else:
            sc = scale * 0.72
            p0, p1 = G.pitch_extent(name, "upright", sc)
            cx = col.x(hand, 4) - (p0 + p1) / 2 * sp
            pg.glyph(name, cx, cy, sp * sc, "upright")
        return top + (t1 - t0) * sp

    def accidental_at(self, pg, col, hand, name, pos, top, scale=1.0):
        t0, t1 = G.time_extent(name, self.mode, scale)
        pg.glyph(name, col.x(hand, pos), top - t0 * self.sp, self.sp * scale, self.mode)

    def key_signature(self, pg, col, hand, clef: Clef, fifths: int, top: float, cancel_from: int = 0):
        sp = self.sp
        step = key_step(self.mode)
        k = 0
        if fifths == 0 and cancel_from:
            for pos in clef.key_positions(cancel_from):
                self.accidental_at(pg, col, hand, "natural", pos, top + k * step * sp)
                k += 1
            return
        name = "sharp" if fifths > 0 else "flat"
        for pos in clef.key_positions(fifths):
            self.accidental_at(pg, col, hand, name, pos, top + k * step * sp)
            k += 1

    def time_signature(self, pg, col, hand, ts, top: float) -> None:
        sp = self.sp
        size = 2.9 * sp
        h = time_sig_height(ts, self.mode)
        if ts.symbol in ("common", "cut"):
            cy = top + h / 2 * sp
            self.symbol_text(pg, "C", col.x(hand, 4), cy, size, "serif-bold")
            if ts.symbol == "cut":
                if self.mode == "rot":
                    pg.line(col.x(hand, -1), cy, col.x(hand, 9), cy, 0.16 * sp)
                else:
                    pg.line(col.x(hand, 4), cy - 1.6 * sp, col.x(hand, 4), cy + 1.6 * sp, 0.16 * sp)
            return
        num = ts.beats_text or str(ts.beats)
        den = str(ts.beat_type)
        if self.mode == "rot":
            cy = top + h / 2 * sp
            self.symbol_text(pg, num, col.x(hand, 6), cy, size, "serif-bold")
            self.symbol_text(pg, den, col.x(hand, 2), cy, size, "serif-bold")
        else:
            self.symbol_text(pg, num, col.x(hand, 4), top + 1.05 * sp, size, "serif-bold")
            self.symbol_text(pg, den, col.x(hand, 4), top + 3.2 * sp, size, "serif-bold")

    def draw_header(self, col: Column) -> None:
        pg = self.page(col)
        h = col.header
        sp = self.sp
        base = col.y_top
        for hand in HANDS:
            clef = h.clefs[hand]
            self.clef_glyph(pg, col, hand, clef, base + h.clef_top * sp)
            if self.mode == "rot":
                p0, p1 = G.pitch_extent(clef.glyph, "rot")
                cx = col.x(hand, clef.anchor_pos)
                bx0, bx1 = min(col.x(hand, 0), cx + p0 * sp), max(col.x(hand, 8), cx + p1 * sp)
            else:
                bx0, bx1 = col.x(hand, -1), col.x(hand, 9)
            self.box(col, bx0, col.system_y, bx1, base + h.height * sp - 0.5 * sp, hand, "header")
            if h.fifths:
                self.key_signature(pg, col, hand, clef, h.fifths, base + h.key_top * sp)
            if h.time is not None:
                self.time_signature(pg, col, hand, h.time, base + h.time_top * sp)

    def barline(self, col: Column, y: float, bl, end: bool = True) -> None:
        pg = self.page(col)
        sp = self.sp
        xa, xb = col.lh_x0, col.rh_x0 + 4 * sp
        thin, thick = 0.16 * sp, 0.5 * sp
        style = bl.style if bl else "regular"
        rep = bl.repeat if bl else None

        def dots(yc):
            for hand in HANDS:
                for p in (3, 5):
                    pg.path(G.circle(col.x(hand, p), yc, 0.22 * sp))

        if end:
            if rep == "backward" or style == "light-heavy":
                pg.rect(xa, y - thick, xb - xa, thick)
                pg.line(xa, y - thick - 0.45 * sp, xb, y - thick - 0.45 * sp, thin)
                if rep == "backward":
                    dots(y - thick - 1.1 * sp)
            elif style == "light-light":
                pg.line(xa, y, xb, y, thin)
                pg.line(xa, y - 0.5 * sp, xb, y - 0.5 * sp, thin)
            elif style == "heavy-heavy":
                pg.rect(xa, y - thick, xb - xa, thick)
                pg.rect(xa, y - 2 * thick - 0.4 * sp, xb - xa, thick)
            elif style in ("dashed", "dotted"):
                pg.line(xa, y, xb, y, thin, dash=(0.6 * sp, 0.5 * sp))
            elif style == "none":
                pass
            else:
                pg.line(xa, y, xb, y, thin)
        else:
            if rep == "forward" or style == "heavy-light":
                pg.rect(xa, y, xb - xa, thick)
                pg.line(xa, y + thick + 0.45 * sp, xb, y + thick + 0.45 * sp, thin)
                if rep == "forward":
                    dots(y + thick + 1.1 * sp)

    def draw_measure(self, col: Column, m: Measure) -> None:
        pg = self.page(col)
        sp = self.sp
        spec = m.spec
        self.barline(col, m.y_end, m.right_barline, end=True)
        if m.left_barline and (m.left_barline.repeat == "forward" or m.left_barline.style == "heavy-light"):
            self.barline(col, m.y_top, m.left_barline, end=False)
        # inline signature changes
        if not m.first_in_column:  # type: ignore[attr-defined]
            y = m.y_top + 0.45 * sp
            if m.left_barline and m.left_barline.repeat == "forward":
                y += 1.4 * sp
            if spec.inline_clefs:
                ext = 0.0
                for hand, clef in spec.inline_clefs.items():
                    bottom = self.clef_glyph(pg, col, hand, clef, y, SMALL_CLEF)
                    ext = max(ext, bottom - y)
                y += ext + 0.5 * sp
            if spec.inline_key:
                prev = self.score.measures[m.index - 1].key.fifths if m.index else 0
                for hand in HANDS:
                    clef = m.start_clef[hand]
                    self.key_signature(pg, col, hand, clef, m.key.fifths, y, cancel_from=prev)
                n = abs(m.key.fifths) or abs(prev)
                y += n * key_step(self.mode) * sp + 0.8 * sp
            if spec.inline_time:
                for hand in HANDS:
                    self.time_signature(pg, col, hand, m.time, y)
        # mid-measure clef changes
        for slot in spec.slots:
            for hand, clef in slot.clef_before.items():
                top = m.slot_y[slot.key] - slot.before * sp + 0.05 * sp
                self.clef_glyph(pg, col, hand, clef, top, SMALL_CLEF)
        for hand in HANDS:
            for ev in m.events[hand]:
                if ev.is_rest:
                    self.draw_rest(col, m, ev)
                else:
                    self.draw_chord(col, m, ev)

    # ------------------------------------------------------------------ rests
    def draw_rest(self, col: Column, m: Measure, ev: Event) -> None:
        pg = self.page(col)
        sp = self.sp
        s = ev.scale
        y = m.slot_y.get(ev.key)
        if y is None:
            return
        name = G.REST_GLYPH.get(ev.type, "rest_quarter")
        hand = ev.hand
        if ev.measure_rest:
            name = "rest_breve" if m.duration >= 8 else "rest_whole"
            y = (m.content_top + m.y_end) / 2
        clef = getattr(ev, "clef", None) or m.start_clef[hand]
        if ev.rest_display:
            dp = clef.staff_position(*ev.rest_display)
            if name == "rest_whole":
                dp -= 2
        else:
            dp = 4
            if getattr(ev, "multi", False):
                dp += 2 if int(ev.voice) % 2 == 1 else -2 if ev.voice.isdigit() else 0
        x0, y0, x1, y1 = G.bbox(name)
        if self.mode == "rot":
            cx = col.x(hand, dp)
            cy = y - (x0 + x1) / 2 * sp * s
        else:
            cx = col.x(hand, dp)
            cy = y + (y0 + y1) / 2 * sp * s
        pg.glyph(name, cx, cy, sp * s, self.mode)
        t0, t1 = G.time_extent(name, self.mode, s)
        p0, p1 = G.pitch_extent(name, self.mode, s)
        for k in range(ev.dots):
            pg.path(G.circle(col.x(hand, dp + 1), cy + (t1 + 0.35 + 0.5 * k) * sp, 0.19 * sp * s))
        ev.geom = {"col": col, "y": y, "x_lo": cx + p0 * sp, "x_hi": cx + p1 * sp, "rest": True}
        self.box(col, cx + p0 * sp, cy + t0 * sp, cx + p1 * sp, cy + t1 * sp, hand, "rest", ev)

    # ------------------------------------------------------------------ chords
    @staticmethod
    def head_kind(ev: Event) -> str:
        nh = ev.notes[0].notehead if ev.notes else "normal"
        if nh in ("x", "cross", "circle-x"):
            return "x"
        if nh == "diamond":
            return "diamond"
        if ev.type in ("breve", "long", "maxima"):
            return "breve"
        if ev.type == "whole":
            return "whole"
        if ev.type == "half":
            return "half"
        return "black"

    def draw_chord(self, col: Column, m: Measure, ev: Event) -> None:
        pg = self.page(col)
        sp = self.sp
        s = ev.scale
        y = m.slot_y.get(ev.key)
        if y is None or not ev.notes:
            return
        hand = ev.hand
        hy = G.HEAD_HALF_TIME.get(ev.type, HEAD_HALF) * sp * s
        shift = 2 * hy * 0.95
        kind = self.head_kind(ev)
        shapes = G.notehead_shapes(kind)
        heads = []
        for n in ev.notes:
            nx = col.x(hand, n.pos)
            ny = y + n.displaced * shift
            pg.shapes(shapes, G.transform("rot", nx, ny, sp * s), sp * s)
            heads.append((n, nx, ny))
        lo = min(n.pos for n in ev.notes)
        hi = max(n.pos for n in ev.notes)
        any_before = any(n.displaced < 0 for n in ev.notes)
        any_after = any(n.displaced > 0 for n in ev.notes)
        # ledger lines
        ya = y - hy - 0.3 * sp * s - (shift if any_before else 0)
        yb = y + hy + 0.3 * sp * s + (shift if any_after else 0)
        for p in range(-2, lo - 1, -2):
            x = col.x(hand, p)
            pg.line(x, ya, x, yb, 0.16 * sp)
        for p in range(10, hi + 1, 2):
            x = col.x(hand, p)
            pg.line(x, ya, x, yb, 0.16 * sp)
        x_lo = col.x(hand, lo) - 0.55 * sp * s
        x_hi = col.x(hand, hi) + 0.55 * sp * s
        if lo <= -2:
            x_lo = min(x_lo, col.x(hand, lo if lo % 2 == 0 else lo + 1) - 0.1 * sp)
        # accidentals
        acc_end = y - hy - (shift if any_before else 0) - 0.22 * sp * s
        widths = ev.acc_widths
        acc_top = acc_end
        for n in ev.notes:
            if not n.accidental:
                continue
            name = G.ACCIDENTAL_GLYPH.get(n.accidental, "natural")
            c = n.acc_col
            end_y = acc_end - (sum(widths[:c]) + 0.12 * c) * sp * s
            t0, t1 = G.time_extent(name, self.mode, s)
            cy = end_y - t1 * sp
            cx = col.x(hand, n.pos)
            pg.glyph(name, cx, cy, sp * s, self.mode)
            p0, p1 = G.pitch_extent(name, self.mode, s)
            x_lo = min(x_lo, cx + p0 * sp)
            x_hi = max(x_hi, cx + p1 * sp)
            acc_top = min(acc_top, cy + t0 * sp)
        # dots
        if ev.dots:
            base = y + hy + (shift if any_after else 0) + 0.38 * sp * s
            done = set()
            for n in ev.notes:
                dp = n.pos if n.pos % 2 else (n.pos - 1 if (ev.stem_dir < 0 and getattr(ev, "multi", False))
                                              else n.pos + 1)
                if dp in done:
                    continue
                done.add(dp)
                for k in range(ev.dots):
                    pg.path(G.circle(col.x(hand, dp), base + (0.2 + 0.5 * k) * sp * s, 0.19 * sp * s))
                x_hi = max(x_hi, col.x(hand, dp) + 0.2 * sp)
        # arpeggio (a wavy line before the chord)
        if ev.arpeggio:
            ay = acc_top - 0.55 * sp
            xs = col.x(hand, lo) - 0.6 * sp
            xe = col.x(hand, hi) + 0.6 * sp
            pts = []
            k = 0
            x = xs
            while x <= xe:
                pts.append((x, ay + (0.22 if k % 2 else -0.22) * sp))
                x += 0.45 * sp
                k += 1
            if len(pts) >= 2:
                pg.path([("M", *pts[0])] + [("L", *p) for p in pts[1:]], fill=False, stroke_width=0.14 * sp)
            acc_top = ay - 0.4 * sp
        geom = {"col": col, "y": y, "hy": hy, "heads": heads, "x_lo": x_lo, "x_hi": x_hi,
                "lo": lo, "hi": hi, "y_top": acc_top, "y_bot": y + ev.after * sp}
        ev.geom = geom
        # stem
        d = ev.stem_dir or 1
        if ev.has_stem:
            stem_y = y + hy * 0.94 if d > 0 else y - hy * 0.94
            geom["stem_y"] = stem_y
            geom["base_x"] = col.x(hand, lo) if d > 0 else col.x(hand, hi)
            if ev.beam_group is None:
                self.single_stem(col, ev)
        # articulations
        if ev.articulations:
            side = -d if ev.has_stem else 1
            xx = (col.x(hand, lo) - 1.15 * sp) if side < 0 else (col.x(hand, hi) + 1.15 * sp)
            for a in ev.articulations:
                if a not in G.GLYPH_BUILDERS:
                    continue
                pg.glyph(a, xx, y, sp * s, self.mode)
                geom["x_lo"] = min(geom["x_lo"], xx - 0.6 * sp)
                geom["x_hi"] = max(geom["x_hi"], xx + 0.6 * sp)
                xx += side * 0.95 * sp
        geom["box"] = self.box(col, geom["x_lo"], geom["y_top"], geom["x_hi"], geom["y_bot"], hand, "chord", ev)

    def single_stem(self, col: Column, ev: Event) -> None:
        pg = self.page(col)
        sp = self.sp
        s = ev.scale
        g = ev.geom
        hand = ev.hand
        d = ev.stem_dir or 1
        length = 3.5 + max(0, ev.flags - 2) * 0.75
        if d > 0:
            tip = col.x(hand, g["hi"]) + length * sp * s
            if not ev.grace:
                tip = max(tip, col.x(hand, 4))
        else:
            tip = col.x(hand, g["lo"]) - length * sp * s
            if not ev.grace:
                tip = min(tip, col.x(hand, 4))
        sy = g["stem_y"]
        pg.line(g["base_x"], sy, tip, sy, 0.12 * sp * max(s, 0.8))
        if ev.flags:
            pg.glyph(f"flag_{'up' if d > 0 else 'down'}_{min(ev.flags, 6)}", tip, sy, sp * s, "rot")
        if ev.grace and ev.grace_slash:
            sx = g["base_x"] + (tip - g["base_x"]) * 0.62
            pg.line(sx - 0.75 * sp * s, sy - 0.9 * sp * s, sx + 0.75 * sp * s, sy + 0.9 * sp * s, 0.1 * sp)
        g["tip_x"] = tip
        g["x_lo"] = min(g["x_lo"], tip - 0.1 * sp)
        g["x_hi"] = max(g["x_hi"], tip + 0.1 * sp)
        if ev.flags:
            g["y_bot"] = max(g["y_bot"], sy + 2.0 * sp * s)

    # ------------------------------------------------------------------ pass B
    def draw_beam_group(self, grp) -> None:
        evs = [e for e in grp.events if e.geom and not e.is_rest]
        runs: List[List[Event]] = []
        for e in evs:
            if runs and runs[-1][-1].geom["col"] is e.geom["col"]:
                runs[-1].append(e)
            else:
                runs.append([e])
        for run in runs:
            if len(run) == 1:
                e = run[0]
                if e.has_stem and "stem_y" in e.geom:
                    self.single_stem(e.geom["col"], e)
                    self.refresh_box(e)
            else:
                self.draw_beam(run, grp)

    def refresh_box(self, e: Event) -> None:
        g = e.geom
        col = g["col"]
        old = g.get("box")
        if old in col.boxes:
            col.boxes.remove(old)
        g["box"] = self.box(col, g["x_lo"], g["y_top"], g["x_hi"], g["y_bot"], e.hand, "chord", e)

    def draw_beam(self, run: List[Event], grp) -> None:
        col = run[0].geom["col"]
        pg = self.page(col)
        sp = self.sp
        s = run[0].scale
        hand = run[0].hand
        d = grp.direction
        levels = max(1, max(e.flags for e in run))
        extra = (levels - 1) * 0.75
        if d > 0:
            tip = max(col.x(hand, e.geom["hi"]) + (3.25 + extra) * sp * s for e in run)
            if not run[0].grace:
                tip = max(tip, col.x(hand, 4))
        else:
            tip = min(col.x(hand, e.geom["lo"]) - (3.25 + extra) * sp * s for e in run)
            if not run[0].grace:
                tip = min(tip, col.x(hand, 4))
        ys = []
        for e in run:
            g = e.geom
            sy = g["y"] + g["hy"] * 0.94 * (1 if d > 0 else -1)
            g["stem_y"] = sy
            base = col.x(hand, g["lo"]) if d > 0 else col.x(hand, g["hi"])
            pg.line(base, sy, tip, sy, 0.12 * sp * max(s, 0.8))
            g["tip_x"] = tip
            ys.append(sy)
        bt = 0.5 * sp * s
        step = 0.75 * sp * s

        def band(level, ya, yb):
            inset = (level - 1) * step
            if d > 0:
                x1 = tip - inset
                x0 = x1 - bt
            else:
                x0 = tip + inset
                x1 = x0 + bt
            pg.rect(x0, ya - 0.06 * sp, x1 - x0, (yb - ya) + 0.12 * sp)

        def hook(i, direction):
            j = i + direction
            if 0 <= j < len(ys):
                return min(1.15 * sp * s, 0.5 * abs(ys[j] - ys[i]))
            return 1.15 * sp * s

        band(1, ys[0], ys[-1])
        for L in range(2, levels + 1):
            use_xml = not grp.auto and any(L in e.beams for e in run)
            if use_xml:
                start = None
                for i, e in enumerate(run):
                    v = e.beams.get(L, "")
                    if v == "begin":
                        start = i
                    elif v == "continue" and start is None:
                        start = i
                    elif v == "end":
                        if start is not None:
                            band(L, ys[start], ys[i])
                        else:
                            band(L, ys[0], ys[i])
                        start = None
                    elif v == "forward hook":
                        band(L, ys[i], ys[i] + hook(i, 1))
                    elif v == "backward hook":
                        band(L, ys[i] - hook(i, -1), ys[i])
                if start is not None and start < len(run) - 1:
                    band(L, ys[start], ys[-1])
            else:
                i = 0
                while i < len(run):
                    if run[i].flags >= L:
                        j = i
                        while j + 1 < len(run) and run[j + 1].flags >= L:
                            j += 1
                        if j > i:
                            band(L, ys[i], ys[j])
                        elif i == 0:
                            band(L, ys[i], ys[i] + hook(i, 1))
                        else:
                            band(L, ys[i] - hook(i, -1), ys[i])
                        i = j + 1
                    else:
                        i += 1
        outer = tip
        inner = tip - d * (bt + (levels - 1) * step)
        self.box(col, min(outer, inner), ys[0] - 0.1 * sp, max(outer, inner), ys[-1] + 0.1 * sp, hand, "beam")
        for e in run:
            g = e.geom
            g["x_lo"] = min(g["x_lo"], tip - 0.1 * sp)
            g["x_hi"] = max(g["x_hi"], tip + 0.1 * sp)
            self.refresh_box(e)

    def unbeamed_fallback(self) -> None:
        """Events whose beam group could not be drawn still get a stem."""
        for col in self.columns:
            for m in col.measures:
                for hand in HANDS:
                    for e in m.events[hand]:
                        if e.geom and not e.is_rest and e.has_stem and "tip_x" not in e.geom \
                                and "stem_y" in e.geom:
                            self.single_stem(col, e)
                            self.refresh_box(e)

    # ------------------------------------------------------------------ pass C
    def iter_events(self, hand: str):
        for m in self.score.measures:
            for e in m.events[hand]:
                if e.geom:
                    yield m, e

    def draw_tuplets(self) -> None:
        sp = self.sp
        for hand in HANDS:
            open_: Dict[Tuple[str, str], Tuple[dict, List[Event]]] = {}
            for m, e in self.iter_events(hand):
                starts = [t for t in e.tuplets if t["type"] == "start"]
                stops = [t for t in e.tuplets if t["type"] == "stop"]
                for t in starts:
                    open_[(e.voice, t["number"])] = (t, [])
                for (voice, num), (info, lst) in open_.items():
                    if voice == e.voice:
                        lst.append(e)
                for t in stops:
                    k = (e.voice, t["number"])
                    if k in open_:
                        info, lst = open_.pop(k)
                        self.draw_tuplet(info, lst)
        _ = sp

    def draw_tuplet(self, info: dict, evs: List[Event]) -> None:
        if not evs:
            return
        col = evs[0].geom["col"]
        evs = [e for e in evs if e.geom["col"] is col]
        pg = self.page(col)
        sp = self.sp
        first = evs[0]
        tm = next((e.time_mod for e in evs if e.time_mod), None) or (3, 2)
        show = info.get("show") or "actual"
        if show == "none":
            text = ""
        elif show == "both":
            text = f"{tm[0]}:{tm[1]}"
        else:
            text = str(tm[0])
        notes = [e for e in evs if not e.is_rest]
        grp = notes[0].beam_group if notes else None
        beamed = bool(notes) and grp is not None and all(e.beam_group is grp for e in notes) and \
            len(notes) == len(evs)
        bracket = (info.get("bracket") == "yes") or (not beamed and info.get("bracket") != "no")
        if info.get("placement") == "above":
            d = 1
        elif info.get("placement") == "below":
            d = -1
        elif notes:
            d = 1 if sum(e.stem_dir for e in notes) >= 0 else -1
        else:
            d = 1
        ya = min(e.geom["y"] for e in evs) - 0.6 * sp
        yb = max(e.geom["y"] for e in evs) + 0.6 * sp
        ym = (ya + yb) / 2
        size = 1.7 * sp
        if beamed and not bracket and "tip_x" in notes[0].geom and notes[0].stem_dir == d:
            tip = notes[0].geom["tip_x"]
            cx = tip + d * 1.05 * sp
        else:
            if d > 0:
                ext = max(e.geom["x_hi"] for e in evs)
            else:
                ext = min(e.geom["x_lo"] for e in evs)
            cx = ext + d * 0.9 * sp
            if bracket:
                tx, ty = self.text_dims(text, "serif-bold-italic", size) if text else (0, 0)
                gap = (ty / 2 + 0.3 * sp) if text else 0
                hook = -d * 0.55 * sp
                pg.line(cx, ya, cx, ym - gap, 0.11 * sp)
                pg.line(cx, ym + gap, cx, yb, 0.11 * sp)
                pg.line(cx, ya, cx + hook, ya, 0.11 * sp)
                pg.line(cx, yb, cx + hook, yb, 0.11 * sp)
        if text:
            bb = self.symbol_text(pg, text, cx, ym, size, "serif-bold-italic")
            self.box(col, bb[0], bb[1], bb[2], bb[3], first.hand, "tuplet")
        self.box(col, cx - 0.3 * sp, ya, cx + 0.3 * sp, yb, first.hand, "tuplet")

    def tie_side(self, e: Event, n) -> int:
        if len(e.notes) > 1:
            ordered = sorted(e.notes, key=lambda q: q.pos)
            idx = ordered.index(n)
            mid = (len(ordered) - 1) / 2
            if idx > mid:
                return 1
            if idx < mid:
                return -1
        if e.stem_dir:
            return -e.stem_dir
        return 1 if n.pos >= 4 else -1

    def draw_ties(self) -> None:
        sp = self.sp
        for hand in HANDS:
            open_: Dict[Tuple[int, int], tuple] = {}
            for m, e in self.iter_events(hand):
                if e.is_rest:
                    continue
                for n, nx, ny in e.geom["heads"]:
                    k = (n.diatonic, int(round(n.alter)))
                    if n.tie_stop and k in open_:
                        a = open_.pop(k)
                        self.draw_tie(a, (e, n, nx, ny))
                    if n.tie_start:
                        open_[k] = (e, n, nx, ny)
        _ = sp

    def draw_tie(self, a, b) -> None:
        sp = self.sp
        ea, na, xa, ya = a
        eb, nb, xb, yb = b
        side = self.tie_side(ea, na)
        hy_a = ea.geom["hy"]
        start_y = ya + max(hy_a + 0.12 * sp, (ea.after * sp - 0.3 * sp) if ea.dots else 0)
        end_y = yb - eb.geom["hy"] - 0.12 * sp
        pa = (xa + side * 0.22 * sp, start_y)
        pb = (xb + side * 0.22 * sp, end_y)
        ca, cb = ea.geom["col"], eb.geom["col"]
        thick = 0.14 * sp
        if ca is cb:
            L = max(1.0, pb[1] - pa[1])
            h = min(0.32 * sp + 0.06 * L, 1.0 * sp)
            self.page(ca).lens(pa, pb, side, h, thick)
            self.box(ca, min(pa[0], pa[0] + side * h), pa[1], max(pa[0], pa[0] + side * h), pb[1], ea.hand, "tie")
        else:
            e1 = (pa[0], min(pa[1] + 3 * sp, ca.end_y + 0.5 * sp))
            self.page(ca).lens(pa, e1, side, 0.45 * sp, thick)
            s2 = (pb[0], max(pb[1] - 3 * sp, cb.y_top + cb.header.height * sp - 1.0 * sp))
            self.page(cb).lens(s2, pb, side, 0.45 * sp, thick)

    def slur_point(self, e: Event, side: int, start: bool) -> Tuple[float, float]:
        sp = self.sp
        g = e.geom
        tip = g.get("tip_x")
        if tip is not None and e.stem_dir == side:
            x = tip + side * 0.45 * sp
            y = g.get("stem_y", g["y"]) + (0.25 if start else -0.25) * sp
        else:
            x = (g["x_hi"] if side > 0 else g["x_lo"]) + side * 0.5 * sp
            if e.notes:
                col = g["col"]
                hx = col.x(e.hand, g["hi"] if side > 0 else g["lo"])
                x = hx + side * 0.9 * sp
            y = g["y"] + (0.45 if start else -0.45) * sp
        return x, y

    def draw_slurs(self) -> None:
        for hand in HANDS:
            open_: Dict[str, Tuple[Event, Optional[str]]] = {}
            order: List[Event] = []
            for m, e in self.iter_events(hand):
                order.append(e)
                for sl in e.slurs:
                    if sl["type"] == "stop" and sl["number"] in open_:
                        a, placement = open_.pop(sl["number"])
                        if a is not e:
                            self.draw_slur(a, e, placement, order)
                for sl in e.slurs:
                    if sl["type"] == "start":
                        open_[sl["number"]] = (e, sl.get("placement"))

    def draw_slur(self, a: Event, b: Event, placement: Optional[str], order: List[Event]) -> None:
        sp = self.sp
        if placement == "above":
            side = 1
        elif placement == "below":
            side = -1
        else:
            da, db = a.stem_dir, b.stem_dir
            if da > 0 and db > 0:
                side = -1
            elif da < 0 and db < 0:
                side = 1
            else:
                side = 1
        pa = self.slur_point(a, side, True)
        pb = self.slur_point(b, side, False)
        ca, cb = a.geom["col"], b.geom["col"]
        thick = 0.16 * sp
        if ca is cb:
            if pb[1] - pa[1] < 1.0 * sp:
                return
            L = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
            h = min(max(0.7 * sp, 0.08 * L), 2.4 * sp)
            ia = order.index(a) if a in order else 0
            for e in order[ia + 1:]:
                g = e.geom
                if g["col"] is not ca or not (pa[1] < g["y"] < pb[1]):
                    continue
                t = (g["y"] - pa[1]) / (pb[1] - pa[1])
                lx = pa[0] + (pb[0] - pa[0]) * t
                ext = g["x_hi"] if side > 0 else g["x_lo"]
                need = (ext - lx) * side + 0.55 * sp
                tt = min(max(t, 0.1), 0.9)
                h = max(h, need / (4 * tt * (1 - tt)))
            h = min(h, 6 * sp)
            self.page(ca).lens(pa, pb, side, h, thick)
            xs = [pa[0], pb[0], (pa[0] + pb[0]) / 2 + side * h]
            self.box(ca, min(xs), pa[1], max(xs), pb[1], a.hand, "slur")
        else:
            e1 = (pa[0], ca.end_y - 0.3 * sp)
            if e1[1] - pa[1] > 1.0 * sp:
                self.page(ca).lens(pa, e1, side, min(1.2 * sp, 0.1 * (e1[1] - pa[1]) + 0.5 * sp), thick)
            s2 = (pb[0], cb.y_top + cb.header.height * sp - 1.2 * sp)
            if pb[1] - s2[1] > 1.0 * sp:
                self.page(cb).lens(s2, pb, side, min(1.2 * sp, 0.1 * (pb[1] - s2[1]) + 0.5 * sp), thick)

    def draw_event_marks(self) -> None:
        """Fermatas, ornaments and fingerings (placed outside everything else)."""
        sp = self.sp
        for hand in HANDS:
            for m, e in self.iter_events(hand):
                g = e.geom
                col = g["col"]
                pg = self.page(col)
                y = g["y"]
                if e.fermata:
                    side = -1 if e.fermata == "inverted" else 1
                    ext = g["x_hi"] if side > 0 else g["x_lo"]
                    if self.mode == "rot":
                        cx = ext + side * 0.45 * sp
                        if side > 0:
                            pg.glyph("fermata", cx, y, sp, "rot")
                        else:
                            pg.shapes(G.glyph("fermata"), lambda gx, gy, cx=cx: (cx - gy * sp, y + gx * sp), sp)
                        self.box(col, cx, y - 1.05 * sp, cx + side * 1.1 * sp, y + 1.05 * sp, hand, "mark")
                    else:
                        cx = ext + side * 1.5 * sp
                        pg.glyph("fermata", cx, y + 0.5 * sp, sp, "upright")
                        self.box(col, cx - 1.05 * sp, y - 0.6 * sp, cx + 1.05 * sp, y + 0.6 * sp, hand, "mark")
                    if side > 0:
                        g["x_hi"] = max(g["x_hi"], cx + 1.15 * sp)
                    else:
                        g["x_lo"] = min(g["x_lo"], cx - 1.15 * sp)
                for orn in e.ornaments:
                    ext = g["x_hi"]
                    if orn == "trill-mark":
                        dx, dy = self.text_dims("tr", "serif-bold-italic", 2.0 * sp)
                        cx = ext + 0.3 * sp + dx / 2
                        bb = self.symbol_text(pg, "tr", cx, y, 2.0 * sp, "serif-bold-italic")
                        self.box(col, *bb[:4], hand, "mark")
                        g["x_hi"] = bb[2]
                    elif orn in G.GLYPH_BUILDERS:
                        cx = ext + 0.9 * sp
                        pg.glyph(orn, cx, y, sp, self.mode)
                        self.box(col, cx - 0.7 * sp, y - 1.0 * sp, cx + 0.7 * sp, y + 1.0 * sp, hand, "mark")
                        g["x_hi"] = cx + 0.8 * sp
                fingers = [n for n in e.notes if n.fingering]
                if fingers:
                    side = 1 if hand == "RH" else -1
                    ext = g["x_hi"] if side > 0 else g["x_lo"]
                    size = 1.45 * sp
                    order = sorted(fingers, key=lambda n: n.pos, reverse=(side < 0))
                    x = ext + side * 0.35 * sp
                    for n in order:
                        dx, dy = self.text_dims(n.fingering, "sans", size)
                        cx = x + side * dx / 2
                        bb = self.symbol_text(pg, n.fingering, cx, y, size, "sans")
                        self.box(col, *bb[:4], hand, "finger")
                        x += side * (dx + 0.15 * sp)

    # ------------------------------------------------------------------ pass D
    def direction_zone(self, d) -> str:
        if d.kind == "pedal":
            return "lh-out"
        if d.kind in ("metronome", "rehearsal"):
            return "rh-out"
        if d.kind == "octave":
            return "rh-out" if d.hand == "RH" else "lh-out"
        if d.hand == "RH":
            if d.kind == "words":
                return "gap" if d.placement == "below" else "rh-out"
            return "rh-out" if d.placement == "above" else "gap"
        if d.placement == "below":
            return "lh-out"
        return "gap"

    def draw_directions(self) -> None:
        sp = self.sp
        wedges: Dict[Tuple[str, int], tuple] = {}
        octaves: Dict[Tuple[str, int], tuple] = {}
        pedal_open: Optional[tuple] = None
        for m in self.score.measures:
            col = m.column
            if col is None:
                continue
            pg = self.page(col)
            for d in sorted(m.directions, key=lambda d: d.onset):
                y = y_at(m, d.onset)
                zone = self.direction_zone(d)
                if d.kind == "dynamics":
                    size = 2.3 * sp
                    dx, dy = self.text_dims(d.text, "serif-bold-italic", size)
                    if self.mode == "rot":
                        # rotated text starts at the note and runs downwards (never across the barline)
                        y0 = max(y - 0.45 * sp, m.content_top + 0.3 * sp)
                        cx, y0 = self.place(col, zone, y0, dy, dx + 0.2 * sp, movable=False)
                        bb = self.symbol_text(pg, d.text, cx, y0, size, "serif-bold-italic", anchor="start")
                    else:
                        cx, y0 = self.place(col, zone, y - dy / 2, dy, dx + 0.2 * sp, movable=False)
                        bb = self.symbol_text(pg, d.text, cx, y0 + dy / 2, size, "serif-bold-italic")
                    self.box(col, *bb[:4], d.hand, "dyn")
                elif d.kind == "words":
                    size = 1.85 * sp
                    italic = "serif-italic"
                    at_top = d.onset == 0 and m.first_in_column and zone == "rh-out"  # type: ignore
                    tempo_like = any(o.kind == "metronome" and o.onset == d.onset for o in m.directions)
                    if d.hand == "RH" and d.placement == "above" and d.onset == 0 and \
                            (m.index == 0 or tempo_like):
                        italic = "serif-bold"   # tempo heading
                    zx0, zx1 = self.zone(col, zone)
                    rot = self.mode == "rot" or self.text_dims(d.text, italic, size, False)[0] > zx1 - zx0
                    dx, dy = self.text_dims(d.text, italic, size, rot)
                    y0 = col.system_y + 0.2 * sp if at_top else y - 0.3 * sp
                    cx, y0 = self.place(col, zone, y0, dy, min(dx, zx1 - zx0) + 0.1 * sp)
                    bb = self.symbol_text(pg, d.text, cx, y0, size, italic, anchor="start", rot=rot)
                    self.box(col, *bb[:4], d.hand, "words")
                elif d.kind == "rehearsal":
                    size = 2.0 * sp
                    dx, dy = self.text_dims(d.text, "sans-bold", size)
                    cx = self.find_x(col, zone, y - dy / 2 - 0.5 * sp, y + dy / 2 + 0.5 * sp, dx + 0.8 * sp)
                    bb = self.symbol_text(pg, d.text, cx, y, size, "sans-bold")
                    x0, y0_, x1, y1_ = bb[0] - 0.3 * sp, bb[1] - 0.3 * sp, bb[2] + 0.3 * sp, bb[3] + 0.3 * sp
                    for (p, q) in (((x0, y0_), (x1, y0_)), ((x1, y0_), (x1, y1_)),
                                   ((x1, y1_), (x0, y1_)), ((x0, y1_), (x0, y0_))):
                        pg.line(p[0], p[1], q[0], q[1], 0.12 * sp)
                    self.box(col, x0, y0_, x1, y1_, d.hand, "reh")
                elif d.kind == "metronome":
                    self.metronome(col, m, d, y, zone)
                elif d.kind == "wedge":
                    k = (d.hand, d.number)
                    if d.subtype in ("crescendo", "diminuendo"):
                        wedges[k] = (col, y, d.subtype, zone)
                    elif d.subtype == "stop" and k in wedges:
                        self.hairpin(wedges.pop(k), col, y)
                elif d.kind == "octave":
                    k = (d.hand, d.number)
                    if d.subtype in ("up", "down"):
                        octaves[k] = (col, y, d, zone)
                    elif d.subtype == "stop" and k in octaves:
                        self.octave_line(octaves.pop(k), col, y)
                elif d.kind == "pedal":
                    line = d.extra.get("line", False)
                    if d.subtype in ("start", "change", "resume"):
                        if pedal_open and d.subtype == "change":
                            self.pedal_mark(pedal_open, col, y, changed=True)
                        pedal_open = (col, y, line)
                        if not line:
                            self.pedal_text(col, y, "Ped.")
                    elif d.subtype in ("stop", "discontinue"):
                        if pedal_open:
                            self.pedal_mark(pedal_open, col, y)
                        pedal_open = None
        # unterminated spanners run to the end of their column
        for (col, y, typ, zone) in wedges.values():
            self.hairpin((col, y, typ, zone), col, col.end_y - 0.5 * sp)

    def metronome(self, col, m, d, y, zone) -> None:
        pg = self.page(col)
        sp = self.sp
        text = f"= {d.text}"
        size = 1.85 * sp
        zx0, zx1 = self.zone(col, zone)
        rot = self.mode == "rot" or self.text_dims(text, "serif-bold", size, False)[0] + 2.0 * sp > zx1 - zx0
        tx, ty = self.text_dims(text, "serif-bold", size, rot)
        note_len = 1.6 * sp
        total_y = (note_len + ty) if rot else max(ty, 3.0 * sp)
        width = max(tx, 2.6 * sp) if rot else tx + 2.0 * sp
        y0 = col.system_y + 0.2 * sp if (m.first_in_column and d.onset == 0) else y - 0.4 * sp  # type: ignore
        cx, y0 = self.place(col, zone, y0, total_y, width)
        kind = {"half": "half", "whole": "whole"}.get(d.subtype, "black")
        s = 0.8
        if rot:
            hx = cx - 0.6 * sp
            hy = y0 + 0.6 * sp
            pg.shapes(G.notehead_shapes(kind), G.transform("rot", hx, hy, sp * s), sp * s)
            if kind != "whole":
                pg.line(hx, hy + 0.45 * sp * s, hx + 2.6 * sp * s, hy + 0.45 * sp * s, 0.11 * sp)
            if d.extra.get("dots"):
                pg.path(G.circle(hx + 0.4 * sp, hy + 1.0 * sp, 0.15 * sp))
            self.symbol_text(pg, text, cx, y0 + note_len, size, "serif-bold", anchor="start", rot=True)
        else:
            hx = cx - tx / 2 - 0.8 * sp
            hy = y0 + 2.2 * sp
            pg.shapes(G.notehead_shapes(kind), G.transform("upright", hx, hy, sp * s), sp * s)
            if kind != "whole":
                pg.line(hx + 0.45 * sp * s, hy, hx + 0.45 * sp * s, hy - 2.6 * sp * s, 0.11 * sp)
            self.symbol_text(pg, text, cx + 0.6 * sp, y0 + 1.2 * sp, size, "serif-bold", anchor="start")
        self.box(col, cx - width / 2, y0, cx + width / 2, y0 + total_y, d.hand, "tempo")

    def hairpin(self, start, col_b, yb) -> None:
        col_a, ya, typ, zone = start
        sp = self.sp
        segs = []
        if col_a is col_b:
            segs.append((col_a, ya, max(yb, ya + 2.0 * sp), True, True))
        else:
            segs.append((col_a, ya, col_a.end_y - 0.4 * sp, True, False))
            cb_top = col_b.y_top + col_b.header.height * sp
            segs.append((col_b, cb_top, max(yb, cb_top + 2 * sp), False, True))
        total = sum(s[2] - s[1] for s in segs) or 1.0
        done = 0.0
        open_w = 0.65 * sp
        for col, y0, y1, _, _ in segs:
            pg = self.page(col)
            f0 = done / total
            f1 = (done + y1 - y0) / total
            done += y1 - y0
            if typ == "diminuendo":
                f0, f1 = 1 - f0, 1 - f1
            w0, w1 = open_w * f0, open_w * f1
            cx = self.find_x(col, zone, y0, y1, 2 * open_w + 0.3 * sp)
            pg.line(cx - w0, y0, cx - w1, y1, 0.11 * sp)
            pg.line(cx + w0, y0, cx + w1, y1, 0.11 * sp)
            self.box(col, cx - open_w, y0, cx + open_w, y1, None, "hairpin")

    def octave_line(self, start, col_b, yb) -> None:
        col_a, ya, d, zone = start
        sp = self.sp
        label = {8: "8", 15: "15", 22: "22"}.get(d.value, "8") + ("va" if d.subtype == "down" else "vb")
        if d.value >= 15:
            label = label.replace("va", "ma").replace("vb", "mb")
        hook_dir = -1 if zone == "rh-out" else 1
        segs = []
        if col_a is col_b:
            segs.append((col_a, ya, yb, True))
        else:
            segs.append((col_a, ya, col_a.end_y, True))
            segs.append((col_b, col_b.y_top + col_b.header.height * sp, yb, False))
        for i, (col, y0, y1, first) in enumerate(segs):
            pg = self.page(col)
            size = 1.6 * sp
            dx, dy = self.text_dims(label, "serif-bold-italic", size)
            cx = self.find_x(col, zone, y0 - 0.5 * sp, y1, dx + 0.2 * sp)
            yy = y0 - 0.5 * sp
            if first:
                bb = self.symbol_text(pg, label, cx, yy, size, "serif-bold-italic", anchor="start")
                yy = bb[3] + 0.3 * sp
            line_x = cx if self.mode == "rot" else cx
            if y1 > yy:
                pg.line(line_x, yy, line_x, y1, 0.1 * sp, dash=(0.5 * sp, 0.4 * sp))
            if i == len(segs) - 1:
                pg.line(line_x, y1, line_x + hook_dir * 0.8 * sp, y1, 0.1 * sp)
            self.box(col, cx - dx / 2, y0 - 0.5 * sp, cx + dx / 2, y1, None, "octave")

    def pedal_text(self, col, y, text) -> None:
        pg = self.page(col)
        sp = self.sp
        size = 1.8 * sp
        dx, dy = self.text_dims(text, "serif-bold-italic", size)
        cx = self.find_x(col, "lh-out", y - 0.2 * sp, y + dy, dx + 0.2 * sp)
        bb = self.symbol_text(pg, text, cx, y - 0.2 * sp, size, "serif-bold-italic", anchor="start")
        self.box(col, *bb[:4], "LH", "pedal")

    def pedal_mark(self, start, col_b, yb, changed=False) -> None:
        col_a, ya, line = start
        sp = self.sp
        if not line:
            self.pedal_text(col_b, yb - 0.6 * sp, "*")
            return
        segs = []
        if col_a is col_b:
            segs.append((col_a, ya, yb))
        else:
            segs.append((col_a, ya, col_a.end_y))
            segs.append((col_b, col_b.y_top + col_b.header.height * sp, yb))
        for i, (col, y0, y1) in enumerate(segs):
            pg = self.page(col)
            x = self.find_x(col, "lh-out", y0, y1, 1.0 * sp)
            pg.line(x, y0, x, y1, 0.12 * sp)
            if i == 0:
                pg.line(x, y0, x + 0.7 * sp, y0, 0.12 * sp)
            if i == len(segs) - 1:
                if changed:
                    pg.line(x, y1, x + 0.6 * sp, y1 - 0.4 * sp, 0.12 * sp)
                else:
                    pg.line(x, y1, x + 0.7 * sp, y1, 0.12 * sp)
            self.box(col, x - 0.4 * sp, y0, x + 0.8 * sp, y1, "LH", "pedal")

    def draw_endings(self) -> None:
        sp = self.sp
        open_ = None
        for m in self.score.measures:
            for bl, edge in ((m.left_barline, "left"), (m.right_barline, "right")):
                if not bl or not bl.ending_type:
                    continue
                if bl.ending_type == "start":
                    open_ = (m, bl)
                elif bl.ending_type in ("stop", "discontinue") and open_:
                    ms, bls = open_
                    self.volta(ms, m, bls, closed=(bl.ending_type == "stop"))
                    open_ = None
        if open_:
            ms, bls = open_
            self.volta(ms, ms, bls, closed=False)
        _ = sp

    def volta(self, m0: Measure, m1: Measure, bl, closed: bool) -> None:
        sp = self.sp
        cols = []
        for m in self.score.measures[m0.index:m1.index + 1]:
            if m.column is None:
                continue
            if not cols or cols[-1][0] is not m.column:
                cols.append([m.column, m.y_top, m.y_end])
            else:
                cols[-1][2] = m.y_end
        label = (bl.ending_text or (bl.ending_number or "1").replace(",", ".,") + ".")
        for i, (col, y0, y1) in enumerate(cols):
            pg = self.page(col)
            y0 += 0.2 * sp
            y1 -= 0.3 * sp
            x = self.find_x(col, "rh-out", y0, y1, 2.2 * sp) + 0.6 * sp
            pg.line(x, y0, x, y1, 0.12 * sp)
            if i == 0:
                pg.line(x, y0, x - 1.4 * sp, y0, 0.12 * sp)
                self.symbol_text(pg, label, x - 0.9 * sp, y0 + 0.4 * sp, 1.5 * sp, "serif-bold", anchor="start")
            if closed and i == len(cols) - 1:
                pg.line(x, y1, x - 1.4 * sp, y1, 0.12 * sp)
            self.box(col, x - 1.6 * sp, y0, x + 0.2 * sp, y1, "RH", "volta")

    def draw_measure_numbers(self) -> None:
        mode = self.opts.measure_numbers
        if mode == "none":
            return
        sp = self.sp
        for col in self.columns:
            pg = self.page(col)
            for k, m in enumerate(col.measures):
                if mode == "system" and k > 0:
                    continue
                text = m.number
                if not text or m.index == 0:
                    continue
                size = 1.45 * sp
                w = text_width(text, "serif-italic", size)
                yref = col.system_y if k == 0 else m.y_top
                y0, y1 = yref - 0.1 * sp, yref + 1.5 * sp
                cx = self.find_x(col, "rh-out", y0, y1, w + 0.3 * sp)
                pg.text(cx, yref + 1.25 * sp, text, size, "serif-italic", "middle", color=GREY)
                self.box(col, cx - w / 2, y0, cx + w / 2, y1, "RH", "mnum")
