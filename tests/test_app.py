"""Tests of the graphical interface, the translations and the Windows installer."""
from __future__ import annotations

import json
import os
import re
import string
import sys
import tempfile
import time
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "examples"))

from vertsheet import Options, render_pages  # noqa: E402
from vertsheet import i18n  # noqa: E402
import make_demo  # noqa: E402

DEMO = os.path.join(ROOT, "examples", "demo.musicxml")


def ensure_demo():
    if not os.path.isfile(DEMO):
        make_demo.main()


def fields(text: str):
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


class I18nTests(unittest.TestCase):
    def test_same_keys_and_placeholders(self):
        self.assertEqual(set(i18n.IT), set(i18n.EN))
        for key in i18n.IT:
            self.assertEqual(fields(i18n.IT[key]), fields(i18n.EN[key]), key)

    def test_switch_language(self):
        old = i18n.get_language()
        try:
            i18n.set_language("it")
            self.assertEqual(i18n.tr("tb.page", i=2, n=5), "Pagina 2 di 5")
            i18n.set_language("en")
            self.assertEqual(i18n.tr("tb.page", i=2, n=5), "Page 2 of 5")
            self.assertEqual(i18n.set_language("xx"), "en")
        finally:
            i18n.set_language(old)

    def test_hand_labels_option(self):
        ensure_demo()
        pages, _, _ = render_pages(DEMO, Options(hand_labels=("M.S.", "M.D.")))
        texts = [op[3] for op in pages[0].ops if op[0] == "text"]
        self.assertIn("M.S.", texts)
        self.assertIn("M.D.", texts)
        self.assertNotIn("L.H.", texts)


def _tk_root():
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        return root
    except Exception:          # no display
        return None


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_demo()
        cls.root = _tk_root()
        if cls.root is None:
            raise unittest.SkipTest("no display for Tk")

    @classmethod
    def tearDownClass(cls):
        if cls.root is not None:
            cls.root.destroy()

    def make_app(self, lang="en"):
        from vertsheet import gui
        settings = json.loads(json.dumps(gui.DEFAULTS))
        settings["language"] = lang
        if getattr(self, "app", None) is not None:
            self.app.dispose()
        for w in self.root.winfo_children():
            w.destroy()
        self.app = gui.App(self.root, settings=settings, persist=False)
        return self.app

    def tearDown(self):
        if getattr(self, "app", None) is not None:
            self.app.dispose()
            self.app = None
        i18n.set_language("en")

    def wait(self, app, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            self.root.update()
            if not app.busy and app._pending is None and (app.pages or app.error):
                return
            time.sleep(0.02)
        self.fail("rendering did not finish")

    def test_open_preview_navigate_export(self):
        app = self.make_app("en")
        self.assertFalse(app.pages)
        app.open_file(DEMO)
        self.wait(app)
        self.assertIsNone(app.error)
        self.assertEqual(len(app.pages), 2)
        self.root.update()
        app.redraw()
        self.assertGreater(len(app.canvas.find_all()), 300)
        self.assertEqual(app.w_page.cget("text"), "Page 1 of 2")
        app.next_page()
        self.assertEqual(app.page_index, 1)
        app.next_page()                      # stays on the last page
        self.assertEqual(app.page_index, 1)
        app.prev_page()
        self.assertEqual(app.page_index, 0)
        app.set_zoom(2.0)
        self.assertAlmostEqual(app.scale / app.px_per_pt, 2.0, places=3)
        app.zoom_by(-1)
        self.assertLess(app.scale / app.px_per_pt, 2.0)
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "demo_vertical.pdf")
            app.export_to(out)
            self.assertTrue(os.path.getsize(out) > 1000)
            self.assertEqual(app.last_output, [out])

    def test_option_change_rerenders(self):
        app = self.make_app("en")
        app.open_file(DEMO)
        self.wait(app)
        cols_3 = len(app.layout.columns)
        pages_3 = len(app.pages)
        app.v["pairs"].set(1)
        self.root.update()
        app._start_render()              # skip the debounce delay
        self.wait(app)
        self.assertEqual(len(app.layout.columns), cols_3)
        self.assertGreater(len(app.pages), pages_3)

    def test_language_switch_and_labels(self):
        app = self.make_app("en")
        app.open_file(DEMO)
        self.wait(app)
        app.set_ui_language("it")
        app._start_render()
        self.wait(app)
        self.assertEqual(app.w_page.cget("text"), "Pagina 1 di 2")
        texts = [op[3] for op in app.pages[0].ops if op[0] == "text"]
        self.assertIn("M.S.", texts)
        i18n.set_language("en")

    def test_invalid_split_pitch_is_reported(self):
        app = self.make_app("en")
        app.open_file(DEMO)
        self.wait(app)
        app.v["hands"].set("split")
        app.v["split"].set("H9")
        app._start_render()
        self.root.update()
        self.assertIn("H9", app.w_status.cget("text"))

    def test_unreadable_file_shows_error(self):
        app = self.make_app("en")
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "broken.musicxml")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("<not-music/>")
            app.input_path = bad
            app._start_render(force=True)
            self.wait(app)
        self.assertTrue(app.error)
        self.assertFalse(app.pages)


