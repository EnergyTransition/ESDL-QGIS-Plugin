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


def connect_ports(out_port, in_port):
    """
    Wire an OutPort to an InPort via the ESDL connectedTo reference.
    This is the canonical ESDL topology link: Asset→OutPort→InPort←Asset.
    Port order is always normalised by the caller (ConnectPortsDialog).
    """
    out_port.connectedTo.append(in_port)


def disconnect_ports(out_port, in_port):
    """Remove the connectedTo link between *out_port* and *in_port*."""
    try:
        out_port.connectedTo.remove(in_port)
    except Exception:
        pass


def get_asset_connections(asset):
    """
    Return a list of *(out_port, in_port, out_asset, in_asset)* tuples for
    every connection involving *asset* — both outgoing (asset owns the
    OutPort) and incoming (asset owns the InPort).
    """
    out_ports, in_ports = get_connectable_ports(asset)
    connections = []

    for out_port in out_ports:
        try:
            targets = list(out_port.connectedTo)
        except Exception:
            continue
        for in_port in targets:
            try:
                in_asset = in_port.eContainer()
            except Exception:
                in_asset = None
            connections.append((out_port, in_port, asset, in_asset))

    for in_port in in_ports:
        try:
            sources = list(in_port.connectedTo)
        except Exception:
            continue
        for out_port in sources:
            try:
                out_asset = out_port.eContainer()
            except Exception:
                out_asset = None
            connections.append((out_port, in_port, out_asset, asset))

    return connections


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


# ---------------------------------------------------------------------------
# CostInformation helpers
# ---------------------------------------------------------------------------


def set_cost_value(asset, cost_ref_name: str, value: float) -> None:
    """
    Write *value* to asset.costInformation.<cost_ref_name>.value, lazily
    creating a CostInformation and a SingleValue when they are absent.

    If the existing profile is a non-SingleValue GenericProfile subtype
    (e.g. DateTimeProfile) the function does nothing — only SingleValue
    supports a plain scalar .value attribute.
    """
    from esdl import esdl

    ci = getattr(asset, "costInformation", None)
    if ci is None:
        ci = esdl.CostInformation(id=str(uuid.uuid4()))
        asset.costInformation = ci

    profile = getattr(ci, cost_ref_name, None)
    if profile is None:
        profile = esdl.SingleValue(id=str(uuid.uuid4()))
        setattr(ci, cost_ref_name, profile)
    elif not isinstance(profile, esdl.SingleValue):
        # Non-scalar profile type — do not overwrite with a raw float
        return

    profile.value = float(value)
