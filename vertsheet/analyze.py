"""Musical analysis that has to happen before layout.

* measure durations / running key, time signature and clef state
* staff positions (with clef changes and 8va / 8vb shifts)
* accidental computation when the file does not carry <accidental> tags
* gap filling with rests (empty hands, split single-staff scores)
* beam groups (from MusicXML or automatic), stem directions
* seconds displacement in chords, accidental stacking columns
* extents of every event along the time axis (used by the spacer)
"""
from __future__ import annotations

from fractions import Fraction
from typing import Dict, List, Tuple

from . import glyphs as G
from .model import (ACCIDENTAL_FOR_ALTER, BeamGroup, Clef, Event, HANDS, Key, Score, TimeSig,
                    TYPE_QUARTERS)

HEAD_HALF = 0.59   # half extent of a normal notehead along the time axis (spaces)


class Options:
    """Rendering options shared by all stages (filled from the CLI)."""

    def __init__(self, **kw):
        self.page_size = kw.get("page_size", (595.28, 841.89))
        self.margin_top = kw.get("margin_top", 40.0)
        self.margin_bottom = kw.get("margin_bottom", 34.0)
        self.margin_side = kw.get("margin_side", 22.0)
        self.pairs = max(1, min(3, int(kw.get("pairs", 3))))
        self.staff_space = kw.get("staff_space")           # None = fit the pair width
        self.symbols = kw.get("symbols", "rot")         # "rot" or "upright"
        self.divider = kw.get("divider", "after")        # after | between | none
        self.measure_numbers = kw.get("measure_numbers", "all")
        self.labels = kw.get("labels", True)
        self.hand_labels = tuple(kw.get("hand_labels") or ("L.H.", "R.H."))   # (left, right)
        self.title = kw.get("title")
        self.composer = kw.get("composer")
        self.justify_last = kw.get("justify_last", False)
        self.spacing = kw.get("spacing", 1.0)
        self.debug = kw.get("debug", False)


# --------------------------------------------------------------------------

def _fill_rests(events: List[Event], start: Fraction, end: Fraction, hand: str, m_index: int,
                voice: str = "1") -> List[Event]:
    """Rests that fill the gaps of a single (voice) timeline."""
    out = []
    cursor = start
    for ev in sorted((e for e in events if not e.grace), key=lambda e: e.onset):
        if ev.onset > cursor:
            out.extend(_rests_between(cursor, ev.onset, hand, m_index, voice))
        cursor = max(cursor, ev.onset + ev.duration)
    if end > cursor:
        out.extend(_rests_between(cursor, end, hand, m_index, voice))
    return out


_REST_VALUES = [(t, TYPE_QUARTERS[t]) for t in ("whole", "half", "quarter", "eighth", "16th", "32nd", "64th")]


def _rests_between(a: Fraction, b: Fraction, hand: str, m_index: int, voice: str) -> List[Event]:
    out = []
    pos = a
    guard = 0
    while pos < b and guard < 64:
        guard += 1
        rem = b - pos
        chosen = None
        for t, d in _REST_VALUES:
            if d <= rem and (pos / d).denominator == 1:
                chosen = (t, d)
                break
        if chosen is None:
            for t, d in _REST_VALUES:
                if d <= rem:
                    chosen = (t, d)
                    break
        if chosen is None:  # tiny tuplet remainder
            chosen = ("64th", rem)
        t, d = chosen
        out.append(Event(onset=pos, duration=d, type=t, is_rest=True, hand=hand,
                         measure_index=m_index, voice=voice))
        pos += d
    return out


# --------------------------------------------------------------------------

