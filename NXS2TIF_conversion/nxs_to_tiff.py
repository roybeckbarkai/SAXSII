#!/usr/bin/env python3
"""
nxs_to_tiff.py  -  Convert SAXS NeXus (.nxs / .h5) files to TIFF.

Written for SOLEIL / SWING files, but the geometry is read from the standard
NeXus classes (NXdetector, NXmonochromator, NXbeam), so files from other
beamlines work as long as they follow NeXus naming.

For every .nxs file in a directory (or a single file) this writes, into
<outdir>/<file stem>/ :

    <stem>_p00_f00.tif ... <stem>_p09_f09.tif   individual frames
                                                 (p = outer scan index, e.g. sample
                                                  position, f = inner index;
                                                  invalid / gap pixels set to 0)
    <stem>_f000.tif ...                          same, for a 1-D scan or single image
    <stem>_mask.edf                             uint8, 1 = gap / masked pixel
                                                 (EDF so it is not listed as an image)
    <stem>.poni                                  pyFAI calibration built from the
                                                 NeXus geometry (binning applied);
                                                 skipped if the geometry is incomplete
    <stem>_meta.json                             geometry + per-frame metadata

Usage
-----
    python nxs_to_tiff.py  /path/to/dir_with_nxs   /path/to/output
    python nxs_to_tiff.py  one_file.nxs            /path/to/output
    python nxs_to_tiff.py  dir  out  --gap-value -1     # frames saved as int32

Requires:  pip install h5py numpy tifffile fabio pyFAI
"""
import argparse
import json
import sys
from pathlib import Path

import h5py
import numpy as np
import tifffile

HC_KEV_A = 12.398419843320026           # h*c in keV * Angstrom

# length units -> metres
_LENGTH = {"m": 1.0, "meter": 1.0, "metre": 1.0, "meters": 1.0,
           "cm": 1e-2, "mm": 1e-3, "millimeter": 1e-3, "millimetre": 1e-3,
           "um": 1e-6, "µm": 1e-6, "μm": 1e-6, "micron": 1e-6, "microns": 1e-6,
           "micrometer": 1e-6, "micrometre": 1e-6,
           "nm": 1e-9, "nanometer": 1e-9, "a": 1e-10, "å": 1e-10, "angstrom": 1e-10,
           "angstroms": 1e-10}

# field names tried in order: standard NeXus first, then SWING / other variants
DISTANCE_KEYS = ("distance", "detector_distance", "sample_distance", "camera_length")
PIXEL_X_KEYS = ("x_pixel_size", "pixel_size_x", "pixel_size")
PIXEL_Y_KEYS = ("y_pixel_size", "pixel_size_z", "pixel_size_y", "pixel_size")
CENTER_X_KEYS = ("beam_center_x", "beam_centre_x", "dir_beam_x", "center_x")
CENTER_Y_KEYS = ("beam_center_y", "beam_centre_y", "dir_beam_z", "dir_beam_y", "center_y")
WAVELENGTH_KEYS = ("wavelength", "incident_wavelength")
ENERGY_KEYS = ("energy", "incident_energy")


# --------------------------------------------------------------------------
# NeXus helpers
# --------------------------------------------------------------------------
def _nx_class(obj):
    v = obj.attrs.get("NX_class", "")
    return v.decode(errors="replace") if isinstance(v, bytes) else str(v)


def _first_entry(h5):
    """Return the first NXentry group (SWING names it like '0_00435')."""
    for v in h5.values():
        if isinstance(v, h5py.Group) and _nx_class(v) == "NXentry":
            return v
    # fall back: first group
    for v in h5.values():
        if isinstance(v, h5py.Group):
            return v
    raise KeyError("No NXentry group found")


def _find_groups(grp, nx_class):
    found = []
    grp.visititems(lambda name, obj: found.append(obj)
                   if isinstance(obj, h5py.Group) and _nx_class(obj) == nx_class else None)
    return found


def _find_detector(entry):
    """The NXdetector holding the images or the geometry (None if there is none)."""
    dets = _find_groups(entry, "NXdetector")
    for d in dets:
        if "data" in d and isinstance(d["data"], h5py.Dataset) and d["data"].ndim >= 2:
            return d
    for d in dets:
        if any(k in d for k in DISTANCE_KEYS + CENTER_X_KEYS):
            return d
    return dets[0] if dets else None


