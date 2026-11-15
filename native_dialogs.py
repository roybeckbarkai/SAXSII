"""Native OS open/folder dialogs, shown by the (local) server on the user's desktop.

The browser cannot hand a web page real file paths, but the server runs on the same
computer, so it can open the system's own dialogs:

  Windows  common file dialog via tkinter (process made DPI-aware so text is sharp)
  macOS    AppleScript `choose file` / `choose folder` via osascript (Tk must not be used
           off the main thread on macOS, and the server calls this from request threads)
  Linux    zenity (GNOME/GTK) or kdialog (KDE); tkinter as a last resort

DialogUnavailable means no dialog could be shown (no desktop session, nothing installed);
the frontend then falls back to its in-page file browser.
"""
import shutil
import subprocess
import sys
import threading
from pathlib import Path

_lock = threading.Lock()  # one dialog at a time

KINDS = {
    "folder": {"title": "Choose image folder", "dir": True},
    "poni": {"title": "Choose PONI file", "name": "PONI calibration", "exts": ["poni"]},
    "mask": {"title": "Choose mask file", "name": "Mask files", "exts": ["msk", "edf", "tif", "tiff"]},
    "nxs": {"title": "Choose NeXus file", "name": "NeXus / HDF5", "exts": ["nxs", "h5", "hdf5"]},
    "dat": {"title": "Choose integrated curves", "name": "SAXSII curves", "exts": ["dat"], "multi": True},
}


class DialogUnavailable(Exception):
    pass


def ask(kind, initial=None):
    """Show the dialog and block until the user closes it.

    Returns a path string, a list of paths (multi-select kinds) or None when cancelled."""
    cfg = KINDS[kind]
    start = _start_dir(initial)
    with _lock:
        if sys.platform == "darwin":
            out = _ask_macos(cfg, start)
        elif sys.platform.startswith("win"):
            out = _ask_tk(cfg, start)
        else:
            out = _ask_linux(cfg, start)
    if not out:
        return None
    if cfg.get("multi"):
        return [str(Path(p)) for p in out]
    return str(Path(out))


def _start_dir(initial):
    if not initial:
        return None
    p = Path(initial)
    if p.is_dir():
        return str(p)
    return str(p.parent) if p.parent.is_dir() else None


def _run(cmd):
    """Run a dialog program. Returns stdout, or None if the user cancelled."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:
        raise DialogUnavailable(str(e))
    if r.returncode == 0:
        return r.stdout
    err = (r.stderr or "").strip()
    # zenity / kdialog exit with 1 on cancel; osascript reports error -128 "User canceled"
    if (r.returncode == 1 and not err) or "-128" in err or "cancel" in err.lower():
        return None
    raise DialogUnavailable(err or f"{cmd[0]} exited with code {r.returncode}")


# ----------------------------------------------------------------------------- macOS

def _as_string(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _ask_macos(cfg, start):
    prompt = f"with prompt {_as_string(cfg['title'])}"
    where = f" default location (POSIX file {_as_string(start)})" if start else ""
    if cfg.get("dir"):
        script = f"POSIX path of (choose folder {prompt}{where})"
    else:
        types = " of type {" + ", ".join(_as_string(e) for e in cfg["exts"]) + "}"
        choose = f"choose file {prompt}{types}{where}"
        if cfg.get("multi"):
            script = (f"set fs to ({choose} with multiple selections allowed)\n"
                      "set out to \"\"\n"
                      "repeat with f in fs\n set out to out & POSIX path of f & linefeed\nend repeat\n"
                      "return out")
        else:
            script = f"POSIX path of ({choose})"
    # `activate` brings the dialog in front of the browser window
    out = _run(["osascript", "-e", "activate", "-e", script])
    return _parse(out, cfg)


# ----------------------------------------------------------------------------- Linux

def _ask_linux(cfg, start):
    if shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", f"--title={cfg['title']}"]
        if start:
            cmd.append(f"--filename={start.rstrip('/')}/")
        if cfg.get("dir"):
            cmd.append("--directory")
        else:
            cmd += [f"--file-filter={cfg['name']} | " + " ".join(f"*.{e}" for e in cfg["exts"]),
                    "--file-filter=All files | *"]
            if cfg.get("multi"):
                cmd += ["--multiple", "--separator=\n"]
        return _parse(_run(cmd), cfg)
    if shutil.which("kdialog"):
        base = start or str(Path.home())
        if cfg.get("dir"):
            cmd = ["kdialog", "--title", cfg["title"], "--getexistingdirectory", base]
        else:
            filt = " ".join(f"*.{e}" for e in cfg["exts"]) + f"|{cfg['name']}"
            cmd = ["kdialog", "--title", cfg["title"], "--getopenfilename", base, filt]
            if cfg.get("multi"):
                cmd[3:3] = ["--multiple", "--separate-output"]
        return _parse(_run(cmd), cfg)
    return _ask_tk(cfg, start)


def _parse(out, cfg):
    if out is None:
        return None
    paths = [line.strip() for line in out.splitlines() if line.strip()]
    if not paths:
        return None
    return paths if cfg.get("multi") else paths[0]


# ----------------------------------------------------------------------------- tkinter (Windows, fallback)

_dpi_done = False


def _make_dpi_aware():
    """Without this Windows renders the dialog at 96 DPI and stretches it: blurry text."""
    global _dpi_done
    if _dpi_done or not sys.platform.startswith("win"):
        return
    _dpi_done = True
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor DPI aware (Windows 8.1+)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # system DPI aware (Vista+)
        except Exception:
            pass


def _ask_tk(cfg, start):
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as e:
        raise DialogUnavailable(str(e))
    _make_dpi_aware()
    try:
        root = tk.Tk()
    except tk.TclError as e:  # no display (e.g. headless server)
        raise DialogUnavailable(str(e))
    try:
        root.withdraw()
        root.attributes("-topmost", True)  # otherwise it can open behind the browser
        root.update()
        kw = dict(parent=root, title=cfg["title"], initialdir=start)
        if cfg.get("dir"):
            return filedialog.askdirectory(mustexist=True, **kw) or None
        types = [(cfg["name"], " ".join(f"*.{e}" for e in cfg["exts"])), ("All files", "*.*")]
        if cfg.get("multi"):
            return list(filedialog.askopenfilenames(filetypes=types, **kw)) or None
        return filedialog.askopenfilename(filetypes=types, **kw) or None
    finally:
        root.destroy()
