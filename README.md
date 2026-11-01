# SAXSII

Azimuthal integration and anisotropy analysis of SAXS detector images, served as a local web app.

```bash
pip install -r requirements.txt
python main.py            # opens http://localhost:5000 in your browser
python main.py --port 8080 --no-browser
```

The server listens on `127.0.0.1` only. Folder, PONI, mask and integration settings are remembered in
`settings.json`.

## Workflow

1. **Images** – open a folder (`.tif/.tiff/.edf/.cbf`, HDF5/NeXus stacks are listed frame by frame).
   *Track folder* polls for new files; *Show newest* displays each new image as it arrives.
2. **Calibration** – load a `.poni` file (from `pyFAI-calib2`) and optionally a `.msk` mask.
   Negative pixels (detector gaps), integer-saturated pixels and the detector's own mask are always excluded.
   The geometry panel lets you adjust distance / wavelength / beam center (or click *Set center* on the image).
3. **Integration** – choose bins, unit and an optional q range (type it or *Pick q min/max* on the image,
   cake or curve). Each integrated image is saved next to it as `<name>.dat`.
4. **1D curves** – overlay, background-subtract (`sample − f·background`, errors in quadrature),
   average (error of the mean), export CSV, reload `.dat` files, add q markers.
5. **Cake** – I(q, χ) map.
6. **Anisotropy** – deviation of the image from its own azimuthal average I<sub>iso</sub>(q):
   `I / I_iso − 1` (relative) or `I − I_iso` (absolute), signed and shown on a diverging colormap.
   With a q range set, the panel below shows I(χ) and the fit `I(χ) = I₀[1 + A cos 2(χ − φ)]`:
   **A** is the anisotropy amplitude (0 = isotropic) and **φ** the orientation (pyFAI χ convention).
   Optional solid-angle and polarization corrections are applied to the image and the reference alike.

## Code layout

| file | role |
| --- | --- |
| `main.py` | entry point, starts the server and opens the browser |
| `server.py` | Flask JSON API and app state |
| `integration_methods.py` | pyFAI integration, caking, masks, geometry |
| `anis_finder.py` | isotropic reference, anisotropy maps, χ profile |
| `image_io.py` | folder listing, image/HDF5 reading, PNG rendering |
| `saving_methods.py` | `.dat` / `.csv` / `.tif` writers |
| `web/` | browser frontend (plain JS + Plotly) |
