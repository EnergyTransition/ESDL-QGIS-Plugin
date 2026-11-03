"""
QGIS map canvas tools used by the Add Asset / Delete Asset flows:
geometry-capture tools (point/line/polygon) for placing a new asset, and a
single-click asset picker for selecting an existing asset on the map.
"""

from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.core import QgsGeometry, QgsWkbTypes
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor


class PointCaptureMapTool(QgsMapTool):
    """Single left-click captures a point geometry."""

    geometry_captured = pyqtSignal(object)  # QgsGeometry

    def __init__(self, canvas):
        super().__init__(canvas)

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            pt = self.toMapCoordinates(event.pos())
            self.geometry_captured.emit(QgsGeometry.fromPointXY(pt))
            self.canvas().unsetMapTool(self)


class LineCaptureMapTool(QgsMapTool):
    """Left-click adds vertices; right-click finishes and emits a LineString."""

    geometry_captured = pyqtSignal(object)  # QgsGeometry

    def __init__(self, canvas):
        super().__init__(canvas)
        self._points = []
        self._rubber_band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self._rubber_band.setColor(QColor(0, 120, 215))
        self._rubber_band.setWidth(2)

    def canvasPressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._points.append(self.toMapCoordinates(event.pos()))
        elif event.button() == Qt.RightButton:
            self._finish()

    def canvasMoveEvent(self, event):
        if not self._points:
            return
        cur = self.toMapCoordinates(event.pos())
        self._rubber_band.reset(QgsWkbTypes.LineGeometry)
        for p in self._points:
            self._rubber_band.addPoint(p, False)
        self._rubber_band.addPoint(cur, True)

    def _finish(self):
        if len(self._points) >= 2:
            geom = QgsGeometry.fromPolylineXY(self._points)
            self._rubber_band.reset()
            self.canvas().unsetMapTool(self)
            self.geometry_captured.emit(geom)

    def deactivated(self):
        self._rubber_band.reset()
        self._points = []


class PolygonCaptureMapTool(QgsMapTool):
    """Left-click adds vertices; right-click finishes and emits a Polygon."""

    geometry_captured = pyqtSignal(object)  # QgsGeometry

    def __init__(self, canvas):
        super().__init__(canvas)
        self._points = []
        self._rubber_band = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
        self._rubber_band.setColor(QColor(0, 120, 215))
        self._rubber_band.setFillColor(QColor(0, 120, 215, 40))
        self._rubber_band.setWidth(2)

    def canvasPressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._points.append(self.toMapCoordinates(event.pos()))
        elif event.button() == Qt.RightButton:
            self._finish()

    def canvasMoveEvent(self, event):
        if not self._points:
            return
        cur = self.toMapCoordinates(event.pos())
        self._rubber_band.reset(QgsWkbTypes.PolygonGeometry)
        for p in self._points:
            self._rubber_band.addPoint(p, False)
        self._rubber_band.addPoint(cur, True)

    def _finish(self):
        if len(self._points) >= 3:
            geom = QgsGeometry.fromPolygonXY([self._points])
            self._rubber_band.reset()
            self.canvas().unsetMapTool(self)
            self.geometry_captured.emit(geom)

    def deactivated(self):
        self._rubber_band.reset()
        self._points = []


class AssetPickerMapTool(QgsMapTool):
    """
    Single left-click identifies the nearest ESDL asset (via the visualizer's
    spatial lookup) and emits *asset_selected* with its id string. Right-click
    cancels.

    Generic on purpose: usable for Delete Asset today, and for any future
    single-click asset-selection flow (e.g. Edit Asset) without duplication.
    """

    asset_selected = pyqtSignal(str)  # asset_id

    def __init__(self, canvas, visualizer):
        super().__init__(canvas)
        self._visualizer = visualizer

    def _tolerance(self) -> float:
        return self.canvas().mapUnitsPerPixel() * 10

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            point = self.toMapCoordinates(event.pos())
            asset_id = self._visualizer.find_asset_id_near(point, self._tolerance())
            if asset_id is not None:
                self.asset_selected.emit(asset_id)
                self.canvas().unsetMapTool(self)
        elif event.button() == Qt.RightButton:
            self.canvas().unsetMapTool(self)
