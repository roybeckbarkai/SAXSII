from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from integration_methods import unit_factor


def curve_frame(curve, unit):
    """DataFrame of a stored curve (radial in A^-1) expressed in `unit`."""
    return pd.DataFrame({
        f"radial-{unit}": np.asarray(curve["radial"]) * unit_factor(unit),
        "intensity": curve["intensity"],
        "sigma": curve["sigma"],
    })


def save_dat(filename, folder, curve, unit):
    stamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    path = Path(folder) / f"{filename}.dat"
    with open(path, "w", newline="") as f:
        f.write(f"#DAT file by SAXSII - Timestamp: {stamp}\n")
        curve_frame(curve, unit).to_csv(f, index=False)
    return path


def save_csv(filename, folder, curve, unit):
    path = Path(folder) / f"{filename}_csv.csv"
    curve_frame(curve, unit).to_csv(path, index=False, header=False)
    return path


def save_tif(path, array):
    import tifffile
    tifffile.imwrite(str(path), np.asarray(array, dtype=np.float32))
    return path

