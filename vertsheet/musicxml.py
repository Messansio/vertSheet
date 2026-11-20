"""MusicXML reader (.musicxml / .xml / compressed .mxl, partwise or timewise).

The parser produces a hand-oriented :class:`~vertsheet.model.Score`: every
event and direction is assigned to either the right hand (RH) or the left
hand (LH) according to a *hand mapping* (see :func:`load_score`).
"""
from __future__ import annotations

import copy
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

from .model import (Barline, Clef, Direction, Event, Key, Measure, Note, Score,
                    TimeSig, infer_type, STEPS)


class MusicXMLError(Exception):
    pass


# --------------------------------------------------------------------------
# low level helpers
# --------------------------------------------------------------------------

def _strip_ns(root: ET.Element) -> None:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def read_xml_bytes(path: str) -> bytes:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            name = None
            try:
                container = ET.fromstring(z.read("META-INF/container.xml"))
                _strip_ns(container)
                rf = container.find(".//rootfile")
                if rf is not None:
                    name = rf.get("full-path")
            except KeyError:
                pass
            if not name:
                cands = [n for n in z.namelist()
                         if n.lower().endswith((".xml", ".musicxml")) and not n.startswith("META-INF")]
                if not cands:
                    raise MusicXMLError("compressed file contains no MusicXML document")
                name = cands[0]
            return z.read(name)
    with open(path, "rb") as fh:
        return fh.read()


def _num(text: Optional[str], default: float = 0) -> float:
    if text is None:
        return default
    try:
        return float(text.strip())
    except ValueError:
        return default


def _frac(text: Optional[str]) -> Fraction:
    if not text:
        return Fraction(0)
    text = text.strip()
    try:
        return Fraction(int(text))
    except ValueError:
        return Fraction(text).limit_denominator(10000)


def _timewise_to_partwise(root: ET.Element) -> ET.Element:
    new = ET.Element("score-partwise")
    for child in root:
        if child.tag != "measure":
            new.append(child)
    parts: Dict[str, ET.Element] = {}
    order: List[str] = []
    for m in root.findall("measure"):
        for p in m.findall("part"):
            pid = p.get("id", "P1")
            if pid not in parts:
                parts[pid] = ET.Element("part", {"id": pid})
                order.append(pid)
            nm = ET.Element("measure", dict(m.attrib))
            for c in p:
                nm.append(c)
            parts[pid].append(nm)
    for pid in order:
        new.append(parts[pid])
    return new


# --------------------------------------------------------------------------
# raw (part-level) structures
# --------------------------------------------------------------------------

@dataclass
class RawMeasure:
    number: str = ""
    implicit: bool = False
    events: List[Event] = field(default_factory=list)
    directions: List[Direction] = field(default_factory=list)
    clefs: List[Tuple[int, Fraction, Clef]] = field(default_factory=list)
    key: Optional[Key] = None
    time: Optional[TimeSig] = None
    left: Optional[Barline] = None
    right: Optional[Barline] = None
    length: Fraction = Fraction(0)


@dataclass
class RawPart:
    pid: str
    name: str = ""
    staves: int = 1
    measures: List[RawMeasure] = field(default_factory=list)


