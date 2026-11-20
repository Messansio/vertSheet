"""Test-suite for vertSheet:  python -m unittest discover -s tests -v"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from contextlib import redirect_stdout
from fractions import Fraction

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "examples"))

from vertsheet import Options, render_pages, write_output  # noqa: E402
from vertsheet.__main__ import main as cli_main, parse_pitch  # noqa: E402
from vertsheet.canvas import page_to_svg  # noqa: E402
from vertsheet.model import Clef, Key, infer_type  # noqa: E402
from vertsheet.mscx import spell  # noqa: E402
import make_demo  # noqa: E402

DEMO = os.path.join(ROOT, "examples", "demo.musicxml")


def ensure_demo():
    if not os.path.isfile(DEMO):
        with open(DEMO, "w", encoding="utf-8") as fh:
            fh.write(make_demo.Builder().build(make_demo.PIECE))


def tiny_musicxml(notes_rh="", notes_lh="", measures=1, staves=2):
    """A minimal two-staff MusicXML document (quarter notes, 4/4)."""
    def notes(spec, staff):
        out = []
        for tok in spec.split():
            step, octv = tok[0], tok[-1]
            alter = {"#": 1, "b": -1}.get(tok[1], 0) if len(tok) == 3 else 0
            a = f"<alter>{alter}</alter>" if alter else ""
            out.append(f"<note><pitch><step>{step}</step>{a}<octave>{octv}</octave></pitch>"
                       f"<duration>1</duration><voice>{staff}</voice><type>quarter</type>"
                       f"<staff>{staff}</staff></note>")
        return "".join(out)
    body = []
    for i in range(measures):
        attrs = ("<attributes><divisions>1</divisions><key><fifths>0</fifths></key><time><beats>4</beats>"
                 f"<beat-type>4</beat-type></time><staves>{staves}</staves>"
                 "<clef number='1'><sign>G</sign><line>2</line></clef>"
                 "<clef number='2'><sign>F</sign><line>4</line></clef></attributes>") if i == 0 else ""
        lh = f"<backup><duration>4</duration></backup>{notes(notes_lh, 2)}" if notes_lh else ""
        body.append(f"<measure number='{i + 1}'>{attrs}{notes(notes_rh, 1)}{lh}</measure>")
    return ("<?xml version='1.0'?><score-partwise><work><work-title>T</work-title></work>"
            "<part-list><score-part id='P1'><part-name>Piano</part-name></score-part></part-list>"
            f"<part id='P1'>{''.join(body)}</part></score-partwise>")


class TheoryTests(unittest.TestCase):
    def test_clef_positions(self):
        self.assertEqual(Clef("G", 2).staff_position("E", 4), 0)   # bottom line of treble
        self.assertEqual(Clef("G", 2).staff_position("F", 5), 8)   # top line
        self.assertEqual(Clef("F", 4).staff_position("G", 2), 0)   # bottom line of bass
        self.assertEqual(Clef("F", 4).staff_position("A", 3), 8)
        self.assertEqual(Clef("C", 3).staff_position("C", 4), 4)   # alto middle line
        self.assertEqual(Clef("G", 2, -1).staff_position("E", 3), 0)  # treble 8vb

    def test_key_signature_positions(self):
        self.assertEqual(Clef("G", 2).key_positions(2), [8, 5])       # F#5, C#5
        self.assertEqual(Clef("F", 4).key_positions(1), [6])          # F#3
        self.assertEqual(Clef("G", 2).key_positions(-3), [4, 7, 3])   # Bb4 Eb5 Ab4
        self.assertEqual(Key(3).alter_for("G"), 1)
        self.assertEqual(Key(-2).alter_for("E"), -1)

    def test_tpc_spelling(self):
        self.assertEqual(spell(60, 14), ("C", 0, 4))
        self.assertEqual(spell(70, 24), ("A", 1, 4))    # A#4
        self.assertEqual(spell(70, 12), ("B", -1, 4))   # Bb4
        self.assertEqual(spell(44, 22), ("G", 1, 2))    # G#2
        self.assertEqual(spell(60, 26), ("B", 1, 3))    # B#3 sounds as C4

    def test_infer_type(self):
        self.assertEqual(infer_type(Fraction(3, 2)), ("quarter", 1))
        self.assertEqual(infer_type(Fraction(1, 3), (3, 2)), ("eighth", 0))

    def test_parse_pitch(self):
        self.assertEqual(parse_pitch("C4"), 60)
        self.assertEqual(parse_pitch("F#3"), 54)
        self.assertEqual(parse_pitch("Bb2"), 46)


class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_demo()
        cls.pages, cls.score, cls.layout = render_pages(DEMO, Options())

    def test_max_three_pairs_per_page(self):
        per_page = {}
        for col in self.layout.columns:
            per_page[col.page] = per_page.get(col.page, 0) + 1
        self.assertTrue(all(n <= 3 for n in per_page.values()))
        self.assertEqual(len(self.pages), max(per_page) + 1)

    def test_pairs_option_is_capped(self):
        self.assertEqual(Options(pairs=7).pairs, 3)
        _, _, lay = render_pages(DEMO, Options(pairs=1))
        self.assertTrue(all(c.slot == 0 for c in lay.columns))

    def test_columns_read_left_to_right(self):
        for a, b in zip(self.layout.columns, self.layout.columns[1:]):
            if a.page == b.page:
                self.assertLess(a.x0, b.x0)
                self.assertGreaterEqual(b.x0, a.x1 - 1e-6)
            else:
                self.assertEqual(b.slot, 0)

    def test_left_hand_column_is_left_of_right_hand(self):
        for col in self.layout.columns:
            self.assertLess(col.lh_x0 + 4 * col.sp, col.rh_x0)
            self.assertGreaterEqual(col.lh_x0, col.x0)
            self.assertLessEqual(col.rh_x0 + 4 * col.sp, col.x1)

    def test_every_measure_once_in_order_and_inside_page(self):
        seen = [m.index for col in self.layout.columns for m in col.measures]
        self.assertEqual(seen, list(range(len(self.score.measures))))
        for col in self.layout.columns:
            y = col.y_top
            for m in col.measures:
                self.assertGreaterEqual(m.y_top, y - 1e-6)
                self.assertGreater(m.y_end, m.y_top)
                y = m.y_end
            self.assertLessEqual(col.end_y, col.y_bottom + 0.5)

    def test_hands_share_the_time_grid(self):
        """Simultaneous LH and RH events must be drawn at the same height."""
        checked = 0
        for m in self.score.measures:
            rh = {e.onset: e.geom["y"] for e in m.events["RH"] if e.geom and not e.grace and not e.measure_rest}
            for e in m.events["LH"]:
                if e.geom and not e.grace and not e.measure_rest and e.onset in rh:
                    self.assertAlmostEqual(e.geom["y"], rh[e.onset], places=6)
                    checked += 1
        self.assertGreater(checked, 10)

    def test_time_flows_downwards(self):
        for m in self.score.measures:
            for hand in ("LH", "RH"):
                ys = [(e.onset, e.geom["y"]) for e in m.events[hand]
                      if e.geom and not e.grace and not e.measure_rest]
                ys.sort()
                for (t0, y0), (t1, y1) in zip(ys, ys[1:]):
                    if t1 > t0:
                        self.assertGreater(y1, y0)

    def test_divider_after_every_pair(self):
        page = self.pages[0]
        W = page.width
        xs = sorted({round(op[1], 2) for op in page.ops
                     if op[0] == "line" and abs(op[1] - op[3]) < 1e-6 and op[4] - op[2] > 0.6 * page.height})
        pair_w = self.layout.pair_w
        expected = [round(22 + (k + 1) * pair_w, 2) for k in range(3)]
        for e in expected:
            self.assertTrue(any(abs(x - e) < 0.05 for x in xs), f"no divider at x={e} (found {xs})")
        self.assertLessEqual(max(expected), W)

    @staticmethod
    def dividers(page):
        """Divider lines: tall vertical grey lines (staff lines are black)."""
        return [op for op in page.ops if op[0] == "line" and abs(op[1] - op[3]) < 1e-6
                and op[4] - op[2] > 0.6 * page.height and op[6] != (0, 0, 0)]

    def test_divider_modes(self):
        pages, _, lay = render_pages(DEMO, Options(divider="after"))
        ncols = len([c for c in lay.columns if c.page == 0])
        self.assertEqual(len(self.dividers(pages[0])), ncols)
        pages, _, lay = render_pages(DEMO, Options(divider="between"))
        self.assertEqual(len(self.dividers(pages[0])), ncols - 1)
        pages, _, _ = render_pages(DEMO, Options(divider="none"))
        self.assertEqual(self.dividers(pages[0]), [])

    def test_upright_and_split_modes_render(self):
        pages, _, _ = render_pages(DEMO, Options(symbols="upright"))
        self.assertTrue(pages and pages[0].ops)
        pages, score, _ = render_pages(DEMO, Options(), rh="P1:1", split=60)
        self.assertTrue(score.split_mode)
        for m in score.measures:
            for e in m.events["LH"]:
                for n in e.notes:
                    self.assertLess(n.midi, 60)


class AnalysisTests(unittest.TestCase):
    def _render(self, xml):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.musicxml")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(xml)
            return render_pages(p, Options())

    def test_computed_accidentals_and_stems(self):
        _, score, _ = self._render(tiny_musicxml("F#5 F#5 F5 C4", "C3 E3 G3 C4"))
        rh = score.measures[0].events["RH"]
        self.assertEqual([e.notes[0].accidental for e in rh], ["sharp", None, "natural", None])
        self.assertEqual(rh[0].stem_dir, -1)     # high note -> stem down
        self.assertEqual(rh[3].stem_dir, 1)      # low note  -> stem up

    def test_empty_hand_gets_measure_rest(self):
        _, score, _ = self._render(tiny_musicxml("C5 D5 E5 F5", ""))
        lh = score.measures[0].events["LH"]
        self.assertEqual(len(lh), 1)
        self.assertTrue(lh[0].measure_rest)

    def test_long_piece_paginates(self):
        pages, score, lay = self._render(tiny_musicxml("C5 D5 E5 F5", "C3 D3 E3 F3", measures=120))
        self.assertGreater(len(pages), 2)
        self.assertEqual(sum(len(c.measures) for c in lay.columns), 120)

    def test_timewise_and_compressed_input(self):
        xml = tiny_musicxml("C5 D5 E5 F5", "C3 D3 E3 F3")
        with tempfile.TemporaryDirectory() as d:
            mxl = os.path.join(d, "t.mxl")
            with zipfile.ZipFile(mxl, "w") as z:
                z.writestr("META-INF/container.xml",
                           "<container><rootfiles><rootfile full-path='score.xml'/></rootfiles></container>")
                z.writestr("score.xml", xml)
            _, score, _ = render_pages(mxl, Options())
            self.assertEqual(len(score.measures[0].events["RH"]), 4)
            root = ET.fromstring(xml)
            tw = ET.Element("score-timewise")
            for c in root:
                if c.tag != "part":
                    tw.append(c)
            for m in root.find("part"):
                nm = ET.SubElement(tw, "measure", dict(m.attrib))
                p = ET.SubElement(nm, "part", {"id": "P1"})
                for c in m:
                    p.append(c)
            path = os.path.join(d, "tw.musicxml")
            ET.ElementTree(tw).write(path)
            _, score2, _ = render_pages(path, Options())
            self.assertEqual(len(score2.measures[0].events["LH"]), 4)


class MuseScoreTests(unittest.TestCase):
    def test_native_mscx(self):
        mscx = """<?xml version="1.0" encoding="UTF-8"?>
