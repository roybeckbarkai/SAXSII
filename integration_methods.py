"""GUI-free azimuthal integration helpers built on pyFAI.

All q values handled by this module are in inverse Angstrom (q_A^-1). Conversion to
the unit the user wants to look at happens only at the edges (plotting / saving).
"""
import locale
from pathlib import Path

import numpy as np
import pyFAI

# Unit keys used throughout the app -> (pyFAI unit, factor from A^-1, display label)
UNITS = {
    "q_A^-1": ("q_A^-1", 1.0, "q (Å⁻¹)"),
    "q_nm^-1": ("q_nm^-1", 10.0, "q (nm⁻¹)"),
}

# Full pixel splitting, CSR sparse matrix, cython implementation.
METHOD = ("full", "csr", "cython")
# Sigma clipping must not use pixel splitting (pyFAI: results are wrong otherwise).
SIGMA_CLIP_METHOD = ("no", "csr", "cython")


class UserError(Exception):
    """An error caused by user input that should be shown to the user verbatim."""


def unit_factor(unit):
    if unit not in UNITS:
        raise UserError(f"Unknown radial unit: {unit}")
    return UNITS[unit][1]


def load_poni(path):
    p = Path(path).expanduser()
    if not p.is_file():
        raise UserError(f"PONI file not found: {p}")
    try:  # some locales use ',' as decimal separator which breaks the PONI parser
        locale.setlocale(locale.LC_NUMERIC, "C")
    except locale.Error:
        pass
    try:
        return pyFAI.load(str(p))
    except Exception as e:
        raise UserError(f"Could not read PONI file {p.name}: {e}") from e


def calibration_info(ai):
    """Geometry in the units shown to the user (Fit2D convention for the beam center)."""
    f2d = ai.getFit2D()
    return {
        "distance_mm": float(f2d["directDist"]),
        "center_x": float(f2d["centerX"]),
        "center_y": float(f2d["centerY"]),
        "tilt": float(f2d["tilt"]),
        "tilt_plan_rotation": float(f2d["tiltPlanRotation"]),
        "pixel_x_um": float(f2d["pixelX"]),
        "pixel_y_um": float(f2d["pixelY"]),
        "wavelength_A": float(ai.wavelength * 1e10) if ai.wavelength else None,
        "detector": ai.detector.name,
    }


def set_calibration(ai, distance_mm=None, center_x=None, center_y=None, wavelength_A=None):
    """Update the geometry in place, keeping tilt and pixel size from the PONI file."""
    f2d = ai.getFit2D()
    ai.setFit2D(
        distance_mm if distance_mm is not None else f2d["directDist"],
        center_x if center_x is not None else f2d["centerX"],
        center_y if center_y is not None else f2d["centerY"],
        tilt=f2d["tilt"],
        tiltPlanRotation=f2d["tiltPlanRotation"],
        pixelX=f2d["pixelX"],
        pixelY=f2d["pixelY"],
    )
    if wavelength_A is not None:
        ai.wavelength = wavelength_A * 1e-10


def build_mask(ai, image, user_mask=None):
    """Boolean mask (True = ignore) combining the user mask, the detector mask and
    pixels that cannot be real counts (negative gap values, integer saturation, NaN)."""
    mask = ~np.isfinite(image)
    mask |= image < 0
    if np.issubdtype(image.dtype, np.integer):
        mask |= image >= np.iinfo(image.dtype).max
    det_mask = ai.detector.mask if ai is not None else None
    if det_mask is not None and det_mask.shape == image.shape:
        mask |= det_mask.astype(bool)
    if user_mask is not None:
        if user_mask.shape != image.shape:
            raise UserError(f"Mask shape {user_mask.shape} does not match image shape {image.shape}")
        mask |= user_mask.astype(bool)
    return mask


def q_range_arg(q_range):
    """(q_min, q_max) in A^-1 with None meaning open -> pyFAI radial_range or None."""
    if q_range is None:
        return None
    lo, hi = q_range
    if lo is None and hi is None:
        return None
    lo = 0.0 if lo is None else float(lo)
    hi = 1e3 if hi is None else float(hi)
    if hi <= lo:
        raise UserError("q max must be larger than q min")
    return (lo, hi)


def integrate_1d(ai, image, mask, bins, q_range=None, sigma_thres=1000.0):
    """Sigma-clipped azimuthal average. Returns dict with radial (A^-1), intensity, sigma."""
    res = ai.sigma_clip_ng(
        image.astype(np.float32),
        bins,
        mask=mask,
        unit="q_A^-1",
        radial_range=q_range_arg(q_range),
        error_model="poisson",
        thres=sigma_thres,
        method=SIGMA_CLIP_METHOD,
    )
    radial = np.asarray(res.radial, dtype=float)
    intensity = np.asarray(res.intensity, dtype=float)
    sigma = np.asarray(res.sigma, dtype=float) if res.sigma is not None else np.zeros_like(intensity)
    keep = np.isfinite(intensity) & np.isfinite(radial)
    count = getattr(res, "count", None)
    if count is not None:
        keep &= np.asarray(count) > 0  # empty bins carry no information
    if not keep.any():
        raise UserError("Integration produced no valid points - check the mask, PONI and q range.")
    return {"radial": radial[keep], "intensity": intensity[keep], "sigma": np.nan_to_num(sigma[keep])}


def cake(ai, image, mask, npt_rad=500, npt_azim=360, q_range=None):
    """2D regrouping I(q, chi). Empty bins are returned as NaN."""
    res = ai.integrate2d(image.astype(np.float32), npt_rad, npt_azim, mask=mask, unit="q_A^-1",
                         radial_range=q_range_arg(q_range), method=METHOD, dummy=np.nan)
    intensity = np.array(res.intensity, dtype=float)
    count = getattr(res, "count", None)
    if count is not None and np.shape(count) == intensity.shape:
        intensity[np.asarray(count) <= 0] = np.nan
    return {"intensity": intensity, "radial": np.asarray(res.radial, float), "chi": np.asarray(res.azimuthal, float)}


def q_to_radius_px(ai, q):
    """Approximate distance (px) from the beam center of the ring at q (A^-1), ignoring tilt.
    Used only to draw rings on the image."""
    info = calibration_info(ai)
    two_theta = 2 * np.arcsin(np.clip(q * info["wavelength_A"] / (4 * np.pi), -1, 1))
    return info["distance_mm"] * np.tan(two_theta) / (info["pixel_x_um"] * 1e-3)