class _Parser:
    def __init__(self, data: bytes):
        try:
            root = ET.fromstring(data)
        except ET.ParseError as exc:
            raise MusicXMLError(f"invalid XML: {exc}") from None
        _strip_ns(root)
        if root.tag == "score-timewise":
            root = _timewise_to_partwise(root)
        if root.tag != "score-partwise":
            raise MusicXMLError(f"not a MusicXML score (root element <{root.tag}>)")
        self.root = root
        self.has_accidentals = root.find(".//accidental") is not None
        self.has_beams = root.find(".//beam") is not None

    # ------------------------------------------------------------------
    def metadata(self) -> Tuple[str, str, str]:
        r = self.root
        title = (r.findtext("work/work-title") or r.findtext("movement-title") or "").strip()
        subtitle = ""
        if r.findtext("work/work-title") and r.findtext("movement-title"):
            subtitle = r.findtext("movement-title").strip()
        composer = ""
        for c in r.findall("identification/creator"):
            if c.get("type") == "composer" and c.text:
                composer = c.text.strip()
        if not title or not composer:
            credits = []
            for cr in r.findall("credit"):
                for cw in cr.findall("credit-words"):
                    if cw.text and cw.text.strip():
                        credits.append((cr.findtext("credit-type") or "", cw))
            for ctype, cw in credits:
                if not title and ctype == "title":
                    title = cw.text.strip()
                if not composer and ctype == "composer":
                    composer = cw.text.strip()
            if not title and credits:
                big = max(credits, key=lambda c: _num(c[1].get("font-size"), 0))
                if _num(big[1].get("font-size"), 0) >= 16:
                    title = big[1].text.strip()
        return title, subtitle, composer

    def part_names(self) -> Dict[str, str]:
        names = {}
        for sp in self.root.findall("part-list/score-part"):
            names[sp.get("id")] = (sp.findtext("part-name") or sp.get("id") or "").strip()
        return names

    # ------------------------------------------------------------------
    def parse_parts(self) -> List[RawPart]:
        names = self.part_names()
        out = []
        for part_el in self.root.findall("part"):
            pid = part_el.get("id", f"P{len(out) + 1}")
            out.append(self._parse_part(part_el, pid, names.get(pid, pid)))
        if not out:
            raise MusicXMLError("the score contains no parts")
        return out

    def _parse_part(self, part_el: ET.Element, pid: str, name: str) -> RawPart:
        part = RawPart(pid=pid, name=name)
        divisions = Fraction(1)
        for m_el in part_el.findall("measure"):
            rm = RawMeasure(number=m_el.get("number", ""), implicit=m_el.get("implicit") == "yes")
            cursor = Fraction(0)
            max_cursor = Fraction(0)
            last_event: Optional[Event] = None
            pending_graces: Dict[Tuple[int, str], List[Event]] = {}
            for el in m_el:
                tag = el.tag
                if tag == "attributes":
                    divisions = self._parse_attributes(el, rm, part, cursor, divisions)
                elif tag == "note":
                    ev, is_chord = self._parse_note(el, cursor, divisions, pid)
                    if is_chord and last_event is not None:
                        if ev.staff == last_event.staff:
                            last_event.notes.extend(ev.notes)
                            self._merge_notations(last_event, ev)
                            continue
                        ev.onset = last_event.onset
                        ev.duration = last_event.duration
                        ev.type, ev.dots = last_event.type, last_event.dots
                        rm.events.append(ev)
                        continue
                    rm.events.append(ev)
                    last_event = ev
                    gkey = (ev.staff, ev.voice)
                    if ev.grace:
                        pending_graces.setdefault(gkey, []).append(ev)
                    else:
                        graces = pending_graces.pop(gkey, [])
                        for k, g in enumerate(graces):
                            g.grace_rank = k - len(graces)
                            g.onset = ev.onset
                        cursor += ev.duration
                        max_cursor = max(max_cursor, cursor)
                    for d in ev.extra_dynamics:  # type: ignore[attr-defined]
                        d.onset = ev.onset
                        rm.directions.append(d)
                elif tag == "backup":
                    cursor -= _frac(el.findtext("duration")) / divisions
                    if cursor < 0:
                        cursor = Fraction(0)
                elif tag == "forward":
                    cursor += _frac(el.findtext("duration")) / divisions
                    max_cursor = max(max_cursor, cursor)
                elif tag == "direction":
                    rm.directions.extend(self._parse_direction(el, cursor, divisions, pid))
                elif tag == "barline":
                    self._parse_barline(el, rm)
            # after-graces with no following note: give them ranks too
            for graces in pending_graces.values():
                for k, g in enumerate(graces):
                    g.grace_rank = k - len(graces)
            rm.length = max_cursor
            part.measures.append(rm)
        return part

    @staticmethod
    def _merge_notations(target: Event, src: Event) -> None:
        for attr in ("slurs", "articulations", "ornaments"):
            for item in getattr(src, attr):
                if item not in getattr(target, attr):
                    getattr(target, attr).append(item)
        target.tuplets.extend(t for t in src.tuplets if t not in target.tuplets)
        target.fermata = target.fermata or src.fermata
        target.arpeggio = target.arpeggio or src.arpeggio
        for k, v in src.beams.items():
            target.beams.setdefault(k, v)
        target.extra_dynamics.extend(src.extra_dynamics)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    def _parse_attributes(self, el, rm: RawMeasure, part: RawPart, cursor, divisions) -> Fraction:
        d = el.findtext("divisions")
        if d:
            divisions = _frac(d) or Fraction(1)
        st = el.findtext("staves")
        if st:
            part.staves = max(part.staves, int(_num(st, 1)))
        k = el.find("key")
        if k is not None and k.findtext("fifths") is not None:
            rm.key = Key(int(_num(k.findtext("fifths"))), (k.findtext("mode") or "major").strip())
        t = el.find("time")
        if t is not None:
            beats_list = [b.text or "4" for b in t.findall("beats")]
            types = [bt.text or "4" for bt in t.findall("beat-type")]
            if beats_list and types:
                # composite meters such as 3+2/8 or 2/4+3/8 -> sum as a ratio
                total = Fraction(0)
                for b, bt in zip(beats_list, types):
                    nb = sum(int(_num(x, 0)) for x in b.split("+"))
                    total += Fraction(nb, int(_num(bt, 4)))
                bt0 = int(_num(types[0], 4))
                beats = int(total * bt0) if (total * bt0).denominator == 1 else int(_num(beats_list[0], 4))
                rm.time = TimeSig(beats, bt0, t.get("symbol"), "+".join(beats_list))
        for c in el.findall("clef"):
            staff = int(_num(c.get("number"), 1))
            clef = Clef((c.findtext("sign") or "G").strip(), int(_num(c.findtext("line"), 0)),
                        int(_num(c.findtext("clef-octave-change"), 0)))
            rm.clefs.append((staff, cursor, clef))
        return divisions

    def _parse_note(self, el, cursor: Fraction, divisions: Fraction, pid: str) -> Tuple[Event, bool]:
        ev = Event(part=pid)
        ev.extra_dynamics = []  # type: ignore[attr-defined]
        grace_el = el.find("grace")
        ev.grace = grace_el is not None
        ev.grace_slash = grace_el is not None and grace_el.get("slash") == "yes"
        ev.cue = el.find("cue") is not None or el.get("size") == "cue" or (
            el.find("type") is not None and el.find("type").get("size") == "cue")
        is_chord = el.find("chord") is not None
        ev.staff = int(_num(el.findtext("staff"), 1))
        ev.voice = (el.findtext("voice") or "1").strip()
        ev.onset = cursor
        ev.duration = Fraction(0) if ev.grace else _frac(el.findtext("duration")) / divisions
        tm = el.find("time-modification")
        if tm is not None:
            ev.time_mod = (int(_num(tm.findtext("actual-notes"), 1)), int(_num(tm.findtext("normal-notes"), 1)))
        ev.dots = len(el.findall("dot"))
        typ = el.findtext("type")
        rest_el = el.find("rest")
        if typ:
            ev.type = typ.strip()
        else:
            ev.type, ev.dots = infer_type(ev.duration, ev.time_mod)
        if rest_el is not None:
            ev.is_rest = True
            ev.measure_rest = rest_el.get("measure") == "yes" or (not typ and not ev.grace)
            ds, do = rest_el.findtext("display-step"), rest_el.findtext("display-octave")
            if ds and do:
                ev.rest_display = (ds.strip(), int(_num(do, 4)))
        else:
            note = Note()
            p = el.find("pitch")
            u = el.find("unpitched")
            if p is not None:
                note.step = (p.findtext("step") or "C").strip()
                note.octave = int(_num(p.findtext("octave"), 4))
                note.alter = _num(p.findtext("alter"), 0)
            elif u is not None:
                note.unpitched = True
                note.step = (u.findtext("display-step") or "B").strip()
                note.octave = int(_num(u.findtext("display-octave"), 4))
            if note.step not in STEPS:
                note.step = "C"
            acc = el.find("accidental")
            if acc is not None and acc.text:
                note.accidental = acc.text.strip()
            for tie in el.findall("tie"):
                if tie.get("type") == "start":
                    note.tie_start = True
                elif tie.get("type") == "stop":
                    note.tie_stop = True
            nh = el.findtext("notehead")
            if nh:
                note.notehead = nh.strip()
            ev.notes.append(note)
        st = el.findtext("stem")
        if st:
            ev.stem_xml = st.strip()
        for b in el.findall("beam"):
            ev.beams[int(_num(b.get("number"), 1))] = (b.text or "").strip()
        for nots in el.findall("notations"):
            self._parse_notations(nots, ev)
        return ev, is_chord

    def _parse_notations(self, nots, ev: Event) -> None:
        for c in nots:
            tag = c.tag
            if tag == "tied" and ev.notes:
                if c.get("type") == "start":
                    ev.notes[-1].tie_start = True
                elif c.get("type") == "stop":
                    ev.notes[-1].tie_stop = True
            elif tag == "slur":
                ev.slurs.append({"type": c.get("type"), "number": c.get("number", "1"),
                                 "placement": c.get("placement")})
            elif tag == "tuplet":
                ev.tuplets.append({"type": c.get("type"), "number": c.get("number", "1"),
                                   "bracket": c.get("bracket"), "show": c.get("show-number", "actual"),
                                   "placement": c.get("placement")})
            elif tag == "articulations":
                for a in c:
                    if a.tag in ("staccato", "accent", "tenuto", "staccatissimo", "strong-accent",
                                 "detached-legato", "spiccato"):
                        ev.articulations.append(a.tag)
            elif tag == "fermata":
                ev.fermata = c.get("type", "upright") or "upright"
            elif tag == "ornaments":
                for o in c:
                    if o.tag in ("trill-mark", "mordent", "inverted-mordent", "turn", "inverted-turn"):
                        ev.ornaments.append(o.tag)
            elif tag == "technical":
                f = c.find("fingering")
                if f is not None and f.text and ev.notes:
                    ev.notes[-1].fingering = f.text.strip()
            elif tag == "arpeggiate":
                ev.arpeggio = True
            elif tag == "dynamics":
                text = self._dynamics_text(c)
                if text:
                    ev.extra_dynamics.append(Direction("dynamics", ev.onset, ev.part, ev.staff,  # type: ignore
                                                       c.get("placement"), text))

    @staticmethod
    def _dynamics_text(el) -> str:
        parts = []
        for d in el:
            if d.tag == "other-dynamics":
                parts.append((d.text or "").strip())
            else:
                parts.append(d.tag)
        return "".join(parts)

    def _parse_direction(self, el, cursor: Fraction, divisions: Fraction, pid: str) -> List[Direction]:
        placement = el.get("placement")
        staff = int(_num(el.findtext("staff"), 1))
        onset = cursor
        off = el.findtext("offset")
        if off:
            onset = cursor + _frac(off) / divisions
            if onset < 0:
                onset = Fraction(0)
        out: List[Direction] = []
        words: List[str] = []
        for dt in el.findall("direction-type"):
            for c in dt:
                tag = c.tag
                if tag == "dynamics":
                    text = self._dynamics_text(c)
                    if text:
                        out.append(Direction("dynamics", onset, pid, staff, placement, text))
                elif tag == "words":
                    if c.text and c.text.strip():
                        words.append(c.text.strip())
                elif tag == "wedge":
                    out.append(Direction("wedge", onset, pid, staff, placement, "",
                                         c.get("type", "stop"), int(_num(c.get("number"), 1))))
                elif tag == "pedal":
                    out.append(Direction("pedal", onset, pid, staff, placement, "",
                                         c.get("type", "start"), 1,
                                         extra={"line": c.get("line") == "yes"}))
                elif tag == "metronome":
                    unit = (c.findtext("beat-unit") or "quarter").strip()
                    dots = len(c.findall("beat-unit-dot"))
                    pm = c.findtext("per-minute")
                    if pm:
                        out.append(Direction("metronome", onset, pid, staff, placement, pm.strip(),
                                             unit, extra={"dots": dots}))
                elif tag == "octave-shift":
                    out.append(Direction("octave", onset, pid, staff, placement, "",
                                         c.get("type", "stop"), int(_num(c.get("number"), 1)),
                                         int(_num(c.get("size"), 8))))
                elif tag == "rehearsal":
                    if c.text and c.text.strip():
                        out.append(Direction("rehearsal", onset, pid, staff, placement, c.text.strip()))
                elif tag == "segno":
                    words.append("Segno")
                elif tag == "coda":
                    words.append("Coda")
        if words:
            out.append(Direction("words", onset, pid, staff, placement, " ".join(words)))
        return out

    @staticmethod
    def _parse_barline(el, rm: RawMeasure) -> None:
        loc = el.get("location", "right")
        bl = Barline(style=(el.findtext("bar-style") or "regular").strip())
        rep = el.find("repeat")
        if rep is not None:
            bl.repeat = rep.get("direction")
        end = el.find("ending")
        if end is not None:
            bl.ending_number = end.get("number")
            bl.ending_type = end.get("type")
            bl.ending_text = (end.text or "").strip() or None
        if loc == "left":
            rm.left = bl
        else:
            rm.right = bl


