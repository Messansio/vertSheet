"""Native reader for MuseScore files (.mscz compressed, .mscx plain).

MuseScore does not need to be installed. Two generations of the file format
are understood:

* **MuseScore 3 / 4** - every measure holds ``<voice>`` blocks; positions
  move with relative ``<location>`` elements, tuplets are opened by an inline
  ``<Tuplet>`` and closed by ``<endTuplet/>``, and spanners (slurs, hairpins,
  pedal, 8va, voltas) are ``<Spanner type=...>`` start/end markers.
* **MuseScore 1 / 2** - the elements of all voices sit directly inside the
  ``<Measure>``; the voice is given by ``<track>``, positions are absolute
  ``<tick>`` values, tuplets are defined once (``<Tuplet id=..>``) and
  referenced by id from each chord, and spanners are opened by
  ``<HairPin id=..>`` / ``<Pedal id=..>`` ... and closed by
  ``<endSpanner id=..>``.

Both are converted into the part-level structures produced by the MusicXML
reader (:class:`~vertsheet.musicxml.RawPart`), so the rest of the pipeline is
shared.

Pitches are spelled from MuseScore's *tonal pitch class* (``tpc``, a position
on the line of fifths) and the MIDI pitch, so enharmonics (F# vs Gb) are
preserved exactly.
"""
from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

from .model import (Barline, Clef, Direction, Event, Key, Note, TimeSig, STEP_SEMITONES,
                    dotted_duration)
from .musicxml import MusicXMLError, RawMeasure, RawPart

GRACE_TAGS = {"acciaccatura", "appoggiatura", "grace4", "grace16", "grace32",
              "grace8after", "grace16after", "grace32after"}
ACCIDENTALS = {
    # MuseScore 3 / 4 (SMuFL names)
    "accidentalSharp": "sharp", "accidentalFlat": "flat", "accidentalNatural": "natural",
    "accidentalDoubleSharp": "double-sharp", "accidentalDoubleFlat": "flat-flat",
    "accidentalSharpSharp": "double-sharp", "accidentalNaturalSharp": "sharp",
    "accidentalNaturalFlat": "flat",
    # MuseScore 2
    "sharp": "sharp", "flat": "flat", "natural": "natural", "sharp2": "double-sharp",
    "flat2": "flat-flat", "double sharp": "double-sharp", "double flat": "flat-flat",
    # MuseScore 1 (numeric)
    "1": "sharp", "2": "flat", "3": "double-sharp", "4": "flat-flat", "5": "natural",
}
BARLINES = {"double": "light-light", "end": "light-heavy", "final": "light-heavy",
            "dashed": "dashed", "dotted": "dotted", "heavy": "heavy", "double-heavy": "heavy-heavy",
            "reverse-end": "heavy-light"}
OTTAVA = {"0": ("down", 8), "1": ("up", 8), "2": ("down", 15), "3": ("up", 15), "4": ("down", 22),
          "5": ("up", 22), "8va": ("down", 8), "8vb": ("up", 8), "15ma": ("down", 15),
          "15mb": ("up", 15), "22ma": ("down", 22), "22mb": ("up", 22)}
# MuseScore 1 / 2 spanners that are opened by an element with an id and closed
# by <endSpanner id=".."/>.
LEGACY_SPANNERS = {"HairPin", "Pedal", "Ottava", "Volta", "Trill", "TextLine", "Slur",
                   "LetRing", "Vibrato", "PalmMute", "Glissando"}
DYNAMIC_SYMS = {"dynamicPiano": "p", "dynamicMezzo": "m", "dynamicForte": "f",
                "dynamicRinforzando": "r", "dynamicSforzando": "s", "dynamicZ": "z",
                "dynamicNiente": "n"}
_NOTE_CHARS = re.compile("[♩-♬\U0001D15D-\U0001D164\U0001D16D]")


