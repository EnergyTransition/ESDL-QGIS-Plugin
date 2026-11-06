"""
ESDL Browser — generic ESDL object browser and editor.
"""

import uuid

from pyecore.ecore import ECollection

from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qgis.PyQt.QtCore import Qt

from ..esdl_ecore import (
    find_reachable_of_types,
    get_asset_attributes,
    get_asset_references,
    instantiate_type,
    object_repr,
)


# =============================================================================
# Shared scalar-attribute editor widgets (EAttribute forms)
# =============================================================================

# Decimal places shown/accepted for EDouble/EFloat attribute editors.
NUMERIC_ATTR_DECIMALS = 1


def make_scalar_attr_editor(attr: dict) -> QWidget:
    """
    Build a Qt input widget for a scalar (non-many) attribute descriptor, as
    returned by esdl_ecore.get_asset_attributes(), pre-filled with its
    current value. Used by EsdlBrowserDialog's per-object form.
    """
    if "options" in attr:
        w = QComboBox()
        w.blockSignals(True)
        w.addItems(attr["options"])
        cur = str(attr.get("value") or attr.get("default") or "").lower()
        for opt in attr["options"]:
            if opt.lower() == cur:
                w.setCurrentText(opt)
                break
        w.blockSignals(False)
    elif attr["type"] in ("EInt", "ELong", "EShort", "EByte"):
        w = QSpinBox()
        w.setRange(-2_147_483_648, 2_147_483_647)
        w.blockSignals(True)
        try:
            v = attr.get("value")
            w.setValue(int(v) if v not in (None, "") else 0)
        except (ValueError, TypeError):
            pass
        w.blockSignals(False)
    elif attr["type"] in ("EDouble", "EFloat"):
        w = QDoubleSpinBox()
        w.setRange(-1e15, 1e15)
        w.setDecimals(NUMERIC_ATTR_DECIMALS)
        w.blockSignals(True)
        try:
            v = attr.get("value")
            w.setValue(float(v) if v not in (None, "") else 0.0)
        except (ValueError, TypeError):
            pass
        w.blockSignals(False)
    else:
        w = QLineEdit()
        v = attr.get("value")
        w.setText(str(v) if v is not None else "")
    return w


def flush_scalar_attr_editor(obj, attr_name: str, widget: QWidget):
    """Write *widget*'s current value back onto obj.attr_name, if possible."""
    try:
        feature = obj.eClass.findEStructuralFeature(attr_name)
        if feature is None:
            return
        if isinstance(widget, QComboBox):
            raw = widget.currentText()
            try:
                obj.eSet(feature, feature.eType.from_string(raw))
            except Exception:
                pass
        elif isinstance(widget, QSpinBox):
            obj.eSet(feature, widget.value())
        elif isinstance(widget, QDoubleSpinBox):
            obj.eSet(feature, widget.value())
        elif isinstance(widget, QLineEdit):
            raw = widget.text()
            if raw:
                try:
                    obj.eSet(feature, feature.eType.from_string(raw))
                except Exception:
                    pass
    except Exception:
        pass


# =============================================================================
# EsdlBrowserDialog — generic ESDL object browser / editor
# =============================================================================


