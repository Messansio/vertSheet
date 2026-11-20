"""vertSheet graphical interface (Tkinter - no third-party packages).

Start it with ``python -m vertsheet`` (without arguments), with the
``vertsheet-gui`` command, or with the installed ``vertSheet.exe``. A score
path given on the command line (e.g. by "Open with") is opened directly.

The window has a settings sidebar on the left and a live preview of the
vertical pages on the right. Layout runs in a background thread, so the
window stays responsive while long scores are engraved.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from tkinter import filedialog, messagebox, ttk
from typing import Callable, List, Optional, Tuple

from . import PAGE_SIZES, __version__, render_pages, write_output
from .analyze import Options
from .convert import (MUSESCORE_EXT, OMR_EXT, ConversionError, _candidates_audiveris,
                      _candidates_musescore, to_musicxml)
from .i18n import LANGUAGES, detect_language, get_language, set_language, tr
from .musicxml import MusicXMLError
from .preview import draw_page, fit_scale

APP = "vertSheet"
AUDIVERIS_URL = "https://audiveris.github.io/audiveris/"
PAGES = ["a4", "a3", "a5", "letter", "legal", "tabloid"]
PAGE_LABEL = {"a4": "A4", "a3": "A3", "a5": "A5", "letter": "Letter", "legal": "Legal",
              "tabloid": "Tabloid"}
FORMATS = [("pdf", ".pdf"), ("svg", ".svg"), ("html", ".html")]
ZOOM_STEPS = [0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0]
ACCENT = "#2f5aa8"
VIEW_BG = "#6b6f78"
MAX_RECENT = 8


# ---------------------------------------------------------------------- utils

def resource_path(*parts: str) -> str:
    """Path of a bundled resource (works from source and from the frozen exe)."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def config_path() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP, "settings.json")


DEFAULTS = {
    "language": "", "recent": [], "last_dir": "", "export_dir": "", "geometry": "",
    "pairs": 3, "page": "a4", "landscape": False, "divider": "after", "symbols": "rot",
    "measure_numbers": "all", "labels": True, "justify_last": False, "spacing": 1.0,
    "staff_auto": True, "staff_space": 6.0, "hands": "auto", "split": "C4", "format": "pdf",
}
CHOICES = {"page": PAGES, "divider": ["after", "between", "none"], "symbols": ["rot", "upright"],
           "measure_numbers": ["all", "system", "none"], "hands": ["auto", "split"],
           "format": [f for f, _ in FORMATS]}


def load_settings() -> dict:
    data = json.loads(json.dumps(DEFAULTS))
    try:
        with open(config_path(), encoding="utf-8") as fh:
            stored = json.load(fh)
    except (OSError, ValueError):
        return data
    if not isinstance(stored, dict):
        return data
    for key, default in DEFAULTS.items():
        v = stored.get(key)
        if v is None:
            continue
        if isinstance(default, bool):
            ok = isinstance(v, bool)
        elif isinstance(default, float):
            ok = isinstance(v, (int, float)) and not isinstance(v, bool)
        elif isinstance(default, int):
            ok = isinstance(v, int) and not isinstance(v, bool)
        elif isinstance(default, list):
            ok = isinstance(v, list) and all(isinstance(x, str) for x in v)
        else:
            ok = isinstance(v, str)
        if ok and key in CHOICES and v not in CHOICES[key]:
            ok = False
        if ok:
            data[key] = v
    data["pairs"] = max(1, min(3, data["pairs"]))
    data["spacing"] = max(0.6, min(2.0, float(data["spacing"])))
    data["staff_space"] = max(3.0, min(9.0, float(data["staff_space"])))
    return data


def save_settings(data: dict) -> None:
    path = config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


def parse_pitch(text: str) -> int:
    from .__main__ import parse_pitch as _pp
    try:
        return _pp(text.strip())
    except Exception:
        raise ValueError(tr("err.pitch", text=text)) from None


def _dpi_aware() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def open_path(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def show_in_folder(path: str) -> None:
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        open_path(os.path.dirname(path))


# ---------------------------------------------------------------------- widgets

class ScrollFrame(ttk.Frame):
    """A vertically scrollable frame (``.inner`` holds the content)."""

    def __init__(self, parent, width: int):
        super().__init__(parent)
        bg = ttk.Style().lookup("TFrame", "background") or "#f0f0f0"
        self.canvas = tk.Canvas(self, width=width, highlightthickness=0, borderwidth=0,
                                background=bg, yscrollincrement=16)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, padding=(14, 8, 12, 16))
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.inner.bind("<Configure>", self._sync)
        self.canvas.bind("<Configure>", self._sync)

    def _sync(self, _e=None) -> None:
        self.canvas.itemconfigure(self._win, width=self.canvas.winfo_width())
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(),
                                            self.inner.winfo_reqheight()))

    def scroll(self, units: int) -> None:
        if self.inner.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(units, "units")


class Choice:
    """Read-only combobox that shows translated labels for a variable of values."""

    def __init__(self, parent, var: tk.Variable, options: List[Tuple[str, str]], width: int = 26):
        self.var = var
        self.options = options
        self.display = tk.StringVar(master=parent)
        self.box = ttk.Combobox(parent, textvariable=self.display, state="readonly", width=width,
                                values=[label for _, label in options])
        self.box.bind("<<ComboboxSelected>>", self._picked)
        self._trace = var.trace_add("write", self._sync)
        self._sync()

    def _picked(self, _e=None) -> None:
        i = self.box.current()
        if i >= 0:
            self.var.set(self.options[i][0])
        self.box.selection_clear()

    def _sync(self, *_a) -> None:
        for value, label in self.options:
            if value == self.var.get():
                self.display.set(label)
                return

    def release(self) -> None:
        try:
            self.var.trace_remove("write", self._trace)
        except tk.TclError:
            pass


# ---------------------------------------------------------------------- the app