def _read(path: str) -> bytes:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".mscx") and "/" not in n.strip("/")]
            names = names or [n for n in z.namelist() if n.lower().endswith(".mscx")]
            if not names:
                raise MusicXMLError("the .mscz archive contains no .mscx score")
            return z.read(names[0])
    with open(path, "rb") as fh:
        return fh.read()


def _plain(el: Optional[ET.Element], syms: Optional[Dict[str, str]] = None) -> str:
    """Visible text of a MuseScore text element.

    Formatting tags (<b>, <font>, ...) are flattened; <sym> symbols are dropped
    unless ``syms`` maps them to a replacement; the <head>/<style> blocks of
    MuseScore 1 HTML text are ignored."""
    if el is None:
        return ""
    parts: List[str] = []

    def walk(e: ET.Element) -> None:
        if e.tag in ("head", "style"):
            return
        if e.tag == "sym":
            if syms:
                parts.append(syms.get((e.text or "").strip(), ""))
            return
        if e.text:
            parts.append(e.text)
        for c in e:
            walk(c)
            if c.tail:
                parts.append(c.tail)

    walk(el)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _text_of(el: Optional[ET.Element], syms: Optional[Dict[str, str]] = None) -> str:
    if el is None:
        return ""
    for tag in ("text", "html-data"):
        t = el.find(tag)
        if t is not None:
            return _plain(t, syms)
    return ""


def _frac(s: Optional[str]) -> Fraction:
    try:
        return Fraction(s.strip()) if s else Fraction(0)
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


def _int(s: Optional[str], default: int = 0) -> int:
    try:
        return int((s or "").strip())
    except ValueError:
        return default


def spell(pitch: int, tpc: int) -> Tuple[str, int, int]:
    """(step, alter, octave) from a MIDI pitch and a MuseScore tonal pitch class."""
    k = tpc - 14 + 1
    step = "FCGDAEB"[k % 7]
    alter = k // 7
    octave = (pitch - alter - STEP_SEMITONES[step]) // 12 - 1
    return step, alter, octave


def _clef(code: str) -> Clef:
    code = (code or "G").strip()
    if code.isdigit():  # MuseScore 1 / 2 numeric clef types
        code = {"0": "G", "1": "G8va", "2": "G15ma", "3": "G8vb", "4": "F", "5": "F8vb",
                "6": "F15mb", "7": "F3", "8": "F5", "9": "C1", "10": "C2", "11": "C3",
                "12": "C4", "13": "TAB", "14": "PERC", "15": "C5"}.get(code, "G")
    sign = code[0].upper()
    if sign not in "GFC":
        return Clef("G", 2)
    octave = 0
    for tag, v in (("8va", 1), ("8vb", -1), ("15ma", 2), ("15mb", -2)):
        if tag in code:
            octave = v
    # an explicit staff line ("F3", "C1") - not the 8 / 15 of an octave clef
    m = re.match(r"[GFC]([1-5])(?![0-9vm])", code)
    line = int(m.group(1)) if m else {"G": 2, "F": 4, "C": 3}[sign]
    if sign == "G" and code.startswith("G_1"):
        line = 1
    return Clef(sign, line, octave)


def _marks(sub: str) -> Tuple[List[str], List[str], Optional[str]]:
    """(articulations, ornaments, fermata) for a MuseScore articulation /
    ornament subtype, in any format version ("articStaccatoAbove", "staccato",
    "ornamentTrill", "prall", "dfermata", ...)."""
    s = sub.lower()
    arts: List[str] = []
    orns: List[str] = []
    if "fermata" in s:
        return arts, orns, ("inverted" if s.startswith("d") or "below" in s else "upright")
    if "prall" in s or "shorttrill" in s or "mordentinverted" in s or "invertedmordent" in s:
        orns.append("inverted-mordent")
    elif "trill" in s:
        orns.append("trill-mark")
    elif "mordent" in s:
        orns.append("mordent")
    elif "turninverted" in s or "reverseturn" in s or "invertedturn" in s:
        orns.append("inverted-turn")
    elif "turn" in s:
        orns.append("turn")
    if orns:
        return arts, orns, None
    if "staccatissimo" in s or "spiccato" in s:
        arts.append("staccatissimo")
    elif "staccato" in s:
        arts.append("staccato")
    if "portato" in s:
        arts += ["staccato", "tenuto"] if "staccato" not in arts else ["tenuto"]
    elif "tenuto" in s:
        arts.append("tenuto")
    if "marcato" in s:
        arts.append("strong-accent")
    elif "accent" in s or s.startswith("sforzato"):
        arts.append("accent")
    return arts, orns, None


