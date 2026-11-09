"""Anisotropy analysis.

The isotropic reference I_iso(x, y) is the azimuthal average of the image evaluated at
the q of every pixel. Comparing the image with it highlights anisotropic scattering:

    subtract:  I - I_iso            (signed, same units as the image)
    divide:    I / I_iso - 1        (signed, relative deviation)

The reference and the image must be in the same "frame", so the azimuthal average is
taken over the very same (optionally corrected) pixel values it is compared with. The
q of every pixel comes from pyFAI, so detector tilt and the PONI beam center are honoured.
"""
import numpy as np

from integration_methods import METHOD, UserError, q_range_arg


def corrected_image(ai, image, solid_angle=False, polarization=None):
    """Image divided by the requested geometric corrections (as float, NaNs untouched)."""
    img = image.astype(float)
    if solid_angle:
        sa = ai.solidAngleArray(image.shape, absolute=False)
        img = img / sa
    if polarization is not None:
        pol = ai.polarization(image.shape, factor=float(polarization))
        img = img / pol
    return img


def pixel_q_map(ai, shape):
    return ai.array_from_unit(shape, "center", "q_A^-1")


def pixel_chi_map(ai, shape):
    return ai.array_from_unit(shape, "center", "chi_deg")


def isotropic_reference(ai, image, mask, bins=1000):
    """Return (q, I(q), I_iso 2D) for an already corrected float image.

    Pixels outside the q range covered by valid bins and masked pixels are NaN."""
    res = ai.integrate1d(image, bins, mask=mask, unit="q_A^-1", correctSolidAngle=False,
                         polarization_factor=None, method=METHOD)
    q = np.asarray(res.radial, float)
    I = np.asarray(res.intensity, float)
    valid = np.isfinite(I)
    count = getattr(res, "count", None)
    if count is not None:
        valid &= np.asarray(count) > 0
    if valid.sum() < 2:
        raise UserError("Not enough valid pixels to build the isotropic reference - check mask and PONI.")
    q, I = q[valid], I[valid]
    qmap = pixel_q_map(ai, image.shape)
    iso = np.interp(qmap, q, I, left=np.nan, right=np.nan)
    iso[mask] = np.nan
    return q, I, iso


def anisotropy_map(ai, image, mask, mode="divide", bins=1000, q_range=None,
                   solid_angle=False, polarization=None):
    """Signed anisotropy map. mode: 'subtract' -> I - I_iso, 'divide' -> I / I_iso - 1."""
    if mode not in ("subtract", "divide"):
        raise UserError(f"Unknown anisotropy mode: {mode}")
    img = corrected_image(ai, image, solid_angle, polarization)
    img[mask] = np.nan
    q, I, iso = isotropic_reference(ai, img, mask, bins)

    if mode == "subtract":
        out = img - iso
    else:
        with np.errstate(divide="ignore", invalid="ignore"):
            out = img / iso - 1.0
        # A ratio against a (near) empty reference is meaningless, not "infinitely anisotropic".
        out[~(iso > 0)] = np.nan

    rng = q_range_arg(q_range)
    if rng is not None:
        qmap = pixel_q_map(ai, image.shape)
        out[(qmap < rng[0]) | (qmap > rng[1])] = np.nan
    out[~np.isfinite(out)] = np.nan
    if not np.isfinite(out).any():
        raise UserError("The anisotropy map is empty - check the q range, mask and PONI.")
    return out, {"q": q, "intensity": I}


