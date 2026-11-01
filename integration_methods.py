import time
import numpy
import re
import fabio
import numpy as np
import pandas as pd
import pyFAI
from silx.gui.qt import QMessageBox, QFont
from utils import dotdict
from saving_methods import save_dat


# Converts any q to the corresponding 2d image distance from the beamcenter.
def convert_radius_to_q(self, radius):
    def func(x):
        return (4 * numpy.pi * (numpy.sin((numpy.arctan2(x * self.pixel_size * 0.001, self.distance * 1000) / 2)))) / (
                self.wavelength / 10 ** (-10))

    return func(radius)  # #


# Converts any 2d image distance from the beamcenter to the corresponding q.
def convert_q_to_radius(self, q):
    def func(q_):
        return (self.distance * 1000) * numpy.tan(
            2 * numpy.arcsin((q_ * (self.wavelength * (10 ** 10))) / (4 * np.pi))) / (self.pixel_size * 0.001)

    return func(q)
    # x = 0
    # rad = self.min_radius
    # delta = 0.1
    # statement = 0
    # while statement == 0:
    #     q_temp = convert_radius_to_q(self, x)
    #     if q_temp + delta > q > q_temp - delta:
    #         rad = x
    #         statement = 1
    #     if x > self.max_radius:
    #         statement = 1
    #         rad = self.max_radius
    #     x = x + delta


# Azimuthal integration.
def full_integration(self, ai, image, mask, poni, dezing_thres, bins, minradius, maxradius, q_choice, datadict,
                     chi_datadict, nxs,
                     nxs_file_dict):
    if not nxs:
        imagefolder = self.imagepath
        imagepath = imagefolder + '/' + image
        img = fabio.open(imagepath)
        filename = image.split('.')[0]
        img_array = img.data
    if nxs:
        imagefolder = self.imagepath
        image_name = image.split('.')[0] + '.nxs'
        image_data = nxs_file_dict[image_name][image]
        img_array = image_data
        filename = image
    t0 = time.time()
    res = ai.sigma_clip_ng(img_array,
                           bins,
                           mask=mask,
                           unit=q_choice,
                           error_model='poisson',
                           thres=dezing_thres,
                           method=("full", "csr", "cython"))
    # chi, chi_I = ai.integrate_radial(img_array,
    #                                  bins,
    #                                  mask=mask,
    #                                  unit='chi_rad',
    #                                  radial_unit='2th_rad',
    #                                  radial_range=(minradius, maxradius),
    #                                  method=("full", "csr", "opencl"))
    df = pd.DataFrame.from_dict({'radial': res.radial, 'intensity': res.intensity, 'sigma': res.sigma},
                                orient='columns')
    df = df[(df.radial > minradius) & (df.radial < maxradius)]
    df = df.dropna(axis=0)
    df.reset_index(inplace=True, drop=True)
    datadict[filename] = df
    save_dat(filename, self.imagepath, df, q_choice)
    #
    # df_chi = pd.DataFrame.from_dict({'Azimuthal': chi, 'intensity': chi_I},
    #                                 orient='columns')
    # df_chi = df_chi.dropna(axis=0)
    # df_chi.reset_index(inplace=True, drop=True)
    # chi_datadict[filename] = df_chi
    # save_dat(filename+"_chi", self.imagepath, df_chi, 'rad')
    print(time.time() - t0)


# 2D plot of Chi VS q VS Intensity.
def caking(self, ai, image, q_choice):
    res2d = ai.integrate2d(image, 300, 360, unit=q_choice)
    return res2d


# Prelimenary step for integration (of any kind).
def send_to_integration(self, imagelist):
    if getattr(self, 'ai', None) is None:
        msg = QMessageBox()
        msg.setWindowTitle("Error")
        msg.setText("Please Load a PONI File (or use the Calibration Tool) before integrating")
        x = msg.exec_()
        return
    tif_images = [image for image in imagelist if image.endswith('.tiff') or image.endswith('.tif')]
    bins, minradius, maxradius, poni, mask, q_choice, dezing_thres, nxs_file_dict, datadict, chi_datadict, loadedlist, \
    plot = self.getIntegrationParams(tif_images[0] if tif_images else None)
    tw = self.tw
    ai = self.ai
    loadeditemsTextList = [str(loadedlist.item(i).text()) for i in range(loadedlist.count())]
    if len(imagelist) == 0:
        msg = QMessageBox()
        msg.setWindowTitle("Error")
        msg.setText("Please Select an Image to Integrate")
        x = msg.exec_()
    else:
        length = len(imagelist)
        i = 1
        for image in imagelist:
            pvalue = int((i / length) * 100)
            self.progressbar.setValue(pvalue)
            self.progressbar.setFont(QFont('Segoe UI', 9))
            # if image not in loadeditemsTextList:
            if image.endswith('.tiff') or image.endswith('.tif'):
                full_integration(self, ai=ai, image=image, poni=poni, mask=mask.data, bins=bins, minradius=minradius,
                                 maxradius=maxradius, q_choice=q_choice, dezing_thres=dezing_thres, datadict=datadict,
                                 chi_datadict=chi_datadict,
                                 nxs=False,
                                 nxs_file_dict=nxs_file_dict)
                filename = image.split('.')[0]
                res = datadict[filename]
                plot.addCurve(x=res.radial, y=res.intensity, legend='{}'.format(filename),
                              linewidth=2)
                if image not in loadeditemsTextList:
                    loadedlist.addItem(image)
            regexp = re.compile(r'(?:nxs - image ).*$')
            if regexp.search(image):
                full_integration(self, ai=ai, image=image, poni=poni, mask=mask.data, bins=bins, minradius=minradius,
                                 maxradius=maxradius, q_choice=q_choice, dezing_thres=dezing_thres, datadict=datadict,
                                 chi_datadict=chi_datadict, nxs=True,
                                 nxs_file_dict=nxs_file_dict)
                res = datadict[image]
                plot.addCurve(x=res.radial, y=res.intensity, legend='{}'.format(image),
                              linewidth=2)
                if image not in loadeditemsTextList:
                    loadedlist.addItem(image)
            if image.endswith('.nxs'):
                None
            i += 1


