"""
Pure pyecore meta-model introspection utilities — no QGIS imports.

Generic helpers for reading and writing primitive EAttribute values on any
pyecore-backed ESDL object.
"""

from collections import namedtuple

from pyecore.ecore import EAttribute, ECollection, EClass, EEnum, EReference

import esdl

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


# ---------------------------------------------------------------------------
# Dict-based descriptors used by the ESDL Browser dialog (ui/esdl_browser_dialog.py)
# ---------------------------------------------------------------------------


def get_asset_attributes(asset):
    """Return a sorted list of attribute descriptor dicts for *asset*."""
    attributes = []
    for x in asset.eClass.eAllStructuralFeatures():
        if not isinstance(x, EAttribute):
            continue
        attr = {
            "name": x.name,
            "type": x.eType.name,
            "many": x.many,
            "required": x.required or x.lowerBound > 0,
            "doc": x.__doc__,
        }
        value = asset.eGet(x)
        if value is not None:
            if x.many and isinstance(value, ECollection):
                if isinstance(x.eType, EEnum):
                    attr["value"] = [v.name for v in value]
                else:
                    try:
                        attr["value"] = [x.eType.to_string(v) for v in value]
                    except Exception:
                        attr["value"] = [str(v) for v in value]
            else:
                try:
                    attr["value"] = x.eType.to_string(value)
                except Exception:
                    attr["value"] = str(value)
        else:
            attr["value"] = None

        if isinstance(x.eType, EEnum):
            attr["type"] = "EEnum"
            attr["options"] = [lit.name for lit in x.eType.eLiterals]
            dv = x.eType.default_value
            attr["default"] = dv.name if dv is not None else None
        elif x.eType.name == "EBoolean":
            attr["type"] = "EBoolean"
            attr["options"] = ["true", "false"]
            dv = x.eType.default_value
            try:
                attr["default"] = x.eType.to_string(dv) if dv is not None else "false"
            except Exception:
                attr["default"] = "false"
        else:
            dv = x.eType.default_value
            try:
                attr["default"] = x.eType.to_string(dv) if dv is not None else None
            except Exception:
                attr["default"] = None

        attributes.append(attr)

    return sorted(attributes, key=lambda a: a["name"])


def get_asset_references(asset):
    """Return a list of reference descriptor dicts for *asset*."""
    references = []
    for x in asset.eClass.eAllStructuralFeatures():
        if not isinstance(x, EReference):
            continue
        ref = {
            "name": x.name,
            "type": x.eType.eClass.name,
            "many": x.many,
            "required": x.required,
            "containment": x.containment,
            # eopposite: this reference is the non-containment side of a
            # containment pair — it is managed automatically by pyecore.
            "eopposite": bool(x.eOpposite and x.eOpposite.containment),
            "types": find_types(x),
            "doc": x.__doc__,
        }
        references.append(ref)
    return references


def find_types(reference):
    """Return a sorted list of concrete type names assignable to *reference*."""
    subtypes = []
    try:
        for eclassifier in esdl.eClass.eClassifiers:
            if isinstance(eclassifier, EClass):
                if (
                    reference.eType.eClass in eclassifier.eAllSuperTypes()
                    and not eclassifier.abstract
                ):
                    subtypes.append(eclassifier.name)
        if not reference.eType.eClass.abstract:
            name = reference.eType.eClass.name
            if name not in subtypes:
                subtypes.append(name)
    except Exception:
        pass
    subtypes.sort()
    return subtypes


def instantiate_type(class_name):
    """Return a new instance of the pyESDL class named *class_name*."""
    return esdl.getEClassifier(class_name)()


def find_reachable_of_types(root, types):
    """
    Return all EObjects in *root*'s containment tree whose eClass.name is in
    *types*. Used for building cross-reference candidate lists.
    """
    type_set = set(types)
    return [inst for inst in root.eAllContents() if inst.eClass.name in type_set]


def object_repr(obj):
    """Return a short human-readable label for an EObject."""
    if obj is None:
        return "(none)"
    name = getattr(obj, "name", None)
    if name is not None:
        return str(name)
    oid = getattr(obj, "id", None)
    if oid is not None:
        return f"{obj.eClass.name} ({str(oid)[:8]}\u2026)"
    return obj.eClass.name
