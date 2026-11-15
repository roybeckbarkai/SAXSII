"""Automatic beamstop (and beamstop arm) detection.

The beamstop is a shadow around the beam center:
  * the disk: rings around the center whose typical (median) intensity is far below the
    brightest ring just outside it - SAXS intensity otherwise only falls with radius;
  * the arm and edges: pixels much darker than the typical intensity of their own ring.
Dark pixels only count when they are connected to the disk / center, so weak scattering
elsewhere on the detector is not masked.

Parameters (all exposed in the UI):
  threshold   shadowed if intensity < threshold x typical intensity at that radius
  smooth      Gaussian smoothing (px) applied first, so counting noise is not mistaken for shadow
  margin      the result is grown by this many px to cover the blurred shadow edge
  min_area    detached dark patches smaller than this (px) are ignored
  bridge_gaps let the arm continue across detector gaps (which are masked anyway)
"""
import numpy as np
from scipy import ndimage

DEFAULTS = {"threshold": 0.3, "smooth": 2.0, "margin": 3, "min_area": 200, "bridge_gaps": True}


def _smoothed(image, valid, sigma):
    """Gaussian smoothing that ignores invalid pixels (normalized convolution)."""
    img = np.where(valid, image, 0.0).astype(float)
    if sigma <= 0:
        return np.where(valid, img, np.nan)
    num = ndimage.gaussian_filter(img, sigma)
    den = ndimage.gaussian_filter(valid.astype(float), sigma)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    out[den < 0.2] = np.nan  # mostly invalid neighbourhood: no reliable value
    return out


def _ring_median(values, rings, ok, n):
    """Median of `values` over each integer-radius ring (NaN for empty rings)."""
    v, r = values[ok], rings[ok]
    order = np.lexsort((v, r))
    v, r = v[order], r[order]
    counts = np.bincount(r, minlength=n)
    starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
    prof = np.full(n, np.nan)
    has = counts > 0
    prof[has] = v[starts[has] + counts[has] // 2]
    return prof


def detect_beamstop(image, invalid, center, threshold=0.3, smooth=2.0, margin=3, min_area=200, bridge_gaps=True):
    """Return (beamstop_mask, stats). `invalid` = pixels already masked (gaps, user mask);
    `center` = (x, y) beam center in pixels."""
    h, w = image.shape
    valid = ~invalid & np.isfinite(image)
    sm = _smoothed(image, valid, float(smooth))

    yy, xx = np.indices((h, w))
    r = np.hypot(xx + 0.5 - center[0], yy + 0.5 - center[1])
    rings = r.astype(np.int64)
    n = int(rings.max()) + 1
    prof = _ring_median(sm, rings, valid & np.isfinite(sm), n)

    # Disk: dark rings inside the brightest ring of the inner part of the detector.
    inner = prof[: max(n // 3, 10)]
    if np.isfinite(inner).any():
        r_peak = int(np.nanargmax(inner))
        dark = ~(prof[:r_peak] >= threshold * inner[r_peak])  # NaN (no data) counts as dark
        r_disk = int(np.nonzero(dark)[0].max()) + 1 if dark.any() else 0
    else:
        r_peak, r_disk = 0, 0
    disk = r < r_disk

    # Arm / edges: much darker than the ring they sit on.
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = sm / prof[rings]
    cand = (valid & (ratio < threshold)) | disk

    seed = disk if r_disk > 0 else (r < 3)
    connect = cand | invalid if bridge_gaps else cand
    labels, _ = ndimage.label(connect)
    keep = np.unique(labels[seed & connect])
    region = np.isin(labels, keep[keep > 0]) & cand

    if min_area > 0 and region.any():
        lab2, n2 = ndimage.label(region)
        sizes = np.bincount(lab2.ravel(), minlength=n2 + 1)
        touches_seed = np.zeros(n2 + 1, bool)
        touches_seed[np.unique(lab2[seed & region])] = True
        ok = (sizes >= min_area) | touches_seed
        ok[0] = False
        region = ok[lab2]

    if margin > 0 and region.any():
        region = ndimage.distance_transform_edt(~region) <= margin

    stats = {"disk_radius_px": r_disk, "pixels": int(region.sum()),
             "arm_pixels": int((region & ~(r < r_disk + margin)).sum()), "peak_radius_px": r_peak}
    return region, stats
