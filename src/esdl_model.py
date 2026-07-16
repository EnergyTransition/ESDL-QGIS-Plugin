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
        return {"All": list_asset_classes()}


def list_present_asset_classes_grouped(energy_system):
    """
    Like list_asset_classes_grouped(), but filtered to class names that have
    at least one instance in *energy_system*.  Used by the bulk reference
    editor, where an empty class would just produce an empty table.
    """
    from collections import OrderedDict

    present = {inst.eClass.name for inst in energy_system.eAllContents()}
    groups = list_asset_classes_grouped()
    filtered = OrderedDict()
    for group_name, class_names in groups.items():
        remaining = [c for c in class_names if c in present]
        if remaining:
            filtered[group_name] = remaining
    return filtered


# ---------------------------------------------------------------------------
# Bulk reference editor configuration
# ---------------------------------------------------------------------------

# Each entry describes one top-level, single-valued containment reference
# that the Bulk Reference Editor (esdl_ui.BulkReferenceEditorDialog) can
# flatten into a table. To support bulk-editing another reference, add an
# entry here — no other code changes are needed.
#
#   label              - shown in the "Reference" picker combo.
#   ref_name           - the EReference name on the asset (e.g. "costInformation").
#   type_name          - concrete type to instantiate if the reference is unset.
#   default_child_type - concrete type to use for that reference's own
#                        single-valued containment sub-references (ESDL cost
#                        fields are typed as abstract `Cost`; SingleValue —
#                        a scalar amount + unit — is the common case).
BULK_EDIT_REFERENCES = [
    {
        "label": "Cost Information",
        "ref_name": "costInformation",
        "type_name": "CostInformation",
        "default_child_type": "SingleValue",
    },
]


def get_bulk_edit_reference(label: str):
    """Return the BULK_EDIT_REFERENCES entry matching *label*, or None."""
    for entry in BULK_EDIT_REFERENCES:
        if entry["label"] == label:
            return entry
    return None


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