def _find_image_dataset(entry, det=None):
    """Find the detector image stack: SWING's scan_data/eiger_image, else the
    NXdetector 'data' field, else the largest numeric >=2-D dataset."""
    sd = entry.get("scan_data")
    if sd is not None and "eiger_image" in sd:
        return sd["eiger_image"]
    if det is not None and "data" in det and isinstance(det["data"], h5py.Dataset) \
            and det["data"].ndim >= 2:
        return det["data"]
    cands = []
    entry.visititems(lambda name, obj: cands.append(obj)
                     if isinstance(obj, h5py.Dataset) and obj.ndim >= 2
                     and obj.dtype.kind in "uif" and min(obj.shape[-2:]) >= 32 else None)
    if not cands:
        raise KeyError("No image dataset found")
    return max(cands, key=lambda d: d.size)


def _scalar(grp, key, default=None):
    """Read a 0-D or 1-element dataset as a Python scalar/str."""
    if grp is None or key not in grp or not isinstance(grp[key], h5py.Dataset):
        return default
    v = grp[key][()]
    if isinstance(v, bytes):
        return v.decode(errors="replace")
    v = np.asarray(v).ravel()
    if v.size == 1:
        x = v[0]
        return x.decode(errors="replace") if isinstance(x, bytes) else x.item()
    return v.tolist()


def _units(ds):
    try:
        u = ds.attrs.get("units", "")
        u = u.decode(errors="replace") if isinstance(u, bytes) else str(u)
    except Exception:                   # some files store non-UTF-8 attributes
        return ""
    return u.strip().lower()


def _number(groups, keys):
    """First numeric value found under any of `keys` in `groups`, with its units."""
    for grp in groups:
        if grp is None:
            continue
        for k in keys:
            v = _scalar(grp, k)
            if isinstance(v, list) and v:  # per-frame values: take the first
                v = v[0]
            if isinstance(v, (int, float)) and np.isfinite(v):
                return float(v), _units(grp[k])
    return None, ""


def _length(groups, keys, guess_factor):
    """Length in metres; `guess_factor(value)` is used when the units are missing
    or unknown (SWING writes 'No unit')."""
    v, u = _number(groups, keys)
    if v is None:
        return None
    return v * _LENGTH.get(u, guess_factor(v))


def read_metadata(entry, det=None):
    """Collect the geometry / beam metadata needed for azimuthal integration."""
    det = det if det is not None else _find_detector(entry)
    # not NXsource: its 'energy' is the storage-ring energy
    beam_groups = _find_groups(entry, "NXmonochromator") + _find_groups(entry, "NXbeam")
    samp = entry.get("sample_info")

    # distance > 20 without units is taken as mm, else m
    dist_m = _length([det], DISTANCE_KEYS, lambda v: 1e-3 if v > 20 else 1.0)
    # pixel: < 1e-3 -> m, < 1 -> mm, else um
    pix_guess = lambda v: 1.0 if v < 1e-3 else (1e-3 if v < 1 else 1e-6)
    pix_x_m = _length([det], PIXEL_X_KEYS, pix_guess)
    pix_y_m = _length([det], PIXEL_Y_KEYS, pix_guess)

    # beam centre in pixels; converted if it is given as a length
    centers = []
    for keys, pix in ((CENTER_X_KEYS, pix_x_m), (CENTER_Y_KEYS, pix_y_m)):
        v, u = _number([det], keys)
        if v is not None and u in _LENGTH and pix:
            v = v * _LENGTH[u] / pix
        centers.append(v)

    # wavelength (< 1e-6 -> m, < 0.25 -> nm, else A), else from the energy
    wl_m = _length(beam_groups, WAVELENGTH_KEYS,
                   lambda v: 1.0 if v < 1e-6 else (1e-9 if v < 0.25 else 1e-10))
    e, eu = _number(beam_groups, ENERGY_KEYS)
    energy_keV = None if e is None else (e / 1000 if eu == "ev" or (eu != "kev" and e > 100) else e)
    if wl_m is None and energy_keV:
        wl_m = HC_KEV_A / energy_keV * 1e-10

    meta = {
        "entry": entry.name.strip("/"),
        "start_time": _scalar(entry, "start_time"),
        "end_time": _scalar(entry, "end_time"),
        "proposal_number": _scalar(entry, "proposal_number"),
        "proposal_title": _scalar(entry, "proposal_title"),
        "detector_group": det.name if det is not None else None,
        "detector": (_scalar(det, "type") or _scalar(det, "description")
                     or _scalar(det, "local_name")),
        "distance_mm": None if dist_m is None else dist_m * 1e3,
        "pixel_size_x_um": None if pix_x_m is None else pix_x_m * 1e6,
        "pixel_size_z_um": None if pix_y_m is None else pix_y_m * 1e6,
        "binning_x": _scalar(det, "binning_x"),
        "binning_y": _scalar(det, "binning_y"),
        "beam_center_x_px": centers[0],
        "beam_center_z_px": centers[1],
        "exposure_time": _scalar(det, "count_time") or _scalar(det, "exposure_time"),
        "energy_keV": energy_keV,
        "wavelength_A": None if wl_m is None else wl_m * 1e10,
    }
    if samp is not None:                 # SWING-specific extras
        meta["sample_name"] = _scalar(samp.get("ChemSAXS"), "sample_name")
        meta["sample_transmission"] = _scalar(samp, "sample_transmission")
        meta["transmitted_photon_flux"] = _scalar(samp, "transmitted_photon_flux")
    sd = entry.get("scan_data")
    if sd is not None and "eiger_timestamp" in sd:
        meta["eiger_timestamp"] = np.asarray(sd["eiger_timestamp"][()]).ravel().tolist()
    return meta