# --------------------------------------------------------------------------
# hand assignment
# --------------------------------------------------------------------------

@dataclass
class HandMap:
    rh: Optional[Tuple[str, int]] = None
    lh: Optional[Tuple[str, int]] = None
    split_source: Optional[Tuple[str, int]] = None
    split_midi: int = 60

    def describe(self) -> str:
        if self.split_source:
            p, s = self.split_source
            return f"single staff {p}:{s} split at MIDI {self.split_midi}"
        return f"RH = {self.rh[0]}:{self.rh[1]}, LH = {self.lh[0]}:{self.lh[1]}"


def _parse_source(spec: str, parts: List[RawPart]) -> Tuple[str, int]:
    ids = [p.pid for p in parts]
    pid, _, staff = spec.partition(":")
    if pid.isdigit() and pid not in ids:
        idx = int(pid) - 1
        if not 0 <= idx < len(parts):
            raise MusicXMLError(f"part index {pid} out of range (score has {len(parts)} parts)")
        pid = ids[idx]
    if pid not in ids:
        raise MusicXMLError(f"unknown part '{pid}' (available: {', '.join(ids)})")
    return pid, int(staff or 1)


def choose_hands(parts: List[RawPart], rh: Optional[str], lh: Optional[str],
                 split_midi: Optional[int]) -> HandMap:
    hm = HandMap()
    if split_midi is not None:
        hm.split_midi = split_midi
    if rh and lh:
        hm.rh, hm.lh = _parse_source(rh, parts), _parse_source(lh, parts)
        return hm
    if rh and not lh:
        hm.split_source = _parse_source(rh, parts)
        return hm
    for p in parts:
        if p.staves >= 2:
            hm.rh, hm.lh = (p.pid, 1), (p.pid, 2)
            if split_midi is None:
                return hm
    if split_midi is not None:
        hm.rh = hm.lh = None
        hm.split_source = (parts[0].pid, 1)
        return hm
    if len(parts) >= 2:
        hm.rh, hm.lh = (parts[0].pid, 1), (parts[1].pid, 1)
        return hm
    hm.split_source = (parts[0].pid, 1)
    return hm


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------