def fill_chi_gaps(chi_deg, prof):
    """Fill empty chi bins (detector gaps, beamstop): first from the bin 180 deg away
    (small-angle patterns are centrosymmetric, I(chi) = I(chi + 180)), then by circular
    linear interpolation between neighbouring bins. Assumes uniform bins over 360 deg."""
    out = np.array(prof, dtype=float)
    n = len(out)
    if n % 2 == 0:
        opposite = np.roll(out, n // 2)
        gap = ~np.isfinite(out)
        out[gap] = opposite[gap]
    ok = np.isfinite(out)
    if not ok.all():
        out[~ok] = np.interp(chi_deg[~ok], chi_deg[ok], out[ok], period=360.0)
    return out


def orientation_factors(chi_deg, prof, reference_deg, subtract_baseline=False):
    """Orientation descriptors of I(chi) about the axis at `reference_deg`
    (beta = chi - reference):

      hermans_f  f = (3 <cos^2 beta> - 1) / 2 with <cos^2 beta> = sum I |sin b| cos^2 b / sum I |sin b|
                 (Hermans orientation factor; the |sin beta| weight treats beta as a polar angle,
                 i.e. assumes symmetry about the reference axis lying in the detector plane).
                 0 = isotropic, 1 = all intensity along the axis, -1/2 = perpendicular to it.
      order_2d   S = 2 <cos^2 beta> - 1 = <cos 2 beta> with a plain (unweighted) average: the
                 in-plane order parameter, no 3D assumption. 0 = isotropic, +-1 = along / across.

    With subtract_baseline the minimum of I(chi) (isotropic background) is removed first.
    Empty bins are filled with fill_chi_gaps so they do not bias the averages."""
    I = fill_chi_gaps(chi_deg, prof)
    if subtract_baseline:
        I = I - I.min()
    if not np.isfinite(I).all() or I.sum() <= 0:
        return {"hermans_f": None, "order_2d": None}
    beta = np.deg2rad(chi_deg - reference_deg)
    cos2 = np.cos(beta) ** 2
    w = I * np.abs(np.sin(beta))
    cos2_3d = (w * cos2).sum() / w.sum() if w.sum() > 0 else np.nan
    cos2_2d = (I * cos2).sum() / I.sum()
    return {"hermans_f": float((3 * cos2_3d - 1) / 2) if np.isfinite(cos2_3d) else None,
            "order_2d": float(2 * cos2_2d - 1)}


def chi_profile(ai, image, mask, q_range, nbins=180, solid_angle=False, polarization=None,
                f_reference_deg=None, f_subtract_baseline=False):
    """Mean intensity vs azimuthal angle chi (deg) for pixels with q in q_range (A^-1).

    Also returns anisotropy descriptors from fitting I(chi) = I0 [1 + A cos 2(chi - phi)]:
      * amplitude: A (0 = isotropic); robust to noise and to missing azimuthal sectors.
      * orientation_deg: phi, the chi of maximum intensity (pyFAI chi convention).
    and the Hermans orientation factor / 2D order parameter (see orientation_factors) about
    f_reference_deg (None = the fitted phi).
    """
    rng = q_range_arg(q_range)
    if rng is None:
        raise UserError("Set a q range (q min and q max) for the chi profile.")
    img = corrected_image(ai, image, solid_angle, polarization)
    img[mask] = np.nan
    _, _, iso = isotropic_reference(ai, img, mask)
    qmap = pixel_q_map(ai, image.shape)
    chi = pixel_chi_map(ai, image.shape)
    sel = (~mask) & np.isfinite(img) & (iso > 0) & (qmap >= rng[0]) & (qmap <= rng[1])
    if sel.sum() == 0:
        raise UserError("No unmasked pixels in that q range.")

    # Average I / I_iso(q) per chi bin, then rescale by the mean intensity of the band.
    # Dividing first removes the q dependence, so detector gaps or a ring partially off the
    # detector (each chi bin seeing a different q mix) do not show up as fake anisotropy.
    edges = np.linspace(-180.0, 180.0, nbins + 1)
    idx = np.clip(np.digitize(chi[sel], edges) - 1, 0, nbins - 1)
    sums = np.bincount(idx, weights=img[sel] / iso[sel], minlength=nbins)
    counts = np.bincount(idx, minlength=nbins)
    with np.errstate(invalid="ignore", divide="ignore"):
        prof = sums / counts * img[sel].mean()
    prof[counts == 0] = np.nan
    centers = 0.5 * (edges[:-1] + edges[1:])

    # Least-squares fit I(chi) = a0 + c cos 2chi + s sin 2chi over the bins that have data
    # (a plain Fourier sum would turn missing bins - gaps, beamstop - into fake anisotropy).
    ok = np.isfinite(prof)
    if ok.sum() < 4:
        raise UserError("Too few azimuthal bins have data in that q range.")
    rad = np.deg2rad(centers[ok])
    design = np.column_stack([np.ones_like(rad), np.cos(2 * rad), np.sin(2 * rad)])
    (a0, c, s), *_ = np.linalg.lstsq(design, prof[ok], rcond=None)
    metrics = {
        "amplitude": float(np.hypot(c, s) / a0) if a0 > 0 else None,
        "orientation_deg": float(np.rad2deg(np.arctan2(s, c)) / 2),
        "max_over_min": float(np.nanmax(prof) / np.nanmin(prof)) if np.nanmin(prof) > 0 else None,
        "coverage": float(ok.mean()),
        "pixels": int(sel.sum()),
        "i0": float(a0),
    }
    ref = metrics["orientation_deg"] if f_reference_deg is None else float(f_reference_deg)
    metrics.update(orientation_factors(centers, prof, ref, f_subtract_baseline))
    metrics["f_reference_deg"] = ref
    metrics["f_baseline_subtracted"] = bool(f_subtract_baseline)
    crad = np.deg2rad(centers)
    fit = a0 + c * np.cos(2 * crad) + s * np.sin(2 * crad)
    return {"chi": centers, "intensity": prof, "fit": fit}, metrics