def _tuplet_info(el: ET.Element) -> dict:
    show = {"1": "both", "2": "none"}.get((el.findtext("numberType") or "").strip(), "actual")
    bracket = {"1": "yes", "2": "no"}.get((el.findtext("bracketType") or "").strip())
    return {"show": show, "bracket": bracket}


class _StaffParser:
    def __init__(self, staff_el: ET.Element, part: RawPart, staff_no: int, voice_base: int,
                 division: int = 480):
        self.el = staff_el
        self.part = part
        self.staff = staff_no
        self.voice_base = voice_base
        self.division = division or 480
        self.time = TimeSig()
        self.events: List[Tuple[int, Event]] = []
        # (kind, role, body element, measure index, onset, voice id)
        self.spanners: List[Tuple[str, str, Optional[ET.Element], int, Fraction, str]] = []
        self._open_ids: Dict[str, Tuple[str, ET.Element, str]] = {}     # MuseScore 1/2 spanners
        self._tuplet_defs: Dict[str, Tuple[int, int, Optional[str], dict]] = {}   # MuseScore 1/2

    # ------------------------------------------------------------------
    def parse(self, measures: List[RawMeasure], nominal: List[Fraction]) -> None:
        number = 0
        mtick = 0      # absolute tick of the measure start (MuseScore 1/2 files)
        for mi, meas in enumerate(self.el.findall("Measure")):
            while len(measures) <= mi:
                measures.append(RawMeasure())
                nominal.append(Fraction(0))
            rm = measures[mi]
            if (meas.get("tick") or "").isdigit():
                mtick = int(meas.get("tick"))
            irregular = meas.find("irregular") is not None
            if self.staff == 1 and self.voice_base == 0:
                if not irregular:
                    number += 1
                rm.number = rm.number or str(number)
            if meas.find("startRepeat") is not None:
                rm.left = Barline("heavy-light", "forward")
            if meas.find("endRepeat") is not None:
                rm.right = Barline("light-heavy", "backward")
            voices = meas.findall("voice")
            length = Fraction(0)
            if voices:                                   # MuseScore 3 / 4
                for vi, voice in enumerate(voices):
                    length = max(length, self._stream(list(voice), rm, mi, vi, None))
            else:                                        # MuseScore 1 / 2
                length = self._stream(list(meas), rm, mi, 0, mtick)
            declared: Optional[Fraction] = None
            if meas.get("len"):
                declared = _frac(meas.get("len")) * 4
                length = declared
                rm.implicit = True
            rm.length = max(rm.length, length)
            nominal[mi] = self.time.duration
            mtick += int((declared if declared else self.time.duration) * self.division)

    def _voice_of(self, el: ET.Element, default: str) -> str:
        t = el.findtext("track")
        if t is not None and t.strip().isdigit():
            return str(self.voice_base + int(t) % 4 + 1)
        return default

    def _tuplet_chain(self, tid: Optional[str]) -> List[Tuple[int, int]]:
        """Ratios of a MuseScore 1/2 tuplet and of the tuplets it is nested in,
        outermost first."""
        chain: List[Tuple[int, int]] = []
        seen = set()
        while tid and tid in self._tuplet_defs and tid not in seen:
            seen.add(tid)
            actual, normal, parent, _ = self._tuplet_defs[tid]
            chain.insert(0, (actual, normal))
            tid = parent
        return chain

    # ------------------------------------------------------------------
    def _stream(self, elements: List[ET.Element], rm: RawMeasure, mi: int, vi: int,
                mtick: Optional[int]) -> Fraction:
        """Read one stream of measure elements; returns the end of the content.

        ``mtick`` is None for a MuseScore 3/4 <voice> (relative positions) and
        the absolute measure start tick for a MuseScore 1/2 measure."""
        legacy = mtick is not None
        cursor = Fraction(0)
        end = Fraction(0)
        stack: List[Tuple[int, int]] = []           # MuseScore 3/4 nested tuplets
        stack_info: List[dict] = []
        stack_start = False
        last: Dict[str, Event] = {}                 # voice -> last chord / rest
        graces: Dict[str, List[Event]] = {}         # voice -> pending grace notes
        cur_tid: Dict[str, Optional[str]] = {}      # voice -> MuseScore 1/2 tuplet id
        fermata: Optional[str] = None
        default_voice = str(self.voice_base + vi + 1)
        pid, staff = self.part.pid, self.staff

        def tuplet_mark(kind: str, number: str, info: dict) -> dict:
            return {"type": kind, "number": number, "bracket": info.get("bracket"),
                    "show": info.get("show", "actual"), "placement": None}

        for el in elements:
            tag = el.tag
            hidden = el.findtext("visible") == "0"
            if tag == "location":
                cursor += _frac(el.findtext("fractions")) * 4
            elif tag == "tick" and legacy:
                cursor = Fraction(_int(el.text, mtick) - mtick, self.division)
            elif tag == "Clef":
                code = el.findtext("concertClefType") or el.findtext("subtype") or "G"
                rm.clefs.append((staff, cursor, _clef(code)))
            elif tag == "KeySig":
                k = el.findtext("concertKey") or el.findtext("accidental") or el.findtext("key")
                if k is not None and staff == 1:
                    try:
                        rm.key = Key(int(k))
                    except ValueError:
                        pass
            elif tag == "TimeSig":
                n, d = el.findtext("sigN"), el.findtext("sigD")
                if n and d:
                    sub = el.findtext("subtype")
                    symbol = {"1": "common", "2": "cut"}.get(sub or "")
                    self.time = TimeSig(int(n), int(d), symbol, n)
                    if staff == 1:
                        rm.time = self.time
            elif tag == "Tempo":
                if not hidden:
                    self._tempo(el, cursor, rm)
            elif tag == "Dynamic":
                if hidden:
                    continue
                sub = (el.findtext("subtype") or "").strip()
                if not sub or sub == "other-dynamics":
                    sub = _text_of(el, DYNAMIC_SYMS)
                if sub:
                    rm.directions.append(Direction("dynamics", cursor, pid, staff, None, sub))
            elif tag in ("StaffText", "SystemText", "Expression", "InstrumentChange"):
                t = _text_of(el)
                if t and not hidden:
                    placement = "below" if el.findtext("placement") == "below" else "above"
                    rm.directions.append(Direction("words", cursor, pid, staff, placement, t))
            elif tag == "RehearsalMark":
                t = _text_of(el)
                if t and not hidden:
                    rm.directions.append(Direction("rehearsal", cursor, pid, staff, "above", t))
            elif tag == "Tuplet":
                try:
                    ratio = (int(el.findtext("actualNotes") or 3), int(el.findtext("normalNotes") or 2))
                except ValueError:
                    ratio = (3, 2)
                if ratio[0] <= 0 or ratio[1] <= 0:
                    ratio = (3, 2)
                if el.get("id") is not None:       # MuseScore 1/2: referenced by id
                    parent = (el.findtext("Tuplet") or "").strip() or None
                    self._tuplet_defs[el.get("id")] = (ratio[0], ratio[1], parent, _tuplet_info(el))
                else:                              # MuseScore 3/4: inline
                    stack.append(ratio)
                    stack_info.append(_tuplet_info(el))
                    stack_start = True
            elif tag == "endTuplet":
                if stack:
                    stack.pop()
                    info = stack_info.pop()
                    if default_voice in last:
                        last[default_voice].tuplets.append(tuplet_mark("stop", str(len(stack) + 1), info))
            elif tag == "Fermata":
                fermata = "inverted" if "Below" in (el.findtext("subtype") or "") else "upright"
            elif tag == "BarLine":
                sub = el.findtext("subtype") or "normal"
                if sub in ("end-repeat", "end-start-repeat"):
                    rm.right = Barline("light-heavy", "backward")
                elif sub == "start-repeat":
                    rm.left = Barline("heavy-light", "forward")
                elif sub in BARLINES:
                    rm.right = Barline(BARLINES[sub])
            elif tag == "Spanner":                 # MuseScore 3/4
                kind = el.get("type", "")
                role = "start" if el.find("next") is not None else \
                    ("stop" if el.find("prev") is not None else "")
                self.spanners.append((kind, role, el.find(kind), mi, cursor, default_voice))
            elif tag == "endSpanner" and legacy:
                opened = self._open_ids.pop(el.get("id", ""), None)
                if opened is not None:
                    kind, body, voice = opened
                    self.spanners.append((kind, "stop", body, mi, cursor, voice))
            elif legacy and tag in LEGACY_SPANNERS and el.get("id") is not None:
                if tag == "Slur":
                    continue    # its start and end are marked on the chords themselves
                voice = self._voice_of(el, default_voice)
                self._open_ids[el.get("id")] = (tag, el, voice)
                self.spanners.append((tag, "start", el, mi, cursor, voice))
            elif tag in ("Chord", "Rest"):
                voice = self._voice_of(el, default_voice) if legacy else default_voice
                if legacy and el.find("tick") is not None:      # MuseScore 1 positions
                    cursor = Fraction(_int(el.findtext("tick"), mtick) - mtick, self.division)
                tid = ((el.findtext("Tuplet") or "").strip() or None) if legacy else None
                chain = list(stack) + self._tuplet_chain(tid)
                ev, dur, grace = self._chordrest(el, tag == "Rest", cursor, chain, voice)
                if ev is not None:
                    if not grace:
                        if stack_start and stack:
                            ev.tuplets.append(tuplet_mark("start", str(len(stack)), stack_info[-1]))
                            stack_start = False
                        if legacy:
                            prev = cur_tid.get(voice)
                            if prev != tid:
                                if prev is not None and voice in last:
                                    last[voice].tuplets.append(
                                        tuplet_mark("stop", "1", self._tuplet_defs[prev][3]))
                                if tid is not None and tid in self._tuplet_defs:
                                    ev.tuplets.append(tuplet_mark("start", "1", self._tuplet_defs[tid][3]))
                            cur_tid[voice] = tid if tid in self._tuplet_defs else None
                        if fermata:
                            ev.fermata = fermata
                            fermata = None
                    rm.events.append(ev)
                    self.events.append((mi, ev))
                if grace:
                    if ev is not None:
                        graces.setdefault(voice, []).append(ev)
                    continue
                # a main chord / rest owns the grace notes before it and moves time on
                pend = graces.pop(voice, [])
                for k, g in enumerate(pend):
                    g.grace_rank = k - len(pend)
                    g.onset = cursor
                if ev is not None:
                    last[voice] = ev
                cursor += dur
                end = max(end, cursor)
        for voice, pend in graces.items():
            for k, g in enumerate(pend):
                g.grace_rank = k - len(pend)
        for voice, tid in cur_tid.items():
            if tid is not None and voice in last:
                last[voice].tuplets.append(tuplet_mark("stop", "1", self._tuplet_defs[tid][3]))
        return end

    # ------------------------------------------------------------------
    def _tempo(self, el: ET.Element, cursor: Fraction, rm: RawMeasure) -> None:
        tel = el.find("text")
        if tel is None:
            tel = el.find("html-data")
        syms = [(s.text or "").lower() for s in tel.iter("sym")] if tel is not None else []
        plain = _NOTE_CHARS.sub("", _plain(tel))
        m = re.search(r"=\s*(\d+(?:[.,]\d+)?)", plain)
        words = plain[:m.start()] if m else plain
        words = re.sub(r"[\s=(\[]+$", "", words).strip()
        if words:
            rm.directions.append(Direction("words", cursor, self.part.pid, self.staff, "above", words))
        if not m:
            return
        unit, dots = "quarter", 0
        for s in syms:
            if "dot" in s:
                dots += 1
            elif "half" in s:
                unit = "half"
            elif "whole" in s:
                unit = "whole"
            elif "8th" in s or "eighth" in s:
                unit = "eighth"
            elif "16th" in s:
                unit = "16th"
        try:
            bpm = str(int(round(float(m.group(1).replace(",", ".")))))
        except ValueError:
            return
        rm.directions.append(Direction("metronome", cursor, self.part.pid, self.staff, "above",
                                       bpm, unit, extra={"dots": dots}))

    def _duration(self, el: ET.Element) -> Fraction:
        d = el.find("duration")
        if d is None:
            return Fraction(0)
        z, n = d.get("z"), d.get("n")
        if z and n:                                # MuseScore 1/2: <duration z="3" n="4"/>
            try:
                return Fraction(int(z), int(n)) * 4
            except (ValueError, ZeroDivisionError):
                return Fraction(0)
        return _frac(d.text) * 4                   # MuseScore 3/4: <duration>3/4</duration>

    def _chordrest(self, el, is_rest, cursor, chain, voice_id):
        """Returns (event or None if it is not drawn, duration, is_grace)."""
        ev = Event(part=self.part.pid, staff=self.staff, voice=voice_id, onset=cursor)
        dtype = (el.findtext("durationType") or "quarter").strip()
        ev.dots = _int(el.findtext("dots"))
        factor = Fraction(1)
        for actual, normal in chain:
            factor *= Fraction(normal, actual)
        if chain:
            ev.time_mod = chain[-1]
        if dtype == "measure":
            ev.duration = self._duration(el) or self.time.duration
            ev.type = "whole"
            ev.measure_rest = True
        else:
            ev.type = dtype
            ev.duration = dotted_duration(dtype, ev.dots) * factor
        grace = [c.tag for c in el if c.tag in GRACE_TAGS]
        if grace:
            ev.grace = True
            ev.grace_slash = grace[0] == "acciaccatura"
            ev.duration = Fraction(0)
        dur = ev.duration
        sd = el.findtext("StemDirection")
        if sd in ("up", "down"):
            ev.stem_xml = sd
        if is_rest:
            if el.findtext("visible") == "0":
                return None, dur, ev.grace
            ev.is_rest = True
            return ev, dur, ev.grace
        for c in el:
            if c.tag in ("Articulation", "Ornament"):
                arts, orns, ferm = _marks(c.findtext("subtype") or "")
                ev.articulations += [a for a in arts if a not in ev.articulations]
                ev.ornaments += [o for o in orns if o not in ev.ornaments]
                if ferm:
                    ev.fermata = ferm
            elif c.tag == "Arpeggio":
                ev.arpeggio = True
            elif c.tag == "Note":
                ev.notes.append(self._note(c))
            elif c.tag == "Slur" and c.get("type") in ("start", "stop"):     # MuseScore 1/2
                ev.slurs.append({"type": c.get("type"), "number": "L" + c.get("id", "0"),
                                 "placement": None})
        if not ev.notes:
            return None, dur, ev.grace
        return ev, dur, ev.grace

    def _note(self, el) -> Note:
        pitch = _int(el.findtext("pitch"), 60)
        tpc_text = el.findtext("tpc")
        if tpc_text is not None and tpc_text.strip().lstrip("-").isdigit():
            step, alter, octave = spell(pitch, int(tpc_text))
        else:
            names = [("C", 0), ("C", 1), ("D", 0), ("E", -1), ("E", 0), ("F", 0), ("F", 1), ("G", 0),
                     ("A", -1), ("A", 0), ("B", -1), ("B", 0)]
            step, alter = names[pitch % 12]
            octave = (pitch - alter - STEP_SEMITONES[step]) // 12 - 1
        n = Note(step=step, octave=octave, alter=alter)
        acc = el.find("Accidental")
        if acc is not None:
            n.accidental = ACCIDENTALS.get((acc.findtext("subtype") or "").strip(), None)
        for sp in el.findall("Spanner"):
            if sp.get("type") == "Tie":
                if sp.find("next") is not None:
                    n.tie_start = True
                if sp.find("prev") is not None:
                    n.tie_stop = True
        if el.find("Tie") is not None:      # MuseScore 1/2 (and early 3)
            n.tie_start = True
        if el.find("endSpanner") is not None:
            n.tie_stop = True
        f = el.find("Fingering")
        if f is not None:
            t = _text_of(f)
            if t:
                n.fingering = t
        return n


