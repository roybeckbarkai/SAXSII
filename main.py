import matplotlib.pyplot as plt
import numpy
import re

import numpy as np
import pandas as pd
import cv2
from silx.gui import qt
from skimage import io
from silx.gui.plot.PlotWindow import PlotWindow
import fabio
import subprocess
from PIL import Image
from silx.gui.plot import tools
from PyQt5 import QtWidgets
from PyQt5.QtWidgets import QInputDialog, QLineEdit
from silx.gui.widgets.BoxLayoutDockWidget import BoxLayoutDockWidget
from silx.gui.qt import QMessageBox
from docking_bars import MyCurveLegendsWidget
from open_methods import open_directory, open_poni, open_mask, open_nxs
from plotting_methods import image_plot_settings, curve_plot_settings, plot_mul_curves, subtractcurves, \
    plot_restricted_radius_image, plot_center_beam_image, avg_selection_1d_func, avg_selection_2d_func, subtract_2d
from saving_methods import save_csv
from integration_methods import full_integration, send_to_integration, convert_radius_to_q, convert_q_to_radius, caking
import logging
import time
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent
from silx.gui.qt import QTreeWidgetItem
import pathlib
import fabio
from silx.gui import colors
from functools import wraps
from anis_finder import integrate_with_anisotropy, subtract_iso, divide_iso



logging.basicConfig(filename='app.log', filemode='a', format='%(asctime)s - %(message)s', datefmt='%d-%b-%y %H:%M:%S')


def InitiateCalibration():
    subprocess.Popen(["pyFAI-calib2"])