def _invalid_pixels(frame):
    """Detector gap / dead-pixel flags: max value of an integer type (Eiger),
    negative values (Pilatus -1/-2), non-finite values for float data."""
    kind = frame.dtype.kind
    if kind == "u":
        return frame == np.iinfo(frame.dtype).max
    if kind == "i":
        return (frame < 0) | (frame == np.iinfo(frame.dtype).max)
    return ~np.isfinite(frame)


def write_poni(meta, poni_path):
    """Write a pyFAI PONI file from the NeXus geometry.

    When the file has binning fields (SWING), the NeXus pixel size is the native
    one and the stored images are binned, so the effective pixel is
    pixel_size * binning. The beam centre is given in image pixels,
    x = column, z/y = row, with no flip of the image.
    """
    try:
        from pyFAI.integrator.azimuthal import AzimuthalIntegrator
    except ImportError:                   # pyFAI < 2024.10
        from pyFAI.azimuthalIntegrator import AzimuthalIntegrator
    from pyFAI.detectors import Detector

    needed = ("distance_mm", "pixel_size_x_um", "pixel_size_z_um",
              "beam_center_x_px", "beam_center_z_px", "wavelength_A")
    missing = [k for k in needed if meta.get(k) is None]
    if missing:
        raise KeyError(f"Cannot build PONI, missing NeXus metadata: {missing}")

    pix_x_um = meta["pixel_size_x_um"] * (meta.get("binning_x") or 1)
    pix_z_um = meta["pixel_size_z_um"] * (meta.get("binning_y") or 1)
    detector = Detector(pixel1=pix_z_um * 1e-6, pixel2=pix_x_um * 1e-6,
                        max_shape=tuple(meta["image_shape"]))
    ai = AzimuthalIntegrator(detector=detector)
    ai.setFit2D(meta["distance_mm"], meta["beam_center_x_px"], meta["beam_center_z_px"],
                pixelX=pix_x_um, pixelY=pix_z_um)
    ai.wavelength = meta["wavelength_A"] * 1e-10

    poni_path = Path(poni_path)
    if poni_path.exists():               # pyFAI's write() appends
        poni_path.unlink()
    ai.write(str(poni_path))
    meta["effective_pixel_size_x_um"] = pix_x_um
    meta["effective_pixel_size_z_um"] = pix_z_um
    return poni_path


# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------
def output_paths(nxs_path, out_root):
    """Files that convert_file() writes for one .nxs file."""
    stem = Path(nxs_path).stem
    out_dir = Path(out_root) / stem
    return {"dir": out_dir,
            "mask": out_dir / f"{stem}_mask.edf",
            "poni": out_dir / f"{stem}.poni",
            "meta": out_dir / f"{stem}_meta.json"}


