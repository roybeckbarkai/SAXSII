from PyQt5.QtCore import QDir
from PyQt5.QtWidgets import QInputDialog, QLineEdit
from silx.gui import colors
from silx.gui.qt import QMessageBox
from saving_methods import save_dat
import numpy as np
import cv2
import numpy
import pandas as pd
import rasterio
import fabio

from glob import glob
import os


def curve_plot_settings(self, plot):
    plot.clear()
    q_choice = self.q_combo.currentText()
    plot.setGraphYLabel('Intensity')
    plot.setGraphXLabel('Scattering vector {}'.format(q_choice))
    plot.setYAxisLogarithmic(True)
    plot.setKeepDataAspectRatio(False)
    plot.setAxesDisplayed(True)
    # plot.setGraphGrid(which='both')
    self.toolbar1.setVisible(False)
    self.toolbar2.setVisible(True)


def image_plot_settings(self, plot):
    # plot.getDefaultColormap().setName('jet')
    cm = plot.getDefaultColormap()
    plot.setDefaultColormap(cm)
    plot.setYAxisLogarithmic(False)
    plot.setKeepDataAspectRatio(True)
    plot.setGraphGrid(which=None)
    plot.setGraphYLabel('')
    plot.setGraphXLabel('')
    self.toolbar1.setVisible(True)
    self.toolbar2.setVisible(False)


def plot_restricted_radius_image(self, plot, image, new_image):
    plot.clear()
    if new_image:
        plot.addImage(image,  resetzoom=True)
        self.displayed_image_range = plot.getDataRange()
        self.max_radius = numpy.sqrt((self.displayed_image_range[0][1] - self.beamcenterx) ** 2 + (
                self.displayed_image_range[1][1] - self.beamcentery) ** 2)
    else:
        centerx = int(self.beamcenterx)
        centery = int(self.beamcentery)
        cv2.circle(image, (centerx, centery), int(self.min_radius), (255, 255, 255), 3)
        cv2.circle(image, (centerx, centery), int(self.max_radius), (255, 255, 255), 3)
        cv2.drawMarker(image, (centerx, centery), color=(255, 255, 255), markerSize=25, thickness=2)
        cm = plot.getColormap()
        pn = plot.getName()
        plot.addImage(image, resetzoom=True, legend='image', replace=False)
        # plot.setDefaultColormap(cm)
        # plot.setDefaultColormap.setName(pn)
        # image_plot_settings(self,plot)
        self.displayed_image_range = plot.getDataRange()


def plot_center_beam_image(self, plot, image):
    plot.clear()
    centerx = int(self.beamcenterx)
    centery = int(self.beamcentery)
    self.beamcenterxdisplay.setText('%.2f' % centerx)
    self.beamcenterydisplay.setText('%.2f' % centery)
    cv2.drawMarker(image, (centerx, centery), color=(255, 255, 255), markerSize=25, thickness=2)
    cv2.circle(image, (centerx, centery), int(self.min_radius), (255, 255, 255), 3)
    cv2.circle(image, (centerx, centery), int(self.max_radius), (255, 255, 255), 3)
    plot.addImage(image, resetzoom=True)
    # image_plot_settings(self,plot)
    self.set_min_button.setEnabled(True)
    self.set_max_button.setEnabled(True)
    self.setqminAction.setEnabled(True)
    self.setqmaxAction.setEnabled(True)
    self.set_max_button.setToolTip('You can also right click the plot!')
    self.set_min_button.setToolTip('You can also right click the plot!')


#
def colorbank():
    bank = ['blue', 'red', 'black', 'green']
    i = 0
    while True:
        yield bank[i]
        i += 1
        i = i % len(bank)


def plot_mul_curves(self):
    loadedlist = self.loadedlistwidget
    plot = self.getPlotWidget()
    plot.clear()
    curve_plot_settings(self, plot)
    datadict = self.idata
    if datadict == {}:
        print('No images')
    else:
        curvelist = [item.text() for item in loadedlist.selectedItems()]
        a = colorbank()
        for curve in curvelist:
            color = next(a)
            if '.tif' in curve:
                name = curve.split('.')[0]
            else:
                name = curve
            res = datadict[name]
            plot.addCurve(x=res['radial'], y=res['intensity'], legend='{}'.format(name),
                          color=color,
                          linewidth=2)


def subtractcurves(self):
    loadedlist = self.loadedlistwidget
    plot = self.getPlotWidget()
    datadict = self.idata
    curvelist = [item.text() for item in loadedlist.selectedItems()]
    curvenames = []
    for curve in curvelist:
        if '.nxs' in curve:
            curvenames.append(curve)
        else:
            curvenames.append(curve.split('.')[0])

    if len(curvelist) == 2:
        name1 = curvenames[0]
        name2 = curvenames[1]
        res1 = datadict[name1]
        res2 = datadict[name2]
        res3_intensity = abs(np.subtract(res1.intensity, res2.intensity))
        res3 = pd.DataFrame.from_dict({'radial': res1.radial, 'intensity': res3_intensity, 'sigma': res1.sigma},
                                      orient='columns')
        name3, ok = QInputDialog().getText(self, "File Name",
                                           "Please enter a file name:", QLineEdit.Normal,
                                           name1 + ' subtracted')
        if ok and name3:
            datadict[name3] = res3
            loadedlist.addItem(name3)
            plot.addCurve(x=res1.radial, y=res3_intensity, legend='{}'.format(name1 + ' SUBTRACT ' + name2),
                          linewidth=1,
                          color='green')
            plot.setGraphGrid(which='both')
            save_dat(name3, self.imagepath, res3, self.q_combo.currentText())
        else:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Critical)
            msg.setText("Error")
            msg.setInformativeText('Please enter a name and try again.')
            msg.setWindowTitle("Error")
            msg.exec_()
    else:
        msg = QMessageBox()
        msg.setWindowTitle("Error")
        msg.setText("Please select only 2 curves to subtract")
        x = msg.exec_()


