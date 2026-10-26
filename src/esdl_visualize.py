"""
Converts a pyESDL EnergySystem into QGIS in-memory vector layers and
provides sync-back so edits can be serialised back to an .esdl file.

Geometry is detected by duck-typing on the pyESDL object (avoids import
conflicts with the local src/esdl.py placeholder file).
"""

from qgis.core import (
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
)
from qgis.PyQt.QtCore import QVariant
from pathlib import Path

# Fields that are part of every asset layer but are NOT ESDL EAttributes
_STANDARD_FIELDS = frozenset({"id", "name", "esdl_class", "area_name"})

# Directory that holds optional per-class PNG icons (e.g. HeatingDemand.png).
# Icons are local-only for now; fetching them from an online repository is not implemented yet.
_ICONS_DIR = Path(__file__).parent / "icons"


def _apply_point_icon(layer, class_name: str) -> None:
    """
    If src/icons/{class_name}.png exists, replace the layer's default marker
    with a raster icon of that file.  Falls back silently to the QGIS default
    marker when no matching icon is found.
    """
    icon_path = _ICONS_DIR / f"{class_name}.png"
    if not icon_path.exists():
        return
    try:
        raster = QgsRasterMarkerSymbolLayer(str(icon_path))
        raster.setSize(8.0)
        sym = QgsMarkerSymbol()
        sym.changeSymbolLayer(0, raster)
        layer.renderer().setSymbol(sym)
    except Exception:
        pass  # leave default symbol intact


def _apply_directional_symbol(layer) -> None:
    """
    Append a midpoint arrowhead marker to a LineString layer so the draw
    direction (A → B) is visible on the map.  The arrow inherits the colour
    of the base line symbol and rotates automatically along the line bearing.
    """
    try:
        from qgis.core import QgsSimpleMarkerSymbolLayerBase

        shape = QgsSimpleMarkerSymbolLayerBase.ArrowHead
    except Exception:
        shape = None

    arrow = QgsSimpleMarkerSymbolLayer()
    if shape is not None:
        arrow.setShape(shape)
    arrow.setSize(3.5)
    # Inherit the colour from the existing base line symbol
    base_color = layer.renderer().symbol().color()
    arrow.setColor(base_color)
    arrow.setStrokeStyle(0)  # no border on arrowhead fill

    marker_sym = QgsMarkerSymbol()
    marker_sym.changeSymbolLayer(0, arrow)

    marker_line = QgsMarkerLineSymbolLayer()
    marker_line.setPlacement(QgsMarkerLineSymbolLayer.CentralPoint)
    marker_line.setSubSymbol(marker_sym)

    layer.renderer().symbol().appendSymbolLayer(marker_line)


# ---------------------------------------------------------------------------
# ESDL EAttribute introspection
# ---------------------------------------------------------------------------


# TODO: review
def _get_esdl_attr_fields(asset):
    """
    Use pyecore meta-model introspection to discover all primitive EAttribute
    fields of the given asset class, excluding the standard layer fields.

    Returns list of (field_name, QVariant_type, default, enum_options_or_None).
    enum_options_or_None is a list of string literal names for EEnum fields,
    or None for plain scalar fields.
    """
    try:
        from pyecore.ecore import EAttribute as _EAttribute, EEnum as _EEnum
    except ImportError:
        return []

    result = []
    seen = set()
    for feature in type(asset).eClass.eAllStructuralFeatures():
        name = feature.name
        if name in _STANDARD_FIELDS or name in seen:
            continue
        if not isinstance(feature, _EAttribute):
            continue  # skip EReference — those are links to other objects
        if getattr(feature, "derived", False):
            continue  # skip computed/derived attributes
        seen.add(name)

        val = getattr(asset, name, None)

        # Enum field — collect literal names; stored as String in QGIS layer
        try:
            etype = getattr(feature, "eType", None)
            is_enum = isinstance(etype, _EEnum)
        except Exception:
            is_enum = False

        if is_enum:
            enum_opts = [lit.name for lit in etype.eLiterals]
            current = getattr(val, "name", str(val)) if val is not None else ""
            result.append((name, QVariant.String, current, enum_opts))
            continue

        # Map Python type to QVariant type; serialise everything else as String
        enum_opts = None
        if isinstance(val, bool):
            qtype, display = QVariant.String, str(val).lower()
        elif isinstance(val, int):
            qtype, display = QVariant.Int, val if val is not None else 0
        elif isinstance(val, float):
            qtype, display = QVariant.Double, val if val is not None else 0.0
        else:
            qtype, display = QVariant.String, str(val) if val is not None else ""
        result.append((name, qtype, display, enum_opts))
    return result


# TODO: _set_esdl_ref() --> check ME code as well
def _set_esdl_attr(asset, name: str, raw_value):
    """
    Write a QGIS feature value back to a pyESDL asset attribute.
    Handles type coercion and enum lookup; silently skips on error.
    """
    current = getattr(asset, name, None)
    try:
        if isinstance(current, bool):
            setattr(asset, name, str(raw_value).lower() == "true")
        elif isinstance(current, int):
            setattr(asset, name, int(raw_value))
        elif isinstance(current, float):
            setattr(asset, name, float(raw_value))
        elif current is not None and not isinstance(current, str):
            # Likely an Enum — look up by name string
            enum_cls = type(current)
            setattr(asset, name, enum_cls.from_string(str(raw_value)))
        else:
            setattr(asset, name, str(raw_value) if raw_value is not None else None)
    except Exception:
        pass  # non-critical; leave the original value unchanged


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


