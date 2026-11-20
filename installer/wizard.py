"""vertSheet setup wizard (Tkinter).

``tools/build_windows.py`` freezes this script into
``vertSheet-Setup-<version>.exe`` with the application files embedded as
``payload.zip``. The wizard installs per user (no administrator rights).

Pages: welcome (with language choice) > destination folder > additional
options > ready > installing > finished.

Command line options (Windows-installer style):

    /S                silent install with default options
    /D=FOLDER         destination folder
    /NODESKTOP  /NOSTARTMENU  /NOSHORTCUTS  /NOASSOC  /PATH
    /LANG=it|en       language of the wizard and of the installed app
    /PAYLOAD=ZIP      use another payload (development)
    /TESTROOT=DIR     sandbox: registry under HKCU\\Software\\vertSheet-selftest,
                      shortcuts inside DIR (used by the automated tests)
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if not getattr(sys, "frozen", False) and ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from vertsheet import __version__                                        # noqa: E402
from vertsheet import winsetup as ws                                     # noqa: E402
from vertsheet.i18n import LANGUAGES, detect_language, get_language, set_language, tr  # noqa: E402

PAGES = ["welcome", "dir", "options", "ready", "progress", "done"]
SELFTEST_PREFIX = "Software\\vertSheet-selftest\\"
BANNER_TOP = (0.20, 0.38, 0.80)
BANNER_BOTTOM = (0.08, 0.15, 0.42)


def resource(*parts: str) -> str:
    base = getattr(sys, "_MEIPASS", None) or ROOT
    return os.path.join(base, *parts)


def human_size(n: Optional[int]) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


def parse_flags(argv: List[str]) -> Dict[str, str]:
    flags: Dict[str, str] = {}
    for a in argv:
        if a.startswith("/") or a.startswith("-"):
            body = a[1:]
            if "=" in body:
                k, v = body.split("=", 1)
                flags[k.upper()] = v.strip('"')
            else:
                flags[body.upper()] = "1"
    return flags


def find_payload(flags: Dict[str, str]) -> Optional[str]:
    if flags.get("PAYLOAD"):
        return flags["PAYLOAD"]
    base = getattr(sys, "_MEIPASS", None)
    if base and os.path.isfile(os.path.join(base, "payload.zip")):
        return os.path.join(base, "payload.zip")
    import tempfile
    dev = os.path.join(tempfile.gettempdir(), "vertSheet-build", "payload.zip")
    return dev if os.path.isfile(dev) else None


def target_from(flags: Dict[str, str]) -> ws.Target:
    root = flags.get("TESTROOT")
    if root:
        return ws.Target(reg_prefix=SELFTEST_PREFIX, desktop=os.path.join(root, "Desktop"),
                         programs=os.path.join(root, "Programs"), notify=False)
    return ws.Target()


def _dpi_aware() -> None:
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def _hex(c) -> str:
    return "#%02x%02x%02x" % tuple(int(round(v * 255)) for v in c)


def error_text(exc: ws.InstallError) -> str:
    if exc.code == "running":
        return tr("wiz.running")
    if exc.code == "noperm":
        return tr("wiz.dir.noperm")
    return tr("wiz.error", msg=exc.detail or exc.code)


class Wizard:
    def __init__(self, root: tk.Tk, payload: str, target: ws.Target, flags: Dict[str, str]):
        self.root = root
        self.payload = payload
        self.target = target
        self.existing = ws.find_existing(target)
        self.size = ws.payload_size(payload)
        self.scale = root.winfo_fpixels("1i") / 96.0
        default_dir = flags.get("D") or (self.existing[0] if self.existing else ws.default_install_dir())
        self.v_dir = tk.StringVar(root, default_dir)
        no_sc = "NOSHORTCUTS" in flags
        self.v_desktop = tk.BooleanVar(root, not (no_sc or "NODESKTOP" in flags))
        self.v_start = tk.BooleanVar(root, not (no_sc or "NOSTARTMENU" in flags))
        self.v_assoc = tk.BooleanVar(root, "NOASSOC" not in flags)
        self.v_path = tk.BooleanVar(root, "PATH" in flags)
        self.v_launch = tk.BooleanVar(root, True)
        self.page = 0
        self.manifest: Optional[dict] = None
        self._q: "queue.Queue" = queue.Queue()
        self._images: Dict[str, tk.PhotoImage] = {}
        self._setup()
        self.show(0)

    # ------------------------------------------------------------ chrome
    def _setup(self) -> None:
        r = self.root
        r.title(tr("wiz.title"))
        w, h = int(660 * self.scale), int(480 * self.scale)
        x = max(0, (r.winfo_screenwidth() - w) // 2)
        y = max(0, (r.winfo_screenheight() - h) // 3)
        r.geometry(f"{w}x{h}+{x}+{y}")
        r.resizable(False, False)
        ico = resource("assets", "vertsheet.ico")
        if os.path.isfile(ico):
            try:
                r.iconbitmap(default=ico)
            except tk.TclError:
                pass
        for size in (64, 128):
            p = resource("assets", f"vertsheet-{size}.png")
            if os.path.isfile(p):
                try:
                    self._images[str(size)] = tk.PhotoImage(master=r, file=p)
                except tk.TclError:
                    pass
        st = ttk.Style(r)
        if "vista" in st.theme_names():
            st.theme_use("vista")
        import tkinter.font as tkfont
        base = tkfont.nametofont("TkDefaultFont")
        fam, size = base.actual("family"), base.actual("size")
        self.f_big = tkfont.Font(r, family=fam, size=size + 6, weight="bold")
        self.f_bold = tkfont.Font(r, family=fam, size=size, weight="bold")
        self.f_banner = tkfont.Font(r, family=fam, size=size + 5, weight="bold")
        st.configure("White.TFrame", background="white")
        st.configure("White.TLabel", background="white")
        st.configure("Big.TLabel", background="white", font=self.f_big, foreground="#1f2a44")
        st.configure("HeadTitle.TLabel", background="white", font=self.f_bold)
        st.configure("HeadSub.TLabel", background="white")
        st.configure("Strong.TLabel", font=self.f_bold)
        st.configure("WStrong.TLabel", background="white", font=self.f_bold, foreground="#1d5a2e")
        st.configure("White.TCheckbutton", background="white")
        st.configure("Hint.TLabel", foreground="#6b7080")
        r.rowconfigure(0, weight=1)
        r.columnconfigure(0, weight=1)
        self.body = ttk.Frame(r)
        self.body.grid(row=0, column=0, sticky="nsew")
        self.body.rowconfigure(0, weight=1)
        self.body.columnconfigure(0, weight=1)
        ttk.Separator(r, orient="horizontal").grid(row=1, column=0, sticky="ew")
        bar = ttk.Frame(r, padding=(12, 10))
        bar.grid(row=2, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)
        self.b_back = ttk.Button(bar, command=self.back, width=12)
        self.b_back.grid(row=0, column=1, padx=(0, 4))
        self.b_next = ttk.Button(bar, command=self.next, width=12, default="active")
        self.b_next.grid(row=0, column=2, padx=(0, 12))
        self.b_cancel = ttk.Button(bar, command=self.cancel, width=12)
        self.b_cancel.grid(row=0, column=3)
        r.protocol("WM_DELETE_WINDOW", self.cancel)
        r.bind("<Return>", lambda e: self.next() if str(self.b_next["state"]) != "disabled" else None)
        r.bind("<Escape>", lambda e: self.cancel())
        r.after(80, self._poll)

    def _clear(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()

    def _banner_page(self) -> ttk.Frame:
        frame = ttk.Frame(self.body, style="White.TFrame")
        frame.grid(row=0, column=0, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        bw = int(176 * self.scale)
        c = tk.Canvas(frame, width=bw, highlightthickness=0, borderwidth=0)
        c.grid(row=0, column=0, sticky="ns")
        self.root.update_idletasks()
        h = max(c.winfo_height(), int(420 * self.scale))
        steps = 48
        for i in range(steps):
            t = i / (steps - 1)
            col = tuple(BANNER_TOP[k] + (BANNER_BOTTOM[k] - BANNER_TOP[k]) * t for k in range(3))
            c.create_rectangle(0, h * i / steps, bw, h * (i + 1) / steps + 1, fill=_hex(col), outline="")
        # the vertical-score motif: faint staff pairs running down the banner
        for k, x in enumerate((0.16, 0.5, 0.84)):
            cx = bw * x
            for j in range(5):
                xx = cx - 14 * self.scale + j * 7 * self.scale
                c.create_line(xx, h * 0.52, xx, h, fill="#5d7fd0" if k != 1 else "#6c8de0")
        img = self._images.get("128")
        y = int(40 * self.scale)
        if img is not None:
            c.create_image(bw / 2, y, image=img, anchor="n")
            y += img.height() + int(14 * self.scale)
        c.create_text(bw / 2, y, text="vertSheet", fill="white", font=self.f_banner, anchor="n")
        c.create_text(bw / 2, y + int(30 * self.scale), text=__version__, fill="#c9d6ff", anchor="n")
        content = ttk.Frame(frame, style="White.TFrame", padding=(int(26 * self.scale), int(24 * self.scale),
                                                                 int(26 * self.scale), int(12 * self.scale)))
        content.grid(row=0, column=1, sticky="nsew")
        content.columnconfigure(0, weight=1)
        return content

    def _header_page(self, title: str, sub: str) -> ttk.Frame:
        frame = ttk.Frame(self.body)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        head = ttk.Frame(frame, style="White.TFrame", padding=(18, 12, 12, 12))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text=title, style="HeadTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(head, text=sub, style="HeadSub.TLabel").grid(row=1, column=0, sticky="w", padx=(14, 0),
                                                              pady=(3, 0))
        img = self._images.get("64")
        if img is not None:
            ttk.Label(head, image=img, style="White.TLabel").grid(row=0, column=1, rowspan=2)
        ttk.Separator(frame, orient="horizontal").grid(row=1, column=0, sticky="ew")
        content = ttk.Frame(frame, padding=(int(34 * self.scale), int(18 * self.scale),
                                            int(30 * self.scale), int(10 * self.scale)))
        content.grid(row=2, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        return content

    def _buttons(self) -> None:
        name = PAGES[self.page]
        self.root.title(tr("wiz.title"))
        self.b_back.configure(text=tr("wiz.back"),
                              state="normal" if name in ("dir", "options", "ready") else "disabled")
        nxt = {"ready": tr("wiz.install"), "done": tr("wiz.finish")}.get(name, tr("wiz.next"))
        self.b_next.configure(text=nxt, state="disabled" if name == "progress" else "normal")
        self.b_cancel.configure(text=tr("wiz.cancel"),
                                state="disabled" if name in ("progress", "done") else "normal")
        if name == "done":
            self.b_back.grid_remove()
            self.b_cancel.grid_remove()
        else:
            self.b_back.grid()
            self.b_cancel.grid()

    # ------------------------------------------------------------ pages
    def show(self, index: int) -> None:
        self.page = index
        self._clear()
        getattr(self, "_page_" + PAGES[index])()
        self._buttons()
        self.b_next.focus_set()

    def _wrap(self) -> int:
        return int(400 * self.scale)

    def _page_welcome(self) -> None:
        c = self._banner_page()
        ttk.Label(c, text=tr("wiz.welcome.title", version=__version__), style="Big.TLabel",
                  wraplength=self._wrap(), justify="left").grid(row=0, column=0, sticky="w")
        ttk.Label(c, text=tr("wiz.welcome.body"), style="White.TLabel", wraplength=self._wrap(),
                  justify="left").grid(row=1, column=0, sticky="w", pady=(16, 0))
        if self.existing:
            ttk.Label(c, text=tr("wiz.welcome.upgrade", old=self.existing[1] or "?", new=__version__),
                      style="WStrong.TLabel", wraplength=self._wrap(), justify="left"
                      ).grid(row=2, column=0, sticky="w", pady=(14, 0))
        c.rowconfigure(3, weight=1)
        row = ttk.Frame(c, style="White.TFrame")
        row.grid(row=4, column=0, sticky="w")
        ttk.Label(row, text=tr("wiz.language"), style="White.TLabel").grid(row=0, column=0, padx=(0, 8))
        names = list(LANGUAGES.values())
        codes = list(LANGUAGES.keys())
        box = ttk.Combobox(row, values=names, state="readonly", width=14)
        box.current(codes.index(get_language()) if get_language() in codes else 0)
        box.grid(row=0, column=1)

        def picked(_e=None):
            set_language(codes[box.current()])
            self.show(self.page)
        box.bind("<<ComboboxSelected>>", picked)

    def _page_dir(self) -> None:
        c = self._header_page(tr("wiz.dir.title"), tr("wiz.dir.sub"))
        ttk.Label(c, text=tr("wiz.dir.body"), wraplength=int(520 * self.scale), justify="left"
                  ).grid(row=0, column=0, columnspan=2, sticky="w")
        e = ttk.Entry(c, textvariable=self.v_dir)
        e.grid(row=1, column=0, sticky="ew", pady=(18, 0), ipady=2)
        ttk.Button(c, text=tr("wiz.dir.browse"), command=self._browse).grid(row=1, column=1, padx=(8, 0),
                                                                          pady=(18, 0))
        c.rowconfigure(2, weight=1)
        self.w_req = ttk.Label(c, text="")
        self.w_req.grid(row=3, column=0, columnspan=2, sticky="w")
        self.w_free = ttk.Label(c, text="")
        self.w_free.grid(row=4, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self._space()
        self.v_dir.trace_add("write", lambda *_a: self._space())

    def _space(self) -> None:
        try:
            self.w_req.configure(text=tr("wiz.dir.required", size=human_size(self.size)))
            self.w_free.configure(text=tr("wiz.dir.free", size=human_size(ws.free_space(self.v_dir.get()))))
        except tk.TclError:
            pass

    def _browse(self) -> None:
        start = self.v_dir.get()
        while start and not os.path.isdir(start):
            parent = os.path.dirname(start)
            if parent == start:
                break
            start = parent
        d = filedialog.askdirectory(parent=self.root, initialdir=start or None, mustexist=False)
        if d:
            d = os.path.normpath(d)
            if os.path.basename(d).lower() != "vertsheet":
                d = os.path.join(d, "vertSheet")
            self.v_dir.set(d)

    def _page_options(self) -> None:
        c = self._header_page(tr("wiz.opts.title"), tr("wiz.opts.sub"))
        items = [(self.v_desktop, "wiz.opts.desktop", None), (self.v_start, "wiz.opts.startmenu", None),
                 (self.v_assoc, "wiz.opts.assoc", "wiz.opts.assoc_hint"), (self.v_path, "wiz.opts.path", None)]
        row = 0
        for var, key, hint in items:
            ttk.Checkbutton(c, text=tr(key), variable=var).grid(row=row, column=0, sticky="w", pady=(0, 2))
            row += 1
            if hint:
                ttk.Label(c, text=tr(hint), style="Hint.TLabel").grid(row=row, column=0, sticky="w",
                                                                     padx=(24, 0), pady=(0, 8))
                row += 1
            else:
                ttk.Frame(c, height=8).grid(row=row, column=0)
                row += 1

    def _page_ready(self) -> None:
        c = self._header_page(tr("wiz.ready.title"), tr("wiz.ready.sub"))
        c.rowconfigure(0, weight=1)
        lines = [tr("wiz.ready.dir"), "      " + self.v_dir.get(), "", tr("wiz.ready.tasks"),
                 "      • " + tr("wiz.ready.copy", size=human_size(self.size))]
        if self.v_desktop.get():
            lines.append("      • " + tr("wiz.opts.desktop"))
        if self.v_start.get():
            lines.append("      • " + tr("wiz.opts.startmenu"))
        if self.v_assoc.get():
            lines.append("      • " + tr("wiz.opts.assoc"))
        if self.v_path.get():
            lines.append("      • " + tr("wiz.opts.path"))
        lines.append("      • " + tr("wiz.ready.uninstall"))
        t = tk.Text(c, height=12, wrap="word", relief="solid", borderwidth=1, padx=10, pady=8,
                    font=tkfontdefault(), background="white")
        t.insert("1.0", "\n".join(lines))
        t.configure(state="disabled")
        t.grid(row=0, column=0, sticky="nsew")

    def _page_progress(self) -> None:
        c = self._header_page(tr("wiz.progress.title"), tr("wiz.progress.sub"))
        self.w_step = ttk.Label(c, text=tr("wiz.step.prepare"))
        self.w_step.grid(row=0, column=0, sticky="w", pady=(10, 6))
        self.w_bar = ttk.Progressbar(c, mode="determinate", maximum=100)
        self.w_bar.grid(row=1, column=0, sticky="ew")
        self.w_file = ttk.Label(c, text="", style="Hint.TLabel")
        self.w_file.grid(row=2, column=0, sticky="w", pady=(6, 0))

    def _page_done(self) -> None:
        c = self._banner_page()
        ttk.Label(c, text=tr("wiz.done.title"), style="Big.TLabel", wraplength=self._wrap(),
                  justify="left").grid(row=0, column=0, sticky="w")
        ttk.Label(c, text=tr("wiz.done.body", version=__version__), style="White.TLabel",
                  wraplength=self._wrap(), justify="left").grid(row=1, column=0, sticky="w", pady=(16, 0))
        ttk.Checkbutton(c, text=tr("wiz.done.launch"), variable=self.v_launch, style="White.TCheckbutton"
                        ).grid(row=2, column=0, sticky="w", pady=(22, 0))

    # ------------------------------------------------------------ navigation
    def next(self) -> None:
        name = PAGES[self.page]
        if name == "dir" and not self._validate_dir():
            return
        if name == "ready":
            self._start()
            return
        if name == "done":
            self._finish()
            return
        if name != "progress":
            self.show(self.page + 1)

    def back(self) -> None:
        if PAGES[self.page] in ("dir", "options", "ready"):
            self.show(self.page - 1)

    def cancel(self) -> None:
        name = PAGES[self.page]
        if name == "progress":
            return
        if name == "done":
            self._finish()
            return
        if messagebox.askyesno(tr("wiz.title"), tr("wiz.cancel_confirm"), icon="warning", parent=self.root):
            self.root.destroy()

    def _validate_dir(self) -> bool:
        p = self.v_dir.get().strip().strip('"')
        drive = os.path.splitdrive(p)[0]
        if not p or not os.path.isabs(p) or not drive or not os.path.exists(drive + "\\") or os.path.isfile(p):
            messagebox.showerror(tr("wiz.title"), tr("wiz.dir.invalid"), parent=self.root)
            return False
        p = os.path.normpath(p)
        self.v_dir.set(p)
        if os.path.isdir(p) and os.listdir(p) and ws.read_manifest(p) is None:
            if not messagebox.askyesno(tr("wiz.title"), tr("wiz.dir.nonempty", path=p), icon="warning",
                                       parent=self.root):
                return False
        if not ws.check_writable(p):
            messagebox.showerror(tr("wiz.title"), tr("wiz.dir.noperm"), parent=self.root)
            return False
        free = ws.free_space(p)
        if free is not None and free < self.size * 1.1:
            messagebox.showerror(tr("wiz.title"), tr("wiz.dir.nospace"), parent=self.root)
            return False
        return True

    def _options(self) -> ws.InstallOptions:
        return ws.InstallOptions(desktop=self.v_desktop.get(), start_menu=self.v_start.get(),
                                 associate=self.v_assoc.get(), add_path=self.v_path.get(),
                                 language=get_language(), version=__version__,
                                 shortcut_desc=tr("shortcut.desc"), assoc_desc=tr("assoc.desc"))

    def _start(self) -> None:
        self.show(PAGES.index("progress"))
        folder = self.v_dir.get()
        opts = self._options()

        def work():
            try:
                m = ws.install(self.payload, folder, opts, self.target,
                               progress=lambda label, frac: self._q.put(("p", label, frac)))
                self._q.put(("ok", m, 1.0))
            except ws.InstallError as exc:
                self._q.put(("err", exc, 0.0))
            except Exception as exc:     # shown to the user
                self._q.put(("err", ws.InstallError("io", f"{type(exc).__name__}: {exc}"), 0.0))

        threading.Thread(target=work, name="install", daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, data, frac = self._q.get_nowait()
                if kind == "p":
                    self._progress(data, frac)
                elif kind == "ok":
                    self.manifest = data
                    self.show(PAGES.index("done"))
                else:
                    messagebox.showerror(tr("wiz.title"), error_text(data), parent=self.root)
                    self.show(PAGES.index("ready"))
        except queue.Empty:
            pass
        self.root.after(60, self._poll)

    def _progress(self, label: str, frac: float) -> None:
        try:
            self.w_bar.configure(value=frac * 100)
            if label.startswith("extract:"):
                self.w_step.configure(text=tr("wiz.step.extract", name="").rstrip(": "))
                self.w_file.configure(text=label[8:])
            else:
                self.w_step.configure(text=tr("wiz.step." + label))
                self.w_file.configure(text="")
        except tk.TclError:
            pass

    def _finish(self) -> None:
        if self.manifest and self.v_launch.get():
            try:
                ws.launch(self.manifest["install_dir"])
            except OSError:
                pass
        self.root.destroy()


def tkfontdefault():
    import tkinter.font as tkfont
    return tkfont.nametofont("TkDefaultFont")


def silent(flags: Dict[str, str], payload: str, target: ws.Target) -> int:
    existing = ws.find_existing(target)
    folder = flags.get("D") or (existing[0] if existing else ws.default_install_dir())
    no_sc = "NOSHORTCUTS" in flags
    opts = ws.InstallOptions(desktop=not (no_sc or "NODESKTOP" in flags),
                             start_menu=not (no_sc or "NOSTARTMENU" in flags),
                             associate="NOASSOC" not in flags, add_path="PATH" in flags,
                             language=get_language(), version=__version__,
                             shortcut_desc=tr("shortcut.desc"), assoc_desc=tr("assoc.desc"))
    log = flags.get("LOG")
    try:
        ws.install(payload, folder, opts, target)
        code, msg = 0, f"installed vertSheet {__version__} in {folder}"
    except ws.InstallError as exc:
        code, msg = 1, f"error ({exc.code}): {exc.detail}"
    if log:
        try:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(msg + "\n")
        except OSError:
            pass
    return code


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    flags = parse_flags(argv)
    set_language(flags.get("LANG", "").lower() or detect_language())
    payload = find_payload(flags)
    target = target_from(flags)
    if "S" in flags:
        return silent(flags, payload, target) if payload else 2
    _dpi_aware()
    root = tk.Tk()
    if not payload or not os.path.isfile(payload):
        root.withdraw()
        messagebox.showerror(tr("wiz.title"), tr("wiz.error", msg="payload.zip not found"))
        root.destroy()
        return 2
    Wizard(root, payload, target, flags)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
