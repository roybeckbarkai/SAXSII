import os
import logging
import numpy as np
import fabio
from silx.gui.qt import QFont
from open_methods import open_mask

logging.basicConfig(filename='app.log', filemode='a', format='%(asctime)s - %(message)s', datefmt='%d-%b-%y %H:%M:%S')


def find_detector_gaps(image):
    """Pixels to exclude from integration (True = masked):
    - detector flags: max value of an integer type (Eiger gaps, 4294967295 for uint32),
      negative values (Pilatus -1 / -2), non-finite values;
    - whole rows / columns with one constant value (module gaps already set to 0,
      e.g. in TIFFs converted from NXS)."""
    img = np.asarray(image)
    if img.dtype.kind == 'u':
        mask = img == np.iinfo(img.dtype).max
    elif img.dtype.kind == 'i':
        mask = (img < 0) | (img == np.iinfo(img.dtype).max)
    else:
        mask = ~np.isfinite(img) | (img < 0) | (img >= 2 ** 32 - 1)
    mask[(img == img[:, :1]).all(axis=1), :] = True
    mask[:, (img == img[:1, :]).all(axis=0)] = True
    return mask


def ensure_mask(self, image=None):
    """Return the loaded mask (fabio image). If no mask is loaded, or it does not
    match the image size, build one from `image` (array or file name in the
    image folder), save it as <name>_auto_mask.edf in the image folder and load it.
    Returns None when there is neither a usable mask nor an image."""
    if isinstance(image, str):
        image = fabio.open(os.path.join(self.imagepath, image)).data
    if image is None:
        image = getattr(self, 'raw_image', None)

    mask_file = getattr(self, 'mask_file', None)
    if mask_file and os.path.isfile(mask_file):
        mask = fabio.open(mask_file)
        if image is None or mask.data.shape == np.shape(image):
            return mask
        logging.error('Mask {} has shape {}, image has {}; creating a new mask'.format(
            mask_file, mask.data.shape, np.shape(image)))
    if image is None:
        return None

    gaps = find_detector_gaps(image)
    name = 'auto_mask_{}x{}.edf'.format(*np.shape(image))
    save_path = os.path.join(self.imagepath, name)
    fabio.edfimage.EdfImage(data=gaps.astype(np.uint8)).write(save_path)
    open_mask(self, save_path)
    self.mask_label.setText('auto-generated Mask file: /{} ({} px masked)'.format(name, int(gaps.sum())))
    self.mask_label.setFont(QFont('Segoe UI', 9))
    return fabio.open(save_path)