class Analyzer:
    def __init__(self, score: Score, opts: Options):
        self.score = score
        self.opts = opts
        self.groups: List[BeamGroup] = []

    def run(self) -> None:
        self._measure_states()
        self._fill_gaps()
        self._positions_and_accidentals()
        if not self.score.has_xml_beams or self.score.split_mode:
            self._auto_beam()
        self._beam_groups()
        self._stems()
        self._chord_layout()

    # ------------------------------------------------------------------
    def _measure_states(self) -> None:
        key, time = Key(), TimeSig()
        clefs = {"RH": Clef("G", 2), "LH": Clef("F", 4)}
        first_clef_seen = {h: False for h in HANDS}
        for m in self.score.measures:
            if m.key_change:
                key = m.key_change
            if m.time_change:
                time = m.time_change
            m.key, m.time = key, time
            for hand in HANDS:
                changes = sorted(m.clef_changes[hand], key=lambda c: c[0])
                m.clef_changes[hand] = changes
                if not first_clef_seen[hand]:
                    if hand in self.score.initial_clef:
                        clefs[hand] = self.score.initial_clef[hand]
                    if changes and changes[0][0] == 0:
                        clefs[hand] = changes[0][1]
                    first_clef_seen[hand] = True
                for onset, c in changes:
                    if onset == 0:
                        clefs[hand] = c
                m.start_clef[hand] = clefs[hand]
                for onset, c in changes:
                    clefs[hand] = c
            nominal = time.duration
            actual = m.duration
            edge = m.implicit or m.index == 0 or m.index == len(self.score.measures) - 1
            if actual <= 0:
                m.duration = nominal
            elif actual < nominal:
                m.duration = actual if edge else nominal   # pickup / final incomplete bar
            else:
                m.duration = actual
        # clef changes placed at the very end of a measure belong to the next one
        for i, m in enumerate(self.score.measures):
            for hand in HANDS:
                keep = []
                for onset, c in m.clef_changes[hand]:
                    if onset >= m.duration and i + 1 < len(self.score.measures):
                        nxt = self.score.measures[i + 1]
                        nxt.clef_changes[hand].insert(0, (Fraction(0), c))
                        nxt.start_clef[hand] = c
                    else:
                        keep.append((onset, c))
                m.clef_changes[hand] = keep

    def _fill_gaps(self) -> None:
        for m in self.score.measures:
            for hand in HANDS:
                evs = m.events[hand]
                if self.score.split_mode and any(not e.grace and not e.is_rest for e in evs):
                    evs.extend(_fill_rests(evs, Fraction(0), m.duration, hand, m.index))
                    evs.sort(key=lambda e: (e.onset, e.grace_rank))
                if not [e for e in evs if not e.grace]:
                    m.events[hand] = [e for e in evs if e.grace] + [Event(
                        onset=Fraction(0), duration=m.duration, type="whole", is_rest=True,
                        measure_rest=True, hand=hand, measure_index=m.index)]
                for e in m.events[hand]:
                    if e.is_rest and not e.grace and e.onset == 0 and e.duration >= m.duration and \
                            len([x for x in m.events[hand] if x.voice == e.voice and not x.grace]) == 1:
                        e.measure_rest = True

    # ------------------------------------------------------------------
    def _octave_shifts(self, hand: str):
        """Chronological list of (measure, onset, shift_in_octaves) for a hand."""
        items = []
        for m in self.score.measures:
            for d in m.directions:
                if d.kind == "octave" and d.hand == hand:
                    if d.subtype in ("down", "up"):
                        octs = 3 if d.value >= 22 else (2 if d.value >= 15 else 1)
                        items.append((m.index, d.onset, -octs if d.subtype == "down" else octs))
                    elif d.subtype == "stop":
                        items.append((m.index, d.onset, 0))
        items.sort(key=lambda t: (t[0], t[1]))
        return items

    def _positions_and_accidentals(self) -> None:
        compute_acc = not self.score.has_xml_accidentals or self.score.split_mode
        for hand in HANDS:
            shifts = self._octave_shifts(hand)
            si = 0
            shift = 0
            for m in self.score.measures:
                clef = m.start_clef[hand]
                changes = m.clef_changes[hand]
                state: Dict[int, int] = {}
                evs = sorted(m.events[hand], key=lambda e: (e.onset, e.grace_rank))
                m.events[hand] = evs
                for ev in evs:
                    for onset, c in changes:
                        if onset <= ev.onset:
                            clef = c
                    while si < len(shifts) and (shifts[si][0], shifts[si][1]) <= (m.index, ev.onset):
                        shift = shifts[si][2]
                        si += 1
                    ev.clef = clef  # type: ignore[attr-defined]
                    for n in ev.notes:
                        n.pos = clef.staff_position(n.step, n.octave) + shift * 7
                        if n.unpitched:
                            n.pos = Clef("G", 2).staff_position(n.step, n.octave)
                        alter = int(round(n.alter))
                        if compute_acc:
                            expected = state.get(n.diatonic, m.key.alter_for(n.step))
                            if n.tie_stop and expected == alter:
                                pass
                            elif alter != expected:
                                n.accidental = ACCIDENTAL_FOR_ALTER.get(alter, "natural")
                            else:
                                n.accidental = None
                            state[n.diatonic] = alter
                        if n.accidental and n.accidental not in G.ACCIDENTAL_GLYPH:
                            n.accidental = ACCIDENTAL_FOR_ALTER.get(alter) if alter or n.accidental == "natural" else None

    # ------------------------------------------------------------------
    def _auto_beam(self) -> None:
        for m in self.score.measures:
            unit = m.time.beam_unit
            for hand in HANDS:
                by_voice: Dict[Tuple[str, bool], List[Event]] = {}
                for ev in m.events[hand]:
                    ev.beams = {}
                    by_voice.setdefault((ev.voice, ev.grace), []).append(ev)
                for (voice, grace), evs in by_voice.items():
                    run: List[Event] = []
                    cur_group = None

                    def flush():
                        if len(run) >= 2:
                            for k, e in enumerate(run):
                                e.beams[1] = "begin" if k == 0 else ("end" if k == len(run) - 1 else "continue")
                                e.auto_beam = True  # type: ignore[attr-defined]
                        run.clear()

                    for ev in evs:
                        beamable = (not ev.is_rest and ev.flags > 0)
                        g = "grace" if grace else ev.onset // unit
                        if not beamable:
                            flush()
                            cur_group = None
                            continue
                        if run and g != cur_group:
                            flush()
                        run.append(ev)
                        cur_group = g
                    flush()

    def _beam_groups(self) -> None:
        open_groups: Dict[Tuple[str, str, bool], BeamGroup] = {}
        for m in self.score.measures:
            for hand in HANDS:
                for ev in m.events[hand]:
                    b = ev.beams.get(1)
                    k = (hand, ev.voice, ev.grace)
                    if not b or ev.is_rest:
                        continue
                    if b == "begin":
                        if k in open_groups:
                            self._close(open_groups.pop(k))
                        grp = BeamGroup([ev], auto=getattr(ev, "auto_beam", False))
                        open_groups[k] = grp
                        ev.beam_group = grp
                    elif b in ("continue", "end"):
                        grp = open_groups.get(k)
                        if grp is None:
                            continue
                        grp.events.append(ev)
                        ev.beam_group = grp
                        if b == "end":
                            self._close(open_groups.pop(k))
        for grp in open_groups.values():
            self._close(grp)

    def _close(self, grp: BeamGroup) -> None:
        if len(grp.events) < 2:
            for e in grp.events:
                e.beam_group = None
            return
        self.groups.append(grp)

    # ------------------------------------------------------------------
    @staticmethod
    def _extreme_rule(positions: List[int]) -> int:
        if not positions:
            return 1
        hi = max(positions) - 4
        lo = 4 - min(positions)
        return -1 if hi >= lo else 1

    def _stems(self) -> None:
        for m in self.score.measures:
            for hand in HANDS:
                voices = {e.voice for e in m.events[hand] if not e.is_rest and not e.grace}
                multi = len(voices) > 1
                m.multi_voice = getattr(m, "multi_voice", {})  # type: ignore[attr-defined]
                m.multi_voice[hand] = multi  # type: ignore[attr-defined]
                for ev in m.events[hand]:
                    ev.multi = multi  # type: ignore[attr-defined]
                    if ev.is_rest:
                        ev.stem_dir = 0
                        continue
                    ev.stem_dir = self._stem_for(ev, multi)
        for grp in self.groups:
            first = grp.events[0]
            notes = [n.pos for e in grp.events for n in e.notes]
            if first.stem_xml in ("up", "down") and not self.score.split_mode:
                d = 1 if first.stem_xml == "up" else -1
            elif first.grace:
                d = 1
            elif getattr(first, "multi", False):
                d = 1 if _voice_num(first.voice) % 2 == 1 else -1
            else:
                d = self._extreme_rule(notes)
            grp.direction = d
            for e in grp.events:
                if not e.is_rest:
                    e.stem_dir = d

    def _stem_for(self, ev: Event, multi: bool) -> int:
        if ev.stem_xml in ("up", "down") and not self.score.split_mode:
            return 1 if ev.stem_xml == "up" else -1
        if ev.grace:
            return 1
        if multi:
            return 1 if _voice_num(ev.voice) % 2 == 1 else -1
        return self._extreme_rule([n.pos for n in ev.notes])

    # ------------------------------------------------------------------
    def _chord_layout(self) -> None:
        mode = self.opts.symbols
        acc_threshold = 6 if mode == "rot" else 3
        for m in self.score.measures:
            for hand in HANDS:
                for ev in m.events[hand]:
                    self._layout_event(ev, mode, acc_threshold)

    def _layout_event(self, ev: Event, mode: str, acc_threshold: int) -> None:
        s = ev.scale
        if ev.is_rest:
            name = G.REST_GLYPH.get(ev.type, "rest_quarter")
            t0, t1 = G.time_extent(name, mode, s)
            if ev.measure_rest:
                ev.before = ev.after = 0.0
            else:
                ev.before, ev.after = -t0 + 0.1, t1 + 0.1 + (0.55 * ev.dots + 0.2 if ev.dots else 0)
            return
        # de-duplicate unisons, sort by position
        ev.notes.sort(key=lambda n: n.pos)
        hy = G.HEAD_HALF_TIME.get(ev.type, HEAD_HALF)
        # seconds displacement
        for n in ev.notes:
            n.displaced = 0
        if ev.stem_dir >= 0:
            prev = None
            for n in ev.notes:
                if prev is not None and n.pos - prev.pos == 1 and prev.displaced == 0:
                    n.displaced = 1
                prev = n
        else:
            prev = None
            for n in reversed(ev.notes):
                if prev is not None and prev.pos - n.pos == 1 and prev.displaced == 0:
                    n.displaced = -1
                prev = n
        before = hy
        after = hy
        if any(n.displaced < 0 for n in ev.notes):
            before += 2 * hy * 0.95
        if any(n.displaced > 0 for n in ev.notes):
            after += 2 * hy * 0.95
        # accidental columns
        acc_notes = [n for n in sorted(ev.notes, key=lambda n: -n.pos) if n.accidental]
        columns: List[List[int]] = []
        widths: List[float] = []
        for n in acc_notes:
            gname = G.ACCIDENTAL_GLYPH.get(n.accidental, "natural")
            t0, t1 = G.time_extent(gname, mode)
            w = t1 - t0
            for ci, col in enumerate(columns):
                if all(abs(p - n.pos) >= acc_threshold for p in col):
                    col.append(n.pos)
                    n.acc_col = ci
                    widths[ci] = max(widths[ci], w)
                    break
            else:
                columns.append([n.pos])
                widths.append(w)
                n.acc_col = len(columns) - 1
        ev.acc_widths = widths
        if widths:
            before += 0.22 + sum(widths) + 0.12 * (len(widths) - 1)
        if ev.dots:
            after = max(after, hy + (2 * hy * 0.95 if any(n.displaced > 0 for n in ev.notes) else 0)) \
                + 0.35 + 0.5 * ev.dots
        if ev.arpeggio:
            before += 1.1
        if ev.flags and ev.stem_dir > 0 and ev.beam_group is None:
            after = max(after, hy + 1.0)
        ev.before = before * s
        ev.after = after * s


def _voice_num(v: str) -> int:
    try:
        return int(v)
    except ValueError:
        return 1


def analyze(score: Score, opts: Options) -> Analyzer:
    a = Analyzer(score, opts)
    a.run()
    return a