def load_score(path: str, rh: Optional[str] = None, lh: Optional[str] = None,
               split: Optional[int] = None) -> Score:
    if path.lower().endswith((".mscz", ".mscx")):
        from .mscx import parse_musescore
        parts, (title, subtitle, composer), has_acc = parse_musescore(path)
        return build_score(parts, title, subtitle, composer, has_acc, False, rh, lh, split)
    parser = _Parser(read_xml_bytes(path))
    parts = parser.parse_parts()
    title, subtitle, composer = parser.metadata()
    return build_score(parts, title, subtitle, composer, parser.has_accidentals, parser.has_beams,
                       rh, lh, split)


def build_score(parts: List[RawPart], title: str, subtitle: str, composer: str,
                has_accidentals: bool, has_beams: bool, rh: Optional[str] = None,
                lh: Optional[str] = None, split: Optional[int] = None) -> Score:
    """Merge part-level data into a two-hand :class:`Score`."""
    hm = choose_hands(parts, rh, lh, split)
    by_id = {p.pid: p for p in parts}
    score = Score(title=title, subtitle=subtitle, composer=composer,
                  has_xml_accidentals=has_accidentals, has_xml_beams=has_beams,
                  source_description=hm.describe())

    sources: Dict[str, Tuple[str, int]] = {}
    if hm.split_source:
        sources = {"RH": hm.split_source, "LH": hm.split_source}
    else:
        sources = {"RH": hm.rh, "LH": hm.lh}
    primary = by_id[sources["RH"][0]]
    n_measures = max(len(p.measures) for p in parts)

    def hand_of(pid: str, staff: int) -> Optional[str]:
        if hm.split_source:
            return "RH" if pid == hm.split_source[0] else None
        for hand, (sp, ss) in sources.items():
            if sp == pid and ss == staff:
                return hand
        rh_pid, lh_pid = sources["RH"][0], sources["LH"][0]
        if pid == rh_pid == lh_pid:  # e.g. third staff of an organ part
            return "LH" if staff > sources["RH"][1] else "RH"
        if pid == rh_pid:
            return "RH"
        if pid == lh_pid:
            return "LH"
        return None

    for i in range(n_measures):
        m = Measure(index=i)
        pm = primary.measures[i] if i < len(primary.measures) else RawMeasure()
        m.number = pm.number or str(i + 1)
        m.implicit = pm.implicit
        m.key_change = pm.key
        m.time_change = pm.time
        m.left_barline, m.right_barline = pm.left, pm.right
        length = Fraction(0)
        used_parts = {sources["RH"][0], sources["LH"][0]}
        for pid in used_parts:
            p = by_id[pid]
            if i >= len(p.measures):
                continue
            rm = p.measures[i]
            length = max(length, rm.length)
            if pid != primary.pid:
                m.key_change = m.key_change or rm.key
                m.time_change = m.time_change or rm.time
                m.right_barline = m.right_barline or rm.right
                m.left_barline = m.left_barline or rm.left
            for ev in rm.events:
                if hm.split_source:
                    if ev.staff != hm.split_source[1]:
                        continue
                    _split_event(ev, hm.split_midi, m)
                    continue
                hand = hand_of(pid, ev.staff)
                if hand is None:
                    continue
                ev.hand = hand
                ev.measure_index = i
                m.events[hand].append(ev)
            for d in rm.directions:
                if hm.split_source:
                    if pid != hm.split_source[0] or d.staff != hm.split_source[1]:
                        continue
                    hand = "LH" if d.kind == "pedal" else "RH"
                else:
                    hand = hand_of(pid, d.staff)
                if hand is None:
                    continue
                d.hand = hand
                d.measure_index = i
                m.directions.append(d)
            for staff, onset, clef in rm.clefs:
                if hm.split_source:
                    continue
                hand = hand_of(pid, staff)
                if hand is not None:
                    m.clef_changes[hand].append((onset, clef))
        m.duration = length  # finalised by the analyser once the time signature is known
        score.measures.append(m)

    if hm.split_source:
        score.initial_clef = {"RH": Clef("G", 2), "LH": Clef("F", 4)}
        score.split_mode = True
    return score


def _split_event(ev: Event, split_midi: int, m: Measure) -> None:
    """Split a single-staff chord into RH (>= split) and LH (< split) parts."""
    if ev.is_rest:
        return  # rests are regenerated per hand by the analyser
    hi = [n for n in ev.notes if n.midi >= split_midi]
    lo = [n for n in ev.notes if n.midi < split_midi]
    for hand, notes in (("RH", hi), ("LH", lo)):
        if not notes:
            continue
        e = copy.copy(ev)
        e.notes = notes
        e.hand = hand
        e.voice = "1"
        e.beams = {}  # beams are rebuilt by the auto-beamer in split mode
        e.slurs = list(ev.slurs) if hand == "RH" or not hi else []
        e.tuplets = list(ev.tuplets)
        e.stem_xml = None
        e.measure_index = m.index
        m.events[hand].append(e)
