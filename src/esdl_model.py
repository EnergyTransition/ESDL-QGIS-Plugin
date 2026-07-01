"""
Pure pyESDL model operations — no QGIS imports.

Handles asset creation, default port provisioning, area attachment,
and port-to-port connection wiring.
"""

import uuid

# ---------------------------------------------------------------------------
# Default port provisioning
# ---------------------------------------------------------------------------


# TODO: review needed
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
# Port connection wiring
# ---------------------------------------------------------------------------


# TODO: refinement needed --> suport also in_port and out_port order?
def connect_ports(out_port, in_port):
    """
    Wire an OutPort to an InPort via the ESDL connectedTo reference.
    This is the canonical ESDL topology link: Asset→OutPort→InPort←Asset.
    """
    out_port.connectedTo.append(in_port)


# TODO: review
def get_connectable_ports(asset):
    """
    Return *(out_ports, in_ports)* lists extracted from an asset's port
    collection.  Uses duck-typing so it works even without a direct esdl import.
    """
    from esdl import esdl

    out_ports, in_ports = [], []
    try:
        for port in asset.port:
            if isinstance(port, esdl.OutPort):
                out_ports.append(port)
            elif isinstance(port, esdl.InPort):
                in_ports.append(port)
    except Exception:
        pass
    return out_ports, in_ports


# ---------------------------------------------------------------------------
# Class discovery
# ---------------------------------------------------------------------------


# TODO: review
def list_asset_classes():
    """
    Return a sorted list of instantiable ESDL asset class names (subclasses
    of esdl.Asset) available in the installed pyESDL package.

    Falls back to a minimal hardcoded list if introspection fails.
    """
    try:
        from esdl import esdl

        base = esdl.Asset
        return sorted(
            name
            for name in dir(esdl)
            if (
                isinstance(getattr(esdl, name, None), type)
                and issubclass(getattr(esdl, name), base)
                and getattr(esdl, name) is not base
            )
        )
    except Exception:
        return [
            "ElectricityDemand",
            "GasDemand",
            "HeatingDemand",
            "Import",
            "PowerPlant",
            "WindTurbine",
        ]


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
