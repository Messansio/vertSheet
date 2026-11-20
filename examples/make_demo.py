"""Generate examples/demo.musicxml - an original piano study that exercises the
features of vertSheet (chords, seconds, accidentals, ties, slurs, triplets,
16th beams with hooks, grace notes, clef changes, 8va, key/time changes,
two voices, repeats + volta, pedal, hairpins, dynamics, fermatas).

The music is written in a tiny text notation:

    pitch(es)+duration[dots][suffixes]
      pitches   G4, F#5, Bb3, Cn5 (n = natural), chords with '+': C4+E4+G4
      rest      r
      duration  w h q e s t   (whole ... 32nd)
      prefixes  (  slur start     [  triplet start     g  grace note
      suffixes  ~ tie   ) slur end   ] triplet end   ! staccato   > accent
                ^ fermata   - tenuto   @3 fingering
    {p} {mf} ...   dynamics           {<} {>} {!}  hairpin start/stop
    {w:text}       words              {tempo:Andante:92}
    {ped} {*}      pedal              {8va} {8va-}  octave shift
    {clef:F}       clef change
"""
from __future__ import annotations

import os
import re
from xml.sax.saxutils import escape

DIV = 24
DUR = {"w": 96, "h": 48, "q": 24, "e": 12, "s": 6, "t": 3}
TYPE = {"w": "whole", "h": "half", "q": "quarter", "e": "eighth", "s": "16th", "t": "32nd"}
FLAGS = {"e": 1, "s": 2, "t": 3}
SHARPS = "FCGDAEB"
FLATS = "BEADGCF"

NOTE_RE = re.compile(r"^(?P<pre>[(\[g]*)(?P<pitches>r|(?:[A-G](?:##|#|bb|b|n)?-?\d\+?)+)"
                     r"(?P<dur>[whqest])(?P<dots>\.*)(?P<post>[~)\]!>^\-]*)(?:@(?P<fing>\d+))?$")
PITCH_RE = re.compile(r"([A-G])(##|#|bb|b|n)?(-?\d)")

PIECE = {
    "title": "Study in Columns",
    "composer": "vertSheet demo",
    "measures": [
        # ---- A: 3/4, G major, repeated, first ending on bar 8
        {"key": 1, "time": (3, 4), "left": "forward",
         "rh": "{tempo:Andante:92} {p} (D5q@5 G4e A4e B4e C5e",
         "lh": "{ped} G3+B3h A3q"},
        {"rh": "D5q G4q! G4q)!", "lh": "B3h. {*}"},
        {"rh": "(E5q C5e D5e E5e F#5e", "lh": "C4h."},
        {"rh": "G5q G4q G4q)", "lh": "B3h."},
        {"rh": "{<} (C5q D5e C5e B4e A4e", "lh": "A3h."},
        {"rh": "B4q C5e B4e A4e G4e {!}", "lh": "G3h."},
        {"rh": "(F#4q G4e A4e B4e G4e)", "lh": "D4q B3q G3q"},
        {"rh": "{>} A4h.~ {!}", "lh": "D3h.", "ending": "1", "right": "backward"},
        # ---- B: higher register, 8va, triplets, 16ths, clef change, grace notes
        {"rh": "{mf} A4q B5q G5e A5e", "lh": "D3+A3h."},
        {"rh": "B5e G5e A5q D5e E5e", "lh": "G3+D4h."},
        {"rh": "{8va} G6q E6e F#6e G6e D6e", "lh": "E3+G3h."},
        {"rh": "C#7q B6e A6e G6e F#6e {8va-}", "lh": "A2+E3h."},
        {"rh": "[G5e F#5e E5e] [D5e C5e B4e] A4q", "lh": "C3+G3h."},
        {"rh": "G4s A4s B4s C5s D5e. E5s F#5q", "lh": "D3h {clef:G} F#4q"},
        {"rh": "{>} gA5e G5q! D5q! B4q!", "lh": "{clef:F} G3+B3+D4q rq G2q"},
        {"rh": "G5h.^ {!}", "lh": "G2+G3h.^", "right": "light-light"},
        # ---- C: 4/4, F major, two voices, seconds in chords, more accidentals
        {"key": -1, "time": (4, 4),
         "rh": "{f} {w:energico} F5e A5e C6e A5e F5e C5e A4e C5e", "lh": "F2+F3w"},
        {"rh": "Bb4+D5q> Bb4+D5q> C5+E5h", "lh": "Bb2e F3e Bb3e D4e C3e G3e C4e E4e"},
        {"rh": "F5q. E5e D5e C5e Bb4e A4e", "lh": "F3+G3h C3+E3h"},
        {"rh": "{<} (A4e Bb4e Bn4e C5e) C#5q D5q {!}", "lh": "D3q Ab2q G2h"},
        {"rh": "{w:dolce} C6h A5h", "rh2": "F5q E5q F5h", "lh": "{ped} F2h F3h"},
        {"rh": "{p} F5w^", "rh2": "A4+C5w", "lh": "F2+C3+F3w^ {*}", "right": "light-heavy"},
    ],
}


