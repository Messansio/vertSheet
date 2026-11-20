"""Entry point of the installed ``vertSheet.exe``.

    vertSheet.exe                       open the window
    vertSheet.exe SCORE                 open the window with a score ("Open with")
    vertSheet.exe --uninstall [--quiet] remove the installation (Settings > Apps)
"""
from __future__ import annotations

import os
import sys
from typing import List, Optional


def _log_crash() -> None:
    """A windowed program has no console: unexpected errors go to
    %TEMP%\\vertsheet-error.log."""
    import datetime
    import tempfile
    import traceback
    try:
        with open(os.path.join(tempfile.gettempdir(), "vertsheet-error.log"), "a", encoding="utf-8") as fh:
            fh.write(f"--- {datetime.datetime.now().isoformat(timespec='seconds')} {sys.argv}\n")
            fh.write(traceback.format_exc())
    except Exception:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv and argv[0].lower() in ("--uninstall", "/uninstall"):
            from .winsetup import run_uninstaller
            return run_uninstaller(argv[1:])
        from .gui import main as gui_main
        return gui_main([a for a in argv if not a.startswith("-psn_")])
    except SystemExit:
        raise
    except BaseException:
        _log_crash()
        raise


if __name__ == "__main__":
    sys.exit(main())
