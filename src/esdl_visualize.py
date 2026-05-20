"""
Converts a pyESDL EnergySystem into QGIS in-memory vector layers and
provides sync-back so edits can be serialised to XML for PUT to ESDL Drive.

Geometry is detected by duck-typing on the pyESDL object (avoids import
conflicts with the local src/esdl.py placeholder file).
"""

from qgis.core import (
    QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
    QgsField, QgsProject,
)
from qgis.PyQt.QtCore import QVariant

# Fields that are part of every asset layer but are NOT ESDL EAttributes
_STANDARD_FIELDS = frozenset({'id', 'name', 'esdl_class', 'area_name'})


# ---------------------------------------------------------------------------
# ESDL EAttribute introspection
# ---------------------------------------------------------------------------

def _get_esdl_attr_fields(asset):
    """
    Use pyecore meta-model introspection to discover all primitive EAttribute
    fields of the given asset class, excluding the standard layer fields.

    Returns list of (field_name, QVariant_type, initial_display_value).
    """
    try:
        from pyecore.ecore import EAttribute as _EAttribute
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
        if getattr(feature, 'derived', False):
            continue  # skip computed/derived attributes
        seen.add(name)

        val = getattr(asset, name, None)
        # Map Python type to QVariant type; serialise everything else as String
        if isinstance(val, bool):
            qtype, display = QVariant.String, str(val).lower()
        elif isinstance(val, int):
            qtype, display = QVariant.Int, val if val is not None else 0
        elif isinstance(val, float):
            qtype, display = QVariant.Double, val if val is not None else 0.0
        else:
            qtype, display = QVariant.String, str(val) if val is not None else ''
        result.append((name, qtype, display))
    return result


def _set_esdl_attr(asset, name: str, raw_value):
    """
    Write a QGIS feature value back to a pyESDL asset attribute.
    Handles type coercion and enum lookup; silently skips on error.
    """
    current = getattr(asset, name, None)
    try:
        if isinstance(current, bool):
            setattr(asset, name, str(raw_value).lower() == 'true')
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
    if name == 'Point':
        return 'Point'
    if name == 'Line':
        return 'Line'
    if name in ('Polygon', 'MultiPolygon'):
        return 'Polygon'
    if name == 'WKT':
        return 'WKT'
    return ''


def _to_qgs_geom(geometry):
    """Convert a pyESDL geometry to a QgsGeometry, or None if unsupported."""
    if geometry is None:
        return None
    kind = _geom_type_name(geometry)
    if kind == 'Point':
        return QgsGeometry.fromPointXY(QgsPointXY(geometry.lon, geometry.lat))
    if kind == 'Line':
        pts = [QgsPointXY(p.lon, p.lat) for p in geometry.point]
        return QgsGeometry.fromPolylineXY(pts) if len(pts) >= 2 else None
    if kind == 'Polygon':
        ring = [QgsPointXY(p.lon, p.lat) for p in geometry.exterior.point]
        return QgsGeometry.fromPolygonXY([ring]) if len(ring) >= 3 else None
    if kind == 'WKT':
        return QgsGeometry.fromWkt(geometry.value)
    return None


def _qgs_geom_type_string(geometry) -> str:
    """Map pyESDL geometry to QGIS layer type string."""
    kind = _geom_type_name(geometry)
    return {'Point': 'Point', 'Line': 'LineString', 'Polygon': 'Polygon',
            'WKT': 'Point'}.get(kind, '')


# ---------------------------------------------------------------------------
# Asset tree walker
# ---------------------------------------------------------------------------