def subtract_2d(self, list_id):
    # Read all data as a list of numpy arrays
    if list_id == 0:
        listw = self.loadedlistwidget
        fp = [self.imagepath + '/' + item.text() for item in listw.selectedItems()]
    else:
        listw = self.tw
        fp = [self.imagepath + '/' + item.text(0) for item in listw.selectedItems()]
    file_list = listw.selectedItems()
    array_list = [read_file(path) for path in fp]
    if len(array_list) == 2:
        # Perform averaging
        array_out = abs(np.subtract(array_list[0], array_list[1]))
        # Get metadata from one of the input files
        with rasterio.open(fp[0]) as src:
            meta = src.meta

        meta.update(dtype=rasterio.float32)
        # Write output file
        text, ok = QInputDialog().getText(self, "File Name",
                                          "Please enter a file name:", QLineEdit.Normal,
                                          'Subtracted')
        if ok and text:
            save_path = '{}/{}.tif'.format(self.imagepath, text)
            with rasterio.open(save_path, 'w', **meta) as dst:
                dst.write(array_out.astype(rasterio.float32), 1)
        else:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Critical)
            msg.setText("Error")
            msg.setInformativeText('Please enter a name and try again.')
            msg.setWindowTitle("Error")
            msg.exec_()
    else:
        msg = QMessageBox()
        msg.setWindowTitle("Error")
        msg.setText("Please select only 2 images to subtract")
        x = msg.exec_()
    pass


def avg_selection_1d_func(self):
    loadedlist = self.loadedlistwidget
    plot = self.getPlotWidget()
    datadict = self.idata
    curvelist = [item.text() for item in loadedlist.selectedItems()]
    curvenames = []
    for curve in curvelist:
        if '.nxs' in curve:
            curvenames.append(curve)
        else:
            curvenames.append(curve.split('.')[0])
    tot_res_intensity = 0
    tot_res_sigma = 0
    N = len(curvenames)
    for i in range(0, N):
        temp_n = curvenames[i]
        temp_res = datadict[temp_n]
        tot_res_intensity += temp_res.intensity
        tot_res_sigma += temp_res.sigma ** 2
    tot_res_intensity /= N
    tot_res_sigma = np.sqrt(tot_res_sigma / N)
    tot_res_radial = temp_res.radial
    text, ok = QInputDialog().getText(self, "File Name",
                                      "Please enter a file name:", QLineEdit.Normal,
                                      'Average')
    if ok and text:
        new_res = pd.DataFrame.from_dict(
            {'radial': tot_res_radial, 'intensity': tot_res_intensity, 'sigma': tot_res_sigma}, orient='columns')
        datadict[text] = new_res
        loadedlist.addItem(text)
        plot.addCurve(x=tot_res_radial, y=tot_res_intensity, linewidth=1, legend='{}'.format(text),
                      color='green')
        plot.setGraphGrid(which='both')
        save_dat(text, self.imagepath, new_res, self.q_combo.currentText())
    else:
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setText("Error")
        msg.setInformativeText('Please enter a name and try again.')
        msg.setWindowTitle("Error")
        msg.exec_()
    return None


def read_file(file):
    with rasterio.open(file) as src:
        return src.read(1)


def avg_selection_2d_func(self, list_id):
    # Read all data as a list of numpy arrays
    if list_id == 0:
        listw = self.loadedlistwidget
        fp = [self.imagepath + '/' + item.text() for item in listw.selectedItems()]
    else:
        listw = self.tw
        fp = [self.imagepath + '/' + item.text(0) for item in listw.selectedItems()]
    array_list = [read_file(path) for path in fp]
    # Perform averaging 
    array_out = np.mean(array_list, axis=0)

    # Get metadata from one of the input files
    with rasterio.open(fp[0]) as src:
        meta = src.meta

    meta.update(dtype=rasterio.float32)
    # Write output file
    text, ok = QInputDialog().getText(self, "File Name",
                                      "Please enter a file name:", QLineEdit.Normal,
                                      'Average')
    if ok and text:
        save_path = '{}/{}.tif'.format(self.imagepath, text)
        with rasterio.open(save_path, 'w', **meta) as dst:
            dst.write(array_out.astype(rasterio.float32), 1)
    else:
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setText("Error")
        msg.setInformativeText('Please enter a name and try again.')
        msg.setWindowTitle("Error")
        msg.exec_()
    pass
