"""Vertical layout: spacing, column (system) breaking and page geometry.

Time flows from the top of a column to its bottom. Each *column* is one
system made of a pair of staves (LH on the left, RH on the right) that share
a common vertical time grid, so simultaneous notes of the two hands sit at
exactly the same height. Up to ``opts.pairs`` (max 3) columns are placed on
each page, read from left to right.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

from . import glyphs as G
from .analyze import Options
from .model import Clef, HANDS, Measure, Score

START_PAD = 1.0
END_PAD = 0.9
MIN_SLOT_PAD = 0.3
SMALL_CLEF = 0.72


def ideal_space(dt: Fraction, factor: float) -> float:
    if dt <= 0:
        return 0.0
    return factor * (1.4 + 1.8 * math.log2(1 + 2 * float(dt)))


@dataclass
class Slot:
    key: Tuple[Fraction, int]
    before: float = 0.0
    after: float = 0.0
    clef_before: Dict[str, Clef] = field(default_factory=dict)


@dataclass
class MeasureSpec:
    slots: List[Slot]
    gaps: List[Tuple[float, float]]
    inline_extra: float = 0.0
    inline_clefs: Dict[str, Clef] = field(default_factory=dict)
    inline_key: bool = False
    inline_time: bool = False

    def total(self, s: float, first: bool) -> float:
        t = sum(max(mn, ideal * s) for mn, ideal in self.gaps)
        return t + (0.0 if first else self.inline_extra)


@dataclass
class Header:
    """Vertical layout of the system start (all values in staff spaces)."""
    clefs: Dict[str, Clef]
    fifths: int
    time: Optional[object]
    clef_top: float = 0.0
    clef_h: float = 0.0
    key_top: float = 0.0
    key_h: float = 0.0
    time_top: float = 0.0
    time_h: float = 0.0
    height: float = 0.0


@dataclass
class Column:
    index: int
    page: int
    slot: int
    x0: float
    width: float
    y_top: float
    y_bottom: float
    sp: float
    lh_x0: float = 0.0
    rh_x0: float = 0.0
    measures: List[Measure] = field(default_factory=list)
    header: Optional[Header] = None
    system_y: float = 0.0
    end_y: float = 0.0
    boxes: list = field(default_factory=list)

    def staff_x0(self, hand: str) -> float:
        return self.lh_x0 if hand == "LH" else self.rh_x0

    def x(self, hand: str, pos: float) -> float:
        return self.staff_x0(hand) + pos * self.sp / 2

    @property
    def x1(self) -> float:
        return self.x0 + self.width


# --------------------------------------------------------------------------

def key_step(mode: str) -> float:
    return 1.22 if mode == "rot" else 0.85


def time_sig_height(time, mode: str) -> float:
    if time is None:
        return 0.0
    if time.symbol in ("common", "cut"):
        return 2.0 if mode == "rot" else 2.6
    digits = max(len(time.beats_text or str(time.beats)), len(str(time.beat_type)))
    if mode == "rot":
        return 1.45 * digits + 0.2
    return 4.3


def clef_extent(clef: Clef, mode: str, scale: float = 1.0) -> Tuple[float, float]:
    sc = scale if mode == "rot" else scale * 0.72
    return G.time_extent(clef.glyph, mode, sc)


def make_header(m: Measure, show_time: bool, mode: str) -> Header:
    h = Header(clefs=dict(m.start_clef), fifths=m.key.fifths, time=m.time if show_time else None)
    y = 2.0  # brace + system start line
    h.clef_top = y + 0.5
    h.clef_h = max(clef_extent(c, mode)[1] - clef_extent(c, mode)[0] for c in h.clefs.values())
    y = h.clef_top + h.clef_h + 0.6
    n = abs(h.fifths)
    if n:
        h.key_top = y
        acc = "sharp" if h.fifths > 0 else "flat"
        t0, t1 = G.time_extent(acc, mode)
        h.key_h = (n - 1) * key_step(mode) + (t1 - t0)
        y += h.key_h + 0.6
    if h.time is not None:
        h.time_top = y
        h.time_h = time_sig_height(h.time, mode)
        y += h.time_h + 0.6
    h.height = y
    return h


class Layout:
    def __init__(self, score: Score, opts: Options):
        self.score = score
        self.opts = opts
        self.mode = opts.symbols
        W, H = opts.page_size
        self.page_w, self.page_h = W, H
        self.pair_w = (W - 2 * opts.margin_side) / opts.pairs
        fit = self.pair_w / 25.0
        self.sp = min(opts.staff_space, fit) if opts.staff_space else min(fit, 7.0)
        self.title_h = 0.0
        self.label_h = 3.2 * self.sp if opts.labels else 1.0 * self.sp
        self.columns: List[Column] = []
        self.n_pages = 0

    # ------------------------------------------------------------------
    def build_specs(self) -> None:
        mode = self.mode
        prev_key = None
        prev_time = None
        prev_clef: Dict[str, Clef] = {}
        for m in self.score.measures:
            events = [e for h in HANDS for e in m.events[h]]
            keys = sorted({e.key for e in events})
            if not keys:
                keys = [(Fraction(0), 0)]
            slots = {k: Slot(k) for k in keys}
            for e in events:
                s = slots[e.key]
                s.before = max(s.before, e.before)
                s.after = max(s.after, e.after)
            ordered = [slots[k] for k in keys]
            # mid-measure clef changes
            spec_inline_clefs: Dict[str, Clef] = {}
            for hand in HANDS:
                for onset, clef in m.clef_changes[hand]:
                    if onset == 0:
                        if not clef.same_as(prev_clef.get(hand)):
                            spec_inline_clefs[hand] = clef
                        continue
                    target = next((s for s in ordered if s.key[0] >= onset), None)
                    if target is None:
                        continue
                    t0, t1 = clef_extent(clef, mode, SMALL_CLEF)
                    target.clef_before[hand] = clef
                    target.before += (t1 - t0) + 0.4
            factor = self.opts.spacing
            gaps: List[Tuple[float, float]] = []
            start = START_PAD
            if m.left_barline and m.left_barline.repeat == "forward":
                start += 1.4
            gaps.append((start + ordered[0].before, 0.0))
            for a, b in zip(ordered, ordered[1:]):
                dt = b.key[0] - a.key[0]
                gaps.append((a.after + b.before + MIN_SLOT_PAD, ideal_space(dt, factor)))
            end = END_PAD
            rb = m.right_barline
            if rb and rb.repeat == "backward":
                end += 1.4
            elif rb and rb.style in ("light-heavy", "light-light", "heavy-heavy"):
                end += 0.6
            last = ordered[-1]
            gaps.append((last.after + end, ideal_space(m.duration - last.key[0], factor)))
            spec = MeasureSpec(ordered, gaps, inline_clefs=spec_inline_clefs)
            # inline signature changes (used when the measure does not start a column)
            extra = 0.0
            if spec_inline_clefs:
                ext = max(clef_extent(c, mode, SMALL_CLEF)[1] - clef_extent(c, mode, SMALL_CLEF)[0]
                          for c in spec_inline_clefs.values())
                extra += ext + 0.5
            if m.key_change and prev_key is not None and m.key_change.fifths != prev_key:
                spec.inline_key = True
                n = abs(m.key_change.fifths) or abs(prev_key)
                extra += n * key_step(mode) + 0.8
            if m.time_change and prev_time is not None and \
                    (m.time_change.beats, m.time_change.beat_type) != prev_time:
                spec.inline_time = True
                extra += time_sig_height(m.time_change, mode) + 0.6
            spec.inline_extra = extra
            m.spec = spec
            prev_key = m.key.fifths
            prev_time = (m.time.beats, m.time.beat_type)
            for hand in HANDS:
                c = m.start_clef[hand]
                for onset, cc in m.clef_changes[hand]:
                    c = cc
                prev_clef[hand] = c

    # ------------------------------------------------------------------
    def _column_frame(self, ci: int) -> Column:
        o = self.opts
        page, k = divmod(ci, o.pairs)
        x0 = o.margin_side + k * self.pair_w
        top = o.margin_top + (self.title_h if page == 0 else 0.0) + self.label_h
        col = Column(ci, page, k, x0, self.pair_w, top, self.page_h - o.margin_bottom, self.sp)
        sp = self.sp
        units = self.pair_w / sp
        extra = max(0.0, units - 25.0)
        left_pad = 5.0 + extra * 0.32
        gap = 7.0 + extra * 0.36
        col.lh_x0 = x0 + left_pad * sp
        col.rh_x0 = col.lh_x0 + 4 * sp + gap * sp
        return col

    def _show_time(self, i: int) -> bool:
        return i == 0 or self.score.measures[i].spec.inline_time

    def break_columns(self) -> None:
        ms = self.score.measures
        n = len(ms)
        i = 0
        ci = 0
        while i < n:
            col = self._column_frame(ci)
            header = make_header(ms[i], self._show_time(i), self.mode)
            avail = (col.y_bottom - col.y_top) / self.sp - header.height
            j = i + 1  # a column always holds at least one measure
            while j < n:
                tot = sum(m.spec.total(0.82, k == 0) for k, m in enumerate(ms[i:j + 1]))
                if tot > avail:
                    break
                j += 1
            col.measures = ms[i:j]
            col.header = header
            self.columns.append(col)
            i = j
            ci += 1
        self.n_pages = (len(self.columns) + self.opts.pairs - 1) // self.opts.pairs

    # ------------------------------------------------------------------
    def place(self) -> None:
        sp = self.sp
        for col in self.columns:
            header = col.header
            avail = (col.y_bottom - col.y_top) / sp - header.height
            specs = [m.spec for m in col.measures]

            def tot(s):
                return sum(sp_.total(s, k == 0) for k, sp_ in enumerate(specs))

            is_last = col is self.columns[-1]
            compress = 1.0
            natural = tot(1.0)
            if is_last and natural <= avail and not self.opts.justify_last and natural < 0.8 * avail:
                s = 1.0
            elif tot(0.0) >= avail:
                s = 0.0
                compress = avail / tot(0.0)
            else:
                lo, hi = 0.0, 1.0
                while tot(hi) < avail and hi < 64:
                    hi *= 2
                for _ in range(60):
                    mid = (lo + hi) / 2
                    if tot(mid) < avail:
                        lo = mid
                    else:
                        hi = mid
                s = (lo + hi) / 2
            col.system_y = col.y_top + 2.0 * sp - 0.2 * sp
            y = col.y_top + header.height * sp
            for k, m in enumerate(col.measures):
                spec = m.spec
                m.column = col
                m.y_top = y
                m.first_in_column = (k == 0)  # type: ignore[attr-defined]
                if k > 0:
                    y += spec.inline_extra * sp * compress
                m.inline_y = m.y_top  # type: ignore[attr-defined]
                m.content_top = y
                gaps = [max(mn, ideal * s) * sp * compress for mn, ideal in spec.gaps]
                y += gaps[0]
                for slot, g in zip(spec.slots, gaps[1:]):
                    m.slot_y[slot.key] = y
                    y += g
                m.y_end = y
            col.end_y = y

    def run(self, title_height: float) -> List[Column]:
        self.title_h = title_height
        self.build_specs()
        self.break_columns()
        self.place()
        return self.columns


def y_at(m: Measure, t: Fraction) -> float:
    """Interpolated y coordinate of musical time ``t`` inside measure ``m``."""
    pts = sorted((k[0], y) for k, y in m.slot_y.items() if k[1] == 0)
    if not pts:
        return m.content_top
    if t <= pts[0][0]:
        return pts[0][1]
    pts.append((m.duration, m.y_end))
    for (t0, y0), (t1, y1) in zip(pts, pts[1:]):
        if t0 <= t <= t1:
            if t1 == t0:
                return y0
            return y0 + (y1 - y0) * float((t - t0) / (t1 - t0))
    return m.y_end
