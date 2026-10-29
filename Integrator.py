import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import tifffile as tif
import fabio
import os
import integration_methods
import math


def integrate_data(data, buffer, mask, dist, q_vec, l2, lamb, ap):
    # code that collects the photon numbers within an arbitrary q binning
    # scheme, and returns the average intensity per solid angle
    # returns the photon q density at q vector bins (photons/(Aˆ-2))
    # data is the signal
    # buffer is the background
    # dist is the q distance of each pixel
    # l2 is the sample-detector distance (cm)
    # lamb is the wavelength,
    # ap is the pixel area
    # nondata = 1000
    # data[data >= nondata] = np.nan
    # buffer[buffer >= nondata] = np.nan
    data[mask > 0] = np.nan
    buffer[mask > 0] = np.nan
    dqv = np.diff(q_vec)
    ll = q_vec - np.concatenate((np.array([dqv[0] / 2], float), dqv / 2))
    ul = q_vec + np.concatenate((dqv / 2, np.array([dqv[-1] / 2], float)))
    B = np.zeros(len(q_vec))
    S = B.copy()
    for i in range(len(q_vec)):
        bd = buffer[np.where((dist >= ll[i]) & (dist <= ul[i]) & (np.isfinite(buffer)))]
        dd = data[np.where((dist >= ll[i]) & (dist <= ul[i]) & (np.isfinite(data)))]
        B[i] = np.mean(bd)
        S[i] = np.mean(dd)
    I = S - B
    finite_index = np.isfinite(I)
    I = I[finite_index]
    q = q_vec[finite_index]
    dI = (I * lamb ** 2 * l2 ** 2 / (4 * np.pi ** 2 * ap) /
          (1 - lamb ** 2 / 8 / np.pi ** 2 * q ** 2) ** 3)
    # dI[np.where(dI == np.nan)] = 0
    return q, dI


def create_qtable(center=(), dist=0, size=(1028, 1062), pixel_size=0.075, wavelength=1.54, save_address=''):
    # gets center in pixels x,y. size is also in pixels. dety and pixel size in mm. wavelength in angstrom
    detector_grid = np.sqrt(np.add.outer((np.arange(size[1]) * pixel_size - center[1] * pixel_size) ** 2,
                                         (np.arange(size[0]) * pixel_size - center[0] * pixel_size) ** 2))
    theta_out_array = np.arctan(np.abs(detector_grid) / dist)  # Array of all possible exit
    qtable = 4 * np.pi * np.sin(theta_out_array) / wavelength
    # tif.imwrite(save_address, qtable, photometric='minisblack')
    return qtable


def find_center(PATH='', pixel_size=0.075):
    # gets calibration properties file and converts it to x,y center position
    with open(PATH, 'r') as poni:
        contents = poni.read().split('\n')
    ycoor = float([i for i in contents if 'Poni1' in i][0].split(': ')[1])
    xcoor = float([i for i in contents if 'Poni2' in i][0].split(': ')[1])
    xpixel = xcoor * 1000 / pixel_size
    ypixel = ycoor * 1000 / pixel_size
    return xpixel, ypixel


def integrate_sample(sample_path, buffer_path, mask_path, poni_path, L2, pixel_size=75, wavelength=1.54, q_min=None,
                     q_max=None, q_vec=None, binning=None, redo_integration=True, qtable_path=None):
    path_int = sample_path + f"integrated_{math.ceil(L2)}_sample.dat"
    if not redo_integration:
        if os.path.isfile(path_int):
            Sp = pd.read_csv(path_int, names=('q', 'I'), skiprows=2, sep=',')
            I_arr = Sp.values[:, 1]  # Read only selected values of I, dI
            q_arr = Sp.values[:, 0]  # Read only selected values of q
            if q_min is not None:
                mini = np.where(q_arr >= q_min)[0][0]
            else:
                mini = 0
            if q_max is not None:
                maxi = np.where(q_arr <= q_max)[-1][-1]
            else:
                maxi = len(q_arr) - 1
            if binning is None:
                return q_arr[mini:maxi], I_arr[mini:maxi]
            else:
                return Methods.logbin_data(q_arr[mini:maxi], I_arr[mini:maxi], binning)

    sample_data = tif.imread(sample_path)
    if buffer_path is not None:
        buffer_data = tif.imread(buffer_path)
    else:
        buffer_data = np.zeros_like(sample_data)
    if mask_path is not None:
        mask_data = fabio.open(mask_path).data
    else:
        mask_data = np.zeros_like(sample_data)
    if qtable_path is None:
        qtable = create_qtable(center=find_center(poni_path)
                               , dist=L2, size=sample_data.shape)
    else:
        qtable = tif.imread(qtable_path)
    if q_vec is None:
        center = find_center(poni_path)
        Ly, Lx = qtable.shape
        yqs = np.unique(qtable[int(center[1]), :])
        xqs = np.unique(qtable[:, int(center[0])])
        q_vec = yqs if len(xqs) < len(yqs) else xqs
        # q_arr = q_arr[q_arr > 1]
        # x = np.arange(int(center[0]), Lx)
        # q_vec = qtable[int(center[1]), x]
        if q_min is not None:
            q_vec = q_vec[q_vec >= q_min]
        if q_max is not None:
            q_vec = q_vec[q_vec <= q_max]
    if binning is not None:
        q_vec = np.linspace(q_vec[0], q_vec[-1], binning)
    

    # After integration:
    q_data, I_data = integrate_data(sample_data, buffer_data, mask_data, qtable, q_vec,
                                    L2 / 10, wavelength, ap=pixel_size ** 2)
    dt = pd.DataFrame({"q": q_data, "I": I_data})
    dt.to_csv(path_int, index=False, sep=',')

    # === NEW CODE TO GENERATE ISOTROPIC 2D IMAGE ===
    from scipy.interpolate import interp1d
    I_interp = interp1d(q_data, I_data, bounds_error=False, fill_value=0)

    # Create new_2d image
    new_2d = I_interp(qtable)

    # Optionally apply mask or normalization
    new_2d[mask_data > 0] = np.nan

    # Save as tif file (optional)
    new_2d_path = sample_path + f"reconstructed_{math.ceil(L2)}_2d.tif"
    tif.imwrite(new_2d_path, new_2d.astype(np.float32))

    # ================================================

    return q_data, I_data, new_2d
