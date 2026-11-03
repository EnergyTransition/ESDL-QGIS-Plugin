"""
Pure pyESDL model operations — no QGIS imports.

Houses ESDL domain logic that operates purely on pyESDL/pyecore objects:
port/connection topology, plus asset-creation, port/area-attachment and
class-discovery helpers used by the Add Asset flow.
"""

import uuid


def iter_port_connections(assets_by_class: dict):
    """
    Yield (in_port, in_asset, out_port, out_asset) for every InPort -> OutPort
    `connectedTo` link found across all assets in *assets_by_class* (a
    class_name -> [{"asset": ..., "area_name": ...}, ...] mapping, as produced
    by esdl_visualize._collect_assets).

    Each connection is yielded exactly once, even though it is discoverable
    from either port's perspective.
    """
    seen = set()
    for entries in assets_by_class.values():
        for entry in entries:
            asset = entry["asset"]
            ports = getattr(asset, "port", None)
            if not ports:
                continue
            for port in ports:
                if type(port).__name__ != "InPort":
                    continue
                connected = getattr(port, "connectedTo", None)
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
                    yield port, asset, out_port, out_asset


# ---------------------------------------------------------------------------
# Asset creation
# ---------------------------------------------------------------------------


def make_temp_asset(class_name: str):
    """
    Instantiate a no-arg pyESDL asset of *class_name* for attribute
    introspection only (e.g. to list its EAttributes before any geometry or
    name is known). Returns None if the class cannot be instantiated.
    """
    try:
        from esdl import esdl

        cls = getattr(esdl, class_name)
        return cls()
    except Exception:
        return None


def create_asset_with_geom(class_name: str, name: str, esdl_geom, attrs: dict = None):
    """
    Instantiate a new pyESDL asset of *class_name*, assign a UUID, and assign
    the given pre-built *esdl_geom* object (Point, Line, or Polygon).

    Returns the new pyESDL asset object.
    """
    from esdl import esdl

    cls = getattr(esdl, class_name)
    asset = cls(id=str(uuid.uuid4()), name=name)
    asset.geometry = esdl_geom
    if attrs:
        for k, v in attrs.items():
            try:
                setattr(asset, k, v)
            except Exception:
                pass
    return asset


def add_default_ports(asset):
    """
    Inspect the asset's meta-model for a 'port' EReference and, if found,
    append one InPort and one OutPort with fresh UUIDs.

    Returns the list of added ports (may be empty if the asset has no ports).
    """
    from esdl import esdl

    try:
        from pyecore.ecore import EReference

        has_port = any(
            f.name == "port" and isinstance(f, EReference)
            for f in type(asset).eClass.eAllStructuralFeatures()
        )
    except Exception:
        has_port = hasattr(asset, "port")

    if not has_port:
        return []

    in_port = esdl.InPort(id=str(uuid.uuid4()), name="In")
    out_port = esdl.OutPort(id=str(uuid.uuid4()), name="Out")
    try:
        asset.port.append(in_port)
        asset.port.append(out_port)
        return [in_port, out_port]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Area attachment
# ---------------------------------------------------------------------------


def attach_to_area(asset, area):
    """Append *asset* to the given ESDL area's asset collection."""
    area.asset.append(asset)


# ---------------------------------------------------------------------------
# Class discovery
# ---------------------------------------------------------------------------


def list_asset_classes_grouped():
    """
    Return an OrderedDict mapping capability group name to a sorted list of
    instantiable ESDL asset class names.

    Groups follow the ESDL capability hierarchy:
      Consumer, Producer, Storage, Conversion, Transport, Other.
    """
    from collections import OrderedDict

    try:
        from esdl import esdl

        groups = OrderedDict([
            ("Consumer", []),
            ("Producer", []),
            ("Storage", []),
            ("Conversion", []),
            ("Transport", []),
            ("Other", []),
        ])
        cap_bases = [
            ("Consumer", getattr(esdl, "Consumer", None)),
            ("Producer", getattr(esdl, "Producer", None)),
            ("Storage", getattr(esdl, "Storage", None)),
            ("Conversion", getattr(esdl, "Conversion", None)),
            ("Transport", getattr(esdl, "Transport", None)),
        ]
        base = esdl.Asset
        for name in sorted(dir(esdl)):
            cls = getattr(esdl, name, None)
            if not (
                isinstance(cls, type)
                and issubclass(cls, base)
                and cls is not base
            ):
                continue
            placed = False
            for group_name, group_base in cap_bases:
                if group_base is not None and issubclass(cls, group_base):
                    groups[group_name].append(name)
                    placed = True
                    break
            if not placed:
                groups["Other"].append(name)
        return OrderedDict((k, v) for k, v in groups.items() if v)
    except Exception:
        return {"All": []}


# ---------------------------------------------------------------------------
# Geometry type hints
# ---------------------------------------------------------------------------

# Maps asset class names to their expected geometry type.
# Default (for any class not listed here) is 'Point'.
# Extend this dict to support new asset types without touching UI code.
ASSET_GEOM_HINTS: dict = {
    # Line geometry
    "Pipe": "Line",
    "ElectricityCable": "Line",
    "HeatNetworkPipe": "Line",
    # Polygon geometry
    "PVPark": "Polygon",
    "WindPark": "Polygon",
    "FossilFuelPlant": "Polygon",
}


def geom_hint_for_class(class_name: str) -> str:
    """Return the expected geometry type ('Point', 'Line', 'Polygon') for *class_name*."""
    return ASSET_GEOM_HINTS.get(class_name, "Point")