@unittest.skipUnless(sys.platform == "win32", "Windows installer")
class WinSetupTests(unittest.TestCase):
    def setUp(self):
        from vertsheet import winsetup as ws
        self.ws = ws
        self.tmp = tempfile.TemporaryDirectory()
        base = self.tmp.name
        self.prefix = f"Software\\vertSheet-unittest-{os.getpid()}\\"
        self.target = ws.Target(reg_prefix=self.prefix, desktop=os.path.join(base, "Desktop"),
                                programs=os.path.join(base, "Programs"), notify=False)
        self.app = os.path.join(base, "Programs files", "vertSheet")

    def tearDown(self):
        self.ws.reg_delete_tree(self.prefix.rstrip("\\"))
        self.tmp.cleanup()

    def payload(self, name, files):
        p = os.path.join(self.tmp.name, name)
        with zipfile.ZipFile(p, "w") as z:
            for rel, data in files.items():
                z.writestr(rel, data)
        return p

    def test_install_upgrade_uninstall(self):
        ws = self.ws
        v1 = self.payload("v1.zip", {"vertSheet.exe": b"MZ1", "vertsheet-cli.exe": b"MZ",
                                     "_internal/old.dll": b"x", "_internal/data/a.txt": b"a"})
        steps = []
        m = ws.install(v1, self.app, ws.InstallOptions(add_path=True, version="1.0.0", language="it"),
                       self.target, progress=lambda label, frac: steps.append((label, frac)))
        self.assertEqual(steps[-1], ("done", 1.0))
        self.assertTrue(all(0 <= f <= 1 for _, f in steps))
        for rel in ("vertSheet.exe", "_internal/old.dll", "_internal/data/a.txt", "install.json"):
            self.assertTrue(os.path.isfile(os.path.join(self.app, rel)), rel)
        for lnk in m["shortcuts"]:
            self.assertTrue(os.path.isfile(lnk), lnk)
        self.assertEqual(len(m["shortcuts"]), 2)
        key = self.prefix + ws.UNINSTALL_SUBKEY
        self.assertEqual(ws.reg_get(key, "DisplayVersion")[0], "1.0.0")
        self.assertIn("--uninstall", ws.reg_get(key, "UninstallString")[0])
        self.assertTrue(ws.reg_key_exists(self.prefix + ws.CLASSES_SUBKEY + "\\.mscz\\OpenWithProgids"))
        self.assertIn(self.app.lower(), (ws.reg_get(self.prefix + "Environment", "Path")[0] or "").lower())
        self.assertEqual(ws.find_existing(self.target), (self.app, "1.0.0"))
        # upgrade: files of the old version that are gone in the new one are removed,
        # options that are switched off are undone
        v2 = self.payload("v2.zip", {"vertSheet.exe": b"MZ2", "vertsheet-cli.exe": b"MZ",
                                     "_internal/new.dll": b"y"})
        m2 = ws.install(v2, self.app, ws.InstallOptions(desktop=False, associate=False, version="1.1.0"),
                        self.target)
        self.assertFalse(os.path.exists(os.path.join(self.app, "_internal", "old.dll")))
        self.assertFalse(os.path.exists(os.path.join(self.app, "_internal", "data")))
        self.assertTrue(os.path.isfile(os.path.join(self.app, "_internal", "new.dll")))
        self.assertEqual(len(m2["shortcuts"]), 1)
        self.assertFalse(os.path.exists(os.path.join(self.target.desktop, "vertSheet.lnk")))
        self.assertFalse(ws.reg_key_exists(self.prefix + ws.CLASSES_SUBKEY + "\\" + ws.PROGID))
        self.assertNotIn(self.app.lower(), (ws.reg_get(self.prefix + "Environment", "Path")[0] or "").lower())
        self.assertEqual(ws.find_existing(self.target)[1], "1.1.0")
        # a file the user put in the folder survives the uninstall
        keep = os.path.join(self.app, "my score.mscz")
        with open(keep, "w") as fh:
            fh.write("mine")
        left = ws.uninstall(self.app, self.target)
        self.assertEqual(left, [])
        self.assertTrue(os.path.isfile(keep))
        self.assertEqual(os.listdir(self.app), ["my score.mscz"])
        self.assertFalse(os.path.exists(os.path.join(self.target.programs, "vertSheet.lnk")))
        self.assertFalse(ws.reg_key_exists(key))
        self.assertIsNone(ws.find_existing(self.target))

    def test_rejects_unsafe_payload(self):
        ws = self.ws
        bad = self.payload("bad.zip", {"vertSheet.exe": b"MZ", "../escape.txt": b"!"})
        with self.assertRaises(ws.InstallError):
            ws.install(bad, self.app, ws.InstallOptions(desktop=False, start_menu=False, associate=False),
                       self.target)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.app), "escape.txt")))

    def test_cleanup_helper_after_exit(self):
        """The PowerShell helper used by a self-uninstall deletes the listed
        files and the folders left empty, and nothing else."""
        import subprocess
        ws = self.ws
        base = os.path.join(self.tmp.name, "app")
        paths = []
        for sub in ("_internal/tcl8/8.5", "_internal/data", ""):
            d = os.path.join(base, *sub.split("/")) if sub else base
            os.makedirs(d, exist_ok=True)
            for k in range(3):
                p = os.path.join(d, f"file{k}.bin")
                with open(p, "wb") as fh:
                    fh.write(b"x")
                paths.append(p)
        user = os.path.join(base, "my scores")
        os.makedirs(user)
        with open(os.path.join(user, "song.mscz"), "w") as fh:
            fh.write("mine")
        captured = {}
        real = ws.spawn_detached
        ws.spawn_detached = lambda cmd, cwd=None, env=None: captured.setdefault("cmd", cmd)
        try:
            ws._delete_after_exit(paths, base, paths)
        finally:
            ws.spawn_detached = real
        cmd = captured["cmd"]
        cmd[cmd.index("-ProcessId") + 1] = "999999"      # a process that has already ended
        subprocess.run(cmd, capture_output=True, timeout=120)
        left = sorted(os.path.relpath(os.path.join(r, f), base) for r, _, fs in os.walk(base) for f in fs)
        self.assertEqual(left, [os.path.join("my scores", "song.mscz")])
        self.assertFalse(os.path.exists(os.path.join(base, "_internal")))
        self.assertFalse(os.path.exists(os.path.dirname(cmd[cmd.index("-File") + 1])))

    def test_uninstall_without_manifest(self):
        os.makedirs(self.app)
        with self.assertRaises(self.ws.InstallError):
            self.ws.uninstall(self.app, self.target)

    def test_known_folders(self):
        self.assertTrue(os.path.isdir(self.ws.known_folder(self.ws.FOLDERID_DESKTOP) or ""))
        self.assertTrue(self.ws.default_install_dir().lower().endswith("vertsheet"))


@unittest.skipUnless(sys.platform == "win32", "Windows installer")
class WizardTests(unittest.TestCase):
    def test_flags(self):
        sys.path.insert(0, os.path.join(ROOT, "installer"))
        import wizard
        flags = wizard.parse_flags(["/S", "/D=C:\\Apps\\vertSheet", "/NOASSOC", "/lang=it"])
        self.assertEqual(flags["D"], "C:\\Apps\\vertSheet")
        self.assertIn("S", flags)
        self.assertIn("NOASSOC", flags)
        self.assertEqual(flags["LANG"], "it")
        self.assertEqual(wizard.human_size(1536), "2 KB")
        self.assertTrue(re.match(r"\d+\.\d MB", wizard.human_size(5 * 1024 * 1024)))


if __name__ == "__main__":
    unittest.main()