def parse_musescore(path: str):
    data = _read(path)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise MusicXMLError(f"invalid MuseScore file: {exc}") from None
    if root.tag != "museScore":
        raise MusicXMLError("not a MuseScore score")
    score_el = root.find("Score")
    if score_el is None and root.find("Part") is not None:
        score_el = root                     # MuseScore 1 has no <Score> wrapper
    if score_el is None:
        raise MusicXMLError("not a MuseScore score")
    division = _int(score_el.findtext("Division") or root.findtext("Division"), 480) or 480
    # ---------------------------------------------------------- metadata
    meta = {m.get("name"): (m.text or "").strip() for m in score_el.findall("metaTag")}
    title = meta.get("workTitle", "") or meta.get("movementTitle", "")
    subtitle = meta.get("subtitle", "")
    composer = meta.get("composer", "")
    staff_els = {int(s.get("id")): s for s in score_el.findall("Staff") if s.get("id", "").isdigit()}
    first = staff_els.get(min(staff_els)) if staff_els else None
    if first is not None:
        for vbox in first.findall("VBox"):
            for t in vbox.findall("Text"):
                style = (t.findtext("style") or t.findtext("subtype") or "").strip().lower()
                txt = _text_of(t)
                if style == "title" and txt:
                    title = txt
                elif style == "subtitle" and txt:
                    subtitle = txt
                elif style == "composer" and txt:
                    composer = txt
    # ---------------------------------------------------------- parts
    parts: List[RawPart] = []
    gid = 1
    for pi, part_el in enumerate(score_el.findall("Part"), 1):
        name = (part_el.findtext("trackName") or part_el.findtext("Instrument/longName") or f"P{pi}").strip()
        rp = RawPart(pid=f"P{pi}", name=name)
        staff_defs = part_el.findall("Staff")
        rp.staves = max(1, len(staff_defs))
        measures: List[RawMeasure] = []
        nominal: List[Fraction] = []
        inst_clefs = {_int(c.get("staff"), 1): (c.text or "G") for c in part_el.findall("Instrument/clef")}
        for k, sdef in enumerate(staff_defs, 1):
            default = sdef.findtext("defaultClef") or sdef.findtext("defaultConcertClef") or \
                inst_clefs.get(k) or ("F" if k == 2 and len(staff_defs) == 2 else "G")
            sid = int(sdef.get("id")) if (sdef.get("id") or "").isdigit() else gid
            staff_el = staff_els.get(sid)
            if staff_el is None:
                staff_el = staff_els.get(gid)
            gid += 1
            if staff_el is None:
                continue
            sp = _StaffParser(staff_el, rp, k, (k - 1) * 4, division)
            sp.parse(measures, nominal)
            if measures:
                if not any(c[0] == k and c[1] == 0 for c in measures[0].clefs):
                    measures[0].clefs.insert(0, (k, Fraction(0), _clef(default)))
            _resolve_spanners(sp, measures)
        rp.measures = measures
        parts.append(rp)
    if not parts:
        raise MusicXMLError("the MuseScore file contains no parts")
    if not any(rm.events for rp in parts for rm in rp.measures):
        raise MusicXMLError("no notes or rests could be read from this MuseScore file "
                            f"(format version {root.get('version', '?')})")
    has_acc = b"<Accidental>" in data
    return parts, (title, subtitle, composer), has_acc