def _geom_to_qgspoint(geometry) -> "QgsPointXY | None":
    """
    Return a representative QgsPointXY for any pyESDL geometry type.
    Point  -> the point itself.
    Line   -> midpoint of the first segment.
    Polygon/WKT -> centroid of the converted QgsGeometry.
    Returns None if the geometry cannot be resolved.
    """
    if geometry is None:
        return None
    kind = _geom_type_name(geometry)
    if kind == "Point":
        return QgsPointXY(geometry.lon, geometry.lat)
    qgs = _to_qgs_geom(geometry)
    if qgs is None or qgs.isEmpty():
        return None
    c = qgs.centroid()
    if c is None or c.isEmpty():
        return None
    return c.asPoint()


def _geom_endpoint(geometry, use_last: bool = False):
    """
    Return (lon, lat) for a connection endpoint on a pyESDL geometry,
    following the Geodan esdl_qgis_plugin convention:
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
    Walk every InPort across all loaded assets, following the Geodan
    esdl_qgis_plugin approach (github.com/Geodan/esdl_qgis_plugin).

    Returns a list of dicts with keys:
        in_port_id, in_asset_id, out_port_id, out_asset_id,
        from_lon, from_lat, to_lon, to_lat

    Endpoint selection (Geodan convention):
    - InPort's asset  (from side): first point for Line, lon/lat for Point.
    - OutPort's asset (to side):   last  point for Line, lon/lat for Point.
    In properly-constructed ESDLs pipe endpoints coincide with connected
    asset positions, so connection lines are near-zero-length dashes that
    record topology without adding visual clutter.
    """
    seen = set()
    result = []
    for entries in assets_by_class.values():
        for e in entries:
            asset = e["asset"]
            try:
                ports = asset.port
            except Exception:
                continue
            for port in ports:
                if type(port).__name__ != "InPort":
                    continue
                try:
                    connected = port.connectedTo
                except Exception:
                    continue
                # connectedTo is an EOrderedSet (iterable) or single ref
                targets = (
                    list(connected)
                    if hasattr(connected, "__iter__")
                    else ([connected] if connected is not None else [])
                )
                for out_port in targets:
                    key = (
                        getattr(port, "id", id(port)),
                        getattr(out_port, "id", id(out_port)),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        out_asset = out_port.eContainer()
                    except Exception:
                        out_asset = None
                    if out_asset is None:
                        continue
                    from_lon, from_lat = _geom_endpoint(
                        getattr(asset, "geometry", None), use_last=False
                    )
                    to_lon, to_lat = _geom_endpoint(
                        getattr(out_asset, "geometry", None), use_last=True
                    )
                    if None in (from_lon, from_lat, to_lon, to_lat):
                        continue
                    result.append(
                        {
                            "in_port_id": getattr(port, "id", ""),
                            "in_asset_id": getattr(asset, "id", ""),
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
        standard = [
            QgsField("id", QVariant.String),
            QgsField("name", QVariant.String),
            QgsField("esdl_class", QVariant.String),
            QgsField("area_name", QVariant.String),
        ]
        pr.addAttributes(standard)

        # Extra ESDL EAttribute fields — introspected from the first asset
        extra_defs = _get_esdl_attr_fields(entries[0]["asset"])
        self._extra_fields[class_name] = [name for name, *_ in extra_defs]
        pr.addAttributes([QgsField(name, qtype) for name, qtype, *_ in extra_defs])
        layer.updateFields()

        # Custom icon for point layers; directional arrow for line layers
        if geom_type_str == "Point":
            _apply_point_icon(layer, class_name)
        elif geom_type_str == "LineString":
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
        units of *map_point* (a QgsPointXY).  Returns the feature's 'id'
        attribute string, or None if nothing is found.
        """
        rect = QgsRectangle(
            map_point.x() - tolerance,
            map_point.y() - tolerance,
            map_point.x() + tolerance,
            map_point.y() + tolerance,
        )
        for class_name, layer in self._layers.items():
            if class_name.startswith("_"):
                continue
            for feat in layer.getFeatures(QgsFeatureRequest().setFilterRect(rect)):
                return feat["id"]
        return None

    def add_asset_to_layer(self, asset, area_name: str = "") -> None:
        """
        Register a newly created pyESDL *asset* in *self._asset_by_id* and
        add its QgsFeature to the appropriate layer.  If no layer exists yet
        for this asset class, one is created and added to the layer group.
        """
        class_name = type(asset).__name__
        self._asset_by_id[asset.id] = asset

        if class_name in self._layers:
            layer = self._layers[class_name]
            geom = _to_qgs_geom(asset.geometry)
            if geom is None:
                return
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
        else:
            # First asset of this class — build a full layer from scratch
            layer = self._build_asset_layer(
                class_name, [{"asset": asset, "area_name": area_name}]
            )
            if layer is None:
                return
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

    def _build_connections_layer(self, conn_list: list):
        """
        Build a dashed-grey LineString memory layer from _collect_connections
        output, following the Geodan esdl_qgis_plugin style.
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
                        _set_esdl_attr(asset, field_name, raw)

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

        # Refresh each asset layer
        for class_name, layer in self._layers.items():
            if class_name.startswith("_"):
                continue
            pr = layer.dataProvider()
            pr.deleteFeatures([f.id() for f in layer.getFeatures()])

            entries = assets_by_class.get(class_name, [])
            if entries:
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
