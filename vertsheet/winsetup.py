"""Windows installation support (used by the setup wizard and the uninstaller).

vertSheet is installed per user, without administrator rights:

* program files in ``%LOCALAPPDATA%\\Programs\\vertSheet`` (or a chosen folder)
* optional shortcuts on the desktop and in the Start menu
* optional "Open with" entries for .mscz / .mscx / .musicxml / .mxl
  (the user's default apps are never changed)
* optional folder on the user PATH for the ``vertsheet-cli`` command
* an entry in Settings > Apps > Installed apps, whose "Uninstall" button runs
  ``vertSheet.exe --uninstall``

Everything that is created is recorded in ``install.json`` inside the
installation folder; the uninstaller removes exactly those files, shortcuts
and registry entries and nothing else.

Registry keys can be redirected under a prefix and shortcut folders can be
overridden (:class:`Target`) so that the whole cycle can be tested without
touching the real system.
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

APP_NAME = "vertSheet"
PUBLISHER = "vertSheet"
PROGID = "vertSheet.Score"
ASSOC_EXTS = (".mscz", ".mscx", ".musicxml", ".mxl")
GUI_EXE = "vertSheet.exe"
CLI_EXE = "vertsheet-cli.exe"
MANIFEST = "install.json"
UNINSTALL_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\vertSheet"
CLASSES_SUBKEY = r"Software\Classes"
ENV_SUBKEY = "Environment"
SHORTCUT_NAME = "vertSheet.lnk"
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"
FOLDERID_PROGRAMS = "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}"
FOLDERID_USER_PROGRAM_FILES = "{5CD7AEE2-2219-4A67-B85D-6C9CE15660CB}"

ProgressFn = Callable[[str, float], None]


class InstallError(Exception):
    """An installation problem; ``code`` selects the message shown to the user."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------- locations