class EsdlBrowserDialog(QDialog):
    """
    Generic ESDL object browser and editor.

    Opens at the EnergySystem root. From there the user can:
      * View and edit EAttributes (scalars, enums, booleans).
      * Navigate into any EReference by clicking its value link.
      * Go back through the navigation history.
      * Add new containment children (type chosen from a picker dialog).
      * Delete containment children.
      * Assign cross-references via a candidate-picker dialog.

    Attribute edits are flushed to the live pyESDL model on each navigation
    step and when the user clicks OK. Clicking Cancel closes the dialog
    without reverting changes already flushed during navigation.
    """

    def __init__(self, energy_system, initial_obj=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ESDL Browser")
        self.setMinimumSize(820, 580)
        self._root = energy_system
        self._obj = energy_system
        self._history = []  # back-navigation stack
        self._current_attr_widgets = {}  # attr_name -> (widget, attr_meta)
        self._dirty_attrs = set()  # attr_name entries the user actually edited
        self.changes = []  # human-readable change descriptions, for the caller's log
        self._build_chrome()
        if initial_obj is not None and initial_obj is not energy_system:
            # Open at a specific object; push root to history so Back works
            self._navigate_to(initial_obj, add_to_history=True)
        else:
            self._navigate_to(energy_system, add_to_history=False)

    # ------------------------------------------------------------------
    # Fixed chrome
    # ------------------------------------------------------------------

    def _build_chrome(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(4)

        # Navigation bar
        nav = QHBoxLayout()
        self._back_btn = QPushButton("← Back")
        self._back_btn.setEnabled(False)
        self._back_btn.setFixedWidth(80)
        self._back_btn.clicked.connect(self._go_back)
        nav.addWidget(self._back_btn)
        self._title_lbl = QLabel()
        self._title_lbl.setStyleSheet("font-weight: bold; font-size: 13px;")
        nav.addWidget(self._title_lbl)
        nav.addStretch()
        outer.addLayout(nav)

        # Breadcrumb
        self._crumb_lbl = QLabel()
        self._crumb_lbl.setStyleSheet("color: #666; font-size: 11px;")
        self._crumb_lbl.setWordWrap(True)
        outer.addWidget(self._crumb_lbl)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        outer.addWidget(sep)

        # Scrollable content
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(self._scroll, stretch=1)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        outer.addWidget(btn_box)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _navigate_to(self, obj, add_to_history=True):
        self._flush_attrs()
        if add_to_history:
            self._history.append(self._obj)
        self._obj = obj
        self._current_attr_widgets = {}
        self._dirty_attrs = set()
        self._back_btn.setEnabled(bool(self._history))
        self._rebuild_content()

    def _go_back(self):
        if not self._history:
            return
        self._flush_attrs()
        self._obj = self._history.pop()
        self._current_attr_widgets = {}
        self._dirty_attrs = set()
        self._back_btn.setEnabled(bool(self._history))
        self._rebuild_content()

    def accept(self):
        self._flush_attrs()
        super().accept()

    # ------------------------------------------------------------------
    # Attribute flush (write widget values back to the model)
    # ------------------------------------------------------------------

    def _mark_dirty(self, attr_name: str):
        """Record that the user (not initial population) changed *attr_name*'s widget."""
        self._dirty_attrs.add(attr_name)

    def _flush_attrs(self):
        # Only touch widgets the user actually edited
        if not self._dirty_attrs:
            return
        obj = self._obj
        type_name = obj.eClass.name
        obj_label = object_repr(obj)
        for attr_name in self._dirty_attrs:
            widget_entry = self._current_attr_widgets.get(attr_name)
            if widget_entry is None:
                continue
            widget, _meta = widget_entry
            feature = obj.eClass.findEStructuralFeature(attr_name)
            old_value = obj.eGet(feature) if feature is not None else None
            flush_scalar_attr_editor(obj, attr_name, widget)
            new_value = obj.eGet(feature) if feature is not None else None
            if new_value != old_value:
                self.changes.append(
                    f"{type_name} '{obj_label}'.{attr_name}: {old_value!r} \u2192 {new_value!r}"
                )
        self._dirty_attrs = set()

    # ------------------------------------------------------------------
    # Content rebuild
    # ------------------------------------------------------------------

    def _rebuild_content(self):
        obj = self._obj
        type_name = obj.eClass.name
        obj_name = getattr(obj, "name", None) or type_name
        self._title_lbl.setText(f"{type_name}  —  {obj_name}")

        parts = []
        c = obj.eContainer()
        while c is not None:
            parts.insert(0, getattr(c, "name", None) or c.eClass.name)
            c = c.eContainer()
        self._crumb_lbl.setText("  /  ".join(parts) if parts else "(root)")

        content = QWidget()
        main = QVBoxLayout(content)
        main.setContentsMargins(4, 4, 4, 4)
        main.setSpacing(8)

        attrs = get_asset_attributes(obj)
        refs = get_asset_references(obj)

        self._build_attrs_section(obj, attrs, main)
        self._build_refs_section(obj, refs, main)
        main.addStretch()
        self._scroll.setWidget(content)

    # ------------------------------------------------------------------
    # Attributes section
    # ------------------------------------------------------------------

    def _build_attrs_section(self, obj, attrs, parent_layout):
        if not attrs:
            return
        parent_layout.addWidget(QLabel("<b>Attributes</b>"))
        box = QWidget()
        form = QFormLayout(box)
        form.setContentsMargins(16, 0, 8, 8)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(3)

        for attr in attrs:
            name = attr["name"]
            lbl = QLabel(name)
            if attr.get("doc"):
                lbl.setToolTip(attr["doc"])
            if attr.get("required"):
                lbl.setStyleSheet("font-style: italic;")

            if attr.get("many"):
                # List-typed attributes: read-only display in this prototype
                val = attr.get("value")
                display = (
                    ", ".join(str(v) for v in val) if isinstance(val, list) else str(val or "")
                )
                w = QLineEdit(display)
                w.setReadOnly(True)
                w.setStyleSheet("color: #888;")
                w.setToolTip("List-typed attribute — read-only in this prototype")
            else:
                w = make_scalar_attr_editor(attr)
                self._current_attr_widgets[name] = (w, attr)
                self._connect_dirty_tracking(w, name)

            form.addRow(lbl, w)

        parent_layout.addWidget(box)

    def _connect_dirty_tracking(self, widget: QWidget, attr_name: str):
        """Mark *attr_name* dirty only on user-driven edits, not initial population."""
        if isinstance(widget, QComboBox):
            widget.currentIndexChanged.connect(lambda _i: self._mark_dirty(attr_name))
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.valueChanged.connect(lambda _v: self._mark_dirty(attr_name))
        elif isinstance(widget, QLineEdit):
            # textEdited (not textChanged) fires only on user keystrokes
            widget.textEdited.connect(lambda _t: self._mark_dirty(attr_name))

    # ------------------------------------------------------------------
    # References section
    # ------------------------------------------------------------------

    def _build_refs_section(self, obj, refs, parent_layout):
        if not refs:
            return
        parent_layout.addWidget(QLabel("<b>References</b>"))
        for ref in refs:
            row = self._make_ref_row(obj, ref)
            parent_layout.addWidget(row)

    def _make_ref_row(self, parent_obj, ref):
        name = ref["name"]
        many = ref["many"]
        containment = ref["containment"]
        eopposite = ref["eopposite"]

        feature = parent_obj.eClass.findEStructuralFeature(name)
        if feature is None:
            return QWidget()

        raw = parent_obj.eGet(feature)
        items = list(raw) if isinstance(raw, ECollection) else ([raw] if raw is not None else [])

        row = QFrame()
        row.setFrameShape(QFrame.StyledPanel)
        row.setLineWidth(1)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(6, 3, 6, 3)
        rl.setSpacing(6)

        # Name column (fixed width)
        if eopposite:
            name_lbl = QLabel(f"{name}  <small style='color:#888'>(auto)</small>")
        else:
            name_lbl = QLabel(f"<b>{name}</b>")
        name_lbl.setFixedWidth(160)
        name_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        if ref.get("doc"):
            name_lbl.setToolTip(ref["doc"])
        rl.addWidget(name_lbl)

        # Value column (stretchy)
        val_col = QWidget()
        vl = QVBoxLayout(val_col)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(2)
        if items:
            for item_obj in items:
                label = f"{object_repr(item_obj)}  [{item_obj.eClass.name}]"
                btn = QPushButton(label)
                btn.setFlat(True)
                btn.setCursor(Qt.PointingHandCursor)
                btn.setStyleSheet(
                    "QPushButton { text-align: left; color: #0055cc; "
                    "border: none; padding: 1px 2px; }"
                    "QPushButton:hover { color: #003a8c; }"
                )
                btn.clicked.connect(lambda checked=False, o=item_obj: self._navigate_to(o))
                vl.addWidget(btn)
        else:
            vl.addWidget(QLabel("<i style='color: #aaa;'>(none)</i>"))
        rl.addWidget(val_col, stretch=2)

        # Action column (skip for eopposite — managed by the other side)
        if not eopposite:
            act_col = QWidget()
            act_col.setFixedWidth(34)
            al = QVBoxLayout(act_col)
            al.setContentsMargins(0, 0, 0, 0)
            al.setSpacing(2)

            if containment:
                add_btn = QPushButton("+")
                add_btn.setFixedSize(26, 22)
                add_btn.setToolTip(f"Add to {name}")
                add_btn.clicked.connect(
                    lambda checked=False, o=parent_obj, r=ref: self._add_containment(o, r)
                )
                for item_obj in items:
                    del_btn = QPushButton("×")
                    del_btn.setFixedSize(26, 22)
                    del_btn.setStyleSheet("color: #c00; font-weight: bold;")
                    del_btn.setToolTip("Delete this item")
                    del_btn.clicked.connect(
                        lambda checked=False, o=item_obj, po=parent_obj, r=ref: (
                            self._delete_containment(o, po, r)
                        )
                    )
                    al.addWidget(del_btn)
                al.addWidget(add_btn)
            else:
                pick_btn = QPushButton("…")
                pick_btn.setFixedSize(26, 22)
                pick_btn.setToolTip(f"Set {name}")
                pick_btn.clicked.connect(
                    lambda checked=False, o=parent_obj, r=ref: self._pick_xref(o, r)
                )
                al.addWidget(pick_btn)
                # Allow clearing a single-valued cross-reference
                if items and not many:
                    clr_btn = QPushButton("×")
                    clr_btn.setFixedSize(26, 22)
                    clr_btn.setStyleSheet("color: #c00; font-weight: bold;")
                    clr_btn.setToolTip("Clear reference")
                    clr_btn.clicked.connect(
                        lambda checked=False, o=parent_obj, r=ref: self._clear_xref(o, r)
                    )
                    al.addWidget(clr_btn)

            al.addStretch()
            rl.addWidget(act_col)

        return row

    # ------------------------------------------------------------------
    # Containment: add / delete
    # ------------------------------------------------------------------

    def _add_containment(self, parent_obj, ref):
        types = ref.get("types", [])
        if not types:
            QMessageBox.information(
                self, "No types", f"No instantiable types found for '{ref['name']}'"
            )
            return
        if len(types) == 1:
            type_name = types[0]
        else:
            dlg = _TypePickerDialog(types, ref["name"], parent=self)
            if dlg.exec_() != QDialog.Accepted:
                return
            type_name = dlg.get_type()
        try:
            new_obj = instantiate_type(type_name)
            if hasattr(new_obj, "id"):
                new_obj.id = str(uuid.uuid4())
            if hasattr(new_obj, "name"):
                new_obj.name = f"New{type_name}"
            feature = parent_obj.eClass.findEStructuralFeature(ref["name"])
            if ref["many"]:
                parent_obj.eGet(ref["name"]).append(new_obj)
            else:
                parent_obj.eSet(feature, new_obj)
            self.changes.append(f"Added {type_name} '{object_repr(new_obj)}' to {ref['name']}")
            self._rebuild_content()
        except Exception as exc:
            QMessageBox.warning(self, "Error", f"Could not create {type_name}: {exc}")

    def _delete_containment(self, item_obj, parent_obj, ref):
        label = object_repr(item_obj)
        item_type_name = item_obj.eClass.name
        if (
            QMessageBox.question(
                self,
                "Confirm delete",
                f"Delete '{label}'?",
                QMessageBox.Yes | QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        try:
            feature = parent_obj.eClass.findEStructuralFeature(ref["name"])
            if ref["many"]:
                parent_obj.eGet(ref["name"]).remove(item_obj)
            else:
                parent_obj.eSet(feature, None)
            self.changes.append(f"Deleted {item_type_name} '{label}' from {ref['name']}")
            # Remove the deleted object from navigation history
            self._history = [h for h in self._history if h is not item_obj]
            if self._obj is item_obj:
                self._obj = parent_obj
            self._rebuild_content()
        except Exception as exc:
            QMessageBox.warning(self, "Error", f"Could not delete: {exc}")

    # ------------------------------------------------------------------
    # Cross-reference picker
    # ------------------------------------------------------------------

    def _pick_xref(self, parent_obj, ref):
        types = ref.get("types", [])
        candidates = find_reachable_of_types(self._root, types)
        if not candidates:
            QMessageBox.information(
                self,
                "No candidates",
                f"No objects of type {', '.join(types)} found in the energy system.",
            )
            return
        dlg = _XRefPickerDialog(candidates, ref["name"], parent=self)
        if dlg.exec_() != QDialog.Accepted:
            return
        chosen = dlg.get_selected()
        if chosen is None:
            return
        try:
            feature = parent_obj.eClass.findEStructuralFeature(ref["name"])
            if ref["many"]:
                parent_obj.eGet(ref["name"]).append(chosen)
            else:
                parent_obj.eSet(feature, chosen)
            self.changes.append(
                f"Set {ref['name']} on '{object_repr(parent_obj)}' \u2192 '{object_repr(chosen)}'"
            )
            self._rebuild_content()
        except Exception as exc:
            QMessageBox.warning(self, "Error", f"Could not set reference: {exc}")

    def _clear_xref(self, parent_obj, ref):
        try:
            feature = parent_obj.eClass.findEStructuralFeature(ref["name"])
            parent_obj.eSet(feature, None)
            self.changes.append(f"Cleared {ref['name']} on '{object_repr(parent_obj)}'")
            self._rebuild_content()
        except Exception as exc:
            QMessageBox.warning(self, "Error", f"Could not clear reference: {exc}")


# =============================================================================
# _TypePickerDialog — choose a concrete type when adding a containment child
# =============================================================================


class _TypePickerDialog(QDialog):
    """Choose one concrete type name from a list before creating a new object."""

    def __init__(self, types: list, ref_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Add to {ref_name}")
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Choose the type to create:"))
        self._combo = QComboBox()
        self._combo.addItems(types)
        layout.addWidget(self._combo)
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def get_type(self) -> str:
        return self._combo.currentText()


# =============================================================================
# _XRefPickerDialog — pick an existing object for a cross-reference
# =============================================================================


class _XRefPickerDialog(QDialog):
    """Pick one EObject from a candidate list to fill a cross-reference slot."""

    def __init__(self, candidates: list, ref_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Select {ref_name}")
        self.setMinimumSize(400, 300)
        self._candidates = candidates
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Available objects for  <b>{ref_name}</b>:"))
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Name / Id", "Type"])
        self._tree.setRootIsDecorated(False)
        self._tree.setAlternatingRowColors(True)
        for obj in candidates:
            item = QTreeWidgetItem([object_repr(obj), obj.eClass.name])
            item.setData(0, Qt.UserRole, obj)
            self._tree.addTopLevelItem(item)
        self._tree.itemDoubleClicked.connect(lambda _i, _c: self.accept())
        layout.addWidget(self._tree)
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def get_selected(self):
        """Return the selected EObject, or None if nothing is selected."""
        items = self._tree.selectedItems()
        return items[0].data(0, Qt.UserRole) if items else None
