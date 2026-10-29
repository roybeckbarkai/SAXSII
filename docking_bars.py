import functools
import numpy
from silx.gui import qt
from silx.gui.plot import Plot1D
from silx.gui.plot.tools.CurveLegendsWidget import CurveLegendsWidget
from silx.gui.widgets.BoxLayoutDockWidget import BoxLayoutDockWidget


class MyCurveLegendsWidget(CurveLegendsWidget):
    """Extension of CurveLegendWidget.

    This widget adds:
    - Set a curve as active with a left click its the legend
    - Adds a context menu with content specific to the hovered legend

    :param QWidget parent: See QWidget
    """

    def __init__(self, parent=None):
        super(MyCurveLegendsWidget, self).__init__(parent)

        # Activate/Deactivate curve with left click on the legend widget
        self.sigCurveClicked.connect(self._switchCurveActive)

        # Add a custom context menu
        self.setContextMenuPolicy(qt.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._contextMenu)

    def _switchCurveActive(self, curve):
        """Set a curve as active.

        This is called from the context menu and when a legend is clicked.

        :param silx.gui.plot.items.Curve curve:
        """
        plot = curve.getPlot()
        plot.setActiveCurve(
            curve.getName() if curve != plot.getActiveCurve() else None)

    def _switchCurveVisibility(self, curve):
        """Toggle the visibility of a curve

        :param silx.gui.plot.items.Curve curve:
        """
        curve.setVisible(not curve.isVisible())

    def _switchCurveYAxis(self, curve):
        """Change the Y axis a curve is attached to.

        :param silx.gui.plot.items.Curve curve:
        """
        yaxis = curve.getYAxis()
        curve.setYAxis('left' if yaxis == 'right' else 'right')

    def _contextMenu(self, pos):
        """Create a show the context menu.

        :param QPoint pos: Position in this widget
        """
        curve = self.curveAt(pos)  # Retrieve curve from hovered legend
        if curve is not None:
            menu = qt.QMenu()  # Create the menu

            # Add an action to activate the curve
            activeCurve = curve.getPlot().getActiveCurve()
            menu.addAction('Unselect' if curve == activeCurve else 'Select',
                           functools.partial(self._switchCurveActive, curve))

            # Add an action to switch the Y axis of a curve
            yaxis = 'right' if curve.getYAxis() == 'left' else 'left'
            menu.addAction('Map to %s' % yaxis,
                           functools.partial(self._switchCurveYAxis, curve))

            # Add an action to show/hide the curve
            menu.addAction('Hide' if curve.isVisible() else 'Show',
                           functools.partial(self._switchCurveVisibility, curve))

            globalPosition = self.mapToGlobal(pos)
            menu.exec_(globalPosition)
