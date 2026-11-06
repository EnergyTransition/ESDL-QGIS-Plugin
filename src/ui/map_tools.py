"""
QGIS map canvas tools used by the Add/Edit/Delete Asset and Add/Delete
Connection flows: geometry-capture tools (point/line/polygon) for placing a
new asset, a single-click asset picker for selecting an existing asset on
the map, and a two-click tool for selecting a source/target asset pair to
connect.
"""

from qgis.gui import QgsMapTool, QgsRubberBand, QgsVertexMarker
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProject,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor

from ..esdl_visualize import _closest_endpoint_on_geom

# All ESDL asset layers/geometries are stored in EPSG:4326,
# regardless of the project's current CRS.
# Map tools must convert between the two whenever they compare a
# clicked point (in canvas/project CRS) against stored lon/lat geometry.
_WGS84_CRS = QgsCoordinateReferenceSystem("EPSG:4326")


def _wgs84_transform(canvas):
    """QgsCoordinateTransform between the map canvas's CRS and EPSG:4326."""
    return QgsCoordinateTransform(
        canvas.mapSettings().destinationCrs(), _WGS84_CRS, QgsProject.instance()
    )


def _to_wgs84(canvas, point):
    """Convert a map-canvas-CRS point to EPSG:4326."""
    return _wgs84_transform(canvas).transform(point)


def _from_wgs84(canvas, point):
    """Convert an EPSG:4326 point to the map canvas's CRS."""
    return _wgs84_transform(canvas).transform(point, QgsCoordinateTransform.ReverseTransform)


class PointCaptureMapTool(QgsMapTool):
    """Single left-click captures a point geometry."""

    geometry_captured = pyqtSignal(object)  # QgsGeometry

    def __init__(self, canvas):
        super().__init__(canvas)

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            pt = self.toMapCoordinates(event.pos())
            pt = _to_wgs84(self.canvas(), pt)
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
            # self._points are kept in canvas CRS for the rubber-band preview;
            # convert to EPSG:4326 only for the final emitted geometry.
            wgs84_points = [_to_wgs84(self.canvas(), p) for p in self._points]
            geom = QgsGeometry.fromPolylineXY(wgs84_points)
            self._rubber_band.reset()
            self.canvas().unsetMapTool(self)
            self.geometry_captured.emit(geom)

    def deactivate(self):
        self._rubber_band.reset()
        self._points = []
        super().deactivate()


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
            # self._points are kept in canvas CRS for the rubber-band preview;
            # convert to EPSG:4326 only for the final emitted geometry.
            wgs84_points = [_to_wgs84(self.canvas(), p) for p in self._points]
            geom = QgsGeometry.fromPolygonXY([wgs84_points])
            self._rubber_band.reset()
            self.canvas().unsetMapTool(self)
            self.geometry_captured.emit(geom)

    def deactivate(self):
        self._rubber_band.reset()
        self._points = []
        super().deactivate()


class AssetPickerMapTool(QgsMapTool):
    """
    Single left-click identifies the nearest ESDL asset (via the visualizer's
    spatial lookup) and emits *asset_selected* with its id string. Right-click
    cancels.

    Generic on purpose: shared by Edit Asset, Delete Asset, and Delete
    Connection, any single-click asset-selection flow, without duplication.
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


class ConnectAssetsMapTool(QgsMapTool):
    """
    Two-step click tool for connecting asset ports.

    1. First click  — identifies the source asset; the connection line
       anchors at whichever end of its geometry (In/Out, never a mid-line
       turn vertex) is closest to the click, highlighted with a red circle.
    2. Second click — identifies the target asset; emits *assets_selected* with
                      the two asset IDs, then deactivates.

    Requires a reference to an ESDLVisualizer for spatial feature lookup.
    """

    assets_selected = pyqtSignal(str, str)  # source_id, target_id

    def __init__(self, canvas, visualizer):
        super().__init__(canvas)
        self._visualizer = visualizer
        self._source_id = None
        self._anchor_pt = None
        self._rubber_band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self._rubber_band.setColor(QColor(255, 140, 0))
        self._rubber_band.setWidth(2)
        self._marker = QgsVertexMarker(canvas)
        self._marker.setIconType(QgsVertexMarker.ICON_CIRCLE)
        self._marker.setColor(QColor(220, 0, 0))
        self._marker.setIconSize(10)
        self._marker.setPenWidth(2)
        self._marker.hide()

    def _tolerance(self) -> float:
        return self.canvas().mapUnitsPerPixel() * 10

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            if self._source_id is not None:
                # Undo the partial pick instead of forcing the user to
                # finish an unwanted connection first.
                self._reset_selection()
            else:
                self.canvas().unsetMapTool(self)
            return
        if event.button() != Qt.LeftButton:
            return
        point = self.toMapCoordinates(event.pos())
        asset_id = self._visualizer.find_asset_id_near(point, self._tolerance())
        if asset_id is None:
            return
        if self._source_id is None:
            self._source_id = asset_id
            src_asset = self._visualizer.get_asset_by_id(asset_id)
            # Asset geometry is stored in EPSG:4326; compare against the
            # click point in that same CRS, then convert the result back to
            # canvas CRS for on-screen display (marker/rubber band).
            anchor_wgs84 = _closest_endpoint_on_geom(
                getattr(src_asset, "geometry", None), _to_wgs84(self.canvas(), point)
            )
            self._anchor_pt = (
                _from_wgs84(self.canvas(), anchor_wgs84) if anchor_wgs84 is not None else None
            )
            if self._anchor_pt is not None:
                self._marker.setCenter(self._anchor_pt)
                self._marker.show()
        elif asset_id != self._source_id:
            self._rubber_band.reset()
            self._marker.hide()
            self.assets_selected.emit(self._source_id, asset_id)
            self.canvas().unsetMapTool(self)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.canvas().unsetMapTool(self)

    def canvasMoveEvent(self, event):
        if self._source_id is None or self._anchor_pt is None:
            return
        cur_pt = self.toMapCoordinates(event.pos())
        self._rubber_band.reset(QgsWkbTypes.LineGeometry)
        self._rubber_band.addPoint(self._anchor_pt)
        self._rubber_band.addPoint(cur_pt)

    def _reset_selection(self):
        """Clear the current source pick so the user can pick a new one."""
        self._source_id = None
        self._anchor_pt = None
        self._rubber_band.reset()
        self._marker.hide()

    def deactivate(self):
        self._rubber_band.reset()
        self._marker.hide()
        self._source_id = None
        self._anchor_pt = None
        super().deactivate()