class App:
    def __init__(self, root: tk.Tk, initial: Optional[str] = None, settings: Optional[dict] = None,
                 persist: bool = True):
        self.root = root
        self.persist = persist
        self.settings = settings if settings is not None else load_settings()
        set_language(self.settings.get("language") or detect_language())
        self.px_per_pt = root.winfo_fpixels("1i") / 72.0
        self.ui_scale = root.winfo_fpixels("1i") / 96.0
        # state
        self.input_path: Optional[str] = None
        self.pages: list = []
        self.score = None
        self.layout = None
        self.render_key = None
        self.error: Optional[str] = None
        self.page_index = 0
        self.zoom_mode = "page"          # "page", "width" or "custom"
        self.zoom = 1.0
        self.scale = 1.0
        self.busy = False
        self.last_output: List[str] = []
        self._job = 0
        self._running_key = None
        self._results: "queue.Queue" = queue.Queue()
        self._pending = None
        self._redraw_pending = None
        self._after_render: Optional[Callable[[], None]] = None
        self._explicit = False
        self._edge_hits = 0
        self._quiet = False
        self._choices: List[Choice] = []
        self._welcome_widgets: list = []
        self._images = {}
        self._make_vars()
        self._setup_window()
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self._alive = True
        self._poll_id = root.after(80, self._poll)
        if initial:
            root.after(50, lambda: self.open_file(initial))

    # ------------------------------------------------------------ variables
    def _make_vars(self) -> None:
        s, r = self.settings, self.root
        self.v = {
            "pairs": tk.IntVar(r, s["pairs"]),
            "page": tk.StringVar(r, s["page"]),
            "landscape": tk.BooleanVar(r, s["landscape"]),
            "divider": tk.StringVar(r, s["divider"]),
            "symbols": tk.StringVar(r, s["symbols"]),
            "measure_numbers": tk.StringVar(r, s["measure_numbers"]),
            "labels": tk.BooleanVar(r, s["labels"]),
            "justify_last": tk.BooleanVar(r, s["justify_last"]),
            "spacing": tk.DoubleVar(r, s["spacing"]),
            "staff_auto": tk.BooleanVar(r, s["staff_auto"]),
            "staff_space": tk.DoubleVar(r, s["staff_space"]),
            "hands": tk.StringVar(r, s["hands"]),
            "split": tk.StringVar(r, s["split"]),
            "title": tk.StringVar(r, ""),
            "composer": tk.StringVar(r, ""),
            "format": tk.StringVar(r, s["format"]),
        }
        for name, var in self.v.items():
            if name != "format":
                delay = 650 if name in ("title", "composer", "split") else 300
                var.trace_add("write", lambda *_a, d=delay: self._option_changed(d))
        self.v["staff_auto"].trace_add("write", lambda *_a: self._sync_states())
        self.v["hands"].trace_add("write", lambda *_a: self._sync_states())
        self.v["spacing"].trace_add("write", lambda *_a: self._sync_spacing_label())

    def _option_values(self) -> tuple:
        vals = []
        for name, var in self.v.items():
            if name == "format":
                continue
            try:
                val = var.get()
            except tk.TclError:
                val = None
            if name == "spacing" and val is not None:
                val = round(float(val), 2)
            vals.append((name, val))
        vals.append(("lang", get_language()))
        return tuple(vals)

    def _current_key(self):
        if not self.input_path:
            return None
        try:
            mtime = os.path.getmtime(self.input_path)
        except OSError:
            mtime = 0
        return (self.input_path, mtime, self._option_values())

    def build_options(self) -> Tuple[Options, Optional[int]]:
        g = {k: v.get() for k, v in self.v.items()}
        w, h = PAGE_SIZES.get(g["page"], PAGE_SIZES["a4"])
        if g["landscape"]:
            w, h = h, w
        opts = Options(page_size=(w, h), pairs=int(g["pairs"]),
                       staff_space=None if g["staff_auto"] else float(g["staff_space"]),
                       symbols=g["symbols"], divider=g["divider"],
                       measure_numbers=g["measure_numbers"], labels=bool(g["labels"]),
                       hand_labels=(tr("hand.lh"), tr("hand.rh")),
                       title=g["title"].strip() or None, composer=g["composer"].strip() or None,
                       justify_last=bool(g["justify_last"]), spacing=round(float(g["spacing"]), 2))
        split = parse_pitch(g["split"]) if g["hands"] == "split" else None
        return opts, split

    # ------------------------------------------------------------ window
    def _setup_window(self) -> None:
        r = self.root
        r.title(APP)
        r.minsize(int(880 * self.ui_scale), int(560 * self.ui_scale))
        geo = self.settings.get("geometry") or ""
        try:
            r.geometry(geo or f"{int(1220 * self.ui_scale)}x{int(800 * self.ui_scale)}")
        except tk.TclError:
            pass
        ico = resource_path("assets", "vertsheet.ico")
        if sys.platform == "win32" and os.path.isfile(ico):
            try:
                r.iconbitmap(default=ico)
            except tk.TclError:
                pass
        for size in (32, 64, 128):
            p = resource_path("assets", f"vertsheet-{size}.png")
            if os.path.isfile(p):
                try:
                    self._images[size] = tk.PhotoImage(master=r, file=p)
                except tk.TclError:
                    pass
        if sys.platform != "win32" and 64 in self._images:
            r.iconphoto(True, self._images[64])
        base = tkfont.nametofont("TkDefaultFont")
        fam = base.actual("family")
        size = base.actual("size")
        self.f_title = tkfont.Font(r, family=fam, size=size + 7, weight="bold")
        self.f_bold = tkfont.Font(r, family=fam, size=size, weight="bold")
        self.f_small = tkfont.Font(r, family=fam, size=max(7, size - 1))
        self.f_big = tkfont.Font(r, family=fam, size=size + 6, weight="bold")
        self.f_body = tkfont.Font(r, family=fam, size=size + 2)
        st = ttk.Style(r)
        if sys.platform == "win32" and "vista" in st.theme_names():
            st.theme_use("vista")
        st.configure("Title.TLabel", font=self.f_title, foreground=ACCENT)
        st.configure("Tag.TLabel", foreground="#5a5f6b")
        st.configure("Hint.TLabel", foreground="#6b7080", font=self.f_small)
        st.configure("Name.TLabel", font=self.f_bold)
        st.configure("Section.TLabelframe.Label", font=self.f_bold, foreground=ACCENT)
        st.configure("Section.TLabelframe", padding=(10, 6, 10, 10))
        st.configure("Primary.TButton", font=self.f_bold, padding=(10, 7))
        st.configure("Status.TLabel", padding=(10, 3))
        st.configure("Saved.TLabel", foreground="#1d7a38")
        for cls in ("TCombobox", "TSpinbox", "TScale"):
            r.bind_class(cls, "<MouseWheel>", self._on_wheel)
        r.bind_all("<MouseWheel>", self._on_wheel)
        keys = {"<Control-o>": self.open_dialog, "<Control-O>": self.open_dialog,
                "<Control-s>": self.export, "<Control-S>": self.export,
                "<F5>": lambda: self.request_render(0, force=True),
                "<Prior>": self.prev_page, "<Next>": self.next_page,
                "<Control-plus>": lambda: self.zoom_by(1), "<Control-equal>": lambda: self.zoom_by(1),
                "<Control-minus>": lambda: self.zoom_by(-1), "<Control-0>": lambda: self.set_zoom("page"),
                "<Control-q>": self.quit}
        for seq, fn in keys.items():
            r.bind(seq, lambda e, f=fn: (f(), "break")[1])

    # ------------------------------------------------------------ build / rebuild
    def _build(self) -> None:
        r = self.root
        self._build_menu()
        self.main = ttk.Frame(r)
        self.main.grid(row=0, column=0, sticky="nsew")
        r.rowconfigure(0, weight=1)
        r.columnconfigure(0, weight=1)
        self.sidebar = ScrollFrame(self.main, width=int(318 * self.ui_scale))
        self.sidebar.grid(row=0, column=0, sticky="ns")
        ttk.Separator(self.main, orient="vertical").grid(row=0, column=1, sticky="ns")
        view = ttk.Frame(self.main)
        view.grid(row=0, column=2, sticky="nsew")
        self.main.rowconfigure(0, weight=1)
        self.main.columnconfigure(2, weight=1)
        self._build_sidebar(self.sidebar.inner)
        self._build_view(view)
        self._build_status()
        self._sync_states()
        self._sync_spacing_label()
        self._update_info()
        self._update_saved()
        self.request_redraw(10)

    def _rebuild(self) -> None:
        for c in self._choices:
            c.release()
        self._choices = []
        old_menu = self.menubar
        for w in (self.main, self.statusbar):
            w.destroy()
        self._welcome_widgets = []
        self._build()
        old_menu.destroy()
        self._update_status_after_rebuild()

    def _build_menu(self) -> None:
        m = tk.Menu(self.root)
        f = tk.Menu(m, tearoff=False)
        f.add_command(label=tr("menu.open"), accelerator="Ctrl+O", command=self.open_dialog)
        self.recent_menu = tk.Menu(f, tearoff=False)
        f.add_cascade(label=tr("menu.recent"), menu=self.recent_menu)
        f.add_separator()
        f.add_command(label=tr("menu.export"), accelerator="Ctrl+S", command=self.export)
        f.add_separator()
        f.add_command(label=tr("menu.quit"), accelerator="Ctrl+Q", command=self.quit)
        m.add_cascade(label=tr("menu.file"), menu=f)
        v = tk.Menu(m, tearoff=False)
        v.add_command(label=tr("menu.prev"), accelerator="PgUp", command=self.prev_page)
        v.add_command(label=tr("menu.next"), accelerator="PgDn", command=self.next_page)
        v.add_separator()
        v.add_command(label=tr("menu.zoom_in"), accelerator="Ctrl++", command=lambda: self.zoom_by(1))
        v.add_command(label=tr("menu.zoom_out"), accelerator="Ctrl+-", command=lambda: self.zoom_by(-1))
        v.add_command(label=tr("menu.fit_page"), accelerator="Ctrl+0", command=lambda: self.set_zoom("page"))
        v.add_command(label=tr("menu.fit_width"), command=lambda: self.set_zoom("width"))
        v.add_command(label=tr("menu.actual"), command=lambda: self.set_zoom(1.0))
        v.add_separator()
        v.add_command(label=tr("menu.refresh"), accelerator="F5",
                      command=lambda: self.request_render(0, force=True))
        m.add_cascade(label=tr("menu.view"), menu=v)
        lang = tk.Menu(m, tearoff=False)
        self._lang_var = tk.StringVar(self.root, get_language())
        for code, name in LANGUAGES.items():
            lang.add_radiobutton(label=name, value=code, variable=self._lang_var,
                                 command=lambda c=code: self.set_ui_language(c))
        m.add_cascade(label=tr("menu.language"), menu=lang)
        h = tk.Menu(m, tearoff=False)
        h.add_command(label=tr("menu.howto"), command=lambda: self._info(tr("menu.howto"), tr("help.howto.body")))
        h.add_command(label=tr("menu.formats"),
                      command=lambda: self._info(tr("menu.formats"), tr("help.formats.body")))
        h.add_separator()
        h.add_command(label=tr("menu.about"), command=self.about)
        m.add_cascade(label=tr("menu.help"), menu=h)
        self.root.config(menu=m)
        self.menubar = m
        self._fill_recent_menu()

    def _fill_recent_menu(self) -> None:
        rm = self.recent_menu
        rm.delete(0, "end")
        recent = [p for p in self.settings.get("recent", []) if os.path.isfile(p)]
        if not recent:
            rm.add_command(label=tr("menu.recent.empty"), state="disabled")
            return
        for i, p in enumerate(recent, 1):
            rm.add_command(label=f"{i}  {os.path.basename(p)}", command=lambda q=p: self.open_file(q))
        rm.add_separator()
        rm.add_command(label=tr("menu.recent.clear"), command=self._clear_recent)

    def _clear_recent(self) -> None:
        self.settings["recent"] = []
        self._fill_recent_menu()
        self.request_redraw(0)

    def _section(self, parent, key: str, row: int) -> ttk.Labelframe:
        lf = ttk.Labelframe(parent, text=tr(key), style="Section.TLabelframe")
        lf.grid(row=row, column=0, sticky="ew", pady=(10, 0))
        lf.columnconfigure(0, weight=1)
        return lf

    def _choice(self, parent, var, options, row, label_key=None) -> None:
        if label_key:
            ttk.Label(parent, text=tr(label_key)).grid(row=row, column=0, sticky="w", pady=(6, 1))
            row += 1
        c = Choice(parent, var, options)
        c.box.grid(row=row, column=0, sticky="ew")
        self._choices.append(c)

    def _build_sidebar(self, p) -> None:
        p.columnconfigure(0, weight=1)
        head = ttk.Frame(p)
        head.grid(row=0, column=0, sticky="ew")
        if 64 in self._images:
            ttk.Label(head, image=self._images[64]).grid(row=0, column=0, rowspan=2, padx=(0, 10))
        ttk.Label(head, text=APP, style="Title.TLabel").grid(row=0, column=1, sticky="sw")
        ttk.Label(head, text=tr("app.tagline"), style="Tag.TLabel", wraplength=int(210 * self.ui_scale)
                  ).grid(row=1, column=1, sticky="nw")
        # 1 - score
        s = self._section(p, "sec.score", 1)
        self.w_name = ttk.Label(s, text=tr("score.none"), style="Name.TLabel",
                                wraplength=int(270 * self.ui_scale))
        self.w_name.grid(row=0, column=0, sticky="w")
        self.w_info = ttk.Label(s, text="", style="Hint.TLabel", wraplength=int(270 * self.ui_scale),
                                justify="left")
        self.w_info.grid(row=1, column=0, sticky="w", pady=(2, 6))
        ttk.Button(s, text=tr("menu.open"), command=self.open_dialog).grid(row=2, column=0, sticky="ew")
        # 2 - layout
        lay = self._section(p, "sec.layout", 2)
        ttk.Label(lay, text=tr("layout.pairs")).grid(row=0, column=0, sticky="w", pady=(0, 2))
        seg = ttk.Frame(lay)
        seg.grid(row=1, column=0, sticky="w")
        for i in (1, 2, 3):
            ttk.Radiobutton(seg, text=f"  {i}  ", value=i, variable=self.v["pairs"],
                            style="Toolbutton").grid(row=0, column=i - 1, padx=(0, 4))
        ttk.Label(lay, text=tr("layout.page")).grid(row=2, column=0, sticky="w", pady=(8, 1))
        prow = ttk.Frame(lay)
        prow.grid(row=3, column=0, sticky="ew")
        prow.columnconfigure(0, weight=1)
        c = Choice(prow, self.v["page"], [(k, PAGE_LABEL[k]) for k in PAGES], width=12)
        c.box.grid(row=0, column=0, sticky="ew")
        self._choices.append(c)
        ttk.Checkbutton(prow, text=tr("layout.landscape"), variable=self.v["landscape"]
                        ).grid(row=0, column=1, padx=(8, 0))
        self._choice(lay, self.v["divider"], [(k, tr("divider." + k)) for k in CHOICES["divider"]],
                     4, "layout.divider")
        self._choice(lay, self.v["symbols"], [(k, tr("symbols." + k)) for k in CHOICES["symbols"]],
                     6, "layout.symbols")
        self._choice(lay, self.v["measure_numbers"],
                     [(k, tr("numbers." + k)) for k in CHOICES["measure_numbers"]], 8, "layout.numbers")
        ttk.Label(lay, text=tr("layout.spacing")).grid(row=10, column=0, sticky="w", pady=(8, 0))
        sprow = ttk.Frame(lay)
        sprow.grid(row=11, column=0, sticky="ew")
        sprow.columnconfigure(0, weight=1)
        ttk.Scale(sprow, from_=0.6, to=2.0, variable=self.v["spacing"], orient="horizontal"
                  ).grid(row=0, column=0, sticky="ew")
        self.w_spacing = ttk.Label(sprow, text="", width=5, anchor="e")
        self.w_spacing.grid(row=0, column=1, padx=(6, 0))
        ttk.Label(lay, text=tr("layout.staff")).grid(row=12, column=0, sticky="w", pady=(8, 1))
        strow = ttk.Frame(lay)
        strow.grid(row=13, column=0, sticky="w")
        ttk.Checkbutton(strow, text=tr("layout.staff_auto"), variable=self.v["staff_auto"]
                        ).grid(row=0, column=0)
        self.w_staff = ttk.Spinbox(strow, from_=3.0, to=9.0, increment=0.5, width=6,
                                   textvariable=self.v["staff_space"], format="%.1f")
        self.w_staff.grid(row=0, column=1, padx=(10, 4))
        ttk.Label(strow, text="pt").grid(row=0, column=2)
        ttk.Checkbutton(lay, text=tr("layout.labels"), variable=self.v["labels"]
                        ).grid(row=14, column=0, sticky="w", pady=(10, 0))
        ttk.Checkbutton(lay, text=tr("layout.justify"), variable=self.v["justify_last"]
                        ).grid(row=15, column=0, sticky="w", pady=(4, 0))
        # 3 - hands
        hd = self._section(p, "sec.hands", 3)
        ttk.Radiobutton(hd, text=tr("hands.auto"), value="auto", variable=self.v["hands"]
                        ).grid(row=0, column=0, sticky="w")
        hrow = ttk.Frame(hd)
        hrow.grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Radiobutton(hrow, text=tr("hands.split"), value="split", variable=self.v["hands"]
                        ).grid(row=0, column=0)
        self.w_split = ttk.Entry(hrow, textvariable=self.v["split"], width=6)
        self.w_split.grid(row=0, column=1, padx=(6, 0))
        ttk.Label(hd, text=tr("hands.hint"), style="Hint.TLabel", wraplength=int(270 * self.ui_scale)
                  ).grid(row=2, column=0, sticky="w", pady=(4, 0))
        # 4 - titles
        ti = self._section(p, "sec.titles", 4)
        ttk.Label(ti, text=tr("titles.title")).grid(row=0, column=0, sticky="w")
        ttk.Entry(ti, textvariable=self.v["title"]).grid(row=1, column=0, sticky="ew")
        ttk.Label(ti, text=tr("titles.composer")).grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(ti, textvariable=self.v["composer"]).grid(row=3, column=0, sticky="ew")
        ttk.Label(ti, text=tr("titles.hint"), style="Hint.TLabel").grid(row=4, column=0, sticky="w",
                                                                        pady=(4, 0))
        # 5 - export
        ex = self._section(p, "sec.export", 5)
        self._choice(ex, self.v["format"], [(k, tr("format." + k)) for k, _ in FORMATS], 0,
                     "export.format")
        ttk.Button(ex, text=tr("export.button"), style="Primary.TButton", command=self.export
                   ).grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.w_saved = ttk.Label(ex, text="", style="Saved.TLabel", wraplength=int(270 * self.ui_scale))
        self.w_saved.grid(row=3, column=0, sticky="w", pady=(6, 0))
        brow = ttk.Frame(ex)
        brow.grid(row=4, column=0, sticky="ew", pady=(4, 0))
        brow.columnconfigure(0, weight=1)
        brow.columnconfigure(1, weight=1)
        self.w_open_out = ttk.Button(brow, text=tr("export.open"), command=self._open_output)
        self.w_open_out.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.w_open_dir = ttk.Button(brow, text=tr("export.folder"), command=self._open_output_dir)
        self.w_open_dir.grid(row=0, column=1, sticky="ew", padx=(3, 0))

    def _build_view(self, v) -> None:
        tb = ttk.Frame(v, padding=(8, 5))
        tb.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.b_prev = ttk.Button(tb, text="◀", width=3, command=self.prev_page)
        self.b_prev.grid(row=0, column=0)
        self.w_page = ttk.Label(tb, text="", width=14, anchor="center")
        self.w_page.grid(row=0, column=1, padx=4)
        self.b_next = ttk.Button(tb, text="▶", width=3, command=self.next_page)
        self.b_next.grid(row=0, column=2)
        ttk.Separator(tb, orient="vertical").grid(row=0, column=3, sticky="ns", padx=10)
        ttk.Button(tb, text="−", width=3, command=lambda: self.zoom_by(-1)).grid(row=0, column=4)
        self.w_zoom = ttk.Label(tb, text="", width=6, anchor="center")
        self.w_zoom.grid(row=0, column=5, padx=2)
        ttk.Button(tb, text="+", width=3, command=lambda: self.zoom_by(1)).grid(row=0, column=6)
        ttk.Button(tb, text=tr("menu.fit_page"), command=lambda: self.set_zoom("page")
                   ).grid(row=0, column=7, padx=(10, 2))
        ttk.Button(tb, text=tr("menu.fit_width"), command=lambda: self.set_zoom("width")
                   ).grid(row=0, column=8, padx=2)
        tb.columnconfigure(9, weight=1)
        ttk.Button(tb, text=tr("tb.save"), style="Primary.TButton", command=self.export
                   ).grid(row=0, column=10, sticky="e")
        self.canvas = tk.Canvas(v, background=VIEW_BG, highlightthickness=0, borderwidth=0,
                                xscrollincrement=20, yscrollincrement=20)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(v, orient="vertical", command=self.canvas.yview)
        vsb.grid(row=1, column=1, sticky="ns")
        hsb = ttk.Scrollbar(v, orient="horizontal", command=self.canvas.xview)
        hsb.grid(row=2, column=0, sticky="ew")
        self.canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        v.rowconfigure(1, weight=1)
        v.columnconfigure(0, weight=1)
        self.canvas.bind("<Configure>", lambda e: self.request_redraw(120, resize=True))

    def _build_status(self) -> None:
        sb = ttk.Frame(self.root)
        sb.grid(row=1, column=0, sticky="ew")
        ttk.Separator(sb, orient="horizontal").grid(row=0, column=0, columnspan=2, sticky="ew")
        self.w_status = ttk.Label(sb, text=tr("status.ready"), style="Status.TLabel")
        self.w_status.grid(row=1, column=0, sticky="w")
        self.w_progress = ttk.Progressbar(sb, mode="indeterminate", length=int(160 * self.ui_scale))
        self.w_progress.grid(row=1, column=1, sticky="e", padx=10)
        self.w_progress.grid_remove()
        sb.columnconfigure(0, weight=1)
        self.statusbar = sb

    # ------------------------------------------------------------ state sync
    def _sync_states(self) -> None:
        try:
            self.w_staff.configure(state="disabled" if self.v["staff_auto"].get() else "normal")
            self.w_split.configure(state="normal" if self.v["hands"].get() == "split" else "disabled")
        except (tk.TclError, AttributeError):
            pass

    def _sync_spacing_label(self) -> None:
        try:
            self.w_spacing.configure(text=f"{int(round(self.v['spacing'].get() * 100))}%")
        except (tk.TclError, AttributeError):
            pass

    def _update_info(self) -> None:
        if not self.input_path:
            self.w_name.configure(text=tr("score.none"))
            self.w_info.configure(text="")
            return
        name = os.path.basename(self.input_path)
        if self.score is not None and self.score.title:
            name = f"{self.score.title}\n{name}"
        self.w_name.configure(text=name)
        if self.score is not None and self.layout is not None and self.pages:
            lines = [tr("score.info", measures=len(self.score.measures), pages=len(self.pages),
                        pairs=len(self.layout.columns)),
                     tr("score.hands", desc=self.score.source_description),
                     tr("score.staff", sp=f"{self.layout.sp:.1f}")]
            self.w_info.configure(text="\n".join(lines))
        else:
            self.w_info.configure(text="")

    def _update_saved(self) -> None:
        ok = bool(self.last_output) and all(os.path.exists(p) for p in self.last_output[:1])
        state = "normal" if ok else "disabled"
        self.w_open_out.configure(state=state)
        self.w_open_dir.configure(state=state)
        if ok:
            name = os.path.basename(self.last_output[0])
            if len(self.last_output) > 1:
                name += f" (+{len(self.last_output) - 1})"
            self.w_saved.configure(text=tr("export.saved", name=name))
        else:
            self.w_saved.configure(text="")

    def _update_toolbar(self) -> None:
        n = len(self.pages)
        self.w_page.configure(text=tr("tb.page", i=self.page_index + 1, n=n) if n else "")
        self.b_prev.configure(state="normal" if n and self.page_index > 0 else "disabled")
        self.b_next.configure(state="normal" if n and self.page_index < n - 1 else "disabled")
        self.w_zoom.configure(text=tr("tb.zoom", pct=int(round(self.scale / self.px_per_pt * 100)))
                              if n else "")

    def set_status(self, text: str) -> None:
        self._status_text = text
        try:
            self.w_status.configure(text=text)
        except tk.TclError:
            pass

    def _update_status_after_rebuild(self) -> None:
        if self.error:
            self.set_status(tr("status.error", msg=self.error.splitlines()[0]))
        elif self.pages and self.score is not None:
            self.set_status(tr("status.done", name=os.path.basename(self.input_path or ""),
                               measures=len(self.score.measures), pages=len(self.pages),
                               secs=getattr(self, "_last_secs", "0.0")))

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        if busy:
            self.w_progress.grid()
            self.w_progress.start(12)
            self.root.configure(cursor="watch" if not self.pages else "")
        else:
            self.w_progress.stop()
            self.w_progress.grid_remove()
            self.root.configure(cursor="")

    # ------------------------------------------------------------ rendering
    def _option_changed(self, delay: int) -> None:
        if self._quiet or not self.input_path:
            return
        self.request_render(delay)

    def request_render(self, delay: int = 300, force: bool = False) -> None:
        if not self.input_path:
            return
        if self._pending is not None:
            self.root.after_cancel(self._pending)
        self._pending = self.root.after(max(1, delay), lambda: self._start_render(force=force))

    def _start_render(self, explicit: bool = False, force: bool = False,
                      then: Optional[Callable[[], None]] = None) -> None:
        self._pending = None
        if not self.input_path:
            return
        key = self._current_key()
        if not force and then is None:
            if key == self._running_key:
                return
            if key == self.render_key and self.pages:
                return
        try:
            opts, split = self.build_options()
        except (ValueError, tk.TclError) as exc:
            self.set_status(tr("status.error", msg=str(exc)))
            return
        self._job += 1
        job = self._job
        self._running_key = key
        self._explicit = explicit or self._explicit
        self._after_render = then
        self._set_busy(True)
        path = self.input_path
        self.set_status(tr("status.loading", name=os.path.basename(path)))
        self.request_redraw(0)

        def work():
            t0 = time.perf_counter()
            try:
                src = to_musicxml(path)
                pages, score, layout = render_pages(src, opts, split=split)
                self._results.put((job, True, (pages, score, layout, key), time.perf_counter() - t0))
            except Exception as exc:     # reported in the window
                self._results.put((job, False, exc, time.perf_counter() - t0))

        threading.Thread(target=work, name="vertsheet-render", daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                job, ok, payload, secs = self._results.get_nowait()
                if job != self._job:
                    continue
                self._finish(ok, payload, secs)
        except queue.Empty:
            pass
        if self._alive:
            self._poll_id = self.root.after(70, self._poll)

    def _finish(self, ok: bool, payload, secs: float) -> None:
        self._running_key = None
        self._set_busy(False)
        explicit, self._explicit = self._explicit, False
        then, self._after_render = self._after_render, None
        if ok:
            pages, score, layout, key = payload
            self.pages, self.score, self.layout, self.render_key = pages, score, layout, key
            self.error = None
            self.page_index = max(0, min(self.page_index, len(pages) - 1))
            self._last_secs = f"{max(secs, 0.1):.1f}"
            self._update_info()
            self.set_status(tr("status.done", name=os.path.basename(self.input_path or ""),
                               measures=len(score.measures), pages=len(pages), secs=self._last_secs))
            self.request_redraw(0)
            if then is not None:
                then()
            return
        exc = payload
        self.pages, self.score, self.layout, self.render_key = [], None, None, None
        self.error = self.friendly_error(exc)
        self._update_info()
        self.set_status(tr("status.error", msg=self.error.splitlines()[0]))
        self.request_redraw(0)
        if explicit:
            ext = os.path.splitext(self.input_path or "")[1].lower()
            if ext in OMR_EXT and isinstance(exc, ConversionError) and not _candidates_audiveris():
                if messagebox.askyesno(tr("err.title"), self.error + "\n\n" + tr("err.omr_link"),
                                       parent=self.root):
                    webbrowser.open(AUDIVERIS_URL)
            else:
                messagebox.showerror(tr("err.title"), self.error, parent=self.root)

    def friendly_error(self, exc: BaseException) -> str:
        ext = os.path.splitext(self.input_path or "")[1].lower()
        if isinstance(exc, ConversionError):
            if ext in OMR_EXT and not _candidates_audiveris():
                return tr("err.omr")
            if ext in MUSESCORE_EXT and not _candidates_musescore():
                return tr("err.musescore", ext=ext)
            return tr("err.convert", msg=str(exc))
        if isinstance(exc, (MusicXMLError, ValueError)):
            return tr("err.read", msg=str(exc))
        if isinstance(exc, OSError):
            return tr("err.read", msg=str(exc))
        return tr("err.generic", msg=f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------ preview drawing
    def request_redraw(self, delay: int = 0, resize: bool = False) -> None:
        if resize and self.pages and self.zoom_mode == "custom":
            return
        if self._redraw_pending is not None:
            self.root.after_cancel(self._redraw_pending)
        self._redraw_pending = self.root.after(max(1, delay), self.redraw)

    def redraw(self) -> None:
        self._redraw_pending = None
        c = self.canvas
        try:
            xf, yf = c.xview()[0], c.yview()[0]
        except tk.TclError:
            return
        c.delete("all")
        for w in self._welcome_widgets:
            w.destroy()
        self._welcome_widgets = []
        w, h = max(c.winfo_width(), 100), max(c.winfo_height(), 100)
        if self.pages:
            page = self.pages[self.page_index]
            if self.zoom_mode in ("page", "width"):
                s = fit_scale(page.width, page.height, w, h, 24, self.zoom_mode)
            else:
                s = self.zoom * self.px_per_pt
            self.scale = s
            pw, ph = page.width * s, page.height * s
            ox = max(24.0, (w - pw) / 2)
            oy = 24.0 if ph + 48 > h else (h - ph) / 2
            c.create_rectangle(ox + 5, oy + 5, ox + pw + 5, oy + ph + 5, fill="#45484f", outline="")
            c.create_rectangle(ox, oy, ox + pw, oy + ph, fill="white", outline="#33353a")
            draw_page(c, page, s, ox, oy)
            c.configure(scrollregion=(0, 0, max(w, pw + 48), max(h, ph + 48)))
            c.xview_moveto(xf)
            c.yview_moveto(yf)
            if self.busy:
                self._badge(tr("preview.busy"))
        else:
            c.configure(scrollregion=(0, 0, w, h))
            if self.busy:
                self._card(w, h, tr("preview.busy"), "", button=False)
            elif self.error:
                self._card(w, h, tr("preview.error"), self.error, button=True)
            else:
                self._welcome(w, h)
        self._update_toolbar()

    def _badge(self, text: str) -> None:
        c = self.canvas
        x, y = c.canvasx(12), c.canvasy(12)
        t = c.create_text(x + 10, y + 8, text=text, anchor="nw", fill="white", font=self.f_bold)
        bb = c.bbox(t)
        r = c.create_rectangle(bb[0] - 10, bb[1] - 6, bb[2] + 10, bb[3] + 6, fill="#2f5aa8", outline="")
        c.tag_raise(t, r)

    def _card(self, w: int, h: int, title: str, body: str, button: bool) -> None:
        c = self.canvas
        cw = min(int(560 * self.ui_scale), w - 40)
        x0 = (w - cw) / 2
        y = max(30.0, h * 0.18)
        items = [c.create_text(x0 + 28, y + 28, text=title, anchor="nw", font=self.f_big,
                               fill="#1f2a44", width=cw - 56)]
        y2 = c.bbox(items[0])[3] + 14
        if body:
            items.append(c.create_text(x0 + 28, y2, text=body, anchor="nw", font=self.f_body,
                                       fill="#333844", width=cw - 56))
            y2 = c.bbox(items[-1])[3] + 18
        if button:
            b = ttk.Button(c, text=tr("welcome.open"), style="Primary.TButton", command=self.open_dialog)
            self._welcome_widgets.append(b)
            c.create_window(x0 + 28, y2, window=b, anchor="nw")
            y2 += 48 * self.ui_scale
        bg = c.create_rectangle(x0, y, x0 + cw, y2 + 14, fill="white", outline="")
        c.tag_lower(bg)

    def _welcome(self, w: int, h: int) -> None:
        c = self.canvas
        cw = min(int(620 * self.ui_scale), w - 40)
        x0 = (w - cw) / 2
        y = max(24.0, h * 0.12)
        pad = 32
        items = []
        tx = x0 + pad
        if 128 in self._images:
            items.append(c.create_image(x0 + pad, y + pad, image=self._images[128], anchor="nw"))
            tx = x0 + pad + 128 + 22
        items.append(c.create_text(tx, y + pad + 8, text=APP, anchor="nw", font=self.f_title,
                                   fill=ACCENT))
        items.append(c.create_text(tx, c.bbox(items[-1])[3] + 4, text=tr("welcome.title"),
                                   anchor="nw", font=self.f_big, fill="#1f2a44",
                                   width=x0 + cw - pad - tx))
        y2 = max(c.bbox(i)[3] for i in items) + 22
        items.append(c.create_text(x0 + pad, y2, text=tr("welcome.body"), anchor="nw",
                                   font=self.f_body, fill="#333844", width=cw - 2 * pad))
        y2 = c.bbox(items[-1])[3] + 22
        b = ttk.Button(c, text=tr("welcome.open"), style="Primary.TButton", command=self.open_dialog)
        self._welcome_widgets.append(b)
        c.create_window(x0 + pad, y2, window=b, anchor="nw")
        b.update_idletasks()
        y2 += b.winfo_reqheight() + 14
        items.append(c.create_text(x0 + pad, y2, text=tr("welcome.formats"), anchor="nw",
                                   font=self.f_small, fill="#6b7080"))
        y2 = c.bbox(items[-1])[3] + 10
        recent = [p for p in self.settings.get("recent", []) if os.path.isfile(p)][:5]
        if recent:
            y2 += 8
            c.create_text(x0 + pad, y2, text=tr("welcome.recent"), anchor="nw", font=self.f_bold,
                          fill="#1f2a44")
            y2 += 24 * self.ui_scale
            for p in recent:
                t = c.create_text(x0 + pad + 12, y2, text=os.path.basename(p), anchor="nw",
                                  font=self.f_body, fill=ACCENT, tags=("link",))
                c.tag_bind(t, "<Button-1>", lambda e, q=p: self.open_file(q))
                c.tag_bind(t, "<Enter>", lambda e: self.canvas.configure(cursor="hand2"))
                c.tag_bind(t, "<Leave>", lambda e: self.canvas.configure(cursor=""))
                y2 = c.bbox(t)[3] + 4
        bg = c.create_rectangle(x0, y, x0 + cw, y2 + pad - 8, fill="white", outline="")
        sh = c.create_rectangle(x0 + 5, y + 5, x0 + cw + 5, y2 + pad - 3, fill="#55585f", outline="")
        c.tag_lower(bg)
        c.tag_lower(sh)

    # ------------------------------------------------------------ navigation / zoom
    def prev_page(self, scroll: str = "top") -> None:
        if self.pages and self.page_index > 0:
            self.page_index -= 1
            self._show_page(scroll)

    def next_page(self, scroll: str = "top") -> None:
        if self.pages and self.page_index < len(self.pages) - 1:
            self.page_index += 1
            self._show_page(scroll)

    def _show_page(self, scroll: str) -> None:
        self.redraw()
        self.canvas.yview_moveto(0.0 if scroll == "top" else 1.0)

    def set_zoom(self, mode) -> None:
        if isinstance(mode, str):
            self.zoom_mode = mode
        else:
            self.zoom_mode, self.zoom = "custom", float(mode)
        self.redraw()

    def zoom_by(self, direction: int) -> None:
        if not self.pages:
            return
        cur = self.scale / self.px_per_pt
        if direction > 0:
            nxt = next((z for z in ZOOM_STEPS if z > cur * 1.02), ZOOM_STEPS[-1])
        else:
            nxt = next((z for z in reversed(ZOOM_STEPS) if z < cur * 0.98), ZOOM_STEPS[0])
        self.set_zoom(nxt)

    def _area_of(self, widget) -> str:
        if widget is None:
            return ""
        name = str(widget)
        if name.startswith(str(self.canvas)):
            return "preview"
        if name.startswith(str(self.sidebar)):
            return "sidebar"
        return ""

    def _on_wheel(self, e):
        try:
            target = self._area_of(self.root.winfo_containing(e.x_root, e.y_root))
        except (tk.TclError, KeyError):
            return "break"
        step = -1 if e.delta > 0 else 1
        if target == "sidebar":
            self.sidebar.scroll(step * 2)
        elif target == "preview" and self.pages:
            if e.state & 0x4:
                self.zoom_by(-step)
            elif e.state & 0x1:
                self.canvas.xview_scroll(step * 3, "units")
            else:
                top, bottom = self.canvas.yview()
                at_edge = (step > 0 and bottom >= 0.999) or (step < 0 and top <= 0.001)
                if at_edge:
                    self._edge_hits += 1
                    if self._edge_hits >= 2:
                        self._edge_hits = 0
                        if step > 0:
                            self.next_page("top")
                        else:
                            self.prev_page("bottom")
                else:
                    self._edge_hits = 0
                    self.canvas.yview_scroll(step * 3, "units")
        return "break"

    # ------------------------------------------------------------ files
    def open_dialog(self) -> None:
        types = [(tr("ft.scores"), "*.mscz *.mscx *.musicxml *.xml *.mxl *.mid *.midi"),
                 ("MuseScore", "*.mscz *.mscx"), ("MusicXML", "*.musicxml *.xml *.mxl"),
                 ("MIDI", "*.mid *.midi"), ("PDF / " + tr("ft.all"), "*.pdf *.png *.jpg *.tif"),
                 (tr("ft.all"), "*.*")]
        initial = self.settings.get("last_dir") or os.path.expanduser("~")
        path = filedialog.askopenfilename(parent=self.root, title=tr("dlg.open"), filetypes=types,
                                          initialdir=initial if os.path.isdir(initial) else None)
        if path:
            self.open_file(path)

    def open_file(self, path: str) -> None:
        path = os.path.abspath(path.strip().strip('"'))
        if not os.path.isfile(path):
            messagebox.showerror(tr("err.title"), tr("err.notfound", path=path), parent=self.root)
            return
        self.input_path = path
        self.settings["last_dir"] = os.path.dirname(path)
        recent = [p for p in self.settings.get("recent", []) if os.path.normcase(p) != os.path.normcase(path)]
        self.settings["recent"] = ([path] + recent)[:MAX_RECENT]
        self._fill_recent_menu()
        self._quiet = True
        try:
            self.v["title"].set("")
            self.v["composer"].set("")
        finally:
            self._quiet = False
        self.pages, self.score, self.layout, self.render_key, self.error = [], None, None, None, None
        self.page_index = 0
        self.zoom_mode = "page"
        self.last_output = []
        self._update_saved()
        self._update_info()
        self.root.title(f"{os.path.basename(path)} – {APP}")
        self._start_render(explicit=True, force=True)

    def export(self) -> None:
        if not self.input_path:
            self.open_dialog()
            return
        fmt = self.v["format"].get()
        ext = dict(FORMATS).get(fmt, ".pdf")
        stem = os.path.splitext(os.path.basename(self.input_path))[0] + "_vertical"
        initial = self.settings.get("export_dir") or os.path.dirname(self.input_path)
        types = [(tr("format." + f), "*" + e) for f, e in FORMATS]
        types.sort(key=lambda t: t[1] != "*" + ext)
        path = filedialog.asksaveasfilename(parent=self.root, title=tr("dlg.export"),
                                            initialdir=initial if os.path.isdir(initial) else None,
                                            initialfile=stem + ext, defaultextension=ext,
                                            filetypes=types)
        if not path:
            return
        if os.path.splitext(path)[1].lower() not in (".pdf", ".svg", ".html", ".htm"):
            path += ext
        self.settings["export_dir"] = os.path.dirname(path)
        self.export_to(path)

    def export_to(self, path: str) -> None:
        if self.pages and self.render_key == self._current_key():
            self._write(path)
        else:
            self._start_render(explicit=True, force=True, then=lambda: self._write(path))

    def _write(self, path: str) -> List[str]:
        self.set_status(tr("status.exporting"))
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            opts, _ = self.build_options()
            title = opts.title if opts.title is not None else (self.score.title if self.score else "")
            composer = opts.composer if opts.composer is not None else (self.score.composer if self.score else "")
            written = write_output(self.pages, path, title, composer)
        except (OSError, ValueError) as exc:
            self.root.configure(cursor="")
            messagebox.showerror(tr("err.title"), tr("err.save", msg=str(exc)), parent=self.root)
            self.set_status(tr("status.error", msg=str(exc)))
            return []
        self.root.configure(cursor="")
        self.last_output = written
        self._update_saved()
        if len(written) == 1:
            self.set_status(tr("status.saved1", name=written[0]))
        else:
            self.set_status(tr("status.saved", n=len(written), folder=os.path.dirname(written[0])))
        return written

    def _open_output(self) -> None:
        if self.last_output and os.path.exists(self.last_output[0]):
            open_path(self.last_output[0])

    def _open_output_dir(self) -> None:
        if self.last_output and os.path.exists(self.last_output[0]):
            show_in_folder(self.last_output[0])

    # ------------------------------------------------------------ misc
    def set_ui_language(self, lang: str) -> None:
        set_language(lang)
        self.settings["language"] = lang
        self._rebuild()
        self.request_render(10)

    def _info(self, title: str, body: str) -> None:
        messagebox.showinfo(title, body, parent=self.root)

    def about(self) -> None:
        self._info(tr("menu.about"), tr("about.body", version=__version__,
                                        py=sys.version.split()[0], tk=tk.TkVersion))

    def _store_settings(self) -> None:
        for name in DEFAULTS:
            if name in self.v and name not in ("title", "composer"):
                try:
                    val = self.v[name].get()
                except tk.TclError:
                    continue
                if isinstance(DEFAULTS[name], float):
                    val = float(val)
                self.settings[name] = val
        try:
            if self.root.state() == "normal":
                self.settings["geometry"] = self.root.geometry()
        except tk.TclError:
            pass

    def dispose(self) -> None:
        """Stop the timers of this window (before destroying or replacing it)."""
        self._alive = False
        for attr in ("_poll_id", "_pending", "_redraw_pending"):
            after_id = getattr(self, attr, None)
            if after_id is not None:
                try:
                    self.root.after_cancel(after_id)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        self._job += 1                     # results still on the way are ignored

    def quit(self) -> None:
        self._store_settings()
        if self.persist:
            save_settings(self.settings)
        self.dispose()
        self.root.destroy()


def selftest(path: str, out: str, timeout_ms: int = 150000) -> int:
    """Open ``path`` in a hidden window, lay it out and export ``out``; used by
    the build to check the frozen program. Returns 0 on success."""
    root = tk.Tk()
    root.withdraw()
    app = App(root, settings=json.loads(json.dumps(DEFAULTS)), persist=False)
    result = {"code": 3}

    def done():
        result["code"] = 0 if app._write(out) else 1
        root.quit()

    def watch():
        if not app.busy and app.error:
            result["code"] = 1
            root.quit()
            return
        root.after(150, watch)

    app.input_path = os.path.abspath(path)
    app._start_render(force=True, then=done)
    root.after(150, watch)
    root.after(timeout_ms, root.quit)
    root.mainloop()
    root.destroy()
    return result["code"]


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) >= 3 and argv[0] == "--selftest":
        return selftest(argv[1], argv[2])
    _dpi_aware()
    if sys.platform == "win32" and not getattr(sys, "frozen", False):
        try:   # own taskbar icon instead of Python's when run from source
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("vertSheet.App")
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    App(root, initial=argv[0] if argv else None)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