class MyPlotWindow(qt.QMainWindow):
    """configuring GUI Parameteres and functionalities"""

    def __init__(self, parent=None):
        super(MyPlotWindow, self).__init__(parent)

        # Creating a PlotWidget
        self._plot = PlotWindow(parent=self, roi=False, print_=False, control=False, yInverted=False, autoScale=False,
                                mask=False, save=False, curveStyle=False, copy=False, grid=False)
        self._plot.setContextMenuPolicy(qt.Qt.ActionsContextMenu)
        self.setqminAction = qt.QAction(self)
        self.setqminAction.setText("Set q min visually")
        self.setqminAction.triggered.connect(self.set_q_min)
        self.setqmaxAction = qt.QAction(self)
        self.setqmaxAction.setText("Set q max visually")
        self.setqmaxAction.triggered.connect(self.set_q_max)
        self.setcenter_action = qt.QAction(self)
        self.setcenter_action.triggered.connect(self.set_center)
        self.setcenter_action.setText("Set center visually")
        self.addmarker_action = qt.QAction(self)
        self.addmarker_action.setText("Add Marker")
        self.clearmarkers_action = qt.QAction(self)
        self.clearmarkers_action.setText("Clear Markers")
        self._plot.addAction(self.setcenter_action)
        self._plot.addAction(self.setqminAction)
        self._plot.addAction(self.setqmaxAction)
        self._plot.addAction(self.addmarker_action)
        self._plot.addAction(self.clearmarkers_action)
        self.setqminAction.setEnabled(False)
        self.setqmaxAction.setEnabled(False)

        # window menu bar
        menuBar = self.menuBar()
        fileMenu = qt.QMenu("&More Options", self)
        menuBar.addMenu(fileMenu)
        self.save_csv_action = qt.QAction('Save Integrated Data as CSV File...', self)
        fileMenu.addAction(self.save_csv_action)
        self.save_csv_action.triggered.connect(self.save_csv_wrap)

        # global parameters
        self.beamcenterx = 0
        self.beamcentery = 0
        self.wavelength = 0
        self.distance = 0
        self.pixel_size = 0
        self.min_radius = 0
        self.max_radius = 0

        # add functionalities to toolbar
        plot_tool_bar = self.getPlotWidget().toolBar()
        toolButton = qt.QToolButton(self)
        toolButton.setCheckable(True)
        toolButton.setIcon(qt.QIcon('files/toggle.ico'))
        plot_tool_bar.addWidget(toolButton)
        # toolButton.clicked.connect(self.check)

        # Bottom Toolbar
        position = tools.PositionInfo(plot=self._plot,
                                      converters=[('Radius from Beam Center (px)', lambda x, y: numpy.sqrt(
                                          (x - self.beamcenterx) ** 2 + (y - self.beamcentery) ** 2)),
                                                  ('Angle', lambda x, y: numpy.degrees(
                                                      numpy.arctan2(y - self.beamcentery, x - self.beamcenterx))),
                                                  ('X Position (px)', lambda x, y: x),
                                                  ('Y Position (px)', lambda x, y: y),
                                                  (u'q (\u212B)', lambda x, y: ((4 * numpy.pi * (numpy.sin((
                                                          numpy.arctan2(
                                                              numpy.sqrt(
                                                                  (
                                                                          (
                                                                                  y - self.beamcentery) * self.pixel_size) ** 2 + (
                                                                          (
                                                                                  x - self.beamcenterx) * self.pixel_size) ** 2),
                                                              self.distance) / 2)))) / (
                                                                                        self.wavelength / 10 ** (
                                                                                    -10))))])

        self.position = position
        toolBar1 = qt.QToolBar("xy", self)
        self.toolbar1 = toolBar1
        self.toolbar1.toggleViewAction().trigger()
        self.addToolBar(qt.Qt.BottomToolBarArea, toolBar1)

        self.toolbar2 = qt.QToolBar('xy2', self)
        self.addToolBar(qt.Qt.BottomToolBarArea, self.toolbar2)
        position2 = tools.PositionInfo(plot=self._plot,
                                       converters=[(u'q (\u212B)', lambda x, y: x), ('Intensity', lambda x, y: y)])
        self.toolbar2.addWidget(position2)
        self.toolbar2.setVisible(False)

        progressbar = qt.QProgressBar(self, objectName="GreenProgressBar")
        progressbar.setFixedSize(310, 30)
        progressbar.setTextVisible(False)
        self.progressbar = progressbar
        toolBar1.addWidget(position)
        toolBar1.addWidget(progressbar)

        # window parameters
        self.setWindowTitle("Saxsii")
        icon = qt.QIcon('files/icon.png')
        self.setWindowIcon(icon)

        # layout coniguration
        options = qt.QWidget(self)
        int_label = qt.QLabel('Integration')
        self.layout = qt.QVBoxLayout(options)
        self.layout.addWidget(int_label)
        button = qt.QPushButton("Calibration Tool", self)
        button.clicked.connect(InitiateCalibration)
        self.layout.addWidget(button)
        button = qt.QPushButton("Load Image Folder", self)
        button.clicked.connect(self.open_directory_wrap)
        self.layout.addWidget(button)
        tw = qt.QTreeWidget(self)
        tw.setSortingEnabled(True)
        self.layout.addWidget(tw, stretch=1)
        self.tw = tw
        tw.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        tw.setHeaderHidden(True)
        tw.itemSelectionChanged.connect(self.ShowImage)
        tw.itemDoubleClicked.connect(self.ShowImage)
        track_check_box = qt.QCheckBox(self)
        track_check_box.clicked.connect(self.track_folder)
        track_check_box.setText('Track Folder Changes')
        track_check_box.setEnabled(True)
        self.track_check_box = track_check_box
        update_image_check_box = qt.QCheckBox(self)
        update_image_check_box.setText('Show Last Created Image')
        update_image_check_box.setEnabled(False)
        self.update_image_check_box = update_image_check_box
        check_box_group = qt.QGroupBox()
        checkbox_sublayout = qt.QFormLayout(check_box_group)
        checkbox_sublayout.addRow(track_check_box, update_image_check_box)
        self.layout.addWidget(check_box_group)
        button = qt.QPushButton("Load PONI File", self)
        button.clicked.connect(self.open_poni_wrap)
        self.layout.addWidget(button)
        poni_label = qt.QLabel(self)
        poni_label.setText('No PONI')
        self.poni_label = poni_label
        self.layout.addWidget(button)
        self.layout.addWidget(poni_label)
        button = qt.QPushButton("Load Mask File", self)
        button.clicked.connect(self.open_mask_wrap)
        self.layout.addWidget(button)
        mask_label = qt.QLabel(self)
        mask_label.setText('No Mask')
        self.mask_label = mask_label
        self.layout.addWidget(button)
        self.layout.addWidget(mask_label)

        # integration paramteres and buttons
        integparams = qt.QGroupBox('Integration Parameters')
        sublayout = qt.QFormLayout(integparams)
        bins = qt.QLineEdit('1000')
        self.bins = bins
        minradius = qt.QLabel('Minimum')
        self.min_radius_display = minradius
        maxradius = qt.QLabel('Maximum')
        self.max_radius_display = maxradius

        sublayout.addRow('Bins:', bins)
        q_combobox = qt.QComboBox()
        sublayout.addRow('Radial unit:', q_combobox)
        q_combobox.addItems([u'q (\u212B)', u'q (nm\u207B\u00B9)'])
        self.q_combo = q_combobox
        sublayout.addRow('Min Radius:', minradius)
        sublayout.addRow('Max Radius:', maxradius)
        self.set_min_button = qt.QPushButton('Set Min Radius Manually')
        self.set_min_button.clicked.connect(self.set_q_min)
        self.set_max_button = qt.QPushButton('Set Max Radius Manually')
        self.set_max_button.clicked.connect(self.set_q_max)
        self.set_center_button = qt.QPushButton('Set Center Manually')
        self.set_center_button.clicked.connect(self.set_center)
        self.set_min_button.setEnabled(False)
        self.set_max_button.setEnabled(False)
        self.set_max_button.setToolTip('Please Load PONI or set center Manually')
        self.set_min_button.setToolTip('Please Load PONI or set center Manually')
        sublayout.addRow(self.set_center_button)
        sublayout.addRow(self.set_min_button, self.set_max_button)
        self.layout.addWidget(integparams)

        # dezinging paramteres
        dezingparameters = qt.QGroupBox('Dezinger Parameters')
        sub_layout_2 = qt.QFormLayout(dezingparameters)
        sigma_thres = qt.QLineEdit('1000')
        sub_layout_2.addRow('Sigma Clip Threshold:', sigma_thres)
        self.layout.addWidget(dezingparameters)
        self.dezing_thres = sigma_thres

        # Integration Buttons
        buttonsWidget = qt.QWidget()
        buttonsWidgetLayout = qt.QHBoxLayout(buttonsWidget)
        buttons = ['Integrate Selected', 'Integrate All']
        Average_TW_Button = qt.QPushButton('Average Selected', self)
        Subtract_TW_Button = qt.QPushButton('Subtract Selected', self)
        Anis_Subtract_Button = qt.QPushButton('Subtract', self)
        Anis_Divide_Button = qt.QPushButton('Divide', self)
        self.sub_button = Anis_Subtract_Button
        self.div_button = Anis_Divide_Button
        addbuttons = [qt.QPushButton(c) for c in buttons]
        addbuttons[0].clicked.connect(self.Integrate_selected)
        addbuttons[1].clicked.connect(self.Integrate_all)
        for button in addbuttons:
            buttonsWidgetLayout.addWidget(button)
        self.layout.addWidget(buttonsWidget)
        Subtract_TW_Button.clicked.connect(lambda: self.subtract_curves_wrap(1, 1))
        Average_TW_Button.clicked.connect(lambda: self.average_selected(1, 1))
        Anis_Subtract_Button.clicked.connect(lambda: self.anis_sub(1, 1))
        Anis_Divide_Button.clicked.connect(lambda: self.anis_div(1, 1))
        
        
        self.layout.addWidget(Average_TW_Button)
        self.layout.addWidget(Subtract_TW_Button)
        anis_group_box = qt.QGroupBox('Anisotropy Highlighting')
        anis_layout = qt.QHBoxLayout(anis_group_box)
        self.default_button = qt.QPushButton('Default', self)
        self.default_button.clicked.connect(self.show_default_image)
        anis_layout.addWidget(Anis_Subtract_Button)
        anis_layout.addWidget(Anis_Divide_Button)
        anis_layout.addWidget(self.default_button)
        self.layout.addWidget(anis_group_box)
        self.layout.addStretch()

        # Integration Data dicts
        self.idata = {}
        self.chi_data = {}
        self.unitdict = {u'q (nm\u207B\u00B9)': "q_nm^-1", u'q (\u212B)': "q_A^-1"}
        self.nxs_file_dict = {}
        self.plotted_before_list = []
        self.image_dict = {}
        self.marker_dict = {}
        # Data Fields
        options2 = qt.QGroupBox('Calibration Data')
        # layout2 = qt.QFormLayout(options2)
        layout2 = qt.QFormLayout(options2)
        self.layout2 = layout2
        wavelength = qt.QLineEdit('0')
        distance = qt.QLineEdit('0')
        beamcenterx = qt.QLineEdit('0')
        beamcentery = qt.QLineEdit('0')
        layout2.addRow('Distance (m):', distance)
        layout2.addRow(u'Wavelength (\u212B):', wavelength)
        layout2.addRow('Beam Center X (px):', beamcenterx)
        layout2.addRow('Beam Center Y (px):', beamcentery)
        self.wavelengthdisplay = wavelength
        self.distancedisplay = distance
        self.beamcenterxdisplay = beamcenterx
        self.beamcenterydisplay = beamcentery

        # Integrated Images List
        # toggle_2 = AnimatedToggle(
        #     checked_color="#FFB000",
        #     pulse_checked_color="#44FFB000"
        # )
        switch = QtWidgets.QCheckBox()
        switch.setText('2D')
        switch.setChecked(False)
        combo_box = QtWidgets.QComboBox()
        combo_box.addItem('1D / 2D plot')
        combo_box.addItem('Chi plots')
        options3 = qt.QWidget(self)
        ana_label = qt.QLabel('Analysis')
        self.layout3 = qt.QVBoxLayout(options3)
        self.layout3.addWidget(ana_label)
        self.layout3.addWidget(combo_box)
        self.layout3.addWidget(switch)
        self.layout3.addWidget(switch)
        self.layout3.addWidget(qt.QLabel('Integrated Images:'))
        loadedlistwidget = qt.QListWidget(self)
        loadedlistwidget.setSortingEnabled(True)
        self.layout3.addWidget(loadedlistwidget)
        self.loadedlistwidget = loadedlistwidget
        loadedlistwidget.itemSelectionChanged.connect(lambda: self.show_data(switch.isChecked()))
        loadedlistwidget.itemDoubleClicked.connect(lambda: self.show_data(switch.isChecked()))
        switch.clicked.connect(lambda: self.show_data(switch.isChecked()))
        loadedlistwidget.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.addmarker_action.triggered.connect(lambda: self.add_marker(switch.isChecked()))
        self.clearmarkers_action.triggered.connect(self.clearmarkers)
        combo_box.currentIndexChanged.connect(self.change_layout)

        tools1d = qt.QLabel('Tools')
        tools1d.setStyleSheet("border: 1px solid black;")
        self.layout3.addWidget(tools1d)
        remove_selected_button = qt.QPushButton('Remove Selected', self)
        clear_all_button = qt.QPushButton('Clear', self)
        lw_options_group = qt.QGroupBox()
        lw_sublayout = qt.QFormLayout(lw_options_group)
        subtracttbut = qt.QPushButton('Subtract Selected', self)
        avg_selection = qt.QPushButton('Average Selected', self)
        lw_sublayout.addRow(remove_selected_button, clear_all_button)
        lw_sublayout.addRow(subtracttbut, avg_selection)
        self.layout3.addWidget(lw_options_group)
        subtracttbut.clicked.connect(lambda: self.subtract_curves_wrap(switch.isChecked(), 0))
        avg_selection.clicked.connect(lambda: self.average_selected(switch.isChecked(), 0))
        clear_all_button.clicked.connect(self.clear_lw)
        remove_selected_button.clicked.connect(self.remove_selected_from_lw)

        # Loaded Directory name
        frame = qt.QLabel(self)
        frame.setText("Directory:")
        frame.setFont(qt.QFont('Segoe UI', 9))
        self.frame = frame
        self.frame.setStyleSheet("border: 0.5px solid black;")

        # Gui Geometry Settings
        gridLayout = qt.QGridLayout()
        gridLayout.setSpacing(2)
        gridLayout.setContentsMargins(0, 0, 0, 0)
        gridLayout.addWidget(options, 1, 0)
        gridLayout.addWidget(self._plot, 1, 1, 2, 1)
        gridLayout.addWidget(frame, 0, 1)
        gridLayout.setRowStretch(1, 1)
        gridLayout.setColumnStretch(1, 1)
        gridLayout.addWidget(options3, 1, 2)
        gridLayout.addWidget(options2, 2, 2)

        centralWidget = qt.QWidget(self)
        centralWidget.setLayout(gridLayout)
        self.setCentralWidget(centralWidget)
        # legend dock
        plot = self._plot
        curveLegendsWidget = MyCurveLegendsWidget()
        curveLegendsWidget.setPlotWidget(plot)
        dock = BoxLayoutDockWidget()
        dock.setWindowTitle('Curve legends')
        dock.setWidget(curveLegendsWidget)
        plot.addDockWidget(qt.Qt.TopDockWidgetArea, dock)

    def getPlotWidget(self):
        return self._plot

    # chekcing toolbar manipulations, use Qaction /toggling and putting the other toolbar - USED FOR LEGEND TOGGLE
    # def check(self):
    #     newpositions = [('X', lambda x, y: x)]
    #     for func in self.position.getConverters()[1:]:
    #         newpositions.append(func)
    #     new_positon=tools.PositionInfo(plot=self._plot,converters=newpositions)
    #     #self.toolbar1_action.setVisible(False)
    #     #self.toolbar1.removeAction(self.toolbar1_action)
    #     #self.toolbar1.addWidget(new_positon)
    #     self.toolbar1.toggleViewAction().trigger()
    #     #http://www.silx.org/doc/silx/latest/modules/gui/plot/actions/examples.html

    def set_q_min(self):
        plot = self.getPlotWidget()
        plot.setGraphCursor(True)

        def mouse_tracker1(dict):
            if dict['event'] == 'mouseClicked' and dict['button'] == 'left':
                x, y = dict['x'], dict['y']
                plot.setGraphCursor(False)
                centerx = int(self.beamcenterx)
                centery = int(self.beamcentery)
                self.min_radius = int(numpy.sqrt((x - centerx) ** 2 + (y - centery) ** 2))
                plot.setCallback()
                if self.min_radius < self.max_radius:
                    self.min_radius_display.setText('%.2f' % self.min_radius)
                    # self.restricted_image = numpy.copy(self.raw_image)
                    # plot_restricted_radius_image(self, plot, self.restricted_image, False)
                else:
                    msg = qt.QMessageBox()
                    msg.setWindowTitle("Error")
                    msg.setText("Maximum is smaller than Minimum!")
                    x = msg.exec_()

        self._plot.setCallback(callbackFunction=mouse_tracker1)

    def set_q_max(self):
        plot = self.getPlotWidget()
        plot.setGraphCursor(True)

        def mouse_tracker2(dict):
            if dict['event'] == 'mouseClicked' and dict['button'] == 'left':
                x, y = dict['x'], dict['y']
                plot.setGraphCursor(False)
                centerx = int(self.beamcenterx)
                centery = int(self.beamcentery)
                plot.setCallback()
                self.max_radius = int(numpy.sqrt((x - centerx) ** 2 + (y - centery) ** 2))
                if self.max_radius > self.min_radius:
                    self.max_radius_display.setText('%.2f' % (self.max_radius))
                    # self.restricted_image = numpy.copy(self.raw_image)
                    # plot_restricted_radius_image(self, plot, self.restricted_image, False)
                else:
                    msg = qt.QMessageBox()
                    msg.setWindowTitle("Error")
                    msg.setText("Maximum is smaller than Minimum!")
                    x = msg.exec_()

        self._plot.setCallback(callbackFunction=mouse_tracker2)

    def set_center(self):
        plot = self.getPlotWidget()
        plot.setGraphCursor(True)

        def mouse_tracker3(dict):
            if dict['event'] == 'mouseClicked' and dict['button'] == 'left':
                x, y = dict['x'], dict['y']
                plot.setGraphCursor(False)
                self.beamcenterx = x
                self.beamcentery = y
                try:
                    self.ai.setFit2D(self.fit2ddata['directDist'], self.beamcenterx, self.beamcentery,
                                     self.fit2ddata['tilt'], self.fit2ddata['tiltPlanRotation'],
                                     self.fit2ddata['pixelX'], self.fit2ddata['pixelY'])
                except Exception:
                    None
                plot.setCallback()
                self.restricted_image = numpy.copy(self.raw_image)
                plot_center_beam_image(self, plot, self.restricted_image)

        self._plot.setCallback(callbackFunction=mouse_tracker3)

    def add_marker(self, checked):
        plot = self.getPlotWidget()
        plot.setGraphCursor(True)

        def mouse_tracker4(dict):
            if dict['event'] == 'mouseClicked' and dict['button'] == 'left':
                if not checked:
                    x, y = float(dict['x']), float(dict['y'])
                    name, ok = QInputDialog().getText(self, "File Name",
                                                      "Please enter a file name:", QLineEdit.Normal)
                    if ok and name:
                        rad = convert_q_to_radius(self, np.abs(x))
                        df = pd.DataFrame({'x': [x], 'rad': [rad]})
                        df = df.dropna(axis=0)
                        df.reset_index(inplace=True, drop=True)
                        self.marker_dict[name] = df
                        self.draw_marker(checked)
                    else:
                        msg = QMessageBox()
                        msg.setIcon(QMessageBox.Critical)
                        msg.setText("Error")
                        msg.setInformativeText('Please enter a name and try again.')
                        msg.setWindowTitle("Error")
                        msg.exec_()
                if checked:
                    x, y = float(dict['x']), float(dict['y'])
                    name, ok = QInputDialog().getText(self, "File Name",
                                                      "Please enter a file name:", QLineEdit.Normal)
                    if ok and name:
                        centerx = self.beamcenterx
                        centery = self.beamcentery
                        radius = np.sqrt(((x - centerx) ** 2 + (y - centery) ** 2))
                        q_ = convert_radius_to_q(self, radius)
                        df = pd.DataFrame({'x': [q_], 'rad': [radius]})
                        df = df.dropna(axis=0)
                        df.reset_index(inplace=True, drop=True)
                        self.marker_dict[name] = df
                        self.draw_marker(checked)
                    else:
                        msg = QMessageBox()
                        msg.setIcon(QMessageBox.Critical)
                        msg.setText("Error")
                        msg.setInformativeText('Please enter a name and try again.')
                        msg.setWindowTitle("Error")
                        msg.exec_()
                plot.setGraphCursor(False)
                plot.setCallback()

        self._plot.setCallback(callbackFunction=mouse_tracker4)

    def draw_marker(self, checked):
        plot = self.getPlotWidget()

        if not checked:
            for key in self.marker_dict.keys():
                x = float(self.marker_dict[key]['x'])
                plot.addCurve(np.ones(1000) * x, (np.linspace(-10, 100, 1000)),
                              legend='{}'.format(key) + " q=" + str(x))
        else:
            centerx = int(self.beamcenterx)
            centery = int(self.beamcentery)
            image = self.raw_image
            img = np.float32(image.copy())
            for key in self.marker_dict.keys():
                rad = float(self.marker_dict[key]['rad'])
                cv2.circle(img, (centerx, centery), int(rad), (0, 0, 0), 3)
                plt.legend()
            plot.addImage(img, resetzoom=True)

        # kFunction=mouse_tracker4)

    def clearmarkers(self, checked):
        self.marker_dict = {}

    def getIntegrationParams(self):
        bins = int(self.bins.text())
        minradius = convert_radius_to_q(self, self.min_radius)
        maxradius = convert_radius_to_q(self, self.max_radius)
        poni = self.poni_file
        mask = fabio.open(self.mask_file)
        dezing_thres = float(self.dezing_thres.text())
        q_choice = self.q_combo.currentText()
        unit_dict = self.unitdict
        q_choice = unit_dict[q_choice]
        if q_choice == "q_nm^-1":
            minradius *= 10
            maxradius *= 10
        plot = self.getPlotWidget()
        curve_plot_settings(self, plot)
        nxs_file_dict = self.nxs_file_dict
        datadict = self.idata
        chi_datadict = self.chi_data
        loadedlist = self.loadedlistwidget
        return bins, minradius, maxradius, poni, mask, q_choice, dezing_thres, nxs_file_dict, datadict, chi_datadict, loadedlist, plot

    def addMask(self, image):
        plot = self.getPlotWidget()
        mask = fabio.open(self.mask_file)
        # # image = image - mask.data
        # img = plot.getImage('image')
        # img.setData(mask.data * 1000, colormap='red')
        # # plot.addImage(mask.data, resetzoom=True, colormap='red', replace=False, legend='mask')
        try:
            plot.addImage(image + mask.data * 10000)
        except:
            print('Mask and image files are of different sizes')





    def showInitalImage(self):
        """inital image logo"""
        plot = self.getPlotWidget()
        plot.getDefaultColormap().setName('jet')
        plot.getDefaultColormap().setNormalization('log')
        im = Image.open('files/saxsii.jpeg')
        im = numpy.flip(im, 0)
        plot.addImage(im)
        try:
            save_file = open('SAVE.txt', "r")
            lines = save_file.readlines()
            try:
                open_directory(self, lines[0].replace("\n", ''))
            except:
                print("No directory listed")
            try:
                open_poni(self, lines[1].replace("\n", ''))
            except:
                print("No poni listed")
            try:
                open_mask(self, lines[2])
            except:
                print("No mask listed")
            save_file.close()
        except:
            print("No save file")

    def track_folder(self):
        if self.track_check_box.isChecked():
            self.update_image_check_box.setEnabled(True)
            tw = self.tw
            update_image = self.update_image_check_box
            plot = self.getPlotWidget()
            imagepath = self.imagepath

            class MonitorFolder(FileSystemEventHandler):
                def on_any_event(self, event):
                    file = pathlib.Path(event.src_path)
                    if event.event_type == 'created':
                        if str(event.src_path).endswith('.tiff') or str(event.src_path).endswith('.tif'):
                            treeitem = QTreeWidgetItem([file.name])
                            tw.insertTopLevelItems(0, [treeitem])
                            filename = file.name
                            filepath = imagepath + '/' + filename
                            if update_image and (str(filepath).endswith('.tiff') or str(filepath).endswith('.tif')):
                                try:
                                    time.sleep(1)
                                    im = fabio.open(filepath)
                                    image = im.data
                                    plot.clear()
                                    # plot.getDefaultColormap().setName('viridis')
                                    # cm = colors.Colormap(name='jet', normalization='log')
                                    # plot.setDefaultColormap(cm)
                                    # plot.setYAxisLogarithmic(False)
                                    plot.setKeepDataAspectRatio(True)
                                    plot.addImage(image)
                                    plot.resetZoom()
                                    plot.getZoomOutAction().trigger()
                                    plot.getZoomInAction().trigger()
                                except Exception as e:
                                    logging.error(f'Something went wrong with loading the image {str(e)}')

                # def on_deleted(self,event):
                #    pass

                # def on_modified(self, event):
                #    pass

            event_handler = MonitorFolder()
            observer = Observer()
            observer.schedule(event_handler, path=self.imagepath, recursive=True)
            logging.error('Monitoring started')
            observer.start()

        if not self.track_check_box.isChecked():
            self.update_image_check_box.setEnabled(False)
            print("need to be implemented")
            # observer.stop()

    def Integrate_selected(self):
        tw = self.tw
        imagelist = [item.text(0) for item in tw.selectedItems()]
        send_to_integration(self, imagelist)

    def Integrate_all(self):
        def get_subtree_nodes(tree_widget_item):
            """Returns all QTreeWidgetItems in the subtree rooted at the given node."""
            nodes = []
            nodes.append(tree_widget_item)
            for i in range(tree_widget_item.childCount()):
                nodes.extend(get_subtree_nodes(tree_widget_item.child(i)))
            return nodes

        def get_all_items(tree_widget):
            """Returns all QTreeWidgetItems in the given QTreeWidget."""
            all_items = []
            for i in range(tree_widget.topLevelItemCount()):
                top_item = tree_widget.topLevelItem(i)
                all_items.extend(get_subtree_nodes(top_item))
            return all_items

        tw = self.tw
        imagelist = get_all_items(tw)
        imagelist_names = [image.text(0) for image in imagelist]
        send_to_integration(self, imagelist_names)

    def open_directory_wrap(self):
        open_directory(self)

    def open_poni_wrap(self):
        open_poni(self)

    def open_mask_wrap(self):
        open_mask(self)

    def open_nxs_wrap(self, path):
        open_nxs(self, path)

    def show_data(self, checked):
        if checked:
            self.ShowImage_2D_list()
            self.draw_marker(1)
        else:
            plot_mul_curves(self)
            self.draw_marker(0)

    def ShowImage(self):

        tw = self.tw
        plot = self.getPlotWidget()
        nxs_file_dict = self.nxs_file_dict
        image_plot_settings(self, plot)
        if not tw.selectedItems():
            print('No image')
        else:
            filepath = self.imagepath + '/' + str(tw.selectedItems()[0].text(0))
            if filepath.endswith('.tiff') or filepath.endswith('.tif'):
                try:
                    image = io.imread(filepath)  # convert to fabio?
                    im = fabio.open(filepath)
                    image = im.data
                except Exception:
                    logging.error('Something went wrong with loading the image')

            if filepath.endswith('.nxs'):
                None
            regexp = re.compile(r'(?:nxs - image ).*$')
            if regexp.search(filepath):
                filename = filepath.split('.')[0].split('/')[-1] + '.nxs'
                image_number = filepath.split('-')[-1]
                image = nxs_file_dict[filename][filename + ' -' + image_number]
        try:
            if filepath not in self.plotted_before_list:
                self.plotted_before_list.append(filepath)
                self.raw_image = image
                plot_restricted_radius_image(self, plot, image, True)
                self.addMask(image)

            else:
                try:
                    self.raw_image = image  # numpy.copy(image)
                    plot_restricted_radius_image(self, plot, image, True)
                    self.addMask(image)

                except Exception:
                    None
        except Exception:
            None

    def ShowImage_2D_list(self):
        listw = self.loadedlistwidget
        plot = self.getPlotWidget()
        nxs_file_dict = self.nxs_file_dict
        image_plot_settings(self, plot)
        selected_list = [item.text() for item in listw.selectedItems()]
        if not selected_list:
            print('No image')
        else:
            filepath = self.imagepath + '/' + selected_list[0]
            if filepath.endswith('.tiff') or filepath.endswith('.tif'):
                try:
                    image = io.imread(filepath)  # convert to fabio?
                    im = fabio.open(filepath)
                    image = im.data
                except Exception:
                    logging.error('Something went wrong with loading the image')

            if filepath.endswith('.nxs'):
                None
            regexp = re.compile(r'(?:nxs - image ).*$')
            if regexp.search(filepath):
                filename = filepath.split('.')[0].split('/')[-1] + '.nxs'
                image_number = filepath.split('-')[-1]
                image = nxs_file_dict[filename][filename + ' -' + image_number]
        try:
            if filepath not in self.plotted_before_list:
                self.plotted_before_list.append(filepath)
                self.raw_image = image
                plot_restricted_radius_image(self, plot, image, True)
                self.addMask(image)
            else:
                try:
                    self.raw_image = image  # numpy.copy(image)
                    plot_restricted_radius_image(self, plot, image, True)
                    self.addMask(image)
                except Exception:
                    None
        except Exception:
            None

    def plot_mul_curves_wrap(self):
        plot_mul_curves(self)

    def subtract_curves_wrap(self, checked, list_id):
        if checked:
            subtract_2d(self, list_id)
        else:
            subtractcurves(self)

    def average_selected(self, checked, list_id):
        if checked:
            avg_selection_2d_func(self, list_id)
        else:
            avg_selection_1d_func(self)


    def anis_sub(self, checked, list_id):
        """
        Toggle subtraction view by the button's checked state.
        - checked == True  -> show anisotropy subtraction
        - checked == False -> restore raw image
        """
        plot = self.getPlotWidget()
        tw = self.tw

    # --- Toggle OFF: return to default ---
        if getattr(self, "anis_sub_active", False):
            self.show_default_image()
            return

        # --- Toggle ON: show anisotropy subtraction ---
        if not tw.selectedItems():
            print("No image selected.")
            return

        image_filename = tw.selectedItems()[0].text(0)
        print(f"Running anisotropy subtraction on {self.imagepath}/{image_filename}")

        try:
            one_D_curve, iso_2d = integrate_with_anisotropy(self, image_filename)
            image = self.raw_image
            mask_data = fabio.open(self.mask_file).data.astype(bool) if self.mask_file else None
            anis_map = subtract_iso(self, image, iso_2d, mask_data)
            print("anis_map valid pixels:", np.isfinite(anis_map).sum())
        except Exception as e:
            print(f"Error during anisotropy subtraction: {e}")
            return

        try:
            image_plot_settings(self, plot)
            plot.addImage(anis_map, resetzoom=True)
            print("anis_map range:", np.nanmin(anis_map), "→", np.nanmax(anis_map))
            print("✅ Anisotropy map displayed successfully.")
            self.sub1_button.setText("Default")
            self.anis_sub_active = True
        except Exception as e:
            print(f"Error displaying anisotropy map: {e}")


    def anis_div(self, checked, list_id):
        """
        Toggle division view by the button's checked state.
        - checked == True  -> show (I/Iiso - 1)
        - checked == False -> restore raw image
        """
        plot = self.getPlotWidget()
        tw = self.tw


    # --- Toggle OFF: return to default ---
        if getattr(self, "anis_div_active", False):
            self.show_default_image()
            return

        # --- Toggle ON: show anisotropy division ---
        if not tw.selectedItems():
            print("No image selected.")
            return

        image_filename = tw.selectedItems()[0].text(0)
        print(f"Running anisotropy division on {self.imagepath}/{image_filename}")

        try:
            one_D_curve, iso_2d = integrate_with_anisotropy(self, image_filename)
            image = self.raw_image
            mask_data = fabio.open(self.mask_file).data.astype(bool) if self.mask_file else None
            ratio_map = divide_iso(self, image, iso_2d, mask_data)
            print("ratio_map valid pixels:", np.isfinite(ratio_map).sum())
        except Exception as e:
            print(f"Error during anisotropy division: {e}")
            return

        try:
            image_plot_settings(self, plot)
            plot.addImage(ratio_map, resetzoom=True)
            print("ratio_map range:", np.nanmin(ratio_map), "→", np.nanmax(ratio_map))
            print("✅ Anisotropy ratio map displayed successfully.")
            self.div1_button.setText("Default")
            self.anis_div_active = True
        except Exception as e:
            print(f"Error displaying ratio map: {e}")

    def show_default_image(self):
        """Resets the plot to show the original 2D raw image."""
        plot = self.getPlotWidget()

        # Check if there is a raw image to restore
        if not hasattr(self, 'raw_image') or self.raw_image is None:
            print("No raw image loaded to display.")
            return
            
        # Clear the plot and add the original image
        plot.clear()
        image_plot_settings(self, plot)
        plot.addImage(self.raw_image, resetzoom=True)
        
        # Reset the active flags
        if hasattr(self, "anis_sub_active"):
            self.anis_sub_active = False
        if hasattr(self, "anis_div_active"):
            self.anis_div_active = False
            
        print("Returned to default 2D image view.")




    def save_csv_wrap(self):
        save_csv(self)

    def remove_selected_from_lw(self):
        lw = self.loadedlistwidget
        selected = lw.selectedItems()
        print(selected)
        for item in selected:
            lw.takeItem(lw.row(item))

    def clear_lw(self):
        lw = self.loadedlistwidget
        lw.clear()

    # Chi plot functions

    def change_layout(self, index):
        while self.layout3.count():
            item = self.layout3.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if index == 0:
            self.define_q_layout()
        if index == 1:
            self.define_chi_layout()

    def define_q_layout(self):
        int_label = qt.QLabel('Integration')
        self.layout.addWidget(int_label)
        button = qt.QPushButton("Calibration Tool", self)
        button.clicked.connect(InitiateCalibration)
        self.layout.addWidget(button)
        button = qt.QPushButton("Load Image Folder", self)
        button.clicked.connect(self.open_directory_wrap)
        self.layout.addWidget(button)
        tw = qt.QTreeWidget(self)
        self.layout.addWidget(tw, stretch=1)
        self.tw = tw
        tw.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        tw.setHeaderHidden(True)
        tw.itemSelectionChanged.connect(self.ShowImage)
        tw.itemDoubleClicked.connect(self.ShowImage)
        track_check_box = qt.QCheckBox(self)
        track_check_box.clicked.connect(self.track_folder)
        track_check_box.setText('Track Folder Changes')
        track_check_box.setEnabled(True)
        self.track_check_box = track_check_box
        update_image_check_box = qt.QCheckBox(self)
        update_image_check_box.setText('Show Last Created Image')
        update_image_check_box.setEnabled(False)
        self.update_image_check_box = update_image_check_box
        check_box_group = qt.QGroupBox()
        checkbox_sublayout = qt.QFormLayout(check_box_group)
        checkbox_sublayout.addRow(track_check_box, update_image_check_box)
        self.layout.addWidget(check_box_group)
        button = qt.QPushButton("Load PONI File", self)
        button.clicked.connect(self.open_poni_wrap)
        self.layout.addWidget(button)
        poni_label = qt.QLabel(self)
        poni_label.setText('No PONI')
        self.poni_label = poni_label
        self.layout.addWidget(button)
        self.layout.addWidget(poni_label)
        button = qt.QPushButton("Load Mask File", self)
        button.clicked.connect(self.open_mask_wrap)
        self.layout.addWidget(button)
        mask_label = qt.QLabel(self)
        mask_label.setText('No Mask')
        self.mask_label = mask_label
        self.layout.addWidget(button)
        self.layout.addWidget(mask_label)

        # integration paramteres and buttons
        integparams = qt.QGroupBox('Integration Parameters')
        sublayout = qt.QFormLayout(integparams)
        bins = qt.QLineEdit('1000')
        self.bins = bins
        minradius = qt.QLabel('Minimum')
        self.min_radius_display = minradius
        maxradius = qt.QLabel('Maximum')
        self.max_radius_display = maxradius

        sublayout.addRow('Bins:', bins)
        q_combobox = qt.QComboBox()
        sublayout.addRow('Radial unit:', q_combobox)
        q_combobox.addItems([u'q (\u212B)', u'q (nm\u207B\u00B9)'])
        self.q_combo = q_combobox
        sublayout.addRow('Min Radius:', minradius)
        sublayout.addRow('Max Radius:', maxradius)
        self.set_min_button = qt.QPushButton('Set Min Radius Manually')
        self.set_min_button.clicked.connect(self.set_q_min)
        self.set_max_button = qt.QPushButton('Set Max Radius Manually')
        self.set_max_button.clicked.connect(self.set_q_max)
        self.set_center_button = qt.QPushButton('Set Center Manually')
        self.set_center_button.clicked.connect(self.set_center)
        self.set_min_button.setEnabled(False)
        self.set_max_button.setEnabled(False)
        self.set_max_button.setToolTip('Please Load PONI or set center Manually')
        self.set_min_button.setToolTip('Please Load PONI or set center Manually')
        sublayout.addRow(self.set_center_button)
        sublayout.addRow(self.set_min_button, self.set_max_button)
        self.layout.addWidget(integparams)

        # dezinging paramteres
        dezingparameters = qt.QGroupBox('Dezinger Parameters')
        sub_layout_2 = qt.QFormLayout(dezingparameters)
        sigma_thres = qt.QLineEdit('1000')
        sub_layout_2.addRow('Sigma Clip Threshold:', sigma_thres)
        self.layout.addWidget(dezingparameters)
        self.dezing_thres = sigma_thres

        # Integration Buttons
        buttonsWidget = qt.QWidget()
        buttonsWidgetLayout = qt.QHBoxLayout(buttonsWidget)
        buttons = ['Integrate Selected', 'Integrate All']
        Average_TW_Button = qt.QPushButton('Average Selected', self)
        Subtract_TW_Button = qt.QPushButton('Subtract Selected', self)
        addbuttons = [qt.QPushButton(c) for c in buttons]
        addbuttons[0].clicked.connect(self.Integrate_selected)
        addbuttons[1].clicked.connect(self.Integrate_all)
        for button in addbuttons:
            buttonsWidgetLayout.addWidget(button)
        self.layout.addWidget(buttonsWidget)
        Subtract_TW_Button.clicked.connect(lambda: self.subtract_curves_wrap(1, 1))
        Average_TW_Button.clicked.connect(lambda: self.average_selected(1, 1))
        self.layout.addWidget(Average_TW_Button)
        self.layout.addWidget(Subtract_TW_Button)
        self.layout.addStretch()

        switch = QtWidgets.QCheckBox()
        switch.setText('2D')
        switch.setChecked(False)
        combo_box = QtWidgets.QComboBox()
        combo_box.addItem('1D / 2D plot')
        combo_box.addItem('Chi plots')
        ana_label = qt.QLabel('Analysis')
        self.layout3.addWidget(ana_label)
        self.layout3.addWidget(combo_box)
        self.layout3.addWidget(switch)
        self.layout3.addWidget(switch)
        self.layout3.addWidget(qt.QLabel('Integrated Images:'))
        loadedlistwidget = qt.QListWidget(self)
        self.layout3.addWidget(loadedlistwidget)
        self.loadedlistwidget = loadedlistwidget
        loadedlistwidget.itemSelectionChanged.connect(lambda: self.show_data(switch.isChecked()))
        loadedlistwidget.itemDoubleClicked.connect(lambda: self.show_data(switch.isChecked()))
        switch.clicked.connect(lambda: self.show_data(switch.isChecked()))
        loadedlistwidget.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.addmarker_action.triggered.connect(lambda: self.add_marker(switch.isChecked()))
        self.clearmarkers_action.triggered.connect(self.clearmarkers)
        combo_box.currentIndexChanged.connect(self.change_layout)

        self.layout3.addWidget(tools1d)
        remove_selected_button = qt.QPushButton('Remove Selected', self)
        clear_all_button = qt.QPushButton('Clear', self)
        lw_options_group = qt.QGroupBox()
        lw_sublayout = qt.QFormLayout(lw_options_group)
        subtracttbut = qt.QPushButton('Subtract Selected', self)
        avg_selection = qt.QPushButton('Average Selected', self)
        lw_sublayout.addRow(remove_selected_button, clear_all_button)
        lw_sublayout.addRow(subtracttbut, avg_selection)

        self.layout3.addWidget(lw_options_group)
        subtracttbut.clicked.connect(lambda: self.subtract_curves_wrap(switch.isChecked(), 0))
        avg_selection.clicked.connect(lambda: self.average_selected(switch.isChecked(), 0))
        clear_all_button.clicked.connect(self.clear_lw)
        remove_selected_button.clicked.connect(self.remove_selected_from_lw)
        try:
            save_file = open('SAVE.txt', "r")
            lines = save_file.readlines()
            try:
                open_directory(self, lines[0].replace("\n", ''))
            except:
                print("No directory listed")
            try:
                open_poni(self, lines[1].replace("\n", ''))
            except:
                print("No poni listed")
            try:
                open_mask(self, lines[2])
            except:
                print("No mask listed")
            save_file.close()
        except:
            print("No save file")

    def define_chi_layout(self):
        int_label = qt.QLabel('Integration')
        self.layout.addWidget(int_label)
        button = qt.QPushButton("Calibration Tool", self)
        button.clicked.connect(InitiateCalibration)
        self.layout.addWidget(button)
        button = qt.QPushButton("Load Image Folder", self)
        button.clicked.connect(self.open_directory_wrap)
        self.layout.addWidget(button)
        tw = qt.QTreeWidget(self)
        self.layout.addWidget(tw, stretch=1)
        self.tw = tw
        tw.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        tw.setHeaderHidden(True)
        tw.itemSelectionChanged.connect(self.show_caked_image)
        tw.itemDoubleClicked.connect(self.show_caked_image)
        track_check_box = qt.QCheckBox(self)
        track_check_box.clicked.connect(self.track_folder)
        track_check_box.setText('Track Folder Changes')
        track_check_box.setEnabled(True)
        self.track_check_box = track_check_box
        update_image_check_box = qt.QCheckBox(self)
        update_image_check_box.setText('Show Last Created Image')
        update_image_check_box.setEnabled(False)
        self.update_image_check_box = update_image_check_box
        check_box_group = qt.QGroupBox()
        checkbox_sublayout = qt.QFormLayout(check_box_group)
        checkbox_sublayout.addRow(track_check_box, update_image_check_box)
        self.layout.addWidget(check_box_group)
        button = qt.QPushButton("Load PONI File", self)
        button.clicked.connect(self.open_poni_wrap)
        self.layout.addWidget(button)
        poni_label = qt.QLabel(self)
        poni_label.setText('No PONI')
        self.poni_label = poni_label
        self.layout.addWidget(button)
        self.layout.addWidget(poni_label)
        button = qt.QPushButton("Load Mask File", self)
        button.clicked.connect(self.open_mask_wrap)
        self.layout.addWidget(button)
        mask_label = qt.QLabel(self)
        mask_label.setText('No Mask')
        self.mask_label = mask_label
        self.layout.addWidget(button)
        self.layout.addWidget(mask_label)

        # integration paramteres and buttons
        integparams = qt.QGroupBox('Integration Parameters')
        sublayout = qt.QFormLayout(integparams)
        bins = qt.QLineEdit('1000')
        self.bins = bins
        minq = qt.QLabel('Minimum')
        self.min_radius_display = minq
        maxq = qt.QLabel('Maximum')
        self.max_radius_display = maxq

        sublayout.addRow('Bins:', bins)
        q_combobox = qt.QComboBox()
        sublayout.addRow('Radial unit:', q_combobox)
        q_combobox.addItems([u'q (\u212B)', u'q (nm\u207B\u00B9)'])
        self.q_combo = q_combobox
        sublayout.addRow('Min q:', minq)
        sublayout.addRow('Max q:', maxq)
        self.set_min_button = qt.QPushButton('Set Min q Manually')
        self.set_min_button.clicked.connect(self.set_q_min)
        self.set_max_button = qt.QPushButton('Set Max q Manually')
        self.set_max_button.clicked.connect(self.set_q_max)
        self.set_center_button = qt.QPushButton('Cursor select q')
        self.set_center_button.clicked.connect(self.set_center)
        self.set_min_button.setEnabled(False)
        self.set_max_button.setEnabled(False)
        self.set_max_button.setToolTip('Please Load PONI or set center Manually')
        self.set_min_button.setToolTip('Please Load PONI or set center Manually')
        sublayout.addRow(self.set_center_button)
        sublayout.addRow(self.set_min_button, self.set_max_button)
        self.layout.addWidget(integparams)

        # dezinging paramteres
        dezingparameters = qt.QGroupBox('Dezinger Parameters')
        sub_layout_2 = qt.QFormLayout(dezingparameters)
        sigma_thres = qt.QLineEdit('1000')
        sub_layout_2.addRow('Sigma Clip Threshold:', sigma_thres)
        self.layout.addWidget(dezingparameters)
        self.dezing_thres = sigma_thres

        # Integration Buttons
        buttonsWidget = qt.QWidget()
        buttonsWidgetLayout = qt.QHBoxLayout(buttonsWidget)
        buttons = ['Integrate Selected', 'Integrate All']
        Average_TW_Button = qt.QPushButton('Average Selected', self)
        Subtract_TW_Button = qt.QPushButton('Subtract Selected', self)
        addbuttons = [qt.QPushButton(c) for c in buttons]
        addbuttons[0].clicked.connect(self.Integrate_selected)
        addbuttons[1].clicked.connect(self.Integrate_all)
        for button in addbuttons:
            buttonsWidgetLayout.addWidget(button)
        self.layout.addWidget(buttonsWidget)
        Subtract_TW_Button.clicked.connect(lambda: self.subtract_curves_wrap(1, 1))
        Average_TW_Button.clicked.connect(lambda: self.average_selected(1, 1))
        self.layout.addWidget(Average_TW_Button)
        self.layout.addWidget(Subtract_TW_Button)
        self.layout.addStretch()

        switch = QtWidgets.QCheckBox()
        switch.setText('2D')
        switch.setChecked(False)
        combo_box = QtWidgets.QComboBox()
        combo_box.addItem('1D / 2D plot')
        combo_box.addItem('Chi plots')
        combo_box.setCurrentIndex(1)
        ana_label = qt.QLabel('Analysis')
        self.layout3.addWidget(ana_label)
        self.layout3.addWidget(combo_box)
        self.layout3.addWidget(switch)
        self.layout3.addWidget(switch)
        self.layout3.addWidget(qt.QLabel('Integrated Images:'))
        loadedlistwidget = qt.QListWidget(self)
        self.layout3.addWidget(loadedlistwidget)
        self.loadedlistwidget = loadedlistwidget
        loadedlistwidget.itemSelectionChanged.connect(lambda: self.show_data(switch.isChecked()))
        loadedlistwidget.itemDoubleClicked.connect(lambda: self.show_data(switch.isChecked()))
        switch.clicked.connect(lambda: self.show_data(switch.isChecked()))
        loadedlistwidget.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.addmarker_action.triggered.connect(lambda: self.add_marker(switch.isChecked()))
        self.clearmarkers_action.triggered.connect(self.clearmarkers)
        combo_box.currentIndexChanged.connect(self.change_layout)

        tools1d = qt.QLabel('Tools')
        tools1d.setStyleSheet("border: 1px solid black;")
        self.layout3.addWidget(tools1d)
        remove_selected_button = qt.QPushButton('Remove Selected', self)
        clear_all_button = qt.QPushButton('Clear', self)
        lw_options_group = qt.QGroupBox()
        lw_sublayout = qt.QFormLayout(lw_options_group)
        subtracttbut = qt.QPushButton('Subtract Selected', self)
        avg_selection = qt.QPushButton('Average Selected', self)
        lw_sublayout.addRow(remove_selected_button, clear_all_button)
        lw_sublayout.addRow(subtracttbut, avg_selection)
        self.layout3.addWidget(lw_options_group)
        subtracttbut.clicked.connect(lambda: self.subtract_curves_wrap(switch.isChecked(), 0))
        avg_selection.clicked.connect(lambda: self.average_selected(switch.isChecked(), 0))
        clear_all_button.clicked.connect(self.clear_lw)
        remove_selected_button.clicked.connect(self.remove_selected_from_lw)
        try:
            save_file = open('SAVE.txt', "r")
            lines = save_file.readlines()
            try:
                open_directory(self, lines[0].replace("\n", ''))
            except:
                print("No directory listed")
            try:
                open_poni(self, lines[1].replace("\n", ''))
            except:
                print("No poni listed")
            try:
                open_mask(self, lines[2])
            except:
                print("No mask listed")
            save_file.close()
        except:
            print("No save file")

    def show_caked_image(self):
        tw = self.tw
        plot = self.getPlotWidget()
        nxs_file_dict = self.nxs_file_dict
        image_plot_settings(self, plot)
        if not tw.selectedItems():
            print('No image')
        else:
            filepath = self.imagepath + '/' + str(tw.selectedItems()[0].text(0))
            if filepath.endswith('.tiff') or filepath.endswith('.tif'):
                try:
                    image = io.imread(filepath)  # convert to fabio?
                    im = fabio.open(filepath)
                    image = im.data
                    res2d = caking(self, self.ai, image, "q_A^-1")
                except Exception:
                    logging.error('Something went wrong with loading the image')

            if filepath.endswith('.nxs'):
                None
            regexp = re.compile(r'(?:nxs - image ).*$')
            if regexp.search(filepath):
                filename = filepath.split('.')[0].split('/')[-1] + '.nxs'
                image_number = filepath.split('-')[-1]
                image = nxs_file_dict[filename][filename + ' -' + image_number]
                res2d = caking(self, self.ai, image, "q_A^-1")
        try:
            if filepath not in self.plotted_before_list:
                self.plotted_before_list.append(filepath)
                self.raw_image = image
                plot_restricted_radius_image(self, plot, res2d, True)
            else:
                try:
                    self.raw_image = image  # numpy.copy(image)
                    plot_restricted_radius_image(self, plot, res2d, True)
                except Exception:
                    None
        except Exception:
            None


def main():
    from styling import return_style
    StyleSheet = return_style()
    global app
    app = qt.QApplication([])
    app.setStyleSheet(StyleSheet)
    app.setStyle('Fusion')
    window = MyPlotWindow()
    window.setAttribute(qt.Qt.WA_DeleteOnClose)
    window.showInitalImage()
    window.showMaximized()
    logging.error('Saxsii Inittialized')
    app.exec()


if __name__ == '__main__':
    main()
