"""
Pure pyESDL model operations — no QGIS imports.

Houses ESDL domain logic that operates purely on pyESDL/pyecore objects:
port/connection topology today, with asset-creation, area-attachment and
class-discovery helpers expected to join it over time.
"""


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
