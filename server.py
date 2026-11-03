"""SAXSII local web server: JSON API + static browser frontend."""
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
from flask import Flask, jsonify, request, send_file, send_from_directory, redirect

import beamstop
import image_io
import native_dialogs
import results
from anis_finder import anisotropy_map, chi_profile, pixel_chi_map, pixel_q_map
from integration_methods import (UNITS, UserError, build_mask, cake, calibration_info, integrate_1d,
                                 load_poni, q_to_radius_px, set_calibration, unit_factor)
from saving_methods import save_csv, save_dat, save_tif

APP_DIR = Path(__file__).resolve().parent  # bundled resources (web/, files/)
# Writable files live next to the executable when frozen with PyInstaller, else next to the sources.
DATA_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else APP_DIR
WEB_DIR = APP_DIR / "web"
SETTINGS_FILE = DATA_DIR / "settings.json"

logging.basicConfig(filename=DATA_DIR / "app.log", filemode="a", level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s", datefmt="%d-%b-%y %H:%M:%S")
log = logging.getLogger("saxsii")

DEFAULT_SETTINGS = {
    "folder": "",
    "poni": "",
    "mask": "",
    "bins": 1000,
    "unit": "q_A^-1",
    "sigma_thres": 1000.0,
    "q_min": None,  # A^-1
    "q_max": None,  # A^-1
    # automatic beamstop mask: detection parameters + the image it was detected on
    "beamstop": {"enabled": False, **beamstop.DEFAULTS, "ref": None},
    "recent": {"folder": [], "poni": [], "mask": [], "nxs": []},  # most recent first
}
RECENT_MAX = 8


class State:
    """Everything the single local user has loaded. Guarded by `lock`."""

    def __init__(self):
        self.lock = threading.RLock()
        self.settings = dict(DEFAULT_SETTINGS)
        self.ai = None
        self.user_mask = None
        self.curves = {}  # name -> {radial (A^-1), intensity, sigma}
        self.markers = {}  # name -> q (A^-1)
        self._maps = {}  # (kind, shape) -> cached per-pixel q / chi arrays
        self.beamstop_mask = None  # detected on settings["beamstop"]["ref"], used for every image
        self.beamstop_stats = None

    # -- persistence
    def load_settings(self):
        try:
            self.settings.update(json.loads(SETTINGS_FILE.read_text()))
        except FileNotFoundError:
            return
        except Exception:
            log.exception("could not read settings")
        self.settings["beamstop"] = {**DEFAULT_SETTINGS["beamstop"], **(self.settings.get("beamstop") or {})}
        self.settings["recent"] = {k: list(v) for k, v in
                                   {**DEFAULT_SETTINGS["recent"], **(self.settings.get("recent") or {})}.items()}
        for key, loader in (("poni", self.set_poni), ("mask", self.set_mask)):
            if self.settings.get(key):
                try:
                    loader(self.settings[key])
                except UserError as e:
                    log.warning("could not restore %s: %s", key, e)
                    self.settings[key] = ""

    def remember(self, kind, path):
        """Put a successfully loaded path at the top of its recent list."""
        lst = self.settings["recent"].setdefault(kind, [])
        path = str(path)
        if path in lst:
            lst.remove(path)
        lst.insert(0, path)
        del lst[RECENT_MAX:]

    def save_settings(self):
        try:
            SETTINGS_FILE.write_text(json.dumps(self.settings, indent=2))
        except Exception:
            log.exception("could not write settings")

    # -- loading
    def set_poni(self, path):
        if not path:
            self.ai, self.settings["poni"] = None, ""
        else:
            self.ai = load_poni(path)
            self.settings["poni"] = str(path)
            self.remember("poni", path)
        self.geometry_changed()

    def geometry_changed(self):
        """Beam center / mask changed: cached maps and the beamstop detection are stale."""
        self._maps.clear()
        self.beamstop_mask = self.beamstop_stats = None

    def set_mask(self, path):
        if not path:
            self.user_mask, self.settings["mask"] = None, ""
        else:
            self.user_mask = image_io.read_mask(path)
            self.settings["mask"] = str(path)
            self.remember("mask", path)
        self.beamstop_mask = self.beamstop_stats = None

    def require_ai(self):
        if self.ai is None:
            raise UserError("Load a PONI file first.")
        return self.ai

    def require_folder(self):
        folder = self.settings.get("folder")
        if not folder or not Path(folder).is_dir():
            raise UserError("Open an image folder first.")
        return folder

    def image(self, image_id):
        return image_io.read_image(self.require_folder(), image_id)

    def mask_for(self, image):
        mask = build_mask(self.ai, image, self.user_mask)
        bs = self.beamstop_for(image.shape)
        if bs is not None:
            mask |= bs
        return mask

    def beamstop_for(self, shape):
        """The auto beamstop mask if enabled (re-detected lazily after geometry changes)."""
        cfg = self.settings["beamstop"]
        if not cfg["enabled"] or self.ai is None:
            return None
        if self.beamstop_mask is None and cfg.get("ref"):
            try:
                self.detect_beamstop(cfg["ref"])
            except UserError as e:
                log.warning("beamstop detection on %s failed: %s", cfg["ref"], e)
                return None
        m = self.beamstop_mask
        return m if m is not None and m.shape == shape else None

    def detect_beamstop(self, image_id):
        ai = self.require_ai()
        img = self.image(image_id)
        cfg = self.settings["beamstop"]
        cal = calibration_info(ai)
        self.beamstop_mask, self.beamstop_stats = beamstop.detect_beamstop(
            img, build_mask(ai, img, self.user_mask), (cal["center_x"], cal["center_y"]),
            **{k: cfg[k] for k in beamstop.DEFAULTS})
        self.beamstop_stats["ref"] = cfg["ref"] = image_id

    def pixel_map(self, kind, shape):
        key = (kind, shape)
        if key not in self._maps:
            ai = self.require_ai()
            self._maps[key] = pixel_q_map(ai, shape) if kind == "q" else pixel_chi_map(ai, shape)
        return self._maps[key]

    def q_range(self):
        return (self.settings.get("q_min"), self.settings.get("q_max"))

    def public(self):
        cal = calibration_info(self.ai) if self.ai is not None else None
        return {
            "settings": self.settings,
            "calibration": cal,
            "units": {k: v[2] for k, v in UNITS.items()},
            "unit_factor": unit_factor(self.settings["unit"]),
            "curves": sorted(self.curves),
            "markers": self.markers,
            "mask_loaded": self.user_mask is not None,
            "beamstop_stats": self.beamstop_stats,
        }


state = State()
app = Flask(__name__, static_folder=None)


@app.errorhandler(UserError)
def user_error(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(Exception)
def server_error(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    log.exception("unhandled error")
    return jsonify(error=f"{type(e).__name__}: {e}"), 500


def body():
    return request.get_json(silent=True) or {}


def num(value, name, cast=float, allow_none=False):
    if value in (None, ""):
        if allow_none:
            return None
        raise UserError(f"{name} is required")
    try:
        return cast(value)
    except (TypeError, ValueError):
        raise UserError(f"{name} must be a number")


def curve_json(name, curve, unit):
    f = unit_factor(unit)
    return {"name": name, "x": (np.asarray(curve["radial"]) * f).tolist(),
            "y": np.nan_to_num(curve["intensity"]).tolist(), "sigma": np.nan_to_num(curve["sigma"]).tolist()}


def render_view(arr, args, symmetric=False, default_scale="log", default_cmap="jet"):
    scale = args.get("scale") or default_scale
    url, vmin, vmax = image_io.render_png(
        arr, cmap=args.get("cmap") or default_cmap, scale=scale,
        lo_pct=num(args.get("lo_pct", 1), "low percentile"), hi_pct=num(args.get("hi_pct", 99.9), "high percentile"),
        vmin=num(args.get("vmin"), "vmin", allow_none=True), vmax=num(args.get("vmax"), "vmax", allow_none=True),
        symmetric=symmetric)
    return {"png": url, "vmin": vmin, "vmax": vmax, "scale": scale, "shape": list(arr.shape)}


# ----------------------------------------------------------------------------- static

@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/web/<path:name>")
def web_files(name):
    return send_from_directory(WEB_DIR, name)


@app.get("/vendor/plotly.min.js")
def plotly_js():
    try:
        import plotly
        path = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
        if path.is_file():
            return send_file(path, mimetype="application/javascript", max_age=86400)
    except ImportError:
        pass
    return redirect("https://cdn.plot.ly/plotly-2.35.2.min.js")


@app.get("/files/<path:name>")
def asset_files(name):
    return send_from_directory(APP_DIR / "files", name)


# ----------------------------------------------------------------------------- state / loading

@app.get("/api/state")
def get_state():
    with state.lock:
        return jsonify(state.public())


@app.post("/api/settings")
def post_settings():
    b = body()
    with state.lock:
        s = state.settings
        if "bins" in b:
            s["bins"] = num(b["bins"], "Bins", int)
            if s["bins"] < 2:
                raise UserError("Bins must be at least 2")
        if "unit" in b:
            unit_factor(b["unit"])
            s["unit"] = b["unit"]
        if "sigma_thres" in b:
            s["sigma_thres"] = num(b["sigma_thres"], "Sigma clip threshold")
        for key in ("q_min", "q_max"):
            if key in b:  # given in the current display unit
                v = num(b[key], key, allow_none=True)
                s[key] = None if v is None else v / unit_factor(s["unit"])
        if s["q_min"] is not None and s["q_max"] is not None and s["q_max"] <= s["q_min"]:
            raise UserError("q max must be larger than q min")
        state.save_settings()
        return jsonify(state.public())


@app.post("/api/folder")
def open_folder():
    path = (body().get("path") or "").strip().strip('"')
    if not path:
        raise UserError("Choose an image folder.")
    with state.lock:
        files = image_io.list_images(path)
        folder = Path(path)
        state.settings["folder"] = str(folder)
        state.remember("folder", folder)
        # Convenience: a single PONI / mask file in the folder is loaded if none is loaded yet.
        auto = []
        for key, pattern, loader in (("poni", "*.poni", state.set_poni), ("mask", "*.msk", state.set_mask)):
            found = sorted(folder.glob(pattern))
            if not state.settings.get(key) and len(found) == 1:
                try:
                    loader(str(found[0]))
                    auto.append(found[0].name)
                except UserError as e:
                    log.warning("could not auto-load %s: %s", found[0], e)
        state.save_settings()
        return jsonify(folder=state.settings["folder"], files=files, auto_loaded=auto, state=state.public())


@app.post("/api/pick")
def pick():
    """Open a native file / folder dialog on this computer (blocks until it is closed)."""
    b = body()
    kind = b.get("kind")
    if kind not in native_dialogs.KINDS:
        raise UserError(f"Unknown dialog: {kind}")
    if request.remote_addr not in ("127.0.0.1", "::1"):
        # The dialog would open on the server's screen, not the remote user's.
        return jsonify(error="Native dialogs unavailable: browser is on another computer", native=False), 501
    initial = b.get("initial") or state.settings.get("folder") or None
    try:
        out = native_dialogs.ask(kind, initial)  # not under state.lock: the user may take a while
    except native_dialogs.DialogUnavailable as e:
        return jsonify(error=f"Native dialogs unavailable: {e}", native=False), 501
    return jsonify(path=out if isinstance(out, str) else None, paths=out if isinstance(out, list) else None)


@app.get("/api/files")
def files():
    with state.lock:
        return jsonify(folder=state.settings["folder"], files=image_io.list_images(state.require_folder()))


@app.get("/api/browse")
def browse():
    """Minimal server-side file browser (the browser cannot hand us local paths)."""
    path = request.args.get("path") or state.settings.get("folder") or str(Path.home())
    exts = tuple(e for e in (request.args.get("exts") or "").lower().split(",") if e)
    p = Path(path).expanduser()
    if p.is_file():
        p = p.parent
    if not p.is_dir():
        raise UserError(f"Not a folder: {p}")
    dirs, files_ = [], []
    try:
        for e in sorted(os.scandir(p), key=lambda e: e.name.lower()):
            if e.name.startswith("."):
                continue
            if e.is_dir():
                dirs.append(e.name)
            elif not exts or e.name.lower().endswith(exts):
                files_.append(e.name)
    except PermissionError:
        raise UserError(f"Permission denied: {p}")
    drives = []
    if os.name == "nt":
        drives = [f"{c}:\\" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{c}:\\")]
    parent = str(p.parent) if p.parent != p else None
    return jsonify(path=str(p), parent=parent, dirs=dirs, files=files_, drives=drives)


@app.post("/api/poni")
def post_poni():
    with state.lock:
        state.set_poni((body().get("path") or "").strip())
        state.save_settings()
        return jsonify(state.public())


@app.post("/api/mask")
def post_mask():
    with state.lock:
        state.set_mask((body().get("path") or "").strip())
        state.save_settings()
        return jsonify(state.public())


@app.post("/api/nxs")
def load_nxs():
    """Convert a NeXus file to one TIFF per frame + mask + PONI (next to the .nxs, in a
    folder named after it; an earlier conversion is reused) and load all three."""
    from NXS2TIF_conversion.nxs_to_tiff import convert_file, output_paths
    path = Path((body().get("path") or "").strip())
    if not path.is_file():
        raise UserError(f"NeXus file not found: {path}")
    with state.lock:
        paths = output_paths(path, path.parent)
        if not all(paths[k].exists() for k in ("mask", "meta")):
            try:
                convert_file(path, path.parent)
            except Exception as e:
                log.exception("NXS conversion failed")
                raise UserError(f"Could not convert {path.name}: {e}") from e
        state.settings["folder"] = str(paths["dir"])
        state.remember("folder", paths["dir"])
        state.remember("nxs", path)
        state.set_mask(str(paths["mask"]))
        warning = None
        if paths["poni"].exists():
            state.set_poni(str(paths["poni"]))
        else:
            warning = f"{path.name} does not contain enough geometry to build a PONI file. Load a PONI file or use the calibration tool."
        state.save_settings()
        return jsonify({**state.public(), "files": image_io.list_images(paths["dir"]), "warning": warning})


@app.post("/api/beamstop")
def post_beamstop():
    """Update the auto-beamstop parameters and (if enabled) re-detect on image `id`."""
    b = body()
    with state.lock:
        cfg = state.settings["beamstop"]
        if "enabled" in b:
            cfg["enabled"] = bool(b["enabled"])
        for key, cast, lo, hi in (("threshold", float, 0.01, 0.99), ("smooth", float, 0, 20),
                                  ("margin", int, 0, 100), ("min_area", int, 0, 10 ** 6)):
            if key in b:
                cfg[key] = min(max(num(b[key], key, cast), lo), hi)
        if "bridge_gaps" in b:
            cfg["bridge_gaps"] = bool(b["bridge_gaps"])
        state.beamstop_mask = state.beamstop_stats = None
        if cfg["enabled"]:
            ref = b.get("id") or cfg.get("ref")
            if not ref:
                raise UserError("Select an image to detect the beamstop on.")
            state.detect_beamstop(ref)
        state.save_settings()
        return jsonify(state.public())


@app.post("/api/beamstop/save")
def save_beamstop():
    """Write user mask + beamstop as a pyFAI-compatible .msk (EDF) file in the image folder."""
    import fabio
    name = (body().get("name") or "").strip()
    if not name:
        raise UserError("Enter a file name.")
    if not name.lower().endswith((".msk", ".edf")):
        name += ".msk"
    with state.lock:
        bs = state.beamstop_mask
        if bs is None:
            raise UserError("Detect the beamstop first.")
        path = Path(state.require_folder()) / name
        if path.exists():
            raise UserError(f"{name} already exists.")
        mask = bs.copy()
        if state.user_mask is not None and state.user_mask.shape == bs.shape:
            mask |= state.user_mask
        fabio.edfimage.EdfImage(data=mask.astype(np.uint8)).write(str(path))
        return jsonify(path=str(path))


@app.post("/api/calibration")
def post_calibration():
    b = body()
    with state.lock:
        ai = state.require_ai()
        set_calibration(ai,
                        distance_mm=num(b.get("distance_mm"), "Distance", allow_none=True),
                        center_x=num(b.get("center_x"), "Center X", allow_none=True),
                        center_y=num(b.get("center_y"), "Center Y", allow_none=True),
                        wavelength_A=num(b.get("wavelength_A"), "Wavelength", allow_none=True))
        state.geometry_changed()
        return jsonify(state.public())


@app.post("/api/calibration/reload")
def reload_calibration():
    with state.lock:
        state.set_poni(state.settings.get("poni"))
        return jsonify(state.public())


@app.post("/api/calib_tool")
def calib_tool():
    exe = shutil.which("pyFAI-calib2")
    if exe is None:
        raise UserError("pyFAI-calib2 was not found on PATH.")
    subprocess.Popen([exe], cwd=state.settings.get("folder") or None)
    return jsonify(ok=True)


# ----------------------------------------------------------------------------- 2D views

@app.post("/api/render")
def render():
    """Render an image. view: raw | cake | subtract | divide."""
    b = body()
    image_id = b.get("id")
    view = b.get("view", "raw")
    if not image_id:
        raise UserError("Select an image first.")
    with state.lock:
        img = state.image(image_id)
        out = {"id": image_id, "view": view}
        if view == "raw":
            out.update(render_view(img, b))
            mask = build_mask(state.ai, img, state.user_mask)
            bs = state.beamstop_for(img.shape)
            if mask.any() or bs is not None:
                out["mask_png"] = image_io.render_mask_png(mask, beamstop=bs)
        elif view == "cake":
            ai = state.require_ai()
            res = cake(ai, img, state.mask_for(img), npt_rad=num(b.get("npt_rad", 500), "radial points", int),
                       npt_azim=num(b.get("npt_azim", 360), "azimuthal points", int), q_range=state.q_range())
            out.update(render_view(res["intensity"], b))
            f = unit_factor(state.settings["unit"])
            r, c = res["radial"] * f, res["chi"]
            out["axes"] = {"x0": float(r[0]), "dx": float(r[1] - r[0]), "y0": float(c[0]), "dy": float(c[1] - c[0])}
        elif view in ("subtract", "divide"):
            ai = state.require_ai()
            pol = num(b.get("polarization"), "Polarization factor", allow_none=True)
            amap, iso = anisotropy_map(ai, img, state.mask_for(img), mode=view, bins=state.settings["bins"],
                                       q_range=state.q_range(), solid_angle=bool(b.get("solid_angle")),
                                       polarization=pol)
            args = dict(b, scale="linear", cmap=b.get("cmap") or "RdBu_r")
            out.update(render_view(amap, args, symmetric=b.get("vmin") in (None, "") and b.get("vmax") in (None, "")))
            out["valid_fraction"] = float(np.isfinite(amap).mean())
        else:
            raise UserError(f"Unknown view: {view}")
        return jsonify(out)


@app.get("/api/pixel")
def pixel():
    """Value and geometry of one detector pixel (for the hover read-out)."""
    image_id = request.args.get("id")
    view = request.args.get("view", "raw")
    x, y = int(round(float(request.args["x"]))), int(round(float(request.args["y"])))
    with state.lock:
        img = state.image(image_id)
        h, w = img.shape
        if not (0 <= x < w and 0 <= y < h):
            return jsonify(inside=False)
        out = {"inside": True, "x": x, "y": y, "value": float(img[y, x])}
        if state.ai is not None:
            cal = calibration_info(state.ai)
            out["q"] = float(state.pixel_map("q", img.shape)[y, x]) * unit_factor(state.settings["unit"])
            out["chi"] = float(state.pixel_map("chi", img.shape)[y, x])
            out["r"] = float(np.hypot(x + 0.5 - cal["center_x"], y + 0.5 - cal["center_y"]))
        if state.user_mask is not None and state.user_mask.shape == img.shape:
            out["masked"] = bool(state.user_mask[y, x])
        bs = state.beamstop_for(img.shape)
        if bs is not None and bs[y, x]:
            out["masked"] = "beamstop"
        return jsonify(out)


@app.post("/api/pick_q")
def pick_q():
    """q (display unit) at a clicked pixel - used to set q min / max / markers on the image."""
    b = body()
    with state.lock:
        img = state.image(b.get("id"))
        x, y = int(round(b["x"])), int(round(b["y"]))
        h, w = img.shape
        if not (0 <= x < w and 0 <= y < h):
            raise UserError("Click inside the image.")
        q = float(state.pixel_map("q", img.shape)[y, x])
        return jsonify(q=q * unit_factor(state.settings["unit"]))


@app.get("/api/rings")
def rings():
    """Ring radii (px) for q min / q max / markers, to draw on the raw image."""
    with state.lock:
        if state.ai is None:
            return jsonify(rings=[])
        out = []
        for label, q in (("q min", state.settings["q_min"]), ("q max", state.settings["q_max"])):
            if q is not None:
                out.append({"label": label, "r": float(q_to_radius_px(state.ai, q)), "kind": "range"})
        for name, q in state.markers.items():
            out.append({"label": name, "r": float(q_to_radius_px(state.ai, q)), "kind": "marker"})
        return jsonify(rings=out)


@app.post("/api/chi_profile")
def post_chi_profile():
    b = body()
    with state.lock:
        ai = state.require_ai()
        img = state.image(b.get("id"))
        pol = num(b.get("polarization"), "Polarization factor", allow_none=True)
        prof, metrics = chi_profile(ai, img, state.mask_for(img), state.q_range(),
                                    nbins=num(b.get("nbins", 180), "chi bins", int),
                                    solid_angle=bool(b.get("solid_angle")), polarization=pol)
        return jsonify(chi=prof["chi"].tolist(), intensity=_json_floats(prof["intensity"]),
                       fit=_json_floats(prof["fit"]), metrics=metrics)


def _json_floats(a):
    return [None if not np.isfinite(v) else float(v) for v in a]


# ----------------------------------------------------------------------------- saved analysis results

def _image_meta(folder, image_id):
    """Position of an image in the folder listing and its acquisition (file) time."""
    from datetime import datetime
    ids = [c["id"] for f in image_io.list_images(folder) for c in (f.get("children") or [f])]
    fname = image_id.partition(image_io.FRAME_SEP)[0]
    mtime = (Path(folder) / fname).stat().st_mtime
    return {"index": ids.index(image_id) + 1 if image_id in ids else None,
            "time": datetime.fromtimestamp(mtime).isoformat(timespec="seconds")}


@app.post("/api/anisotropy/analyze")
def analyze_anisotropy():
    """Compute I(chi) for one image over the current q range, save the profile (.dat) and
    put its fit parameters into analysis/anisotropy_results.csv."""
    b = body()
    image_id = b.get("id")
    with state.lock:
        ai = state.require_ai()
        folder = state.require_folder()
        q_min, q_max = state.q_range()
        if q_min is None or q_max is None:
            raise UserError("Set q min and q max first.")
        img = state.image(image_id)
        pol = num(b.get("polarization"), "Polarization factor", allow_none=True)
        sa = bool(b.get("solid_angle"))
        prof, m = chi_profile(ai, img, state.mask_for(img), (q_min, q_max),
                              nbins=num(b.get("nbins", 180), "chi bins", int), solid_angle=sa, polarization=pol)
        bs_on = state.beamstop_for(img.shape) is not None
        tag = f"q{q_min:.4g}-{q_max:.4g}A"
        name = f"{image_io.curve_name(image_id)}_chi_{tag}.dat"
        results.write_profile(
            results.analysis_dir(folder, "anisotropy") / name,
            {"chi_deg": prof["chi"], "intensity": prof["intensity"], "fit": prof["fit"]},
            {"image": image_id, "q_range_A^-1": f"{q_min:.6g} - {q_max:.6g}",
             "fit": "I(chi) = I0 [1 + A cos 2(chi - phi)]",
             "A": f"{m['amplitude']:.6g}" if m["amplitude"] is not None else "nan",
             "phi_deg": f"{m['orientation_deg']:.4f}", "I0": f"{m['i0']:.6g}",
             "solid_angle_correction": sa, "polarization_factor": pol,
             "poni": state.settings.get("poni"), "mask": state.settings.get("mask") or "none",
             "auto_beamstop_mask": bs_on})
        row = {"id": f"{image_id}|{tag}", "image": image_id, **_image_meta(folder, image_id),
               "q_min_A": q_min, "q_max_A": q_max,
               **{k: m[k] for k in ("amplitude", "orientation_deg", "i0", "max_over_min", "coverage", "pixels")},
               "solid_angle": sa, "polarization": pol, "beamstop_masked": bs_on,
               "profile": f"anisotropy/{name}"}
        results.TABLES["anisotropy"].upsert(folder, row)
        return jsonify(row=row, chi=prof["chi"].tolist(), intensity=_json_floats(prof["intensity"]),
                       fit=_json_floats(prof["fit"]), metrics=m)


def _table(kind):
    if kind not in results.TABLES:
        raise UserError(f"Unknown result table: {kind}")
    return results.TABLES[kind]


@app.get("/api/results")
def get_results():
    """All result tables of the open folder (columns + rows)."""
    with state.lock:
        folder = state.settings.get("folder")
        out = []
        for t in results.TABLES.values():
            rows = t.load(folder) if folder and Path(folder).is_dir() else []
            out.append({**t.describe(), "rows": rows, "file": str(t.path(folder)) if folder else None})
        return jsonify(tables=out)


@app.post("/api/results/delete")
def delete_results():
    b = body()
    with state.lock:
        rows = _table(b.get("kind")).delete(state.require_folder(), b.get("ids") or [])
        return jsonify(count=len(rows))


@app.post("/api/results/profiles")
def result_profiles():
    """The saved 1D profiles (e.g. I(chi)) of some result rows, for overlay plots."""
    b = body()
    with state.lock:
        folder = Path(state.require_folder())
        rows = {r["id"]: r for r in _table(b.get("kind")).load(folder)}
        out = []
        for rid in b.get("ids") or []:
            r = rows.get(rid)
            if not r or not r.get("profile"):
                continue
            p = folder / results.ANALYSIS_DIR / r["profile"]
            if not p.is_file():
                continue
            df = results.read_profile(p)
            out.append({"id": rid, "label": f"{r['image']}", "columns": list(df.columns),
                        "data": {c: _json_floats(df[c].to_numpy(float)) for c in df.columns}})
        return jsonify(profiles=out)


@app.post("/api/save_figure")
def save_figure():
    """Store a figure exported by the browser (PNG data URL and/or SVG text)."""
    import base64
    import re
    b = body()
    name = re.sub(r'[\\/:*?"<>|]+', "_", (b.get("name") or "").strip()) or "figure"
    with state.lock:
        out_dir = results.analysis_dir(state.require_folder(), "figures")
        paths = []
        if b.get("png", "").startswith("data:image/png;base64,"):
            p = out_dir / f"{name}.png"
            p.write_bytes(base64.b64decode(b["png"].split(",", 1)[1]))
            paths.append(str(p))
        if b.get("svg"):
            svg = b["svg"]
            if svg.startswith("data:image/svg+xml,"):
                from urllib.parse import unquote
                svg = unquote(svg.split(",", 1)[1])
            p = out_dir / f"{name}.svg"
            p.write_text(svg, encoding="utf-8")
            paths.append(str(p))
        if not paths:
            raise UserError("Nothing to save.")
        return jsonify(paths=paths)


@app.post("/api/anisotropy/save_map")
def save_anisotropy_map():
    """Save the anisotropy map (float32 TIFF, NaN = no data) of an image."""
    b = body()
    image_id, mode = b.get("id"), b.get("mode", "divide")
    with state.lock:
        ai = state.require_ai()
        img = state.image(image_id)
        pol = num(b.get("polarization"), "Polarization factor", allow_none=True)
        amap, _ = anisotropy_map(ai, img, state.mask_for(img), mode=mode, bins=state.settings["bins"],
                                 q_range=state.q_range(), solid_angle=bool(b.get("solid_angle")), polarization=pol)
        path = results.analysis_dir(state.require_folder(), "anisotropy") / f"{image_io.curve_name(image_id)}_map_{mode}.tif"
        save_tif(path, amap)
        return jsonify(path=str(path))


@app.post("/api/open_folder")
def open_folder_in_os():
    """Show the analysis folder in Explorer / Finder / the file manager."""
    if request.remote_addr not in ("127.0.0.1", "::1"):
        raise UserError("Only possible on the computer running SAXSII.")
    with state.lock:
        p = results.analysis_dir(state.require_folder())
    if sys.platform.startswith("win"):
        os.startfile(p)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(p)])
    return jsonify(path=str(p))


# ----------------------------------------------------------------------------- 1D integration / curves

@app.post("/api/integrate")
def integrate():
    b = body()
    with state.lock:
        ai = state.require_ai()
        folder = state.require_folder()
        s = state.settings
        image_id = b.get("id")
        img = state.image(image_id)
        curve = integrate_1d(ai, img, state.mask_for(img), s["bins"], state.q_range(), s["sigma_thres"])
        name = image_io.curve_name(image_id)
        state.curves[name] = curve
        save_dat(name, folder, curve, s["unit"])
        return jsonify(curve_json(name, curve, s["unit"]))


@app.post("/api/curves")
def get_curves():
    names = body().get("names") or []
    with state.lock:
        unit = state.settings["unit"]
        missing = [n for n in names if n not in state.curves]
        if missing:
            raise UserError(f"Unknown curve(s): {', '.join(missing)}")
        return jsonify(unit=unit, label=UNITS[unit][2], curves=[curve_json(n, state.curves[n], unit) for n in names])


def _on_grid(curve, radial):
    """Curve interpolated onto another radial grid (NaN outside its own range)."""
    kw = dict(left=np.nan, right=np.nan)
    return (np.interp(radial, curve["radial"], curve["intensity"], **kw),
            np.interp(radial, curve["radial"], curve["sigma"], **kw))


def _store_new_curve(name, curve):
    name = (name or "").strip()
    if not name:
        raise UserError("Enter a name.")
    keep = np.isfinite(curve["intensity"])
    curve = {k: np.asarray(v)[keep] for k, v in curve.items()}
    state.curves[name] = curve
    save_dat(name, state.require_folder(), curve, state.settings["unit"])
    return name


@app.post("/api/curves/subtract")
def subtract_curves():
    """sample - factor * background, with errors added in quadrature."""
    b = body()
    with state.lock:
        try:
            sample, bg = state.curves[b["sample"]], state.curves[b["background"]]
        except KeyError as e:
            raise UserError(f"Unknown curve {e}")
        factor = num(b.get("factor", 1.0), "Scale factor")
        bi, bs = _on_grid(bg, sample["radial"])
        curve = {"radial": sample["radial"], "intensity": sample["intensity"] - factor * bi,
                 "sigma": np.sqrt(sample["sigma"] ** 2 + (factor * bs) ** 2)}
        name = _store_new_curve(b.get("name"), curve)
        return jsonify(name=name, curves=sorted(state.curves))


@app.post("/api/curves/average")
def average_curves():
    b = body()
    names = b.get("names") or []
    if len(names) < 2:
        raise UserError("Select at least 2 curves to average.")
    with state.lock:
        curves = [state.curves[n] for n in names]
        radial = curves[0]["radial"]
        Is, Ss = zip(*(_on_grid(c, radial) for c in curves))
        n = len(curves)
        curve = {"radial": radial, "intensity": np.mean(Is, axis=0),
                 "sigma": np.sqrt(np.sum(np.square(Ss), axis=0)) / n}  # error of the mean
        name = _store_new_curve(b.get("name"), curve)
        return jsonify(name=name, curves=sorted(state.curves))


@app.post("/api/curves/remove")
def remove_curves():
    names = body().get("names")
    with state.lock:
        if names is None:
            state.curves.clear()
        for n in names or []:
            state.curves.pop(n, None)
        return jsonify(curves=sorted(state.curves))


@app.post("/api/curves/load_dat")
def load_dat_curves():
    """Load previously saved .dat files from the current folder back into the curve list."""
    import pandas as pd
    names = body().get("files") or []
    with state.lock:
        folder = Path(state.require_folder())
        for fname in names:
            path = folder / fname  # fname may also be an absolute path (native dialog)
            if not path.is_file():
                raise UserError(f"File not found: {path}")
            try:
                df = pd.read_csv(path, comment="#")
            except Exception as e:
                raise UserError(f"Could not read {path.name}: {e}") from e
            col = df.columns[0]
            unit = col.split("radial-", 1)[-1] if col.startswith("radial-") else "q_A^-1"
            f = unit_factor(unit) if unit in UNITS else 1.0
            state.curves[Path(fname).stem] = {
                "radial": df.iloc[:, 0].to_numpy(float) / f, "intensity": df.iloc[:, 1].to_numpy(float),
                "sigma": df.iloc[:, 2].to_numpy(float) if df.shape[1] > 2 else np.zeros(len(df))}
        return jsonify(curves=sorted(state.curves))


@app.post("/api/curves/export")
def export_curves():
    names = body().get("names") or []
    if not names:
        raise UserError("Select curves to export.")
    with state.lock:
        folder = state.require_folder()
        paths = [str(save_csv(n, folder, state.curves[n], state.settings["unit"])) for n in names]
        return jsonify(paths=paths)


# ----------------------------------------------------------------------------- 2D image arithmetic

def _new_tif_name(name):
    name = (name or "").strip()
    if not name:
        raise UserError("Enter a file name.")
    if not name.lower().endswith((".tif", ".tiff")):
        name += ".tif"
    path = Path(state.require_folder()) / name
    if path.exists():
        raise UserError(f"{name} already exists.")
    return path


@app.post("/api/images/average")
def average_images():
    b = body()
    ids = b.get("ids") or []
    if len(ids) < 2:
        raise UserError("Select at least 2 images to average.")
    with state.lock:
        arrays = [state.image(i).astype(np.float64) for i in ids]
        if len({a.shape for a in arrays}) != 1:
            raise UserError("Images have different shapes.")
        path = save_tif(_new_tif_name(b.get("name")), np.mean(arrays, axis=0))
        return jsonify(path=str(path), files=image_io.list_images(state.require_folder()))


@app.post("/api/images/subtract")
def subtract_images():
    """a - factor * b (signed, float32)."""
    b = body()
    with state.lock:
        a, bg = state.image(b.get("a")).astype(np.float64), state.image(b.get("b")).astype(np.float64)
        if a.shape != bg.shape:
            raise UserError("Images have different shapes.")
        factor = num(b.get("factor", 1.0), "Scale factor")
        path = save_tif(_new_tif_name(b.get("name")), a - factor * bg)
        return jsonify(path=str(path), files=image_io.list_images(state.require_folder()))


# ----------------------------------------------------------------------------- markers

@app.post("/api/markers")
def post_markers():
    b = body()
    with state.lock:
        if b.get("clear"):
            state.markers.clear()
        elif b.get("remove"):
            state.markers.pop(b["remove"], None)
        else:
            name = (b.get("name") or "").strip()
            if not name:
                raise UserError("Enter a marker name.")
            state.markers[name] = num(b.get("q"), "q") / unit_factor(state.settings["unit"])
        return jsonify(markers=state.markers)


def create_app():
    state.load_settings()
    return app