class Builder:
    def __init__(self):
        self.out = []
        self.key = 0
        self.ties = {1: set(), 2: set()}
        self.acc_state = {}

    def w(self, s):
        self.out.append(s)

    # ------------------------------------------------------------------
    def key_alter(self, step):
        if self.key > 0 and step in SHARPS[:self.key]:
            return 1
        if self.key < 0 and step in FLATS[:-self.key]:
            return -1
        return 0

    def parse_voice(self, text, staff):
        """Returns a list of items: ('dir', xml) or ('note', dict)."""
        items = []
        in_tuplet = False
        tuplet_first = False
        for tok in text.split():
            if tok.startswith("{"):
                items.append(("dir", tok[1:-1]))
                continue
            m = NOTE_RE.match(tok)
            if not m:
                raise ValueError(f"bad token {tok!r}")
            pre, post = m.group("pre"), m.group("post")
            if "[" in pre:
                in_tuplet = True
                tuplet_first = True
            dur = DUR[m.group("dur")]
            add = dur
            for _ in m.group("dots"):
                add //= 2
                dur += add
            note = {
                "rest": m.group("pitches") == "r",
                "pitches": [] if m.group("pitches") == "r" else PITCH_RE.findall(m.group("pitches")),
                "dur": dur, "type": TYPE[m.group("dur")], "dots": len(m.group("dots")),
                "flags": FLAGS.get(m.group("dur"), 0), "grace": "g" in pre,
                "slur_start": "(" in pre, "slur_stop": ")" in post, "tie": "~" in post,
                "staccato": "!" in post, "accent": ">" in post, "fermata": "^" in post,
                "tenuto": "-" in post, "fing": m.group("fing"),
                "tuplet": in_tuplet, "tuplet_start": tuplet_first and in_tuplet,
                "tuplet_stop": "]" in post,
            }
            tuplet_first = False
            if note["tuplet"]:
                note["dur"] = note["dur"] * 2 // 3
            if "]" in post:
                in_tuplet = False
            if note["grace"]:
                note["dur"] = 0
            items.append(("note", note))
        return items

    def beams(self, notes):
        """Assign beam values per beat (quarter) for one voice."""
        pos = 0
        groups = []
        cur, cur_beat = [], None
        for n in notes:
            if n["grace"]:
                continue
            beat = pos // DIV
            if n["rest"] or n["flags"] == 0:
                if len(cur) > 1:
                    groups.append(cur)
                cur, cur_beat = [], None
            else:
                if cur and beat != cur_beat:
                    if len(cur) > 1:
                        groups.append(cur)
                    cur = []
                cur.append(n)
                cur_beat = beat
            pos += n["dur"]
        if len(cur) > 1:
            groups.append(cur)
        for g in groups:
            for k, n in enumerate(g):
                n.setdefault("beam", {})[1] = "begin" if k == 0 else ("end" if k == len(g) - 1 else "continue")
            for level in (2, 3):
                i = 0
                while i < len(g):
                    if g[i]["flags"] >= level:
                        j = i
                        while j + 1 < len(g) and g[j + 1]["flags"] >= level:
                            j += 1
                        if j > i:
                            g[i]["beam"][level] = "begin"
                            for q in range(i + 1, j):
                                g[q]["beam"][level] = "continue"
                            g[j]["beam"][level] = "end"
                        else:
                            g[i]["beam"][level] = "forward hook" if i == 0 else "backward hook"
                        i = j + 1
                    else:
                        i += 1

    # ------------------------------------------------------------------
    def direction(self, text, staff):
        placement = "below" if staff == 1 else "above"
        if text in ("pp", "p", "mp", "mf", "f", "ff", "sf", "sfz", "fp"):
            body = f"<dynamics><{text}/></dynamics>"
        elif text in ("<", ">"):
            body = f'<wedge type="{"crescendo" if text == "<" else "diminuendo"}"/>'
        elif text == "!":
            body = '<wedge type="stop"/>'
        elif text == "ped":
            body, placement = '<pedal type="start" line="yes"/>', "below"
        elif text == "*":
            body, placement = '<pedal type="stop" line="yes"/>', "below"
        elif text == "8va":
            body, placement = '<octave-shift type="down" size="8"/>', "above"
        elif text == "8va-":
            body, placement = '<octave-shift type="stop" size="8"/>', "above"
        elif text.startswith("w:"):
            body, placement = f"<words>{escape(text[2:])}</words>", "above"
        elif text.startswith("tempo:"):
            _, word, bpm = text.split(":")
            self.w(f'<direction placement="above"><direction-type><words font-weight="bold">{escape(word)}'
                   f'</words></direction-type><staff>{staff}</staff></direction>')
            body = (f"<metronome><beat-unit>quarter</beat-unit><per-minute>{bpm}</per-minute></metronome>")
            placement = "above"
        elif text.startswith("clef:"):
            sign = text[5:]
            line = 2 if sign == "G" else 4
            self.w(f'<attributes><clef number="{staff}"><sign>{sign}</sign><line>{line}</line></clef></attributes>')
            return
        else:
            raise ValueError(text)
        self.w(f'<direction placement="{placement}"><direction-type>{body}</direction-type>'
               f'<staff>{staff}</staff></direction>')

    def note_xml(self, n, staff, voice):
        if n["rest"]:
            chord = [None]
        else:
            chord = n["pitches"]
        new_ties = set()
        for idx, p in enumerate(chord):
            parts = ["<note>"]
            if n["grace"]:
                parts.append('<grace slash="yes"/>')
            if idx > 0:
                parts.append("<chord/>")
            acc_xml = ""
            tie_stop = tie_start = False
            if p is None:
                parts.append("<rest/>")
            else:
                step, acc, octv = p
                alter = {"#": 1, "##": 2, "b": -1, "bb": -2, "n": 0, None: None, "": None}[acc or None]
                if alter is None:
                    alter = self.acc_state.get((staff, step, octv), self.key_alter(step))
                pkey = (step, alter, octv)
                expected = self.acc_state.get((staff, step, octv), self.key_alter(step))
                tie_stop = pkey in self.ties[staff]
                if alter != expected and not tie_stop:
                    acc_xml = "<accidental>" + {1: "sharp", -1: "flat", 0: "natural", 2: "double-sharp",
                                                -2: "flat-flat"}[alter] + "</accidental>"
                self.acc_state[(staff, step, octv)] = alter
                alt = f"<alter>{alter}</alter>" if alter else ""
                parts.append(f"<pitch><step>{step}</step>{alt}<octave>{octv}</octave></pitch>")
                if n["tie"]:
                    tie_start = True
                    new_ties.add(pkey)
            if not n["grace"]:
                parts.append(f"<duration>{n['dur']}</duration>")
            if tie_stop:
                parts.append('<tie type="stop"/>')
            if tie_start:
                parts.append('<tie type="start"/>')
            parts.append(f"<voice>{voice}</voice><type>{n['type']}</type>" + "<dot/>" * n["dots"])
            parts.append(acc_xml)
            if n["tuplet"]:
                parts.append("<time-modification><actual-notes>3</actual-notes>"
                             "<normal-notes>2</normal-notes></time-modification>")
            parts.append(f"<staff>{staff}</staff>")
            if idx == 0:
                for lvl, val in sorted(n.get("beam", {}).items()):
                    parts.append(f'<beam number="{lvl}">{val}</beam>')
            nots = []
            if tie_stop:
                nots.append('<tied type="stop"/>')
            if tie_start:
                nots.append('<tied type="start"/>')
            if idx == 0:
                if n["slur_start"]:
                    nots.append('<slur type="start" number="1"/>')
                if n["slur_stop"]:
                    nots.append('<slur type="stop" number="1"/>')
                if n["tuplet_start"]:
                    nots.append('<tuplet type="start" bracket="no"/>')
                if n["tuplet_stop"]:
                    nots.append('<tuplet type="stop"/>')
                arts = "".join(f"<{a}/>" for a in ("staccato", "accent", "tenuto") if n[a])
                if arts:
                    nots.append(f"<articulations>{arts}</articulations>")
                if n["fermata"]:
                    nots.append('<fermata type="upright"/>')
                if n["fing"]:
                    nots.append(f"<technical><fingering>{n['fing']}</fingering></technical>")
            if nots:
                parts.append("<notations>" + "".join(nots) + "</notations>")
            parts.append("</note>")
            self.w("".join(parts))
        # ties carried to the next note of this staff
        if not n["grace"]:
            self.ties[staff] = new_ties

    def voice(self, text, staff, voice):
        items = self.parse_voice(text, staff)
        self.beams([it[1] for it in items if it[0] == "note"])
        total = 0
        for kind, it in items:
            if kind == "dir":
                self.direction(it, staff)
            else:
                self.note_xml(it, staff, voice)
                total += it["dur"]
        return total

    def build(self, piece):
        self.w('<?xml version="1.0" encoding="UTF-8"?>')
        self.w('<score-partwise version="3.1">')
        self.w(f"<work><work-title>{escape(piece['title'])}</work-title></work>")
        self.w(f'<identification><creator type="composer">{escape(piece["composer"])}</creator></identification>')
        self.w('<part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>')
        self.w('<part id="P1">')
        for i, m in enumerate(piece["measures"], 1):
            self.w(f'<measure number="{i}">')
            self.acc_state = {}
            if m.get("left") or m.get("ending"):
                rep = '<repeat direction="forward"/>' if m.get("left") == "forward" else ""
                style = "<bar-style>heavy-light</bar-style>" if rep else ""
                end = f'<ending number="{m["ending"]}" type="start"/>' if m.get("ending") else ""
                self.w(f'<barline location="left">{style}{end}{rep}</barline>')
            attrs = []
            if i == 1:
                attrs.append(f"<divisions>{DIV}</divisions>")
            if "key" in m:
                self.key = m["key"]
                attrs.append(f"<key><fifths>{m['key']}</fifths></key>")
            if "time" in m:
                attrs.append(f"<time><beats>{m['time'][0]}</beats><beat-type>{m['time'][1]}</beat-type></time>")
            if i == 1:
                attrs.append("<staves>2</staves><clef number=\"1\"><sign>G</sign><line>2</line></clef>"
                             "<clef number=\"2\"><sign>F</sign><line>4</line></clef>")
            if attrs:
                self.w("<attributes>" + "".join(attrs) + "</attributes>")
            length = self.voice(m["rh"], 1, 1)
            if m.get("rh2"):
                self.w(f"<backup><duration>{length}</duration></backup>")
                self.voice(m["rh2"], 1, 2)
            self.w(f"<backup><duration>{length}</duration></backup>")
            self.voice(m["lh"], 2, 5)
            right = m.get("right")
            if right or m.get("ending"):
                if right == "backward":
                    style, rep = "light-heavy", '<repeat direction="backward"/>'
                else:
                    style, rep = right or "regular", ""
                end = f'<ending number="{m["ending"]}" type="stop"/>' if m.get("ending") else ""
                self.w(f'<barline location="right"><bar-style>{style}</bar-style>{end}{rep}</barline>')
            self.w("</measure>")
        self.w("</part></score-partwise>")
        return "\n".join(self.out)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "demo.musicxml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(Builder().build(PIECE))
    print("wrote", path)


if __name__ == "__main__":
    main()