def _resolve_spanners(sp: _StaffParser, measures: List[RawMeasure]) -> None:
    """Turn the spanner start/end markers collected while reading a staff into
    MusicXML-like data: slurs are attached to the nearest chords of their
    voice, trills become ornaments, the other spanners become directions."""
    by_voice: Dict[str, List[Tuple[int, Event]]] = {}
    for mi, ev in sp.events:
        by_voice.setdefault(ev.voice, []).append((mi, ev))
    slur_counter = 0
    open_vol = False
    pid = sp.part.pid
    for kind, role, body, mi, cursor, voice_id in sp.spanners:
        sub = (body.findtext("subtype") or "").strip() if body is not None else ""
        rm = measures[mi]
        if kind == "Slur":
            evs = [(m, e) for m, e in by_voice.get(voice_id, []) if not e.is_rest]
            if role == "start":
                cand = [e for m, e in evs if (m, e.onset) >= (mi, cursor)]
                if cand:
                    slur_counter += 1
                    cand[0].slurs.append({"type": "start", "number": f"s{slur_counter}", "placement": None})
            else:
                cand = [e for m, e in evs if (m, e.onset) <= (mi, cursor)]
                if cand:
                    # pair with the most recent unmatched slur start of this voice
                    open_nums = []
                    for m, e in evs:
                        for s in e.slurs:
                            if s["type"] == "start":
                                open_nums.append(s["number"])
                            elif s["type"] == "stop" and s["number"] in open_nums:
                                open_nums.remove(s["number"])
                    if open_nums:
                        cand[-1].slurs.append({"type": "stop", "number": open_nums[-1], "placement": None})
        elif kind == "Trill":
            if role == "start":
                cand = [e for m, e in by_voice.get(voice_id, [])
                        if not e.is_rest and not e.grace and (m, e.onset) >= (mi, cursor)]
                if cand and "trill-mark" not in cand[0].ornaments:
                    cand[0].ornaments.append("trill-mark")
        elif kind == "HairPin":
            pos_mi, pos_t = _end_pos(measures, mi, cursor, role)
            typ = ("diminuendo" if sub in ("1", "3") else "crescendo") if role == "start" else "stop"
            measures[pos_mi].directions.append(Direction("wedge", pos_t, pid, sp.staff, None, "", typ, 1))
        elif kind == "Pedal":
            pos_mi, pos_t = _end_pos(measures, mi, cursor, role)
            measures[pos_mi].directions.append(Direction("pedal", pos_t, pid, sp.staff, "below", "",
                                                         role, 1, extra={"line": True}))
        elif kind == "Ottava":
            pos_mi, pos_t = _end_pos(measures, mi, cursor, role)
            if role == "start":
                direction, size = OTTAVA.get(sub, ("down", 8))
                measures[pos_mi].directions.append(Direction("octave", pos_t, pid, sp.staff, None, "",
                                                             direction, 1, size))
            else:
                measures[pos_mi].directions.append(Direction("octave", pos_t, pid, sp.staff, None, "",
                                                             "stop", 1, 8))
        elif kind == "Volta" and sp.staff == 1:
            if role == "start":
                endings = (body.findtext("endings") if body is not None else "") or "1"
                text = ""
                if body is not None:
                    text = _plain(body.find("beginText")) or _plain(body.find("text"))
                bl = rm.left or Barline()
                bl.ending_number, bl.ending_type, bl.ending_text = endings, "start", text or None
                rm.left = bl
                open_vol = True
            elif open_vol:
                end_mi = mi - 1 if cursor == 0 and mi > 0 else mi
                rme = measures[end_mi]
                bl = rme.right or Barline()
                bl.ending_type = "stop"
                rme.right = bl
                open_vol = False


def _end_pos(measures, mi, cursor, role):
    """Spanner ends at a barline belong to the end of the previous measure."""
    if role == "stop" and cursor == 0 and mi > 0:
        prev = measures[mi - 1]
        return mi - 1, prev.length
    return mi, cursor
