"""Saved analysis results, kept next to the data in <image folder>/analysis/.

Each analysis kind (anisotropy now; Guinier, Porod, ... later) has a ResultTable: one row
per analysed image (and parameter set), stored as <kind>_results.csv so it survives
restarts, opens in Excel/Origin, and can be plotted column-vs-column in the Results tab.
Adding a new analysis = registering a table here and calling `upsert` with its row.
"""
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ANALYSIS_DIR = "analysis"


def analysis_dir(folder, *sub):
    p = Path(folder, ANALYSIS_DIR, *sub)
    p.mkdir(parents=True, exist_ok=True)
    return p


class ResultTable:
    def __init__(self, kind, title, columns):
        """columns: list of (key, label, numeric). The first column must be 'id'."""
        self.kind, self.title, self.columns = kind, title, columns
        self.keys = [c[0] for c in columns]

    def path(self, folder):
        return Path(folder, ANALYSIS_DIR, f"{self.kind}_results.csv")

    def load(self, folder):
        p = self.path(folder)
        if not p.is_file():
            return []
        df = pd.read_csv(p)
        df = df.astype(object).where(pd.notna(df), None)
        return df.to_dict("records")

    def _write(self, folder, rows):
        analysis_dir(folder)
        df = pd.DataFrame(rows)
        # known columns first (in order), anything unknown (e.g. from a newer version) after
        cols = [k for k in self.keys if k in df.columns] + [k for k in df.columns if k not in self.keys]
        df[cols].to_csv(self.path(folder), index=False)

    def upsert(self, folder, row):
        """Insert or replace (same 'id') a row and rewrite the CSV."""
        rows = [r for r in self.load(folder) if r.get("id") != row["id"]]
        rows.append({k: _plain(v) for k, v in row.items()})
        self._write(folder, rows)
        return rows

    def delete(self, folder, ids):
        rows = [r for r in self.load(folder) if r.get("id") not in set(ids)]
        if rows:
            self._write(folder, rows)
        elif self.path(folder).exists():
            self.path(folder).unlink()
        return rows

    def describe(self):
        return {"kind": self.kind, "title": self.title,
                "columns": [{"key": k, "label": l, "numeric": n} for k, l, n in self.columns]}


def _plain(v):
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    return v


def write_profile(path, columns, header):
    """Write a 1D result (e.g. I(chi)) with '#' metadata lines, readable by any program."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w", newline="") as f:
        f.write(f"# SAXSII {stamp}\n")
        for k, v in header.items():
            f.write(f"# {k}: {v}\n")
        pd.DataFrame(columns).to_csv(f, index=False)
    return path


def read_profile(path):
    return pd.read_csv(path, comment="#")


TABLES = {
    "anisotropy": ResultTable("anisotropy", "Anisotropy (I(χ) fit)", [
        ("id", "ID", False),
        ("image", "Image", False),
        ("index", "Image number", True),
        ("time", "Acquisition time", False),
        ("q_min_A", "q min (Å⁻¹)", True),
        ("q_max_A", "q max (Å⁻¹)", True),
        ("amplitude", "Anisotropy A", True),
        ("orientation_deg", "Orientation φ (°)", True),
        ("i0", "Mean intensity I₀", True),
        ("max_over_min", "max / min", True),
        ("coverage", "χ coverage", True),
        ("pixels", "Pixels used", True),
        ("solid_angle", "Solid-angle corr.", False),
        ("polarization", "Polarization factor", True),
        ("beamstop_masked", "Beamstop masked", False),
        ("profile", "Profile file", False),
    ]),
}