<museScore version="4.60"><Score><Division>480</Division>
<metaTag name="workTitle">Tiny</metaTag>
<Part id="1"><Staff><StaffType group="pitched"/></Staff><Staff><defaultClef>F</defaultClef></Staff>
<trackName>Piano</trackName></Part>
<Staff id="1"><Measure><voice><KeySig><concertKey>2</concertKey></KeySig>
<TimeSig><sigN>3</sigN><sigD>4</sigD></TimeSig>
<Dynamic><subtype>mf</subtype></Dynamic>
<Chord><durationType>quarter</durationType><Note><pitch>74</pitch><tpc>16</tpc></Note></Chord>
<Tuplet><normalNotes>2</normalNotes><actualNotes>3</actualNotes><baseNote>eighth</baseNote></Tuplet>
<Chord><durationType>eighth</durationType><Note><pitch>73</pitch><tpc>21</tpc></Note></Chord>
<Chord><durationType>eighth</durationType><Note><pitch>71</pitch><tpc>19</tpc></Note></Chord>
<Chord><durationType>eighth</durationType><Note><pitch>69</pitch><tpc>17</tpc></Note></Chord>
<endTuplet/>
<Chord><durationType>quarter</durationType><Note><pitch>67</pitch><tpc>15</tpc></Note></Chord>
</voice></Measure></Staff>
<Staff id="2"><Measure><voice>
<Chord><durationType>half</durationType><dots>1</dots><Note><pitch>50</pitch><tpc>16</tpc></Note></Chord>
</voice></Measure></Staff></Score></museScore>"""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "tiny.mscz")
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("tiny.mscx", mscx)
            pages, score, _ = render_pages(p, Options())
        m = score.measures[0]
        self.assertEqual(score.title, "Tiny")
        self.assertEqual(m.key.fifths, 2)
        self.assertEqual(m.duration, Fraction(3))
        rh = m.events["RH"]
        self.assertEqual([(e.notes[0].step, e.notes[0].octave) for e in rh],
                         [("D", 5), ("C", 5), ("B", 4), ("A", 4), ("G", 4)])
        self.assertEqual(rh[1].onset, Fraction(1))
        self.assertEqual(rh[4].onset, Fraction(2))
        self.assertEqual(rh[1].time_mod, (3, 2))
        self.assertEqual(m.start_clef["LH"].sign, "F")
        self.assertTrue(any(d.kind == "dynamics" and d.text == "mf" for d in m.directions))

    def test_musescore2_file(self):
        """MuseScore 1/2 layout: no <voice> blocks, absolute <tick> positions,
        tuplets and spanners referenced by id, <duration z n>, old names."""
        mscx = """<?xml version="1.0" encoding="UTF-8"?>
