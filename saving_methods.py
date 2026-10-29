import pandas as pd
from silx.gui.qt import QMessageBox
from datetime import datetime


def save_csv(self):
    filepath = self.imagepath
    q_choice = self.q_combo.currentText()
    loadedlist = self.loadedlistwidget
    curvelist = [item.text() for item in loadedlist.selectedItems()]
    curvenames = []
    for curve in curvelist:
        if '.nxs' in curve:
            curvenames.append(curve)
        else:
            curvenames.append(curve.split('.')[0])
    if not curvelist:
        msg = QMessageBox()
        msg.setWindowTitle("Error")
        msg.setText("Please Select Integrated Curve to Save")
        x = msg.exec_()
    else:
        for curve in curvenames:
            df = pd.read_csv(r'{}/{}.dat'.format(filepath, curve), header=None, sep=",", skiprows=2)
            df.to_csv(filepath + '/{}_csv.csv'.format(curve), index=False, header=False)


def save_dat(filename, filepath, res, q_choice):
    now = datetime.now()
    # dd/mm/YY H:M:S
    dt_string = now.strftime("%d/%m/%Y %H:%M:%S")
    date_time = 'DAT file by SAXSII - Timestamp: {}'.format(dt_string)
    res_to_save = res.copy()
    res_to_save.columns = [f'radial-{q_choice}' if x == 'radial' else x for x in res_to_save.columns]
    save_path = '{}/{}.dat'.format(filepath, filename)
    with open(save_path, 'w') as f:
        # f.write('# List of Vars\n')
        f.write('#{}\n'.format(date_time))
    res_to_save.to_csv(save_path, mode='a', index=False)
    f.close()
