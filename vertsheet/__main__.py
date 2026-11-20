"""Command line interface:  python -m vertsheet INPUT [-o OUTPUT] [options]"""
from __future__ import annotations

import argparse
import os
import re
import sys

from . import PAGE_SIZES, __version__, render_pages, write_output
from .analyze import Options
from .convert import ConversionError, to_musicxml
from .model import STEP_SEMITONES
from .musicxml import MusicXMLError


def parse_pitch(text: str) -> int:
    """'C4' -> 60, 'F#3' -> 54, 'Bb2' -> 46, or a plain MIDI number."""
    if text.isdigit():
        return int(text)
    m = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d)", text.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"invalid pitch '{text}' (examples: C4, F#3, Bb2, 60)")
    step, acc, octv = m.groups()
    return 12 * (int(octv) + 1) + STEP_SEMITONES[step.upper()] + {"#": 1, "b": -1, "": 0}[acc]


def page_size(text: str):
    t = text.lower()
    landscape = t.endswith("-landscape") or t.endswith("-l")
    t = t.replace("-landscape", "").replace("-l", "")
    if t in PAGE_SIZES:
        w, h = PAGE_SIZES[t]
    else:
        m = re.fullmatch(r"(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(mm|in|pt)?", t)
        if not m:
            raise argparse.ArgumentTypeError(f"unknown page size '{text}'")
        unit = {"mm": 72 / 25.4, "in": 72.0, "pt": 1.0, None: 72 / 25.4}[m.group(3)]
        w, h = float(m.group(1)) * unit, float(m.group(2)) * unit
    return (h, w) if landscape else (w, h)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vertsheet",
        description="Re-engrave piano sheet music so it is read VERTICALLY: time runs top-to-bottom, "
                    "each page holds up to 3 pairs of columns (left hand | right hand) read from left "
                    "to right, with a divider line after every pair.")
    p.add_argument("input", help="MusicXML file (.musicxml, .xml, .mxl). With MuseScore installed also "
                                 ".mscz/.mid; with Audiveris also scanned .pdf/.png/.jpg")
    p.add_argument("-o", "--output", help="output file: .pdf (default), .svg (one file per page) or .html")
    p.add_argument("--format", choices=["pdf", "svg", "html", "all"],
                   help="output format when -o is not given (default pdf)")
    g = p.add_argument_group("page layout")
    g.add_argument("--pairs", type=int, default=3, choices=[1, 2, 3],
                   help="pairs of columns (LH+RH) per page, maximum 3 (default 3)")
    g.add_argument("--page", type=page_size, default=PAGE_SIZES["a4"],
                   help="page size: a4, a3, a5, letter, legal, tabloid, WxH[mm|in|pt]; "
                        "add -landscape for landscape (default a4)")
    g.add_argument("--staff-space", type=float, default=None,
                   help="distance between staff lines in points (default: as large as the pair "
                        "width allows, at most 7 pt)")
    g.add_argument("--spacing", type=float, default=1.0,
                   help="horizontal->vertical spacing factor; >1 gives more room per note (default 1.0)")
    g.add_argument("--margin", type=float, default=None, help="side margin in points (default 22)")
    g.add_argument("--divider", choices=["after", "between", "none"], default="after",
                   help="divider line after every pair (default), only between pairs, or none")
    g.add_argument("--symbols", choices=["rotated", "upright"], default="rotated",
                   help="draw clefs, rests, accidentals and texts rotated with the staff (default) or upright")
    g.add_argument("--measure-numbers", choices=["all", "system", "none"], default="all")
    g.add_argument("--no-labels", action="store_true", help="hide the L.H. / R.H. column labels")
    g.add_argument("--justify-last", action="store_true", help="stretch the last column to the page bottom")
    h = p.add_argument_group("hands")
    h.add_argument("--rh", help="source of the right hand as PART[:STAFF], e.g. P1:1 or 2 (part number)")
    h.add_argument("--lh", help="source of the left hand as PART[:STAFF], e.g. P1:2")
    h.add_argument("--split", type=parse_pitch,
                   help="split a single-staff part between the hands at this pitch (e.g. C4): "
                        "notes >= pitch go to the right hand")
    t = p.add_argument_group("titles")
    t.add_argument("--title", help="override the title")
    t.add_argument("--composer", help="override the composer")
    p.add_argument("--debug", action="store_true", help="draw collision boxes")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--version", action="version", version=f"vertsheet {__version__}")
    return p


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv:                      # no arguments: open the graphical interface
        from .gui import main as gui_main
        return gui_main([])
    args = build_parser().parse_args(argv)
    if not os.path.isfile(args.input):
        print(f"error: file not found: {args.input}", file=sys.stderr)
        return 2
    opts = Options(page_size=args.page, pairs=args.pairs, staff_space=args.staff_space,
                   symbols="rot" if args.symbols == "rotated" else "upright", divider=args.divider,
                   measure_numbers=args.measure_numbers, labels=not args.no_labels,
                   title=args.title, composer=args.composer, justify_last=args.justify_last,
                   spacing=args.spacing, debug=args.debug)
    if args.margin is not None:
        opts.margin_side = args.margin
    try:
        src = to_musicxml(args.input, verbose=not args.quiet)
        pages, score, layout = render_pages(src, opts, rh=args.rh, lh=args.lh, split=args.split)
    except (MusicXMLError, ConversionError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    stem = os.path.splitext(args.input)[0] + "_vertical"
    if args.output:
        outs = [args.output]
    elif args.format == "all":
        outs = [stem + ".pdf", stem + ".html", stem + ".svg"]
    else:
        outs = [stem + "." + (args.format or "pdf")]
    title = opts.title if opts.title is not None else score.title
    written = []
    for out in outs:
        try:
            written += write_output(pages, out, title, score.composer)
        except (ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    if not args.quiet:
        per_col = [len(c.measures) for c in layout.columns]
        print(f"{score.title or os.path.basename(args.input)}: {len(score.measures)} measures, "
              f"{len(layout.columns)} column pairs on {len(pages)} page(s) "
              f"(measures per pair: {', '.join(map(str, per_col))})")
        print(f"hands: {score.source_description}; staff space {layout.sp:.2f} pt")
        for w in written:
            print("wrote", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
