"""
Pure pyecore meta-model introspection utilities — no QGIS imports.

Generic helpers for reading and writing primitive EAttribute values on any
pyecore-backed ESDL object.
"""

from collections import namedtuple

from pyecore.ecore import EAttribute, EEnum

AttributeDescriptor = namedtuple(
    "AttributeDescriptor", ["name", "value", "is_enum", "enum_options"]
)


def describe_asset_attributes(asset, exclude=frozenset()):
    """
    Introspect *asset*'s pyecore meta-model and return one AttributeDescriptor
    per primitive EAttribute, skipping names in *exclude* and derived/computed
    attributes.

    *exclude* is caller-defined (e.g. the standard, non-ESDL fields a QGIS
    layer already carries) — this module has no opinion on layer schemas.
    """
    descriptors = []
    seen = set()
    for feature in type(asset).eClass.eAllStructuralFeatures():
        name = feature.name
        if name in exclude or name in seen:
            continue
        if not isinstance(feature, EAttribute):
            continue  # skip EReference — those are links to other objects
        if getattr(feature, "derived", False):
            continue  # skip computed/derived attributes
        seen.add(name)

        value = getattr(asset, name, None)
        etype = getattr(feature, "eType", None)
        is_enum = isinstance(etype, EEnum)
        enum_options = [lit.name for lit in etype.eLiterals] if is_enum else None
        descriptors.append(AttributeDescriptor(name, value, is_enum, enum_options))
    return descriptors


def coerce_attr_value(value, is_enum: bool = False):
    """
    Coerce a raw ESDL attribute *value* to a plain Python value suitable for
    storage in a single text/number attribute-table cell: enum literals
    become their string name, booleans become lowercase "true"/"false"
    strings, numbers pass through unchanged, and anything else is stringified.
    """
    if is_enum:
        return getattr(value, "name", str(value)) if value is not None else ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return value if value is not None else 0
    return str(value) if value is not None else ""


def set_esdl_attr(asset, name: str, raw_value) -> None:
    """
    Write a value back to a pyESDL asset attribute, coercing *raw_value*
    (typically a string from an edited feature) to the attribute's current
    Python type. Silently skips on error.
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
