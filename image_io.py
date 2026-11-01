"""Reading detector images from a folder and rendering arrays to PNG."""
import base64
import io
import os
from collections import OrderedDict
from pathlib import Path

import fabio
import numpy as np
from PIL import Image

from integration_methods import UserError

IMAGE_EXTS = (".tif", ".tiff", ".edf", ".cbf", ".img", ".mccd")
HDF5_EXTS = (".h5", ".hdf5", ".nxs")
FRAME_SEP = "#"  # image id of a frame inside a multi-frame HDF5 file: "file.h5#3"


def _find_hdf5_dataset(h5):
    """Path of the image stack inside an HDF5/NeXus file: the largest 2D/3D numeric dataset."""
    import h5py
    best = [None, 0]

    def visit(name, obj):
        if isinstance(obj, h5py.Dataset) and obj.ndim in (2, 3) and obj.dtype.kind in "iuf":
            if obj.size > best[1]:
                best[0], best[1] = name, obj.size

    h5.visititems(visit)
    return best[0]


def hdf5_frames(path):
    import h5py
    with h5py.File(path, "r") as h5:
        ds = _find_hdf5_dataset(h5)
        if ds is None:
            return 0
        shape = h5[ds].shape
        return shape[0] if len(shape) == 3 else 1


def list_images(folder):
    """List the images of a folder as [{id, name, frames, mtime}] (HDF5 frames expanded)."""
    folder = Path(folder)
    if not folder.is_dir():
        raise UserError(f"Folder not found: {folder}")
    items = []
    for entry in sorted(os.scandir(folder), key=lambda e: e.name.lower()):
        if not entry.is_file():
            continue
        ext = os.path.splitext(entry.name)[1].lower()
        mtime = entry.stat().st_mtime
        if ext in IMAGE_EXTS:
            if os.path.splitext(entry.name)[0].lower().endswith("mask"):
                continue  # masks (e.g. <stem>_mask.edf from the NXS converter) are not images
            items.append({"id": entry.name, "name": entry.name, "mtime": mtime})
        elif ext in HDF5_EXTS:
            try:
                n = hdf5_frames(entry.path)
            except Exception:
                n = 0
            if n == 1:
                items.append({"id": entry.name, "name": entry.name, "mtime": mtime})
            elif n > 1:
                children = [{"id": f"{entry.name}{FRAME_SEP}{i}", "name": f"frame {i}", "mtime": mtime}
                            for i in range(n)]
                items.append({"id": entry.name, "name": entry.name, "mtime": mtime, "children": children})
    return items


def curve_name(image_id):
    """Name of the integrated curve / .dat file for an image id."""
    if FRAME_SEP in image_id:
        fname, frame = image_id.split(FRAME_SEP, 1)
        return f"{os.path.splitext(fname)[0]}_f{int(frame):04d}"
    return os.path.splitext(image_id)[0]


class ImageCache:
    def __init__(self, size=6):
        self.size = size
        self._data = OrderedDict()

    def get(self, key, loader):
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        value = loader()
        self._data[key] = value
        if len(self._data) > self.size:
            self._data.popitem(last=False)
        return value

    def clear(self):
        self._data.clear()


_cache = ImageCache()


def read_image(folder, image_id):
    """Read an image (or a frame of an HDF5 stack) as a numpy array, cached by mtime."""
    fname, _, frame = image_id.partition(FRAME_SEP)
    path = Path(folder) / fname
    if not path.is_file():
        raise UserError(f"Image not found: {path}")
    key = (str(path), frame, path.stat().st_mtime)

    def load():
        ext = path.suffix.lower()
        if ext in HDF5_EXTS:
            import h5py
            with h5py.File(path, "r") as h5:
                ds = _find_hdf5_dataset(h5)
                if ds is None:
                    raise UserError(f"No image data found in {fname}")
                d = h5[ds]
                arr = np.asarray(d[int(frame or 0)] if d.ndim == 3 else d[()])
            if ext == ".nxs":
                arr = np.flip(arr, 0)  # beamline NeXus files are stored upside down (kept from the Qt version)
            return arr
        try:
            return fabio.open(str(path)).data
        except Exception as e:
            raise UserError(f"Could not read {fname}: {e}") from e

    return _cache.get(key, load)


def read_mask(path):
    p = Path(path)
    if not p.is_file():
        raise UserError(f"Mask file not found: {p}")
    try:
        return fabio.open(str(p)).data.astype(bool)
    except Exception as e:
        raise UserError(f"Could not read mask {p.name}: {e}") from e


# ----------------------------------------------------------------------------- rendering

def _limits(values, scale, lo_pct, hi_pct, symmetric):
    v = values[np.isfinite(values)]
    if scale == "log":
        v = v[v > 0]
    if v.size == 0:
        return (1.0, 10.0) if scale == "log" else (0.0, 1.0)
    if symmetric:
        m = float(np.percentile(np.abs(v), hi_pct)) or 1.0
        return -m, m
    lo, hi = (float(x) for x in np.percentile(v, [lo_pct, hi_pct]))
    if hi <= lo:
        hi = lo * 10 if scale == "log" else lo + 1
    return lo, hi


def render_png(arr, cmap="jet", scale="log", lo_pct=1.0, hi_pct=99.9, vmin=None, vmax=None, symmetric=False):
    """Color-map an array to an RGBA PNG data URL. NaNs are transparent.

    Returns (data_url, vmin, vmax)."""
    from matplotlib import colormaps
    a = np.asarray(arr, dtype=float)
    auto_lo, auto_hi = _limits(a, scale, lo_pct, hi_pct, symmetric)
    vmin = auto_lo if vmin is None else float(vmin)
    vmax = auto_hi if vmax is None else float(vmax)
    if scale == "log":
        vmin = max(vmin, 1e-12)
        vmax = max(vmax, vmin * 1.0001)
        with np.errstate(divide="ignore", invalid="ignore"):
            norm = (np.log10(np.clip(a, vmin, None)) - np.log10(vmin)) / (np.log10(vmax) - np.log10(vmin))
    else:
        norm = (a - vmin) / ((vmax - vmin) or 1.0)
    rgba = colormaps[cmap](np.clip(np.nan_to_num(norm, nan=0.0), 0, 1), bytes=True)
    rgba[~np.isfinite(a), 3] = 0
    return to_data_url(rgba), vmin, vmax


def render_mask_png(mask, color=(255, 0, 255), alpha=110):
    rgba = np.zeros(mask.shape + (4,), dtype=np.uint8)
    rgba[mask] = (*color, alpha)
    return to_data_url(rgba)


def to_data_url(rgba):
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG", compress_level=1)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
