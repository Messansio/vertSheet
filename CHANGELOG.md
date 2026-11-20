# Changelog

## 1.0.0 — first release

**vertSheet re-engraves piano sheet music to be read vertically**: time runs
from the top of the page to the bottom, each page holds up to 3 pairs of
columns (left hand | right hand) read from left to right, with a divider line
after every pair.

### Windows program
- Window with a settings sidebar and a live preview of the vertical pages
  (page navigation, zoom, fit page / fit width).
- Layout options: column pairs per page, page size and orientation, divider
  lines, rotated or upright symbols, measure numbers, note spacing, staff size,
  hand labels, title / composer override, splitting a single staff between the
  hands.
- Export to PDF, SVG (one file per page) or HTML.
- Italian and English interface (detected automatically); the engraved hand
  labels follow the language (M.S./M.D. or L.H./R.H.).
- Remembers settings and recent files; opens scores passed by "Open with".

### Installation
- `vertSheet-Setup-1.0.0.exe`: installation wizard, per user, no administrator
  rights; optional desktop / Start menu shortcuts, "Open with" for .mscz,
  .mscx, .musicxml and .mxl (default apps are not changed) and
  `vertsheet-cli` on the PATH; upgrades in place; uninstall from
  Settings ▸ Apps, which removes exactly what Setup created.
- `vertSheet-1.0.0-portable.zip`: the same program without installation.

### Engraving engine (pure Python, no dependencies)
- Reads MusicXML (`.musicxml`, `.xml`, `.mxl`, partwise and timewise) and
  MuseScore 1, 2, 3 and 4 files (`.mscz`, `.mscx`) natively; MIDI and other
  formats through MuseScore, scanned PDFs / images through Audiveris when
  those programs are installed.
- Clefs and clef changes, key and time signatures, chords, seconds, ledger
  lines, accidentals, dots, stems, flags, beams with secondary beams, rests,
  multiple voices, grace notes, tuplets, ties and slurs (also across columns),
  articulations, fermatas, ornaments, arpeggios, fingerings, dynamics,
  hairpins, text, tempo marks, rehearsal marks, pedal lines, 8va/8vb/15ma
  lines, repeats, voltas, double and final barlines.
- Duration-based spacing with a shared left/right-hand time grid, column
  breaking and justification, collision-free placement of texts and lines.
- Its own vector music symbols and PDF / SVG writers.
- Command line: `vertsheet-cli` (installed) or `python -m vertsheet`.