def _collect_assets(area, out_by_class: dict, out_areas: list, area_name=''):
    """Recursively collect assets and area geometries."""
    if area.geometry is not None and _geom_type_name(area.geometry) == 'Polygon':
        out_areas.append({'area': area, 'parent': area_name})

    for asset in area.asset:
        class_name = type(asset).__name__
        out_by_class.setdefault(class_name, []).append(
            {'asset': asset, 'area_name': area.name or ''}
        )

    for sub in area.area:
        _collect_assets(sub, out_by_class, out_areas, area.name or '')


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
        xml = viz.to_xml()   # serialise back for PUT
    """

    def __init__(self, esh, energy_system):
        self._esh = esh
        self._es = energy_system
        self._layers = {}         # class_name -> QgsVectorLayer
        self._asset_by_id = {}    # asset.id -> pyESDL asset object
        self._extra_fields = {}   # class_name -> [field_name, ...]

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
                self._layers['_areas'] = area_layer
                QgsProject.instance().addMapLayer(area_layer, False)
                group.addLayer(area_layer)

        return list(self._layers.values())

    def _build_asset_layer(self, class_name: str, entries: list):
        # Find first usable geometry to determine layer type
        geom_type_str = ''
        for e in entries:
            geom_type_str = _qgs_geom_type_string(e['asset'].geometry)
            if geom_type_str:
                break
        if not geom_type_str:
            return None

        layer = QgsVectorLayer(
            f"{geom_type_str}?crs=EPSG:4326", class_name, "memory"
        )
        pr = layer.dataProvider()

        # Standard fields (always present)
        standard = [
            QgsField("id",         QVariant.String),
            QgsField("name",       QVariant.String),
            QgsField("esdl_class", QVariant.String),
            QgsField("area_name",  QVariant.String),
        ]
        pr.addAttributes(standard)

        # Extra ESDL EAttribute fields — introspected from the first asset
        extra_defs = _get_esdl_attr_fields(entries[0]['asset'])
        self._extra_fields[class_name] = [name for name, _, _ in extra_defs]
        pr.addAttributes([QgsField(name, qtype) for name, qtype, _ in extra_defs])
        layer.updateFields()

        # Make id and esdl_class read-only in the attribute form
        form_cfg = layer.editFormConfig()
        for read_only_field in ('id', 'esdl_class'):
            idx = layer.fields().indexOf(read_only_field)
            if idx >= 0:
                form_cfg.setReadOnly(idx, True)
        layer.setEditFormConfig(form_cfg)

        features = []
        for e in entries:
            asset = e['asset']
            geom = _to_qgs_geom(asset.geometry)
            if geom is None:
                continue
            self._asset_by_id[asset.id] = asset

            # Build attribute list: standard fields first, then extra
            extra_vals = []
            for name, qtype, _ in extra_defs:
                val = getattr(asset, name, None)
                if isinstance(val, bool):
                    extra_vals.append(str(val).lower())
                elif isinstance(val, (int, float)):
                    extra_vals.append(val if val is not None else 0)
                else:
                    extra_vals.append(str(val) if val is not None else '')

            f = QgsFeature()
            f.setGeometry(geom)
            f.setAttributes([
                asset.id,
                asset.name or '',
                class_name,
                e['area_name'],
                *extra_vals,
            ])
            features.append(f)

        pr.addFeatures(features)
        layer.updateExtents()
        return layer

    def _build_area_layer(self, area_list: list):
        layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "Areas", "memory")
        pr = layer.dataProvider()
        pr.addAttributes([
            QgsField("id",   QVariant.String),
            QgsField("name", QVariant.String),
        ])
        layer.updateFields()

        features = []
        for e in area_list:
            area = e['area']
            geom = _to_qgs_geom(area.geometry)
            if geom is None:
                continue
            f = QgsFeature()
            f.setGeometry(geom)
            f.setAttributes([area.id or '', area.name or ''])
            features.append(f)

        pr.addFeatures(features)
        layer.updateExtents()
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
            if class_name == '_areas':
                continue
            extra = self._extra_fields.get(class_name, [])
            for feat in layer.getFeatures():
                asset_id = feat['id']
                asset = self._asset_by_id.get(asset_id)
                if asset is None:
                    continue

                # name
                asset.name = feat['name']

                # Geometry (Point only — most common)
                if (feat.geometry() is not None
                        and _geom_type_name(asset.geometry) == 'Point'
                        and not feat.geometry().isEmpty()):
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

    def remove_layers(self):
        """Remove all managed layers from QgsProject."""
        ids = [layer.id() for layer in self._layers.values()]
        QgsProject.instance().removeMapLayers(ids)
        self._layers.clear()
