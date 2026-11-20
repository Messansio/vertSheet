"""Core data model shared by the parser, the analyser, the layout engine and
the engraver.

Coordinates / conventions used throughout the project
-----------------------------------------------------
* Musical time is measured in quarter notes using ``fractions.Fraction`` so
  that tuplets never accumulate rounding errors.
* A *staff position* (``pos``) counts diatonic steps from the bottom staff
  line: 0 = bottom line, 1 = first space, 2 = second line ... 8 = top line.
* "RH" / "LH" are the two hands. Every pair of columns on the output page is
  one LH column (left) and one RH column (right).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

HANDS = ("LH", "RH")
STEPS = "CDEFGAB"
STEP_SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

TYPE_QUARTERS: Dict[str, Fraction] = {
    "maxima": Fraction(32), "long": Fraction(16), "breve": Fraction(8),
    "whole": Fraction(4), "half": Fraction(2), "quarter": Fraction(1),
    "eighth": Fraction(1, 2), "16th": Fraction(1, 4), "32nd": Fraction(1, 8),
    "64th": Fraction(1, 16), "128th": Fraction(1, 32), "256th": Fraction(1, 64),
}
TYPE_ORDER = list(TYPE_QUARTERS.keys())
TYPE_FLAGS = {"eighth": 1, "16th": 2, "32nd": 3, "64th": 4, "128th": 5, "256th": 6}

SHARP_ORDER = "FCGDAEB"
FLAT_ORDER = "BEADGCF"
# Staff positions of key-signature accidentals in the treble clef.
TREBLE_SHARP_POS = [8, 5, 9, 6, 3, 7, 4]
TREBLE_FLAT_POS = [4, 7, 3, 6, 2, 5, 1]

ACCIDENTAL_FOR_ALTER = {0: "natural", 1: "sharp", -1: "flat",
                        2: "double-sharp", -2: "flat-flat"}


def diatonic(step: str, octave: int) -> int:
    return octave * 7 + STEPS.index(step)


def dotted_duration(type_name: str, dots: int) -> Fraction:
    base = TYPE_QUARTERS.get(type_name, Fraction(1))
    total, add = base, base
    for _ in range(dots):
        add /= 2
        total += add
    return total


def infer_type(duration: Fraction, time_mod: Optional[Tuple[int, int]] = None) -> Tuple[str, int]:
    """Best (type, dots) for a duration (used when <type> is missing)."""
    if duration <= 0:
        return "quarter", 0
    written = duration
    if time_mod:
        actual, normal = time_mod
        written = duration * Fraction(actual, normal)
    for t in TYPE_ORDER:
        for d in range(3):
            if dotted_duration(t, d) == written:
                return t, d
    for t in TYPE_ORDER:  # nearest shorter-or-equal plain value
        if TYPE_QUARTERS[t] <= written:
            return t, 0
    return "256th", 0


@dataclass
class Clef:
    sign: str = "G"
    line: int = 0
    octave_change: int = 0

    def _ref(self) -> Tuple[int, int]:
        sign = self.sign.upper()
        if sign == "F":
            ref, line = diatonic("F", 3), self.line or 4
        elif sign == "C":
            ref, line = diatonic("C", 4), self.line or 3
        elif sign == "G":
            ref, line = diatonic("G", 4), self.line or 2
        else:  # percussion / TAB / none: behave like a treble clef
            ref, line = diatonic("G", 4), 2
        return ref + 7 * self.octave_change, line

    def staff_position(self, step: str, octave: int) -> int:
        ref, line = self._ref()
        return diatonic(step, octave) - ref + (line - 1) * 2

    @property
    def anchor_pos(self) -> int:
        """Staff position of the line the clef symbol wraps around."""
        return (self._ref()[1] - 1) * 2

    @property
    def glyph(self) -> str:
        return {"G": "clef_g", "F": "clef_f", "C": "clef_c"}.get(self.sign.upper(), "clef_g")

    def key_positions(self, fifths: int) -> List[int]:
        """Staff positions for the key signature accidentals in this clef."""
        shift = (self.staff_position("G", 4) - 2) % 7
        if shift > 3:
            shift -= 7
        if fifths >= 0:
            out = []
            for p in TREBLE_SHARP_POS[:fifths]:
                q = p + shift
                if q > 9:
                    q -= 7
                if q < 2:
                    q += 7
                out.append(q)
            return out
        out = []
        for p in TREBLE_FLAT_POS[:-fifths]:
            q = p + shift
            if q > 8:
                q -= 7
            if q < 1:
                q += 7
            out.append(q)
        return out

    def same_as(self, other: Optional["Clef"]) -> bool:
        return (other is not None and self.sign == other.sign and
                (self.line or 0) == (other.line or 0) and self.octave_change == other.octave_change)


@dataclass
class Key:
    fifths: int = 0
    mode: str = "major"

    def alter_for(self, step: str) -> int:
        if self.fifths > 0 and step in SHARP_ORDER[: self.fifths]:
            return 1
        if self.fifths < 0 and step in FLAT_ORDER[: -self.fifths]:
            return -1
        return 0


@dataclass
class TimeSig:
    beats: int = 4
    beat_type: int = 4
    symbol: Optional[str] = None  # "common", "cut", "single-number" ...
    beats_text: str = "4"

    @property
    def duration(self) -> Fraction:
        return Fraction(4 * self.beats, self.beat_type)

    @property
    def beam_unit(self) -> Fraction:
        """Length of one beaming group (used by the auto-beamer)."""
        unit = Fraction(4, self.beat_type)
        if self.beat_type >= 8 and self.beats % 3 == 0 and self.beats > 3:
            return unit * 3  # compound meters: dotted beats
        if self.beat_type >= 8 and self.beats == 3:
            return unit * 3
        if self.beat_type == 2:
            return Fraction(1)
        return unit


@dataclass
class Note:
    step: str = "C"
    octave: int = 4
    alter: float = 0
    accidental: Optional[str] = None
    tie_start: bool = False
    tie_stop: bool = False
    fingering: Optional[str] = None
    notehead: str = "normal"
    unpitched: bool = False
    # computed by the analyser
    pos: int = 0
    displaced: int = 0       # -1 head pushed earlier, +1 pushed later (seconds)
    acc_col: int = 0

    @property
    def midi(self) -> int:
        return 12 * (self.octave + 1) + STEP_SEMITONES[self.step] + int(round(self.alter))

    @property
    def diatonic(self) -> int:
        return diatonic(self.step, self.octave)


@dataclass
class Event:
    """A chord (one or more noteheads sharing a stem) or a rest."""
    part: str = ""
    staff: int = 1
    voice: str = "1"
    onset: Fraction = Fraction(0)
    duration: Fraction = Fraction(0)
    type: str = "quarter"
    dots: int = 0
    notes: List[Note] = field(default_factory=list)
    is_rest: bool = False
    measure_rest: bool = False
    rest_display: Optional[Tuple[str, int]] = None
    grace: bool = False
    grace_slash: bool = False
    grace_rank: int = 0
    cue: bool = False
    beams: Dict[int, str] = field(default_factory=dict)
    stem_xml: Optional[str] = None
    time_mod: Optional[Tuple[int, int]] = None
    tuplets: List[dict] = field(default_factory=list)
    slurs: List[dict] = field(default_factory=list)
    articulations: List[str] = field(default_factory=list)
    ornaments: List[str] = field(default_factory=list)
    fermata: Optional[str] = None
    arpeggio: bool = False
    hand: str = "RH"
    measure_index: int = 0
    # computed by the analyser
    stem_dir: int = 0            # +1 up (towards the right on the page), -1 down, 0 none
    beam_group: Optional["BeamGroup"] = None
    acc_widths: List[float] = field(default_factory=list)
    before: float = 0.0          # extent before the slot centre (staff spaces)
    after: float = 0.0           # extent after the slot centre (staff spaces)
    geom: Optional[dict] = None  # filled by the engraver

    @property
    def key(self) -> Tuple[Fraction, int]:
        return (self.onset, self.grace_rank)

    @property
    def flags(self) -> int:
        return TYPE_FLAGS.get(self.type, 0)

    @property
    def scale(self) -> float:
        if self.grace:
            return 0.62
        if self.cue:
            return 0.75
        return 1.0

    @property
    def has_stem(self) -> bool:
        return (not self.is_rest and self.type not in ("whole", "breve", "long", "maxima")
                and self.stem_xml != "none")


@dataclass
class BeamGroup:
    events: List[Event] = field(default_factory=list)
    auto: bool = False
    direction: int = 1


@dataclass
class Direction:
    kind: str            # dynamics, words, wedge, pedal, metronome, rehearsal, octave
    onset: Fraction = Fraction(0)
    part: str = ""
    staff: int = 1
    placement: Optional[str] = None
    text: str = ""
    subtype: str = ""    # crescendo / diminuendo / stop / start / change ...
    number: int = 1
    value: int = 0       # octave-shift size, metronome bpm ...
    extra: dict = field(default_factory=dict)
    hand: str = "RH"
    measure_index: int = 0


@dataclass
class Barline:
    style: str = "regular"
    repeat: Optional[str] = None      # "forward" / "backward"
    ending_number: Optional[str] = None
    ending_type: Optional[str] = None  # start / stop / discontinue
    ending_text: Optional[str] = None


@dataclass
class Measure:
    index: int
    number: str = ""
    duration: Fraction = Fraction(4)
    implicit: bool = False
    events: Dict[str, List[Event]] = field(default_factory=lambda: {"LH": [], "RH": []})
    directions: List[Direction] = field(default_factory=list)
    clef_changes: Dict[str, List[Tuple[Fraction, Clef]]] = field(default_factory=lambda: {"LH": [], "RH": []})
    key_change: Optional[Key] = None
    time_change: Optional[TimeSig] = None
    left_barline: Optional[Barline] = None
    right_barline: Optional[Barline] = None
    # state at the start of the measure (filled by the analyser)
    key: Key = field(default_factory=Key)
    time: TimeSig = field(default_factory=TimeSig)
    start_clef: Dict[str, Clef] = field(default_factory=dict)
    # layout results
    spec: object = None
    y_top: float = 0.0
    y_end: float = 0.0
    content_top: float = 0.0
    slot_y: Dict[Tuple[Fraction, int], float] = field(default_factory=dict)
    column: object = None


@dataclass
class Score:
    title: str = ""
    subtitle: str = ""
    composer: str = ""
    measures: List[Measure] = field(default_factory=list)
    has_xml_accidentals: bool = False
    has_xml_beams: bool = False
    source_description: str = ""
    initial_clef: Dict[str, Clef] = field(default_factory=dict)
    split_mode: bool = False