def convert_file(nxs_path, out_root, gap_value=0, overwrite=False, progress=None):
    """progress: optional callable(done, total) called after each scan position.
    Returns the meta dict; meta['poni_file'] is None if no PONI could be built
    (the reason is in meta['poni_error'])."""
    import fabio

    nxs_path = Path(nxs_path)
    stem = nxs_path.stem
    paths = output_paths(nxs_path, out_root)
    out_dir = paths["dir"]
    out_dir.mkdir(parents=True, exist_ok=True)

    with h5py.File(nxs_path, "r") as h5:
        entry = _first_entry(h5)
        det = _find_detector(entry)
        ds = _find_image_dataset(entry, det)
        meta = read_metadata(entry, det)

        shape = ds.shape                      # e.g. (10, 10, 1083, 1035)
        img_shape = shape[-2:]
        scan_shape = shape[:-2]               # (positions, frames), (frames,) or ()
        n_frames = int(np.prod(scan_shape))
        print(f"\n{nxs_path.name}: image stack {ds.name} {shape} -> "
              f"{n_frames} frames of {img_shape}")

        meta["image_dataset"] = ds.name
        meta["scan_shape"] = list(scan_shape)
        meta["image_shape"] = list(img_shape)
        meta["n_frames"] = n_frames
        meta["gap_value_in_frame_tiffs"] = gap_value
        meta["frame_index_convention"] = (
            "p = outer scan index (sample position), f = inner index (frame at that position); "
            "flat frame number = p*scan_shape[1] + f"
        )

        mask = np.zeros(img_shape, dtype=bool)
        if det is not None and "pixel_mask" in det and det["pixel_mask"].shape[-2:] == img_shape:
            pm = np.asarray(det["pixel_mask"][()]).reshape(-1, *img_shape)
            mask |= (pm != 0).any(axis=0)     # NeXus: non-zero = bad pixel
        frame_names = []

        # Iterate over the outer index only -> one hyperslab read per position,
        # keeps memory at ~10 frames instead of the whole 450 MB stack.
        if ds.dtype.kind == "f":
            frame_dtype = np.float32
        else:
            frame_dtype = np.int32 if (gap_value < 0 or ds.dtype.kind == "i") else np.uint32
        n_pos = scan_shape[0] if len(scan_shape) > 1 else 1
        for p in range(n_pos):
            block = ds[p] if len(scan_shape) > 1 else ds[()]
            block = np.asarray(block).reshape(-1, *img_shape)

            for f, frame in enumerate(block):
                gaps = _invalid_pixels(frame)
                mask |= gaps

                out = frame.astype(frame_dtype)
                out[gaps] = gap_value
                if len(scan_shape) > 1:
                    name = f"{stem}_p{p:02d}_f{f:02d}.tif"
                else:
                    name = f"{stem}_f{f:03d}.tif"
                fp = out_dir / name
                if overwrite or not fp.exists():
                    tifffile.imwrite(fp, out, photometric="minisblack")
                frame_names.append(name)

            if progress is not None:
                progress(p + 1, n_pos)

        fabio.edfimage.EdfImage(data=mask.astype(np.uint8)).write(str(paths["mask"]))
        try:
            write_poni(meta, paths["poni"])
            meta["poni_file"] = paths["poni"].name
        except KeyError as e:
            meta["poni_file"] = None
            meta["poni_error"] = str(e.args[0])
            if paths["poni"].exists():       # don't leave a stale PONI behind
                paths["poni"].unlink()
            print(f"  !! {meta['poni_error']}")

        meta["frame_files"] = frame_names
        meta["mask_file"] = paths["mask"].name
        meta["n_masked_pixels"] = int(mask.sum())
        with open(paths["meta"], "w") as fh:
            json.dump(meta, fh, indent=2, default=str)

        print(f"  wrote {len(frame_names)} frame TIFFs, mask"
              f"{', poni' if meta['poni_file'] else ''} -> {out_dir}")
        print(f"  dist={meta['distance_mm']} mm  wl={meta['wavelength_A']} A  "
              f"beam=({meta['beam_center_x_px']}, {meta['beam_center_z_px']}) px  "
              f"pix={meta['pixel_size_x_um']} um  binning={meta['binning_x']}")
    return meta


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help=".nxs file or directory containing .nxs files")
    ap.add_argument("output", help="output directory (one sub-folder per .nxs)")
    ap.add_argument("--gap-value", type=int, default=0,
                    help="value written into gap pixels of frame TIFFs (default 0; "
                         "negative -> int32 output)")
    ap.add_argument("--overwrite", action="store_true", help="re-write existing frame TIFFs")
    ap.add_argument("--recursive", action="store_true", help="search sub-directories too")
    args = ap.parse_args()

    inp = Path(args.input)
    if inp.is_file():
        files = [inp]
    else:
        files = sorted(inp.rglob("*.nxs") if args.recursive else inp.glob("*.nxs"))
    if not files:
        sys.exit(f"No .nxs files found in {inp}")

    print(f"Found {len(files)} file(s)")
    failed = []
    for f in files:
        try:
            convert_file(f, args.output, gap_value=args.gap_value,
                         overwrite=args.overwrite)
        except Exception as e:                      # keep going on bad files
            print(f"  !! FAILED {f.name}: {e}")
            failed.append(f.name)
    if failed:
        print("\nFailed files:", *failed, sep="\n  ")


if __name__ == "__main__":
    main()
