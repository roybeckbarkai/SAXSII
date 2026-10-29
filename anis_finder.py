import numpy as np
from scipy.interpolate import interp1d
from integration_methods import full_integration, convert_radius_to_q
import os
from scipy.ndimage import gaussian_filter1d
from scipy.ndimage import median_filter



def integrate_with_anisotropy(main_window, image_filename):
    """
    Performs 1D integration on the selected image (filename, not array)
    using the same pipeline as main.py. Reconstructs a smoothed isotropic
    2D diffraction pattern. If selected min/max radius produce no valid
    pixels, falls back once to full detector and warns the user.
    """

    (
        bins,
        min_q,
        max_q,
        poni,
        mask,
        q_choice,
        dezing_thres,
        nxs_file_dict,
        datadict,
        chi_datadict,
        loadedlist,
        plot
    ) = main_window.getIntegrationParams()

    h, w = main_window.raw_image.shape
    cx, cy = main_window.beamcenterx, main_window.beamcentery
    max_possible_radius = np.sqrt(cx**2 + cy**2 + (w - cx)**2 + (h - cy)**2)

    minradius = 0
    maxradius = max_possible_radius
    ai = main_window.ai
    poni_path = main_window.poni_file

    if not os.path.exists(poni_path):
        poni_path = os.path.basename(poni_path)

    def run_integration(min_r, max_r):
        """Internal helper to avoid recursion."""
        full_integration(
            main_window,
            ai,
            image_filename,
            mask.data if hasattr(mask, "data") else mask,
            poni_path,
            dezing_thres,
            bins,
            min_r,
            max_r,
            q_choice,
            datadict,
            chi_datadict,
            False,
            nxs_file_dict
        )
        filename_key = image_filename.split('.')[0]
        if filename_key not in datadict:
            return None
        df = datadict[filename_key]
        if df.empty or df["intensity"].size == 0:
            return None
        return df

    # Try with user-selected range
    df = run_integration(minradius, maxradius)

    # Fallback once if empty
    if df is None:
        print(f"⚠️ No data found for radii ({minradius}, {maxradius}). Using full detector instead.")
        df = run_integration(0, 0)
        if df is None:
            raise RuntimeError(
                f"Integration failed even for full detector. "
                f"Check mask, image, or PONI alignment."
            )

    q = df["radial"].to_numpy()
    I = df["intensity"].to_numpy()

    # Smooth the 1D curve to reduce ring artifacts
    I_smooth = gaussian_filter1d(I, sigma=2)
    one_D_curve = {"q": q, "I": I_smooth}

    # Reconstruct isotropic 2D image
    image = main_window.raw_image
    h, w = image.shape
    cx, cy = main_window.beamcenterx, main_window.beamcentery
    y, x = np.indices(image.shape)
    r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    q_pixel = convert_radius_to_q(main_window, r)

    interp_func = interp1d(q, I_smooth, bounds_error=False, fill_value=0)
    iso_2d = interp_func(q_pixel)

    if hasattr(mask, "data"):
        iso_2d[mask.data > 0] = np.nan

    return one_D_curve, iso_2d


def subtract_iso(main_window, original_image, iso_2d, mask=None):
    if original_image.shape != iso_2d.shape:
        raise ValueError("Shape mismatch between original image and iso_2d")

    # --- Compute raw subtraction ---
    sub_2d = original_image.astype(float) - iso_2d.astype(float)

    # --- Apply mask ---
    if mask is not None:
        sub_2d[mask] = np.nan

    # --- Apply q-range limits (respect min/max radius if set) ---
    if main_window.min_radius > 0 and main_window.max_radius > 0:
        cx, cy = main_window.beamcenterx, main_window.beamcentery
        y, x = np.indices(original_image.shape)
        r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
        outside = (r < main_window.min_radius) | (r > main_window.max_radius)
        sub_2d[outside] = np.nan

    # --- Mask negative or zero values for log scaling ---
    sub_2d[sub_2d <= 0] = np.nan

    return sub_2d




def divide_iso(main_window, original_image, iso_2d, mask=None):
    if original_image.shape != iso_2d.shape:
        raise ValueError("Shape mismatch between original image and iso_2d")

    # --- Avoid division by zero ---
    iso_safe = iso_2d.astype(float)
    iso_safe[iso_safe == 0] = np.nan

    # --- Compute normalized deviation ---
    ratio_map = (original_image.astype(float) / iso_safe) - 1.0

    # --- Apply mask ---
    if mask is not None:
        ratio_map[mask] = np.nan

    # --- Apply q-range limits ---
    if main_window.min_radius > 0 and main_window.max_radius > 0:
        cx, cy = main_window.beamcenterx, main_window.beamcentery
        y, x = np.indices(original_image.shape)
        r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
        outside = (r < main_window.min_radius) | (r > main_window.max_radius)
        ratio_map[outside] = np.nan

    # --- Remove non-finite and extreme values ---
    ratio_map[np.isinf(ratio_map)] = np.nan
    ratio_map[np.isnan(ratio_map)] = np.nan

    return ratio_map