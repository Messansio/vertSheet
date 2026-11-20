"""Optional conversion of non-MusicXML inputs through external programs.

* MuseScore (3 or 4) converts .mscz/.mscx/.mid/.midi/.kar/.gp/.cap... to MusicXML
* Audiveris (optical music recognition) converts scanned .pdf/.png/.jpg/.tif

Neither program is required for MusicXML input.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
from typing import List, Optional

MUSICXML_EXT = (".musicxml", ".xml", ".mxl")
NATIVE_EXT = (".mscz", ".mscx")   # read directly by vertsheet.mscx, no MuseScore needed
MUSESCORE_EXT = (".mid", ".midi", ".kar", ".gp", ".gpx", ".gp5", ".gp4",
                 ".cap", ".capx", ".ove", ".bww", ".md", ".mgu", ".sgu")
OMR_EXT = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")


class ConversionError(Exception):
    pass


def _candidates_musescore() -> List[str]:
    names = ["MuseScore4", "MuseScore3", "mscore4portable", "mscore", "musescore", "mscore3", "mscore4"]
    found = [shutil.which(n) for n in names]
    pf = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")]
    for base in pf:
        if base:
            found += glob.glob(os.path.join(base, "MuseScore*", "bin", "MuseScore*.exe"))
    found += ["/Applications/MuseScore 4.app/Contents/MacOS/mscore",
              "/Applications/MuseScore 3.app/Contents/MacOS/mscore"]
    return [f for f in found if f and os.path.isfile(f)]


def _candidates_audiveris() -> List[str]:
    found = [shutil.which("Audiveris"), shutil.which("audiveris")]
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),):
        found += glob.glob(os.path.join(base, "Audiveris", "bin", "Audiveris*"))
        found += glob.glob(os.path.join(base, "Audiveris", "Audiveris.exe"))
    found += ["/Applications/Audiveris.app/Contents/MacOS/Audiveris", "/opt/audiveris/bin/Audiveris"]
    return [f for f in found if f and os.path.isfile(f)]


def to_musicxml(path: str, verbose: bool = False) -> str:
    """Return a path to a MusicXML version of ``path`` (converting if needed)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in MUSICXML_EXT or ext in NATIVE_EXT:
        return path
    tmp = tempfile.mkdtemp(prefix="vertsheet_")
    if ext in MUSESCORE_EXT:
        exes = _candidates_musescore()
        if not exes:
            raise ConversionError(
                f"'{ext}' files need MuseScore to be converted to MusicXML, but MuseScore was not found.\n"
                "Install MuseScore (https://musescore.org) or export the score as MusicXML yourself "
                "(File > Export > MusicXML).")
        out = os.path.join(tmp, "score.musicxml")
        cmd = [exes[0], "-o", out, path]
        _run(cmd, verbose)
        if not os.path.isfile(out):
            raise ConversionError("MuseScore did not produce a MusicXML file")
        return out
    if ext in OMR_EXT:
        exes = _candidates_audiveris()
        if not exes:
            raise ConversionError(
                f"'{ext}' files are images of sheet music: they need optical music recognition.\n"
                "Install Audiveris (https://audiveris.github.io) so it can be run automatically, or "
                "convert the file to MusicXML with MuseScore / Audiveris / PlayScore / SmartScore first.")
        cmd = [exes[0], "-batch", "-export", "-output", tmp, path]
        _run(cmd, verbose, timeout=1800)
        results = glob.glob(os.path.join(tmp, "**", "*.mxl"), recursive=True) + \
            glob.glob(os.path.join(tmp, "**", "*.musicxml"), recursive=True)
        if not results:
            raise ConversionError("Audiveris could not recognise any music in the file")
        return sorted(results)[0]
    raise ConversionError(f"unsupported input format '{ext}'")


def _run(cmd: List[str], verbose: bool, timeout: Optional[int] = 600) -> None:
    if verbose:
        print("running:", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ConversionError(f"{os.path.basename(cmd[0])} timed out") from None
    except OSError as exc:
        raise ConversionError(f"could not run {cmd[0]}: {exc}") from None
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        raise ConversionError(f"{os.path.basename(cmd[0])} failed:\n" + "\n".join(msg))
