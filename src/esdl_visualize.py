"""
Converts a pyESDL EnergySystem into QGIS in-memory vector layers and
provides sync-back so edits can be serialised back to an .esdl file.
"""

from qgis.core import (
    Qgis,
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsField,
    QgsProject,
    QgsRectangle,
    QgsFeatureRequest,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsMarkerLineSymbolLayer,
    QgsSimpleMarkerSymbolLayer,
    QgsRasterMarkerSymbolLayer,
    QgsPalLayerSettings,
    QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtCore import QVariant, Qt
from qgis.PyQt.QtGui import QColor, QFont
from pathlib import Path

from .esdl_ecore import coerce_attr_value, describe_asset_attributes, set_esdl_attr
from .esdl_model import iter_port_connections

# Fields that are part of every QGIS asset layer but are NOT necessarily ESDL EAttributes.
_STANDARD_FIELD_NAMES = ("id", "name", "esdl_class", "area_name")
_STANDARD_FIELDS = frozenset(_STANDARD_FIELD_NAMES)

# ---------------------------------------------------------------------------
#### Config ####
# ---------------------------------------------------------------------------

# Directory that holds optional per-class PNG icons (e.g. HeatingDemand.png).
# Icons are local-only for now; fetching them from an online repository is not implemented yet.
_ICONS_DIR = Path(__file__).parent / "icons"

# Capability base-class names (from the ESDL meta-model) mapped to the fill
# colour used for the circle background.
_CAPABILITY_COLORS = {
    "Consumer": "#649dc9",  # hsl(206, 48%, 59%)
    "Producer": "#92a800",  # hsl(68, 100%, 33%)
    "Storage": "#cd1326",  # hsl(354, 83%, 44%)
    "Transport": "#ffcc00",  # hsl(48, 100%, 50%)
    "Conversion": "#b573ba",  # hsl(296, 34%, 59%)
}

# Circle fill colour used as exception when capability can't be determined.
_UNKNOWN_CAPABILITY_COLOR = "lightgray"

# Marker sizes (mm) for the capability circle and its overlaid PNG icon.
_CIRCLE_SIZE = 8.0
_ICON_SIZE = 5.0

# Do not apply circular background for Joint asset.
_ICON_HAS_OWN_BACKGROUND = {"Joint"}
_SMALL_ICON_SIZE = 2.0

# Font size (mm) for the capital-letter abbreviation.
_LABEL_FONT_SIZE = 3.0

# Default line colours for specific transport asset classes;
_LINE_COLORS = {
    "ElectricityCable": "#ff9090",
    "Pipe": "#3B47E3",
}

# ---------------------------------------------------------------------------


def _class_abbreviation(class_name: str) -> str:
    """
    Short abbreviation built from the capital letters in *class_name*, used
    as a fallback label when no PNG icon exists for the class, e.g. PowerToX -> PTX.
    """
    letters = "".join(ch for ch in class_name if ch.isupper())
    return letters or class_name[:3].upper()


def _apply_abbreviation_label(layer, class_name: str) -> None:
    """
    Show *class_name*'s capital-letter abbreviation centred on every point feature. 
    Used in place of a PNG icon when none is available. Silently does
    nothing if labelling can't be configured (e.g. API differences across
    QGIS versions).
    """
    try:
        text = _class_abbreviation(class_name)

        settings = QgsPalLayerSettings()
        settings.fieldName = f"'{text}'"
        settings.isExpression = True
        settings.placement = Qgis.LabelPlacement.OverPoint

        font = QFont("Arial")

        text_format = QgsTextFormat()
        text_format.setFont(font)
        text_format.setSizeUnit(Qgis.RenderUnit.Millimeters)
        text_format.setSize(_LABEL_FONT_SIZE)
        text_format.setColor(QColor("black"))

        settings.setFormat(text_format)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
    except Exception:
        pass  # leave layer without a text label


def _get_asset_capability(asset) -> "str | None":
    """
    Return the ESDL capability name (Producer, Consumer, Storage, Transport,
    or Conversion) for *asset* by walking its class hierarchy.
    Returns None if no capability base class is found.
    """
    if asset is None:
        return None
    class_names = {klass.__name__ for klass in type(asset).__mro__}
    for capability in _CAPABILITY_COLORS:
        if capability in class_names:
            return capability
    return None


def _apply_point_icon(layer, class_name: str, asset=None) -> None:
    """
    Build a MapEditor-style point symbol: a circle filled with the colour of
    the asset's ESDL capability (Producer/Conversion/Transport/Consumer/
    Storage). The class's PNG icon (src/icons/{class_name}.png) is layered on
    top when available; otherwise the class's capital-letter abbreviation is shown 
    as a text label instead. Falls back to an uncoloured circle when the
    capability can't be determined.
    """
    capability_color = _CAPABILITY_COLORS.get(_get_asset_capability(asset))
    icon_path = _ICONS_DIR / f"{class_name}.png"
    has_icon = icon_path.exists()

    try:
        sym = QgsMarkerSymbol()

        if has_icon and class_name in _ICON_HAS_OWN_BACKGROUND:
            raster = QgsRasterMarkerSymbolLayer(str(icon_path))
            raster.setSize(_SMALL_ICON_SIZE)
            sym.changeSymbolLayer(0, raster)
        else:
            circle = QgsSimpleMarkerSymbolLayer()
            circle.setShape(Qgis.MarkerShape.Circle)
            circle.setSize(_CIRCLE_SIZE)
            circle.setColor(QColor(capability_color or _UNKNOWN_CAPABILITY_COLOR))
            circle.setStrokeColor(QColor("black"))
            circle.setStrokeWidth(0.2)
            sym.changeSymbolLayer(0, circle)

            if has_icon:
                raster = QgsRasterMarkerSymbolLayer(str(icon_path))
                raster.setSize(_ICON_SIZE)
                sym.appendSymbolLayer(raster)

        layer.renderer().setSymbol(sym)
    except Exception:
        pass  # leave default symbol intact

    if not has_icon:
        _apply_abbreviation_label(layer, class_name)


def _apply_line_color(layer, class_name: str) -> None:
    """
    Set *layer*'s line symbol colour to the value in _LINE_COLORS for
    *class_name*, if one is defined. Leaves the QGIS-assigned default colour
    untouched for classes not in that mapping.
    """
    color = _LINE_COLORS.get(class_name)
    if color is None:
        return
    try:
        layer.renderer().symbol().setColor(QColor(color))
    except Exception:
        pass  # leave default symbol colour intact


def _apply_directional_symbol(layer) -> None:
    """
    Append a midpoint arrowhead marker to a LineString layer so the draw
    direction (A → B) is visible on the map.  The arrow inherits the colour
    of the base line symbol and rotates automatically along the line bearing.
    """
    arrow = QgsSimpleMarkerSymbolLayer()
    arrow.setShape(Qgis.MarkerShape.ArrowHeadFilled)
    arrow.setSize(3.5)
    # Inherit the colour from the existing base line symbol
    base_color = layer.renderer().symbol().color()
    arrow.setColor(base_color)
    arrow.setStrokeStyle(Qt.PenStyle.NoPen)  # no border on arrowhead fill

    marker_sym = QgsMarkerSymbol()
    marker_sym.changeSymbolLayer(0, arrow)

    marker_line = QgsMarkerLineSymbolLayer()
    marker_line.setPlacement(Qgis.MarkerLinePlacement.CentralPoint)
    marker_line.setSubSymbol(marker_sym)

    layer.renderer().symbol().appendSymbolLayer(marker_line)


# ---------------------------------------------------------------------------
# ESDL EAttribute introspection
# ---------------------------------------------------------------------------


def _get_esdl_attr_fields(asset):
    """
    Build QGIS field descriptors from the asset's ESDL EAttributes
    (discovered via esdl_ecore.describe_asset_attributes), excluding the
    standard layer fields.

    Returns list of (field_name, QVariant_type, default, enum_options_or_None).
    enum_options_or_None is a list of string literal names for EEnum fields,
    or None for plain scalar fields.
    """
    result = []
    for desc in describe_asset_attributes(asset, exclude=_STANDARD_FIELDS):
        if desc.is_enum:
            qtype = QVariant.String
        elif isinstance(desc.value, bool):
            qtype = QVariant.String
        elif isinstance(desc.value, int):
            qtype = QVariant.Int
        elif isinstance(desc.value, float):
            qtype = QVariant.Double
        else:
            qtype = QVariant.String
        default = coerce_attr_value(desc.value, desc.is_enum)
        result.append((desc.name, qtype, default, desc.enum_options))
    return result


# ---------------------------------------------------------------------------
# Geometry helpers (duck-typed — no direct import of esdl.esdl classes)
# ---------------------------------------------------------------------------


def _geom_type_name(g) -> str:
    """Return 'Point', 'Line', 'Polygon', 'WKT', or '' for unknown."""
    name = type(g).__name__
    if name == "Point":
        return "Point"
    if name == "Line":
        return "Line"
    if name in ("Polygon", "MultiPolygon"):
        return "Polygon"
    if name == "WKT":
        return "WKT"
    return ""


def _to_qgs_geom(geometry):
    """Convert a pyESDL geometry to a QgsGeometry, or None if unsupported."""
    if geometry is None:
        return None
    kind = _geom_type_name(geometry)
    if kind == "Point":
        return QgsGeometry.fromPointXY(QgsPointXY(geometry.lon, geometry.lat))
    if kind == "Line":
        pts = [QgsPointXY(p.lon, p.lat) for p in geometry.point]
        return QgsGeometry.fromPolylineXY(pts) if len(pts) >= 2 else None
    if kind == "Polygon":
        ring = [QgsPointXY(p.lon, p.lat) for p in geometry.exterior.point]
        return QgsGeometry.fromPolygonXY([ring]) if len(ring) >= 3 else None
    if kind == "WKT":
        return QgsGeometry.fromWkt(geometry.value)
    return None


def _closest_endpoint_on_geom(geometry, to_point: "QgsPointXY") -> "QgsPointXY | None":
    """
    Return the connection anchor point on *geometry* nearest to *to_point*.
    Point geometries return themselves. Line geometries only ever snap to
    one of their two ends (In/Out) \u2014 never a mid-line turn vertex, since a
    turn isn't a real port location. Polygon/WKT fall back to a single
    representative point (first exterior vertex / centroid).
    """
    if geometry is None or to_point is None:
        return None
    kind = _geom_type_name(geometry)
    if kind == "Point":
        return QgsPointXY(geometry.lon, geometry.lat)
    if kind == "Line":
        try:
            pts = list(geometry.point)
        except Exception:
            pts = []
        if not pts:
            return None
        first = QgsPointXY(pts[0].lon, pts[0].lat)
        last = QgsPointXY(pts[-1].lon, pts[-1].lat)
        return first if first.distance(to_point) <= last.distance(to_point) else last
    if kind == "Polygon":
        try:
            pt = geometry.exterior.point[0]
            return QgsPointXY(pt.lon, pt.lat)
        except Exception:
            pass
    qgs = _to_qgs_geom(geometry)
    if qgs is None or qgs.isEmpty():
        return None
    c = qgs.centroid()
    if c is None or c.isEmpty():
        return None
    return c.asPoint()


def _geom_endpoint(geometry, use_last: bool = False):
    """
    Return (lon, lat) for a connection endpoint on a pyESDL geometry:
    - Point  : lon/lat directly.
    - Line   : first point when use_last=False, last point when use_last=True.
    - Polygon: first exterior point.
    - WKT    : centroid of the parsed geometry.
    Returns (None, None) if the geometry cannot be resolved.
    """
    if geometry is None:
        return None, None
    kind = _geom_type_name(geometry)
    if kind == "Point":
        return geometry.lon, geometry.lat
    if kind == "Line":
        try:
            pts = list(geometry.point)
            if pts:
                p = pts[-1] if use_last else pts[0]
                return p.lon, p.lat
        except Exception:
            pass
    if kind == "Polygon":
        try:
            pts = list(geometry.exterior.point)
            if pts:
                return pts[0].lon, pts[0].lat
        except Exception:
            pass
    if kind == "WKT":
        qgs = _to_qgs_geom(geometry)
        if qgs and not qgs.isEmpty():
            c = qgs.centroid()
            if c and not c.isEmpty():
                pt = c.asPoint()
                return pt.x(), pt.y()
    return None, None


def _qgs_geom_type_string(geometry) -> str:
    """Map pyESDL geometry to QGIS layer type string."""
    kind = _geom_type_name(geometry)
    return {
        "Point": "Point",
        "Line": "LineString",
        "Polygon": "Polygon",
        "WKT": "Point",
    }.get(kind, "")


def qgs_geom_to_esdl(qgs_geom, geom_hint: str):
    """
    Convert a QgsGeometry to the matching pyESDL geometry object.

    geom_hint: 'Point', 'Line', or 'Polygon'.
    Returns None if conversion fails or the geometry is too small to be valid.
    """
    from esdl import esdl

    try:
        if geom_hint == "Point":
            pt = qgs_geom.asPoint()
            return esdl.Point(lat=pt.y(), lon=pt.x())
        if geom_hint == "Line":
            line = esdl.Line()
            for pt in qgs_geom.asPolyline():
                line.point.append(esdl.Point(lat=pt.y(), lon=pt.x()))
            return line if len(line.point) >= 2 else None
        if geom_hint == "Polygon":
            poly = esdl.Polygon()
            exterior = esdl.SubPolygon()
            for pt in qgs_geom.asPolygon()[0]:
                exterior.point.append(esdl.Point(lat=pt.y(), lon=pt.x()))
            poly.exterior = exterior
            return poly if len(exterior.point) >= 3 else None
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Asset tree walker
# ---------------------------------------------------------------------------


def _collect_assets(area, out_by_class: dict, out_areas: list, area_name=""):
    """Recursively collect assets and area geometries."""
    if area.geometry is not None and _geom_type_name(area.geometry) == "Polygon":
        out_areas.append({"area": area, "parent": area_name})

    for asset in area.asset:
        class_name = type(asset).__name__
        out_by_class.setdefault(class_name, []).append(
            {"asset": asset, "area_name": area.name or ""}
        )

    for sub in area.area:
        _collect_assets(sub, out_by_class, out_areas, area.name or "")


def _collect_connections(assets_by_class: dict) -> list:
    """
    Build QGIS-ready connection records — with geometry endpoints attached —
    from every InPort -> OutPort link discovered by
    esdl_model.iter_port_connections().

    Returns a list of dicts with keys:
        in_port_id, in_asset_id, out_port_id, out_asset_id,
        from_lon, from_lat, to_lon, to_lat

    Endpoint selection convention:
    - InPort's asset  (from side): first point for Line, lon/lat for Point.
    - OutPort's asset (to side):   last  point for Line, lon/lat for Point.
    In properly-constructed ESDLs pipe endpoints coincide with connected
    asset positions, so connection lines are near-zero-length dashes that
    record topology without adding visual clutter.
    """
    result = []
    for in_port, in_asset, out_port, out_asset in iter_port_connections(assets_by_class):
        from_lon, from_lat = _geom_endpoint(
            getattr(in_asset, "geometry", None), use_last=False
        )
        to_lon, to_lat = _geom_endpoint(
            getattr(out_asset, "geometry", None), use_last=True
        )
        if None in (from_lon, from_lat, to_lon, to_lat):
            continue
        result.append(
            {
                "in_port_id": getattr(in_port, "id", ""),
                "in_asset_id": getattr(in_asset, "id", ""),
                "out_port_id": getattr(out_port, "id", ""),
                "out_asset_id": getattr(out_asset, "id", ""),
                "from_lon": from_lon,
                "from_lat": from_lat,
                "to_lon": to_lon,
                "to_lat": to_lat,
            }
        )
    return result


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class ESDLVisualizer:
    """
    Creates QGIS memory layers from a loaded EnergySystem and can sync
    user edits back into the in-memory ESDL object for re-serialisation.

    Usage:
        viz = ESDLVisualizer(esh, energy_system)
        viz.create_and_add_layers()
        # ... user edits layers in QGIS ...
        xml = viz.to_xml()   # serialise back to a .esdl file
    """

    def __init__(self, esh, energy_system):
        self._esh = esh
        self._es = energy_system
        self._layers = {}  # class_name -> QgsVectorLayer
        self._asset_by_id = {}  # asset.id -> pyESDL asset object
        self._extra_fields = {}  # class_name -> [field_name, ...]
        self._group = None  # QgsLayerTreeGroup created by create_and_add_layers

    # ------------------------------------------------------------------
    # Build layers
    # ------------------------------------------------------------------

    def create_and_add_layers(self):
        """Walk the ESDL tree, build memory layers, and add to QgsProject."""
        assets_by_class = {}
        area_list = []

        root_area = self._es.instance[0].area
        _collect_assets(root_area, assets_by_class, area_list)

        group_name = self._es.name or "ESDL"
        root = QgsProject.instance().layerTreeRoot()
        group = root.insertGroup(0, group_name)
        self._group = group

        # One layer per asset class
        for class_name, entries in assets_by_class.items():
            layer = self._build_asset_layer(class_name, entries)
            if layer is not None:
                self._layers[class_name] = layer
                QgsProject.instance().addMapLayer(layer, False)
                group.addLayer(layer)

        # Optional area layer
        if area_list:
            area_layer = self._build_area_layer(area_list)
            if area_layer is not None:
                self._layers["_areas"] = area_layer
                QgsProject.instance().addMapLayer(area_layer, False)
                group.addLayer(area_layer)

        # Connections layer — built from OutPort.connectedTo references.
        # Display-only in this version: existing connections are rendered,
        # but there is no UI yet to add or remove them.
        conn_list = _collect_connections(assets_by_class)
        if conn_list:
            conn_layer = self._build_connections_layer(conn_list)
            if conn_layer is not None:
                self._layers["_connections"] = conn_layer
                QgsProject.instance().addMapLayer(conn_layer, False)
                group.insertLayer(0, conn_layer)  # draw below asset points

        return list(self._layers.values())

    def _build_asset_layer(self, class_name: str, entries: list):
        # Find first usable geometry to determine layer type
        geom_type_str = ""
        for e in entries:
            geom_type_str = _qgs_geom_type_string(e["asset"].geometry)
            if geom_type_str:
                break
        if not geom_type_str:
            return None

        layer = QgsVectorLayer(f"{geom_type_str}?crs=EPSG:4326", class_name, "memory")
        pr = layer.dataProvider()

        # Standard fields (always present)
        standard = [QgsField(name, QVariant.String) for name in _STANDARD_FIELD_NAMES]
        pr.addAttributes(standard)

        # Extra ESDL EAttribute fields — introspected from the first asset
        extra_defs = _get_esdl_attr_fields(entries[0]["asset"])
        self._extra_fields[class_name] = [name for name, *_ in extra_defs]
        pr.addAttributes([QgsField(name, qtype) for name, qtype, *_ in extra_defs])
        layer.updateFields()

        # Custom icon for point layers; directional arrow for line layers
        if geom_type_str == "Point":
            _apply_point_icon(layer, class_name, entries[0]["asset"])
        elif geom_type_str == "LineString":
            _apply_line_color(layer, class_name)
            _apply_directional_symbol(layer)

        # Make id and esdl_class read-only in the attribute form
        form_cfg = layer.editFormConfig()
        for read_only_field in ("id", "esdl_class"):
            idx = layer.fields().indexOf(read_only_field)
            if idx >= 0:
                form_cfg.setReadOnly(idx, True)
        layer.setEditFormConfig(form_cfg)

        features = []
        for e in entries:
            asset = e["asset"]
            geom = _to_qgs_geom(asset.geometry)
            if geom is None:
                continue
            self._asset_by_id[asset.id] = asset

            # Build attribute list: standard fields first, then extra
            extra_vals = []
            for name, qtype, *_ in extra_defs:
                val = getattr(asset, name, None)
                if isinstance(val, bool):
                    extra_vals.append(str(val).lower())
                elif isinstance(val, (int, float)):
                    extra_vals.append(val if val is not None else 0)
                else:
                    extra_vals.append(str(val) if val is not None else "")

            f = QgsFeature()
            f.setGeometry(geom)
            f.setAttributes(
                [
                    asset.id,
                    asset.name or "",
                    class_name,
                    e["area_name"],
                    *extra_vals,
                ]
            )
            features.append(f)

        pr.addFeatures(features)
        layer.updateExtents()
        return layer

    def _build_area_layer(self, area_list: list):
        layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "Areas", "memory")
        pr = layer.dataProvider()
        pr.addAttributes(
            [
                QgsField("id", QVariant.String),
                QgsField("name", QVariant.String),
            ]
        )
        layer.updateFields()

        features = []
        for e in area_list:
            area = e["area"]
            geom = _to_qgs_geom(area.geometry)
            if geom is None:
                continue
            f = QgsFeature()
            f.setGeometry(geom)
            f.setAttributes([area.id or "", area.name or ""])
            features.append(f)

        pr.addFeatures(features)
        layer.updateExtents()
        return layer

    def _build_connections_layer(self, conn_list: list):
        """
        Build a dashed-grey LineString memory layer from _collect_connections
        output.
        """
        layer = QgsVectorLayer("LineString?crs=EPSG:4326", "Connections", "memory")
        pr = layer.dataProvider()
        pr.addAttributes(
            [
                QgsField("in_port_id", QVariant.String),
                QgsField("in_asset_id", QVariant.String),
                QgsField("out_port_id", QVariant.String),
                QgsField("out_asset_id", QVariant.String),
            ]
        )
        layer.updateFields()

        features = []
        for c in conn_list:
            f = QgsFeature(layer.fields())
            f.setGeometry(
                QgsGeometry.fromPolylineXY(
                    [
                        QgsPointXY(c["from_lon"], c["from_lat"]),
                        QgsPointXY(c["to_lon"], c["to_lat"]),
                    ]
                )
            )
            f.setAttributes(
                [
                    c["in_port_id"],
                    c["in_asset_id"],
                    c["out_port_id"],
                    c["out_asset_id"],
                ]
            )
            features.append(f)

        if not features:
            return None
        pr.addFeatures(features)
        layer.updateExtents()
        symbol = QgsLineSymbol.createSimple({"line_style": "dash", "color": "grey"})
        layer.renderer().setSymbol(symbol)
        return layer

    # ------------------------------------------------------------------
    # Add new assets at runtime
    # ------------------------------------------------------------------

    def known_classes(self) -> set:
        """Return the set of asset class names currently held in layers."""
        return {k for k in self._layers if not k.startswith("_")}

    def get_asset_by_id(self, asset_id: str):
        """Return the pyESDL asset for *asset_id*, or None."""
        return self._asset_by_id.get(asset_id)

    def find_asset_id_near(self, map_point, tolerance: float):
        """
        Search all asset layers for the first feature within *tolerance* map
        units of *map_point* (a QgsPointXY, in the map canvas/project CRS).
        Returns the feature's 'id' attribute string, or None if nothing is
        found.
        """
        rect = QgsRectangle(
            map_point.x() - tolerance,
            map_point.y() - tolerance,
            map_point.x() + tolerance,
            map_point.y() + tolerance,
        )
        # map_point/tolerance are expressed in the project CRS, which may no
        # longer be EPSG:4326. setDestinationCrs makes the
        # feature request transform the filter rect into each layer's native
        # CRS before filtering, instead of comparing mismatched coordinates.
        request = QgsFeatureRequest().setFilterRect(rect)
        request.setDestinationCrs(
            QgsProject.instance().crs(), QgsProject.instance().transformContext()
        )
        for class_name, layer in self._layers.items():
            if class_name.startswith("_"):
                continue
            for feat in layer.getFeatures(request):
                return feat["id"]
        return None

    def add_asset_to_layer(self, asset, area_name: str = ""):
        """
        Register a newly created pyESDL *asset* in *self._asset_by_id* and
        add its QgsFeature to the appropriate layer.  If no layer exists yet
        for this asset class, one is created and added to the layer group.

        Returns the QgsVectorLayer the asset was added to (whether reused or
        newly created), or None if the asset's geometry could not be added.
        """
        class_name = type(asset).__name__
        self._asset_by_id[asset.id] = asset

        if class_name in self._layers:
            layer = self._layers[class_name]
            geom = _to_qgs_geom(asset.geometry)
            if geom is None:
                return None
            extra_names = self._extra_fields.get(class_name, [])
            extra_vals = []
            for name in extra_names:
                val = getattr(asset, name, None)
                if isinstance(val, bool):
                    extra_vals.append(str(val).lower())
                elif isinstance(val, (int, float)):
                    extra_vals.append(val if val is not None else 0)
                else:
                    extra_vals.append(str(val) if val is not None else "")
            f = QgsFeature(layer.fields())
            f.setGeometry(geom)
            f.setAttributes(
                [asset.id, asset.name or "", class_name, area_name, *extra_vals]
            )
            layer.dataProvider().addFeatures([f])
            layer.updateExtents()
            layer.triggerRepaint()
            return layer
        else:
            # First asset of this class — build a full layer from scratch
            layer = self._build_asset_layer(
                class_name, [{"asset": asset, "area_name": area_name}]
            )
            if layer is None:
                return None
            self._layers[class_name] = layer
            root = QgsProject.instance().layerTreeRoot()
            group_name = self._es.name or "ESDL"
            group = next(
                (
                    c
                    for c in root.children()
                    if hasattr(c, "name") and c.name() == group_name
                ),
                None,
            )
            QgsProject.instance().addMapLayer(layer, False)
            if group is not None:
                group.addLayer(layer)
            else:
                root.addLayer(layer)
            layer.triggerRepaint()
            return layer

    def add_connection_to_layer(self, out_asset, in_asset) -> None:
        """
        Draw a dashed connection line between two manually connected assets.
        out_asset owns the OutPort; in_asset owns the InPort.
        The 'Connections' layer is created on first use.
        """
        from_lon, from_lat = _geom_endpoint(
            getattr(out_asset, "geometry", None), use_last=True
        )
        to_lon, to_lat = _geom_endpoint(
            getattr(in_asset, "geometry", None), use_last=False
        )
        if None in (from_lon, from_lat, to_lon, to_lat):
            return
        line_geom = QgsGeometry.fromPolylineXY(
            [
                QgsPointXY(from_lon, from_lat),
                QgsPointXY(to_lon, to_lat),
            ]
        )

        if "_connections" not in self._layers:
            layer = QgsVectorLayer("LineString?crs=EPSG:4326", "Connections", "memory")
            pr = layer.dataProvider()
            pr.addAttributes(
                [
                    QgsField("in_port_id", QVariant.String),
                    QgsField("in_asset_id", QVariant.String),
                    QgsField("out_port_id", QVariant.String),
                    QgsField("out_asset_id", QVariant.String),
                ]
            )
            layer.updateFields()
            symbol = QgsLineSymbol.createSimple({"line_style": "dash", "color": "grey"})
            layer.renderer().setSymbol(symbol)
            self._layers["_connections"] = layer
            root = QgsProject.instance().layerTreeRoot()
            group_name = self._es.name or "ESDL"
            group = next(
                (
                    c
                    for c in root.children()
                    if hasattr(c, "name") and c.name() == group_name
                ),
                None,
            )
            QgsProject.instance().addMapLayer(layer, False)
            if group is not None:
                group.insertLayer(0, layer)
            else:
                root.addLayer(layer)

        layer = self._layers["_connections"]
        f = QgsFeature(layer.fields())
        f.setGeometry(line_geom)
        f.setAttributes(["", in_asset.id, "", out_asset.id])
        layer.dataProvider().addFeatures([f])
        layer.updateExtents()
        layer.triggerRepaint()

    # ------------------------------------------------------------------
    # Sync edits back into ESDL
    # ------------------------------------------------------------------

    def sync_from_layers(self):
        """
        Read current feature attributes/geometry from all asset layers
        and apply them back to the in-memory pyESDL asset objects.
        """
        for class_name, layer in self._layers.items():
            if class_name.startswith("_"):  # skip _areas, _connections, etc.
                continue
            extra = self._extra_fields.get(class_name, [])
            for feat in layer.getFeatures():
                asset_id = feat["id"]
                asset = self._asset_by_id.get(asset_id)
                if asset is None:
                    continue

                # name
                asset.name = feat["name"]

                # Geometry (Point only — most common)
                if (
                    feat.geometry() is not None
                    and _geom_type_name(asset.geometry) == "Point"
                    and not feat.geometry().isEmpty()
                ):
                    pt = feat.geometry().asPoint()
                    asset.geometry.lon = pt.x()
                    asset.geometry.lat = pt.y()

                # All discovered ESDL EAttribute fields
                for field_name in extra:
                    raw = feat[field_name]
                    if raw is not None:
                        set_esdl_attr(asset, field_name, raw)

    # ------------------------------------------------------------------
    # Serialise
    # ------------------------------------------------------------------

    def to_xml(self) -> str:
        """Serialise the (possibly modified) EnergySystem to XML string."""
        return self._esh.to_string()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def refresh_layers(self):
        """
        Rebuild all layer features from the current state of the energy system.

        Keeps QGIS map features in sync with the in-memory pyESDL model after
        add / delete / attribute-edit operations. Existing layer objects
        (with their styling) are preserved; only the feature data is replaced.
        An asset-class layer that no longer has any matching assets (e.g.
        after deleting the last asset of that type) is removed entirely
        rather than left empty.
        """
        assets_by_class = {}
        area_list = []
        try:
            root_area = self._es.instance[0].area
        except (IndexError, AttributeError):
            return
        _collect_assets(root_area, assets_by_class, area_list)

        # Rebuild the id-lookup from scratch
        self._asset_by_id = {
            entry["asset"].id: entry["asset"]
            for entries in assets_by_class.values()
            for entry in entries
            if getattr(entry["asset"], "id", None)
        }

        # Refresh each asset layer; collect any class layers that no longer
        # have matching assets so they can be removed below.
        emptied_classes = []
        for class_name, layer in self._layers.items():
            if class_name.startswith("_"):
                continue

            entries = assets_by_class.get(class_name, [])
            if not entries:
                emptied_classes.append(class_name)
                continue

            pr = layer.dataProvider()
            pr.deleteFeatures([f.id() for f in layer.getFeatures()])

            extra_defs = _get_esdl_attr_fields(entries[0]["asset"])
            features = []
            for e in entries:
                asset = e["asset"]
                geom = _to_qgs_geom(asset.geometry)
                if geom is None:
                    continue
                extra_vals = []
                for name, qtype, *_ in extra_defs:
                    val = getattr(asset, name, None)
                    if isinstance(val, bool):
                        extra_vals.append(str(val).lower())
                    elif isinstance(val, (int, float)):
                        extra_vals.append(val if val is not None else 0)
                    else:
                        extra_vals.append(str(val) if val is not None else "")
                f = QgsFeature(layer.fields())
                f.setGeometry(geom)
                f.setAttributes(
                    [
                        asset.id,
                        asset.name or "",
                        class_name,
                        e["area_name"],
                        *extra_vals,
                    ]
                )
                features.append(f)
            if features:
                pr.addFeatures(features)

            layer.updateExtents()
            layer.triggerRepaint()

        if emptied_classes:
            ids = [self._layers[class_name].id() for class_name in emptied_classes]
            QgsProject.instance().removeMapLayers(ids)
            for class_name in emptied_classes:
                del self._layers[class_name]
                self._extra_fields.pop(class_name, None)

        # Refresh connections layer
        if "_connections" in self._layers:
            conn_layer = self._layers["_connections"]
            pr = conn_layer.dataProvider()
            pr.deleteFeatures([f.id() for f in conn_layer.getFeatures()])
            conn_list = _collect_connections(assets_by_class)
            if conn_list:
                feats = []
                for c in conn_list:
                    f = QgsFeature(conn_layer.fields())
                    f.setGeometry(
                        QgsGeometry.fromPolylineXY(
                            [
                                QgsPointXY(c["from_lon"], c["from_lat"]),
                                QgsPointXY(c["to_lon"], c["to_lat"]),
                            ]
                        )
                    )
                    f.setAttributes(
                        [
                            c["in_port_id"],
                            c["in_asset_id"],
                            c["out_port_id"],
                            c["out_asset_id"],
                        ]
                    )
                    feats.append(f)
                pr.addFeatures(feats)
            conn_layer.updateExtents()
            conn_layer.triggerRepaint()

    def remove_layers(self):
        """Remove all managed layers and the layer group from QgsProject."""
        ids = [layer.id() for layer in self._layers.values()]
        QgsProject.instance().removeMapLayers(ids)
        self._layers.clear()
        if self._group is not None:
            QgsProject.instance().layerTreeRoot().removeChildNode(self._group)
            self._group = None