<museScore version="2.06"><programVersion>2.1.0</programVersion><Score><Division>480</Division>
<Part><Staff id="1"><StaffType group="pitched"><name>stdNormal</name></StaffType></Staff>
<Staff id="2"><defaultClef>F</defaultClef></Staff><trackName>Piano</trackName>
<Instrument><clef staff="2">F</clef></Instrument></Part>
<Staff id="1">
<VBox><Text><style>Title</style><text>Old Tiny</text></Text></VBox>
<Measure number="1">
<KeySig><accidental>-2</accidental></KeySig>
<TimeSig><sigN>3</sigN><sigD>4</sigD></TimeSig>
<Tempo><tempo>1.66667</tempo><text><sym>unicodeNoteQuarterUp</sym> = 100</text></Tempo>
<Tempo><tempo>2</tempo><visible>0</visible><text><sym>unicodeNoteQuarterUp</sym> = 120</text></Tempo>
<HairPin id="7"><subtype>0</subtype></HairPin>
<Chord><durationType>quarter</durationType><Articulation><subtype>staccato</subtype></Articulation>
<Note><pitch>74</pitch><tpc>16</tpc></Note></Chord>
<Tuplet id="1"><normalNotes>2</normalNotes><actualNotes>3</actualNotes><baseNote>eighth</baseNote></Tuplet>
<Chord><Tuplet>1</Tuplet><durationType>eighth</durationType>
<Note><Accidental><subtype>sharp</subtype></Accidental><pitch>73</pitch><tpc>21</tpc></Note></Chord>
<Chord><Tuplet>1</Tuplet><durationType>eighth</durationType><Note><pitch>71</pitch><tpc>19</tpc></Note></Chord>
<Chord><Tuplet>1</Tuplet><durationType>eighth</durationType><Note><pitch>69</pitch><tpc>17</tpc></Note></Chord>
<endSpanner id="7"/>
<Chord><durationType>quarter</durationType><Note><pitch>67</pitch><tpc>15</tpc><Tie id="3"/></Note></Chord>
</Measure>
<Measure number="2">
<tick>1920</tick>
<Dynamic><subtype>p</subtype></Dynamic>
<tick>1440</tick>
<Chord><durationType>half</durationType><dots>1</dots>
<Note><pitch>67</pitch><tpc>15</tpc><endSpanner id="3"/></Note></Chord>
</Measure>
</Staff>
<Staff id="2">
<Measure number="1">
<Rest><durationType>measure</durationType><duration z="3" n="4"/></Rest>
<Clef><concertClefType>G</concertClefType></Clef>
</Measure>
<Measure number="2">
<Chord><durationType>quarter</durationType><Note><pitch>62</pitch><tpc>16</tpc></Note></Chord>
<Rest><durationType>quarter</durationType><visible>0</visible></Rest>
<Chord><durationType>quarter</durationType><Note><pitch>67</pitch><tpc>15</tpc></Note></Chord>
</Measure>
</Staff></Score></museScore>"""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "old.mscz")
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("old.mscx", mscx)
            pages, score, _ = render_pages(p, Options())
        self.assertEqual(score.title, "Old Tiny")
        m1, m2 = score.measures
        self.assertEqual(m1.key.fifths, -2)
        self.assertEqual(m1.duration, Fraction(3))
        rh = m1.events["RH"]
        self.assertEqual([(e.notes[0].step, e.notes[0].octave) for e in rh],
                         [("D", 5), ("C", 5), ("B", 4), ("A", 4), ("G", 4)])
        self.assertEqual((rh[1].notes[0].alter, rh[1].notes[0].accidental), (1, "sharp"))
        self.assertEqual([e.onset for e in rh],
                         [0, 1, Fraction(4, 3), Fraction(5, 3), 2])
        self.assertEqual(rh[1].time_mod, (3, 2))
        self.assertEqual(rh[1].tuplets[0]["type"], "start")
        self.assertEqual(rh[3].tuplets[-1]["type"], "stop")
        self.assertEqual(rh[0].articulations, ["staccato"])
        self.assertTrue(rh[4].notes[0].tie_start)
        self.assertTrue(m2.events["RH"][0].notes[0].tie_stop)
        # visible tempo only, without the symbol name leaking into the text
        tempos = [d.text for m in score.measures for d in m.directions if d.kind == "metronome"]
        self.assertEqual(tempos, ["100"])
        self.assertFalse(any("unicode" in d.text for d in m1.directions))
        wedges = [(d.subtype, d.onset) for d in m1.directions if d.kind == "wedge"]
        self.assertEqual(wedges, [("crescendo", 0), ("stop", 2)])
        self.assertTrue(any(d.kind == "dynamics" and d.text == "p" and d.onset == 1 for d in m2.directions))
        # LH: whole-measure rest, a clef change at the barline, a hidden rest that still takes time
        self.assertTrue(m1.events["LH"][0].measure_rest)
        self.assertEqual(m2.start_clef["LH"].sign, "G")
        self.assertEqual([e.onset for e in m2.events["LH"]], [0, 2])


class OutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_demo()

    def test_pdf_svg_html(self):
        pages, score, _ = render_pages(DEMO, Options())
        with tempfile.TemporaryDirectory() as d:
            pdf = write_output(pages, os.path.join(d, "o.pdf"))[0]
            with open(pdf, "rb") as fh:
                data = fh.read()
            self.assertTrue(data.startswith(b"%PDF-1.4"))
            self.assertIn(b"%%EOF", data[-10:])
            self.assertEqual(data.count(b"/Type /Page "), len(pages))
            svgs = write_output(pages, os.path.join(d, "o.svg"))
            self.assertEqual(len(svgs), len(pages))
            for s in svgs:
                ET.parse(s)  # well-formed XML
            html = write_output(pages, os.path.join(d, "o.html"))[0]
            with open(html, encoding="utf-8") as fh:
                self.assertEqual(fh.read().count("<svg"), len(pages))
        self.assertIn("<svg", page_to_svg(pages[0]))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "cli.pdf")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cli_main([DEMO, "-o", out, "--pairs", "2", "--page", "letter"])
            self.assertEqual(code, 0)
            self.assertTrue(os.path.getsize(out) > 1000)
            self.assertIn("column pairs", buf.getvalue())

    def test_cli_errors(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "bad.musicxml")
            with open(bad, "w") as fh:
                fh.write("<not-music/>")
            proc = subprocess.run([sys.executable, "-m", "vertsheet", bad], cwd=ROOT,
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("not a MusicXML score", proc.stderr)


if __name__ == "__main__":
    unittest.main()
