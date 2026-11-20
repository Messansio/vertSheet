"""vertSheet - re-engrave piano sheet music to be read vertically.

Time runs from the top of the page to the bottom. Every page holds up to
three *pairs* of columns; each pair is one system made of a left-hand staff
(left column) and a right-hand staff (right column) rotated so that pitch
rises to the right. Pairs are read from left to right and are separated by
divider lines.

Typical use::

    from vertsheet import render
    render("song.musicxml", "song_vertical.pdf")
"""
from __future__ import annotations

import os
from typing import List, Optional

from .analyze import Options, analyze
from .canvas import Page, PDFWriter, page_to_svg, pages_to_html
from .engrave import Engraver
from .layout import Layout
from .musicxml import load_score

__all__ = ["render", "render_pages", "Options", "PAGE_SIZES"]
__version__ = "1.0.0"

PAGE_SIZES = {
    "a4": (595.28, 841.89), "a3": (841.89, 1190.55), "a5": (419.53, 595.28),
    "letter": (612.0, 792.0), "legal": (612.0, 1008.0), "tabloid": (792.0, 1224.0),
}


def render_pages(path: str, opts: Optional[Options] = None, rh: Optional[str] = None,
                 lh: Optional[str] = None, split: Optional[int] = None):
    """Parse, analyse, lay out and engrave ``path``. Returns (pages, score, layout)."""
    opts = opts or Options()
    score = load_score(path, rh=rh, lh=lh, split=split)
    if not score.measures:
        raise ValueError("the score contains no measures")
    analyzer = analyze(score, opts)
    layout = Layout(score, opts)
    title = opts.title if opts.title is not None else score.title
    composer = opts.composer if opts.composer is not None else score.composer
    title_h = (24 if title else 0) + (14 if score.subtitle else 0) + (12 if composer else 0)
    title_h += 6 if title_h else 0
    layout.run(title_h)
    pages = Engraver(score, opts, layout, analyzer).run()
    return pages, score, layout


def write_output(pages: List[Page], out: str, title: str = "", author: str = "") -> List[str]:
    ext = os.path.splitext(out)[1].lower()
    written = []
    if ext == ".pdf":
        w = PDFWriter(title, author)
        for p in pages:
            w.add(p)
        w.save(out)
        written.append(out)
    elif ext == ".svg":
        base = out[:-4]
        for i, p in enumerate(pages, 1):
            name = out if len(pages) == 1 else f"{base}-{i:03d}.svg"
            with open(name, "w", encoding="utf-8") as fh:
                fh.write(page_to_svg(p))
            written.append(name)
    elif ext in (".html", ".htm"):
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(pages_to_html(pages, title))
        written.append(out)
    else:
        raise ValueError(f"unknown output format '{ext}' (use .pdf, .svg or .html)")
    return written


def render(path: str, out: str, opts: Optional[Options] = None, **kw) -> List[str]:
    pages, score, _ = render_pages(path, opts, **kw)
    o = opts or Options()
    title = o.title if o.title is not None else score.title
    return write_output(pages, out, title, score.composer)
