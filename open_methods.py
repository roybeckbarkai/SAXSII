from silx.gui.qt import QFileDialog, QTreeWidgetItem, QFont, QApplication, QMessageBox, Qt
from os.path import isfile, join
from os import listdir
import pyFAI
import numpy as np
from nexusformat.nexus import nxload
import logging

logging.basicConfig(filename='app.log', filemode='a', format='%(asctime)s - %(message)s', datefmt='%d-%b-%y %H:%M:%S')
from Image_Classes import Image


def open_directory(self, FP=None):
    nxs_file_dict = self.nxs_file_dict
    directory_frame = self.frame
    tw = self.tw
    tw.clear()
    if FP is None:
        filepath = QFileDialog.getExistingDirectory(None, 'Select Folder')
    else:
        filepath = FP
    try:
        data = ["\n", "\n", "\n"]
        i = 0
        with open("SAVE.txt", 'r') as file:
            for line in file:
                if line.__contains__('\n'):
                    data[i] = line
                else:
                    data[i] = line + '\n'
                if i == 2:
                    data[i] = line
                i = i + 1
            data[0] = filepath + '\n'
        with open("SAVE.txt", 'w') as file:
            file.writelines(data)
    except Exception:
        logging.error('could not save directory')
    try:
        if self.imagepath != filepath:  # user changed directory
            self.min_radius_display.setText('Minimum')
            self.max_radius_display.setText('Maximum')
            self.min_radius = 0
            self.max_radius = 0
            # FIX-ME what other parameters to reset in this case?
    except Exception:
        logging.error('tried to open a new directory and something went wrong...')
    directory_frame.setText('Directory :{}'.format(filepath))
    self.imagepath = filepath
    try:
        onlyfiles = [f for f in listdir(filepath) if
                     isfile(join(filepath, f)) and (f.endswith('.tif') or f.endswith('.tiff') or f.endswith('.h5'))]
        for file in onlyfiles:
            treeitem = QTreeWidgetItem([str(file)])
            if file.endswith('.tiff') or file.endswith('.tif') or file.endswith('.h5'):
                image = Image(file)
                self.image_dict[file] = image
            if file.endswith('.nxs'):
                try:
                    self.open_nxs_wrap(filepath + '/' + file)
                    keys = nxs_file_dict[file].keys()
                    for key in keys:
                        treeitemchild = QTreeWidgetItem([key])
                        treeitem.addChild(treeitemchild)
                        image = Image(treeitemchild.text(0))
                        self.image_dict[treeitemchild.text(0)] = image
                except Exception:
                    logging.error('tried to open an nxs file and something went wrong...')
            tw.addTopLevelItem(treeitem)
    except FileNotFoundError:
        logging.error('FileNotFoundError error...')


import sys, platform, traceback, importlib.util, locale
from pathlib import Path
import pyFAI, numpy as np

def load_poni_safely(path_like):
    p = Path(path_like).expanduser().resolve()
    print("=== Environment ===")
    print("Python:", sys.version.split()[0], "| OS:", platform.platform())
    print("fabio present:", importlib.util.find_spec("fabio") is not None)
    print("LC_NUMERIC before:", locale.setlocale(locale.LC_NUMERIC))
    try:
        locale.setlocale(locale.LC_NUMERIC, 'C')
    except Exception:
        pass

    print("=== File ===")
    print("Path:", str(p))
    print("Exists:", p.exists(), "| Size:", p.stat().st_size if p.exists() else "NA")
    head = ""
    try:
        head = p.read_text(encoding="utf-8", errors="ignore").splitlines()[:8]
        print("Head:", head)
    except Exception as e:
        print("Read error:", repr(e))

    try:
        print("=== Loading with pyFAI.load ===")
        return pyFAI.load(str(p))
    except Exception:
        print("pyFAI.load failed:\n", traceback.format_exc())
        try:
            print("=== Retrying with AzimuthalIntegrator.sload ===")
            from pyFAI.azimuthalIntegrator import AzimuthalIntegrator
            ai = AzimuthalIntegrator()
            return ai.sload(str(p))
        except Exception:
            print("sload failed:\n", traceback.format_exc())
            raise