def known_folder(guid: str) -> Optional[str]:
    """Path of a Windows known folder (follows OneDrive / folder redirection)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        u = uuid.UUID(guid)
        g = GUID(u.fields[0], u.fields[1], u.fields[2],
                 (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))
        out = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD,
                                                 wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
        if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) != 0:
            return None
        path = out.value
        ctypes.windll.ole32.CoTaskMemFree(out)
        return path
    except (AttributeError, OSError, ValueError):
        return None


def default_install_dir() -> str:
    base = known_folder(FOLDERID_USER_PROGRAM_FILES)
    if not base:
        base = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Programs")
    return os.path.join(base, APP_NAME)


@dataclass
class Target:
    """Where an installation writes outside its own folder."""
    reg_prefix: str = ""                 # e.g. "Software\\vertSheet-selftest\\" for tests
    desktop: Optional[str] = None
    programs: Optional[str] = None
    notify: bool = True                  # tell Explorer about association / PATH changes

    def key(self, sub: str) -> str:
        return self.reg_prefix + sub

    def desktop_dir(self) -> str:
        return self.desktop or known_folder(FOLDERID_DESKTOP) or \
            os.path.join(os.path.expanduser("~"), "Desktop")

    def programs_dir(self) -> str:
        return self.programs or known_folder(FOLDERID_PROGRAMS) or \
            os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")


@dataclass
class InstallOptions:
    desktop: bool = True
    start_menu: bool = True
    associate: bool = True
    add_path: bool = False
    language: str = "en"
    version: str = "1.0.0"
    shortcut_desc: str = "Piano sheet music, read vertically"
    assoc_desc: str = "Score (vertSheet)"


# ---------------------------------------------------------------- registry

def _winreg():
    import winreg
    return winreg


def reg_set(path: str, name: str, value, kind: Optional[int] = None) -> None:
    wr = _winreg()
    if kind is None:
        kind = wr.REG_DWORD if isinstance(value, int) else wr.REG_SZ
    with wr.CreateKeyEx(wr.HKEY_CURRENT_USER, path, 0, wr.KEY_WRITE) as k:
        wr.SetValueEx(k, name, 0, kind, value)


def reg_get(path: str, name: str) -> Tuple[Optional[object], Optional[int]]:
    wr = _winreg()
    try:
        with wr.OpenKey(wr.HKEY_CURRENT_USER, path, 0, wr.KEY_READ) as k:
            value, kind = wr.QueryValueEx(k, name)
            return value, kind
    except OSError:
        return None, None


def reg_delete_value(path: str, name: str) -> None:
    wr = _winreg()
    try:
        with wr.OpenKey(wr.HKEY_CURRENT_USER, path, 0, wr.KEY_WRITE) as k:
            wr.DeleteValue(k, name)
    except OSError:
        pass


def reg_delete_tree(path: str) -> None:
    wr = _winreg()
    try:
        with wr.OpenKey(wr.HKEY_CURRENT_USER, path, 0, wr.KEY_READ) as k:
            subs = []
            i = 0
            while True:
                try:
                    subs.append(wr.EnumKey(k, i))
                except OSError:
                    break
                i += 1
    except OSError:
        return
    for s in subs:
        reg_delete_tree(path + "\\" + s)
    try:
        wr.DeleteKey(wr.HKEY_CURRENT_USER, path)
    except OSError:
        pass


def reg_key_exists(path: str) -> bool:
    wr = _winreg()
    try:
        with wr.OpenKey(wr.HKEY_CURRENT_USER, path, 0, wr.KEY_READ):
            return True
    except OSError:
        return False


def _notify_assoc() -> None:
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)   # SHCNE_ASSOCCHANGED
    except (AttributeError, OSError):
        pass


def _notify_env() -> None:
    try:
        import ctypes
        result = ctypes.c_size_t()
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 2000,
                                                 ctypes.byref(result))
    except (AttributeError, OSError):
        pass


def find_existing(target: Optional[Target] = None) -> Optional[Tuple[str, str]]:
    """(install folder, version) of an installed vertSheet, if any."""
    target = target or Target()
    if sys.platform != "win32":
        return None
    loc, _ = reg_get(target.key(UNINSTALL_SUBKEY), "InstallLocation")
    ver, _ = reg_get(target.key(UNINSTALL_SUBKEY), "DisplayVersion")
    if isinstance(loc, str) and loc and os.path.isfile(os.path.join(loc, MANIFEST)):
        return loc, ver if isinstance(ver, str) else ""
    return None


def register_uninstall(target: Target, install_dir: str, version: str, size_kb: int) -> None:
    exe = os.path.join(install_dir, GUI_EXE)
    key = target.key(UNINSTALL_SUBKEY)
    reg_set(key, "DisplayName", APP_NAME)
    reg_set(key, "DisplayVersion", version)
    reg_set(key, "Publisher", PUBLISHER)
    reg_set(key, "InstallLocation", install_dir)
    reg_set(key, "DisplayIcon", f'"{exe}",0')
    reg_set(key, "UninstallString", f'"{exe}" --uninstall')
    reg_set(key, "QuietUninstallString", f'"{exe}" --uninstall --quiet')
    reg_set(key, "NoModify", 1)
    reg_set(key, "NoRepair", 1)
    reg_set(key, "EstimatedSize", max(1, int(size_kb)))
    reg_set(key, "InstallDate", datetime.date.today().strftime("%Y%m%d"))


def register_associations(target: Target, install_dir: str, description: str) -> None:
    exe = os.path.join(install_dir, GUI_EXE)
    classes = target.key(CLASSES_SUBKEY)
    command = f'"{exe}" "%1"'
    reg_set(classes + "\\" + PROGID, "", description)
    reg_set(classes + "\\" + PROGID + r"\DefaultIcon", "", f'"{exe}",0')
    reg_set(classes + "\\" + PROGID + r"\shell\open\command", "", command)
    app = classes + "\\Applications\\" + GUI_EXE
    reg_set(app, "FriendlyAppName", APP_NAME)
    reg_set(app + r"\shell\open\command", "", command)
    for ext in ASSOC_EXTS:
        reg_set(classes + "\\" + ext + r"\OpenWithProgids", PROGID, "")
        reg_set(app + r"\SupportedTypes", ext, "")
    if target.notify:
        _notify_assoc()


def unregister_associations(target: Target) -> None:
    classes = target.key(CLASSES_SUBKEY)
    reg_delete_tree(classes + "\\" + PROGID)
    reg_delete_tree(classes + "\\Applications\\" + GUI_EXE)
    for ext in ASSOC_EXTS:
        reg_delete_value(classes + "\\" + ext + r"\OpenWithProgids", PROGID)
    if target.notify:
        _notify_assoc()


def _same_dir(a: str, b: str) -> bool:
    return os.path.normcase(os.path.expandvars(a)).rstrip("\\/") == os.path.normcase(b).rstrip("\\/")


def add_to_path(target: Target, folder: str) -> bool:
    key = target.key(ENV_SUBKEY)
    cur, kind = reg_get(key, "Path")
    parts = [p for p in (cur or "").split(";") if p] if isinstance(cur, str) else []
    if any(_same_dir(p, folder) for p in parts):
        return False
    parts.append(folder)
    reg_set(key, "Path", ";".join(parts), kind if kind is not None else _winreg().REG_EXPAND_SZ)
    if target.notify:
        _notify_env()
    return True


def remove_from_path(target: Target, folder: str) -> None:
    key = target.key(ENV_SUBKEY)
    cur, kind = reg_get(key, "Path")
    if not isinstance(cur, str):
        return
    parts = [p for p in cur.split(";") if p]
    keep = [p for p in parts if not _same_dir(p, folder)]
    if len(keep) == len(parts):
        return
    if keep:
        reg_set(key, "Path", ";".join(keep), kind if kind is not None else _winreg().REG_EXPAND_SZ)
    else:
        reg_delete_value(key, "Path")
    if target.notify:
        _notify_env()


# ---------------------------------------------------------------- shortcuts

def _powershell() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    exe = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return exe if os.path.isfile(exe) else (shutil.which("powershell") or "powershell")


def create_shortcut(lnk: str, target_exe: str, workdir: str, description: str = "",
                    arguments: str = "") -> None:
    os.makedirs(os.path.dirname(lnk), exist_ok=True)
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:VS_LNK);"
              "$s.TargetPath = $env:VS_TARGET; $s.WorkingDirectory = $env:VS_DIR;"
              "$s.IconLocation = $env:VS_ICON; $s.Description = $env:VS_DESC;"
              "$s.Arguments = $env:VS_ARGS; $s.Save()")
    env = dict(os.environ, VS_LNK=lnk, VS_TARGET=target_exe, VS_DIR=workdir,
               VS_ICON=target_exe + ",0", VS_DESC=description, VS_ARGS=arguments)
    try:
        proc = subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                               "Bypass", "-Command", script], env=env, capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=90, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallError("shortcut", str(exc)) from None
    if proc.returncode != 0 or not os.path.isfile(lnk):
        err = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()
        raise InstallError("shortcut", f"{lnk}: {err[-300:]}")


# ---------------------------------------------------------------- manifest

def read_manifest(install_dir: Optional[str]) -> Optional[dict]:
    if not install_dir:
        return None
    try:
        with open(os.path.join(install_dir, MANIFEST), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("app") != APP_NAME:
        return None
    return data


def _write_manifest(install_dir: str, data: dict) -> None:
    with open(os.path.join(install_dir, MANIFEST), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def _inside(base: str, path: str) -> bool:
    base = os.path.normcase(os.path.abspath(base))
    path = os.path.normcase(os.path.abspath(path))
    try:
        return os.path.commonpath([base, path]) == base and path != base
    except ValueError:
        return False


def _manifest_paths(install_dir: str, manifest: dict) -> List[str]:
    out = []
    for rel in manifest.get("files", []):
        if not isinstance(rel, str):
            continue
        p = os.path.normpath(os.path.join(install_dir, rel))
        if _inside(install_dir, p):
            out.append(p)
    return out


# ---------------------------------------------------------------- install

def payload_size(payload: str) -> int:
    with zipfile.ZipFile(payload) as z:
        return sum(i.file_size for i in z.infolist() if not i.is_dir())


def check_writable(folder: str) -> bool:
    probe_dir = folder
    while probe_dir and not os.path.isdir(probe_dir):
        parent = os.path.dirname(probe_dir)
        if parent == probe_dir:
            return False
        probe_dir = parent
    try:
        fd, tmp = tempfile.mkstemp(prefix=".vs-probe-", dir=probe_dir)
        os.close(fd)
        os.remove(tmp)
        return True
    except OSError:
        return False


def free_space(folder: str) -> Optional[int]:
    probe = folder
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return None
        probe = parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def is_running(install_dir: str) -> bool:
    exe = os.path.join(install_dir, GUI_EXE)
    if not os.path.isfile(exe):
        return False
    try:
        with open(exe, "r+b"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def _remove_integration(manifest: dict, target: Target, install_dir: str) -> None:
    for lnk in manifest.get("shortcuts", []):
        if isinstance(lnk, str) and lnk.lower().endswith(".lnk") and \
                os.path.basename(lnk).lower().startswith(APP_NAME.lower()):
            try:
                os.remove(lnk)
            except OSError:
                pass
    if manifest.get("associations"):
        unregister_associations(target)
    if manifest.get("path"):
        remove_from_path(target, install_dir)
    reg_delete_tree(target.key(UNINSTALL_SUBKEY))


def _remove_files(paths: List[str]) -> List[str]:
    failed = []
    for p in paths:
        try:
            if os.path.isfile(p) or os.path.islink(p):
                os.remove(p)
        except OSError:
            failed.append(p)
    return failed


def _remove_empty_dirs(install_dir: str, paths: List[str], include_root: bool) -> None:
    dirs = set()
    for p in paths:
        d = os.path.dirname(p)
        while _inside(install_dir, d):
            dirs.add(d)
            d = os.path.dirname(d)
    for d in sorted(dirs, key=len, reverse=True):
        try:
            os.rmdir(d)
        except OSError:
            pass
    if include_root:
        try:
            os.rmdir(install_dir)
        except OSError:
            pass


def install(payload: str, install_dir: str, opts: InstallOptions, target: Optional[Target] = None,
            progress: Optional[ProgressFn] = None) -> dict:
    """Install the application contained in the ``payload`` zip. Returns the manifest."""
    target = target or Target()
    report = progress or (lambda label, frac: None)
    install_dir = os.path.abspath(install_dir)
    report("prepare", 0.0)
    if not check_writable(install_dir):
        raise InstallError("noperm", install_dir)
    old = read_manifest(install_dir)
    if old is not None or os.path.isfile(os.path.join(install_dir, GUI_EXE)):
        if is_running(install_dir):
            raise InstallError("running")
    if old is not None:
        report("remove_old", 0.02)
        _remove_integration(old, Target(reg_prefix=old.get("reg_prefix", target.reg_prefix),
                                        desktop=target.desktop, programs=target.programs,
                                        notify=False), install_dir)
        old_paths = _manifest_paths(install_dir, old)
        _remove_files(old_paths)
        _remove_empty_dirs(install_dir, old_paths, include_root=False)
    os.makedirs(install_dir, exist_ok=True)
    extracted: List[str] = []
    try:
        with zipfile.ZipFile(payload) as z:
            members = [i for i in z.infolist() if not i.is_dir()]
            total = sum(i.file_size for i in members) or 1
            done = 0
            for info in members:
                rel = info.filename.replace("\\", "/")
                dest = os.path.normpath(os.path.join(install_dir, rel))
                if not _inside(install_dir, dest) or os.path.isabs(rel) or rel.startswith("/"):
                    raise InstallError("payload", f"unsafe path in payload: {rel}")
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with z.open(info) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                extracted.append(rel)
                done += info.file_size
                report("extract:" + rel, 0.05 + 0.8 * done / total)
    except InstallError:
        _remove_files([os.path.join(install_dir, r) for r in extracted])
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        _remove_files([os.path.join(install_dir, r) for r in extracted])
        raise InstallError("io", str(exc)) from None
    manifest = {
        "app": APP_NAME, "version": opts.version, "install_dir": install_dir,
        "installed": datetime.datetime.now().isoformat(timespec="seconds"),
        "language": opts.language, "reg_prefix": target.reg_prefix,
        "files": extracted, "shortcuts": [], "associations": False, "path": False,
    }
    # the manifest is written early so that a failure below can still be undone
    _write_manifest(install_dir, manifest)
    exe = os.path.join(install_dir, GUI_EXE)
    report("shortcuts", 0.88)
    links = []
    if opts.desktop:
        links.append(os.path.join(target.desktop_dir(), SHORTCUT_NAME))
    if opts.start_menu:
        links.append(os.path.join(target.programs_dir(), SHORTCUT_NAME))
    for lnk in links:
        create_shortcut(lnk, exe, install_dir, opts.shortcut_desc)
        manifest["shortcuts"].append(lnk)
        _write_manifest(install_dir, manifest)
    report("register", 0.95)
    size_kb = sum(os.path.getsize(os.path.join(install_dir, r)) for r in extracted) // 1024
    register_uninstall(target, install_dir, opts.version, size_kb)
    if opts.associate:
        register_associations(target, install_dir, opts.assoc_desc)
        manifest["associations"] = True
    if opts.add_path:
        manifest["path"] = add_to_path(target, install_dir) or manifest["path"]
    _write_manifest(install_dir, manifest)
    report("done", 1.0)
    return manifest


# ---------------------------------------------------------------- uninstall

def running_from(install_dir: str) -> bool:
    """True when this process is the installed program itself."""
    return bool(getattr(sys, "frozen", False)) and _inside(install_dir, os.path.abspath(sys.executable))


def uninstall(install_dir: str, target: Optional[Target] = None, defer_locked: bool = False) -> List[str]:
    """Remove an installation. Returns the files left for later deletion.

    Shortcuts, registry entries and PATH are removed at once. When this
    process *is* the installed program (``vertSheet.exe --uninstall``) and
    ``defer_locked`` is set, none of its files is touched while it runs -
    Python still loads modules from them - and a helper deletes them all once
    the process has ended. Otherwise the files are deleted directly, and any
    that are locked are handed to the helper (with ``defer_locked``)."""
    install_dir = os.path.abspath(install_dir)
    manifest = read_manifest(install_dir)
    if manifest is None:
        raise InstallError("notfound", install_dir)
    if target is None:
        target = Target(reg_prefix=manifest.get("reg_prefix", ""))
    _remove_integration(manifest, target, install_dir)
    paths = _manifest_paths(install_dir, manifest) + [os.path.join(install_dir, MANIFEST)]
    if defer_locked and running_from(install_dir):
        _delete_after_exit(paths, install_dir, paths)
        return paths
    failed = _remove_files(paths)
    _remove_empty_dirs(install_dir, paths, include_root=not failed)
    if failed and defer_locked:
        _delete_after_exit(failed, install_dir, paths)
    return failed


def _delete_after_exit(files: List[str], install_dir: str, all_paths: List[str]) -> None:
    """Files of this very process (the running exe and its libraries) are
    removed by a hidden PowerShell helper that waits for the process to end."""
    dirs = set()
    for p in all_paths:
        d = os.path.dirname(p)
        while _inside(install_dir, d):
            dirs.add(d)
            d = os.path.dirname(d)
    dirs_sorted = sorted(dirs, key=len, reverse=True) + [install_dir]
    tmp = tempfile.mkdtemp(prefix="vertsheet-uninstall-")
    files_list = os.path.join(tmp, "files.txt")
    dirs_list = os.path.join(tmp, "dirs.txt")
    with open(files_list, "w", encoding="utf-8") as fh:
        fh.write("\n".join(files))
    with open(dirs_list, "w", encoding="utf-8") as fh:
        fh.write("\n".join(dirs_sorted))
    script = os.path.join(tmp, "cleanup.ps1")
    with open(script, "w", encoding="utf-8-sig") as fh:
        fh.write(
            "param([int]$ProcessId, [string]$Files, [string]$Dirs)\n"
            "$ErrorActionPreference = 'SilentlyContinue'\n"
            "Wait-Process -Id $ProcessId\n"
            "Start-Sleep -Milliseconds 300\n"
            "$list = [System.IO.File]::ReadAllLines($Files)\n"
            "for ($i = 0; $i -lt 30; $i++) {\n"
            "  $left = New-Object System.Collections.Generic.List[string]\n"
            "  foreach ($f in $list) {\n"
            "    if ($f -and [System.IO.File]::Exists($f)) {\n"
            "      try { [System.IO.File]::Delete($f) } catch { $left.Add($f) }\n"
            "    }\n"
            "  }\n"
            "  if ($left.Count -eq 0) { break }\n"
            "  $list = $left\n"
            "  Start-Sleep -Milliseconds 500\n"
            "}\n"
            "$folders = [System.IO.File]::ReadAllLines($Dirs)\n"
            "for ($k = 0; $k -lt 12; $k++) {\n"
            "  $pending = 0\n"
            "  foreach ($d in $folders) {\n"
            "    if ($d -and [System.IO.Directory]::Exists($d)) {\n"
            "      try {\n"
            "        if ([System.IO.Directory]::GetFileSystemEntries($d).Length -eq 0) {\n"
            "          [System.IO.Directory]::Delete($d)\n"
            "        }\n"
            "      } catch {}\n"
            "      if ([System.IO.Directory]::Exists($d)) { $pending++ }\n"
            "    }\n"
            "  }\n"
            "  if ($pending -eq 0) { break }\n"
            "  Start-Sleep -Milliseconds 500\n"
            "}\n"
            "Remove-Item -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path) -Recurse -Force\n")
    spawn_detached([_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-WindowStyle", "Hidden", "-File", script, "-ProcessId", str(os.getpid()),
                    "-Files", files_list, "-Dirs", dirs_list], cwd=tempfile.gettempdir())


def remove_user_settings() -> None:
    base = os.environ.get("APPDATA")
    if not base:
        return
    folder = os.path.join(base, APP_NAME)
    for name in ("settings.json", "settings.json.tmp"):
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass
    try:
        os.rmdir(folder)
    except OSError:
        pass


def run_uninstaller(argv: List[str]) -> int:
    """Entry point of ``vertSheet.exe --uninstall [--quiet] [--dir FOLDER]``."""
    from .i18n import set_language, tr
    quiet = "--quiet" in argv or "/quiet" in argv
    install_dir = None
    if "--dir" in argv and argv.index("--dir") + 1 < len(argv):
        install_dir = argv[argv.index("--dir") + 1]
    elif getattr(sys, "frozen", False):
        install_dir = os.path.dirname(os.path.abspath(sys.executable))
    manifest = read_manifest(install_dir)
    if manifest and manifest.get("language"):
        set_language(manifest["language"])
    messagebox = None
    root = None
    if not quiet:
        import tkinter as tk
        from tkinter import messagebox as mb
        messagebox = mb
        root = tk.Tk()
        root.withdraw()
        ico = os.path.join(getattr(sys, "_MEIPASS", ""), "assets", "vertsheet.ico")
        if os.path.isfile(ico):
            try:
                root.iconbitmap(default=ico)
            except tk.TclError:
                pass
    try:
        if manifest is None:
            if messagebox:
                messagebox.showerror(tr("uni.title"), tr("uni.notfound"), parent=root)
            return 1
        if messagebox and not messagebox.askyesno(
                tr("uni.title"), tr("uni.confirm", version=manifest.get("version", "")),
                icon="warning", parent=root):
            return 0
        try:
            uninstall(install_dir, defer_locked=True)
            if not manifest.get("reg_prefix"):      # not for sandboxed test installs
                remove_user_settings()
        except (InstallError, OSError) as exc:
            _log_error("uninstall")
            if messagebox:
                messagebox.showerror(tr("uni.title"), tr("uni.error", msg=str(exc)), parent=root)
            return 1
        if messagebox:
            messagebox.showinfo(tr("uni.title"), tr("uni.done"), parent=root)
        return 0
    finally:
        if root is not None:
            root.destroy()


def _log_error(what: str) -> None:
    """Append the current exception to %TEMP%\\vertsheet-setup.log (for support)."""
    import traceback
    try:
        with open(os.path.join(tempfile.gettempdir(), "vertsheet-setup.log"), "a", encoding="utf-8") as fh:
            fh.write(f"--- {datetime.datetime.now().isoformat(timespec='seconds')} {what}\n")
            fh.write(traceback.format_exc())
    except Exception:
        pass


def clean_child_env() -> Dict[str, str]:
    """Environment for starting the installed app from the (frozen) wizard."""
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("_PYI", "_MEI")) and k.upper() not in ("TCL_LIBRARY", "TK_LIBRARY")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def spawn_detached(cmd: List[str], cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> None:
    """Start a process that outlives this one.

    The standard handles are redirected (a windowed program has none, which
    makes Popen fail); console programs get their own hidden console
    (CREATE_NO_WINDOW - with DETACHED_PROCESS PowerShell exits at once), and
    the child breaks away from this process's job object when allowed, so it
    is not terminated together with its parent."""
    kw = dict(cwd=cwd, env=env, close_fds=True, stdin=subprocess.DEVNULL,
              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(cmd, creationflags=base | CREATE_BREAKAWAY_FROM_JOB, **kw)
    except OSError:
        subprocess.Popen(cmd, creationflags=base, **kw)


def launch(install_dir: str) -> None:
    exe = os.path.join(install_dir, GUI_EXE)
    spawn_detached([exe], cwd=install_dir, env=clean_child_env())


__all__ = ["InstallError", "InstallOptions", "Target", "default_install_dir", "find_existing",
           "install", "uninstall", "run_uninstaller", "payload_size", "check_writable",
           "free_space", "launch", "spawn_detached"]