def open_poni(self, FP=None):
    try:
        if FP is None:
            print("1")
            filepath = QFileDialog.getOpenFileName(self, filter='*.poni')
            print("2")
            filepath = filepath[0]
        else:
            print("3")
            filepath = FP
        print("a")
        self.poni_file = filepath
        print(self.poni_file)
        print("b")
        self.poni_label.setText('loaded PONI file: /{}'.format(filepath.split("/")[-1]))
        print("c")
        self.poni_label.setFont(QFont('Segoe UI', 9))
        print("d")
        ai = load_poni_safely(self.poni_file)
        print("e")
        self.ai = ai
        print("ai set")
        data_dict = ai.get_config()
        self.fit2ddata = ai.getFit2D()
        detector = data_dict['detector']

        try:
            self.pixel_size = self.fit2ddata['pixelX']
        except Exception:
            logging.error('Something went wrong with the pixel size load...')
        layout2 = self.layout2
        self.distancedisplay.setText('%.2f' % data_dict['dist'])
        self.wavelengthdisplay.setText(str(data_dict['wavelength']))
        if self.beamcenterx == 0 or '/'.join(filepath[0].split('/')[:-1]) != self.frame.text().split('Directory :')[
            -1]:  # when we load a NEW poni (or this is the first poni loaded)
            self.beamcenterx = self.fit2ddata['centerX']
            self.beamcentery = self.fit2ddata['centerY']
            self.beamcenterxdisplay.setText('%.2f' % self.fit2ddata['centerX'])
            self.beamcenterydisplay.setText('%.2f' % self.fit2ddata['centerY'])
        else:
            ai.setFit2D(self.fit2ddata['directDist'], self.beamcenterx, self.beamcentery, self.fit2ddata['tilt'],
                        self.fit2ddata['tiltPlanRotation'], self.fit2ddata['pixelX'], self.fit2ddata[
                            'pixelY'])  # we manually changed parameters and we need other parameters to integrate
        self.wavelength = data_dict['wavelength']
        self.distance = data_dict['dist']
        self.set_min_button.setEnabled(True)
        self.set_max_button.setEnabled(True)
        self.setqminAction.setEnabled(True)
        self.setqmaxAction.setEnabled(True)
        self.set_max_button.setToolTip('')
        self.set_min_button.setToolTip('')
        data = ["\n", "\n", "\n"]
        i = 0
        with open("SAVE.txt", 'r') as file:
            for line in file:
                if line.__contains__('\n'):
                    data[i] = line
                else:
                    data[i] = line + '\n'
                if i == 2:
                    data[i] = line
                i = i + 1
            data[1] = filepath + '\n'
        with open("SAVE.txt", 'w') as file:
            file.writelines(data)
    except Exception:
        logging.error('tried to open an poni file and something went wrong...')


def open_mask(self, FP=None):
    try:
        if FP is None:
            filepath = QFileDialog.getOpenFileName(self, filter='Mask files (*.msk *.edf *.tif *.tiff)')
            filepath = filepath[0]
        else:
            filepath = FP
        self.mask_file = filepath
        self.mask_label.setText('loaded Mask file: /{}'.format(filepath.split("/")[-1]))
        self.mask_label.setFont(QFont('Segoe UI', 9))
        data = ["\n", "\n", "\n"]
        i = 0
        with open("SAVE.txt", 'r') as file:
            for line in file:
                if line.__contains__('\n'):
                    data[i] = line
                else:
                    data[i] = line + '\n'
                if i == 2:
                    data[i] = line
                i = i + 1
            data[2] = filepath
        with open("SAVE.txt", 'w') as file:
            file.writelines(data)

    except Exception:
        logging.error('tried to open an mask file and something went wrong...')


def load_nxs(self, FP=None):
    """Convert a NeXus file to one TIFF per frame + PONI + mask (next to the .nxs,
    in a folder named after it) and load all three, so integration can start
    right away. A previous conversion is reused when its outputs are present.
    If the file lacks the geometry for a PONI, the images and mask are still
    loaded and the user is asked to load / create a PONI."""
    from NXS2TIF_conversion.nxs_to_tiff import convert_file, output_paths

    if FP is None:
        filepath = QFileDialog.getOpenFileName(self, 'Select NeXus File', filter='*.nxs')[0]
    else:
        filepath = FP
    if not filepath:
        return
    nxs_path = Path(filepath)
    paths = output_paths(nxs_path, nxs_path.parent)
    try:
        if not all(paths[k].exists() for k in ('mask', 'meta')):
            self.frame.setText('Converting {} ...'.format(nxs_path.name))
            QApplication.setOverrideCursor(Qt.WaitCursor)

            def progress(done, total):
                self.progressbar.setValue(int(done / total * 100))
                QApplication.processEvents()

            try:
                convert_file(nxs_path, nxs_path.parent, progress=progress)
            finally:
                QApplication.restoreOverrideCursor()
    except Exception as e:
        logging.error('tried to convert an nxs file and something went wrong... {}'.format(e))
        QMessageBox.critical(self, 'NXS Error', 'Could not convert {}:\n{}'.format(nxs_path.name, e))
        return
    open_directory(self, str(paths['dir']))
    open_mask(self, str(paths['mask']))
    if paths['poni'].exists():
        open_poni(self, str(paths['poni']))
    else:
        QMessageBox.warning(self, 'No Calibration',
                            '{} does not contain enough geometry to build a PONI file.\n'
                            'Load a PONI file or use the Calibration Tool.'.format(nxs_path.name))


def open_nxs(self, path):
    def find_scan_data(tree):
        location = ''
        keys = tree.keys()
        if 'scan_data' in keys:
            return location
        for key in keys:
            location += str(tree[key])
            find_scan_data(tree[key])
        return location

    file = path.split('/')[-1]
    nxs_file_dict = self.nxs_file_dict
    nxs_file_dict[file] = {}
    nxs_file = nxload(path)
    nxs_folder = find_scan_data(nxs_file)
    images_loc = nxs_file[nxs_folder].scan_data.eiger_image
    images_data = np.array(images_loc)
    images_data1 = np.flip(images_data, 1)
    image_count = list(range(1, len(images_data1) + 1))
    zipped = zip(image_count, images_data1)
    for item in zipped:
        nxs_file_dict[file]['{} - image {}'.format(file, item[0])] = item[1]
