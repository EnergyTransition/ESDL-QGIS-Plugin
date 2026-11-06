"""
Table Editor — bulk-edit asset attributes and configured references across
all assets of the same ESDL class in one table.
"""

from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QStandardItem, QStandardItemModel

from ..esdl_ecore import (
    clear_path_leaf,
    get_asset_attributes,
    get_asset_references,
    instantiate_type,
    list_registered_quantity_and_units,
    object_repr,
    qau_label,
    resolve_path,
    resolve_qau_target,
    set_qau_reference,
)
from ..esdl_model import BULK_EDIT_REFERENCES, list_present_asset_classes_grouped
from .esdl_browser_dialog import (
    EsdlBrowserDialog,
    flush_scalar_attr_editor,
    make_scalar_attr_editor,
)


class TableEditorDialog(QDialog):
    """
    Lets the user pick an asset type, then browse a small, fixed tree:
      * an "attributes" root for the asset's own scalar attributes, and
      * one root per configured reference (currently just "Cost
        Information" — see esdl_model.BULK_EDIT_REFERENCES), each showing
        its own scalar sub-fields (investmentCosts, installationCosts, …)
        as children.
    Selecting a tree node shows one row per matching asset with a column
    per scalar attribute at that level — so e.g. all HeatingDemand names/
    capacities, or all HeatProducer investment costs, can be reviewed and
    edited in one table instead of one asset at a time.

    To support editing another reference, add an entry to
    BULK_EDIT_REFERENCES — no other code changes are needed.

    Rows where a reference is unset are greyed out (the "attributes" root
    is never unset — every asset has itself). Each row has a checkbox; the
    "+ Create <ref> reference" / "- Delete <ref> reference" buttons below
    the table act on whichever rows are checked, falling back to the row
    under the cursor if none are checked. Cost fields are typed as
    abstract `Cost` in ESDL, so a fixed default_child_type ('SingleValue')
    is used when creating one — configurable per BULK_EDIT_REFERENCES entry.
    """

    _CHECK_COL = 0

    def __init__(self, energy_system, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Table Editor")
        self.setMinimumSize(860, 520)
        self._energy_system = energy_system
        self._path = []
        self._attrs = []
        self._instances = []
        self._cell_targets = {}  # (instance_idx, attr_name) -> (leaf_obj, widget)
        self._has_checkbox_col = False
        self._instance_row_offset = 0
        self._asset_col = 0
        self._attr_start_col = 1
        self._qau_ref_name = None
        self._qau_col = None
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)

        intro_lbl = QLabel(
            "Bulk-edit attributes across every asset of a chosen type in one "
            "table. Pick an asset type below, then a level in the tree on the "
            "left: <b>&lt;type&gt; attributes</b> shows the asset's own "
            "fields, while the other entries drill into a <i>reference</i> "
            "it contains — a nested object such as its cost information — "
            "whose own fields are then shown as table columns. Only the "
            "<b>Cost Information</b> reference is supported at the moment."
        )
        intro_lbl.setWordWrap(True)
        intro_lbl.setStyleSheet("color: #555;")
        outer.addWidget(intro_lbl)
        outer.addSpacing(16)

        top = QHBoxLayout()
        top.addWidget(QLabel("Asset type:"))
        self._class_combo = QComboBox()
        self._populate_class_combo()
        self._class_combo.currentIndexChanged.connect(self._on_selector_changed)
        top.addWidget(self._class_combo, stretch=1)
        outer.addLayout(top)

        self._path_lbl = QLabel(
            "<i style='color:#888'>Select a level in the tree.</i>"
        )
        self._path_lbl.setWordWrap(True)
        outer.addWidget(self._path_lbl)

        outer.addSpacing(8)

        splitter = QSplitter(Qt.Horizontal)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Reference hierarchy"])
        self._tree.currentItemChanged.connect(self._on_tree_selection_changed)
        splitter.addWidget(self._tree)

        self._table = QTableWidget()
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.currentCellChanged.connect(self._on_table_row_changed)
        self._table.itemChanged.connect(self._on_table_item_changed)
        splitter.addWidget(self._table)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        outer.addWidget(splitter, stretch=1)

        outer.addSpacing(8)

        # Row actions: create/delete act on checked rows (or the row under
        # the cursor if none are checked), kept separate from the data
        # columns so their purpose stays clear.
        row_actions = QHBoxLayout()
        self._row_status_lbl = QLabel("")
        row_actions.addWidget(self._row_status_lbl, stretch=1)
        self._create_row_btn = QPushButton("+ Create reference")
        self._create_row_btn.setToolTip(
            "Create this reference for the checked (or selected) asset(s)"
        )
        self._create_row_btn.setEnabled(False)
        self._create_row_btn.clicked.connect(self._on_create_selected_row)
        row_actions.addWidget(self._create_row_btn)
        self._delete_row_btn = QPushButton("- Delete reference")
        self._delete_row_btn.setStyleSheet("color: #c00;")
        self._delete_row_btn.setToolTip(
            "Delete this reference from the checked (or selected) asset(s)"
        )
        self._delete_row_btn.setEnabled(False)
        self._delete_row_btn.clicked.connect(self._on_delete_selected_row)
        row_actions.addWidget(self._delete_row_btn)
        outer.addLayout(row_actions)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        outer.addWidget(btn_box)

        self._rebuild_tree()

    def _populate_class_combo(self):
        groups = list_present_asset_classes_grouped(self._energy_system)
        model = QStandardItemModel(self._class_combo)
        first_idx = None
        for group_name, class_names in groups.items():
            header = QStandardItem(group_name)
            header.setFlags(Qt.NoItemFlags)
            header.setForeground(QColor("#2D3C6B"))
            font = header.font()
            font.setBold(True)
            font.setPointSize(font.pointSize() + 2)
            header.setFont(font)
            model.appendRow(header)
            for cls_name in class_names:
                row = QStandardItem(cls_name)
                model.appendRow(row)
                if first_idx is None:
                    first_idx = model.rowCount() - 1
        self._class_combo.setModel(model)
        if first_idx is not None:
            self._class_combo.setCurrentIndex(first_idx)

    def _current_class_name(self):
        text = self._class_combo.currentText()
        return text if text and text[0].isalpha() else None

    def _on_selector_changed(self, _index: int):
        self._rebuild_tree()

    # ------------------------------------------------------------------
    # Tree: an "attributes" root for the asset's own scalar attributes,
    # plus one root per configured reference (BULK_EDIT_REFERENCES), each
    # showing its own scalar sub-fields as children. No further drilling,
    # no type ambiguity to resolve — deliberately simple.
    # ------------------------------------------------------------------

    def _rebuild_tree(self):
        self._tree.clear()
        self._table.setRowCount(0)
        self._table.setColumnCount(0)
        class_name = self._current_class_name()
        if not class_name:
            return

        attrs_root = QTreeWidgetItem([f"{class_name} attributes"])
        attrs_root.setData(0, Qt.UserRole, {"path": [], "type_name": class_name})
        self._tree.addTopLevelItem(attrs_root)

        for ref_entry in BULK_EDIT_REFERENCES:
            root_path = [
                {"ref_name": ref_entry["ref_name"], "type_name": ref_entry["type_name"]}
            ]
            root = QTreeWidgetItem([f"{ref_entry['label']}  ({ref_entry['type_name']})"])
            root.setData(
                0, Qt.UserRole, {"path": root_path, "type_name": ref_entry["type_name"]}
            )
            self._tree.addTopLevelItem(root)

            probe = instantiate_type(ref_entry["type_name"])
            for ref in get_asset_references(probe):
                if ref["many"] or not ref["containment"] or ref["eopposite"]:
                    continue
                if not ref["types"]:
                    continue
                child_type = ref_entry["default_child_type"]
                if child_type not in ref["types"]:
                    child_type = ref["types"][0]  # configured default not valid here
                child_path = root_path + [
                    {"ref_name": ref["name"], "type_name": child_type}
                ]
                child = QTreeWidgetItem([f"{ref['name']}  ({child_type})"])
                child.setData(
                    0, Qt.UserRole, {"path": child_path, "type_name": child_type}
                )
                root.addChild(child)

        self._tree.expandAll()
        self._tree.setCurrentItem(attrs_root)  # triggers a table rebuild via signal

    def _on_tree_selection_changed(self, current, _previous):
        if current is None:
            return
        meta = current.data(0, Qt.UserRole)
        if meta is None:
            return
        self._path = meta["path"]
        crumbs = "  →  ".join(seg["ref_name"] for seg in self._path) or "attributes"
        self._path_lbl.setText(f"<b>{crumbs}</b>  ({meta['type_name']})")
        self._update_action_button_labels()
        self._rebuild_table(meta["type_name"])

    def _update_action_button_labels(self):
        # The "<class> attributes" root (empty path) has nothing to create
        # or delete — every asset always has itself.
        has_ref = bool(self._path)
        self._create_row_btn.setVisible(has_ref)
        self._delete_row_btn.setVisible(has_ref)
        if has_ref:
            node_label = self._path[-1]["ref_name"]
            self._create_row_btn.setText(f"+ Create {node_label} reference")
            self._delete_row_btn.setText(f"- Delete {node_label} reference")

    # ------------------------------------------------------------------
    # Table: one row per matching asset, one column per scalar attribute.
    # Rows where the reference is unset are greyed out. A checkbox column
    # (plus a "select all/none" toggle row) is only shown for reference
    # levels, where the Create / Delete buttons below the table act on
    # whichever rows are checked — the plain "<class> attributes" level has
    # nothing to create/delete, so no checkboxes are shown there.
    # ------------------------------------------------------------------

    def _table_row(self, instance_idx: int) -> int:
        """Map an index into self._instances to its actual table row."""
        return instance_idx + self._instance_row_offset

    def _rebuild_table(self, level_type_name: str):
        class_name = self._current_class_name()
        self._cell_targets = {}
        self._has_checkbox_col = bool(self._path)
        self._instance_row_offset = 1 if self._has_checkbox_col else 0
        if self._has_checkbox_col:
            self._asset_col = 1
            self._attr_start_col = 2
        else:
            self._asset_col = 0
            self._attr_start_col = 1
        # QTableWidget does not remove a cell's widget just because setItem()
        # is later called on the same cell — force-clear by dropping all rows
        # first, otherwise a row that goes from set→unset (or vice versa)
        # keeps showing its old editors/placeholder instead of updating.
        self._table.setRowCount(0)

        if not class_name:
            self._table.setColumnCount(0)
            self._update_row_actions()
            return

        self._attrs = [
            a
            for a in get_asset_attributes(instantiate_type(level_type_name))
            if not a.get("many")
        ]
        # A handful of ESDL value types (SingleValue, profiles, ...) carry a
        # profileQuantityAndUnit reference. Rather than force users to drill
        # into it as another tree level, it gets one extra column here, with
        # a "ref_" prefix on the header to visually set it apart from plain
        # scalar attribute columns.
        self._qau_ref_name = next(
            (
                r["name"]
                for r in get_asset_references(instantiate_type(level_type_name))
                if r["name"] == "profileQuantityAndUnit"
            ),
            None,
        )
        self._instances = [
            inst
            for inst in self._energy_system.eAllContents()
            if inst.eClass.name == class_name
        ]

        if not self._attrs and not self._qau_ref_name:
            self._table.setColumnCount(0)
            self._update_row_actions()
            return

        attr_names = [a["name"] for a in self._attrs]
        headers = (["", "Asset"] if self._has_checkbox_col else ["Asset"]) + attr_names
        self._qau_col = None
        if self._qau_ref_name:
            self._qau_col = self._attr_start_col + len(attr_names)
            headers = headers + [f"ref_{self._qau_ref_name}"]
        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setRowCount(len(self._instances) + self._instance_row_offset)

        unset_bg = QColor(240, 240, 240)
        span_width = len(attr_names) + (1 if self._qau_ref_name else 0)

        self._table.blockSignals(True)

        if self._has_checkbox_col:
            select_all_item = QTableWidgetItem()
            select_all_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            select_all_item.setCheckState(Qt.Unchecked)
            self._table.setItem(0, self._CHECK_COL, select_all_item)
            label_item = QTableWidgetItem("Select all / none")
            label_item.setFlags(label_item.flags() & ~Qt.ItemIsEditable)
            font = label_item.font()
            font.setItalic(True)
            label_item.setFont(font)
            self._table.setItem(0, self._asset_col, label_item)
            self._table.setSpan(0, self._asset_col, 1, 1 + span_width)

        for idx, inst in enumerate(self._instances):
            row = self._table_row(idx)
            leaf = resolve_path(inst, self._path, create_missing=False)

            if self._has_checkbox_col:
                check_item = QTableWidgetItem()
                check_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                check_item.setCheckState(Qt.Unchecked)
                self._table.setItem(row, self._CHECK_COL, check_item)

            name_item = QTableWidgetItem(object_repr(inst))
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            if leaf is None:
                name_item.setBackground(unset_bg)
                name_item.setForeground(QColor("#999"))
            self._table.setItem(row, self._asset_col, name_item)

            leaf_attrs_by_name = (
                {a["name"]: a for a in get_asset_attributes(leaf)}
                if leaf is not None
                else {}
            )

            for i, attr_meta in enumerate(self._attrs):
                col = self._attr_start_col + i
                if leaf is not None:
                    row_attr = leaf_attrs_by_name.get(attr_meta["name"], attr_meta)
                    widget = make_scalar_attr_editor(row_attr)
                    self._table.setCellWidget(row, col, widget)
                    self._cell_targets[(idx, attr_meta["name"])] = (leaf, widget)
                else:
                    placeholder = QTableWidgetItem("(not set)")
                    placeholder.setFlags(placeholder.flags() & ~Qt.ItemIsEditable)
                    placeholder.setForeground(QColor("#aaa"))
                    placeholder.setBackground(unset_bg)
                    self._table.setItem(row, col, placeholder)

            if self._qau_ref_name:
                if leaf is not None:
                    feature = leaf.eClass.findEStructuralFeature(self._qau_ref_name)
                    current_qau = leaf.eGet(feature) if feature is not None else None
                    combo = self._make_qau_combo(current_qau, idx)
                    self._table.setCellWidget(row, self._qau_col, combo)
                else:
                    placeholder = QTableWidgetItem("(not set)")
                    placeholder.setFlags(placeholder.flags() & ~Qt.ItemIsEditable)
                    placeholder.setForeground(QColor("#aaa"))
                    placeholder.setBackground(unset_bg)
                    self._table.setItem(row, self._qau_col, placeholder)
        self._table.blockSignals(False)

        self._table.resizeColumnsToContents()
        if self._has_checkbox_col:
            self._table.setColumnWidth(self._CHECK_COL, 26)
        # Don't force-select the first row on every rebuild (e.g. when the
        # user just switches tree levels) — leave nothing selected and let
        # callers that need a specific row selected (Create/Delete) set it
        # explicitly afterward.
        self._table.clearSelection()
        self._table.setCurrentCell(-1, -1)
        self._update_row_actions()

    # ------------------------------------------------------------------
    # Quantity & Unit (QaU) picker — a dedicated dropdown column for the
    # profileQuantityAndUnit reference, instead of a generic tree level.
    # ------------------------------------------------------------------

    _QAU_DEFINE_NEW = object()  # sentinel combo-data value

    def _make_qau_combo(self, current_qau, instance_idx: int) -> QComboBox:
        combo = QComboBox()
        combo.blockSignals(True)
        qaus = list_registered_quantity_and_units(self._energy_system)
        current_target = resolve_qau_target(current_qau)
        matched_idx = None
        for i, q in enumerate(qaus):
            combo.addItem(qau_label(q), q)
            if current_target is not None and current_target is q:
                matched_idx = i
        if matched_idx is None:
            placeholder_label = (
                "(custom, not linked)" if current_target is not None else "(none)"
            )
            combo.insertItem(0, placeholder_label, None)
            combo.setCurrentIndex(0)
        else:
            combo.setCurrentIndex(matched_idx)
        combo.addItem("+ Define new…", self._QAU_DEFINE_NEW)
        combo.blockSignals(False)
        combo.currentIndexChanged.connect(
            lambda _index, c=combo, idx=instance_idx: self._on_qau_combo_changed(c, idx)
        )
        return combo

    def _on_qau_combo_changed(self, combo: QComboBox, instance_idx: int):
        data = combo.currentData()
        if data is self._QAU_DEFINE_NEW:
            self._open_qau_browser_shortcut()
            self._refresh_current_level()
            return
        if data is None:
            return  # placeholder ("(custom, not linked)" / "(none)") — no-op

        inst = self._instances[instance_idx]
        leaf = resolve_path(inst, self._path, create_missing=False)
        if leaf is not None and self._qau_ref_name:
            set_qau_reference(leaf, self._qau_ref_name, data)

    def _open_qau_browser_shortcut(self):
        """Shortcut to define a new shared QaU via the generic ESDL Browser."""
        esi = getattr(self._energy_system, "energySystemInformation", None)
        container = getattr(esi, "quantityAndUnits", None) if esi is not None else None
        if container is None:
            QMessageBox.information(
                self,
                "Not available",
                "This energy system has no energySystemInformation.quantityAndUnits "
                "container to add a new Quantity & Unit to.",
            )
            return
        dlg = EsdlBrowserDialog(self._energy_system, initial_obj=container, parent=self)
        dlg.exec_()

    def _refresh_current_level(self):
        current = self._tree.currentItem()
        if current is None:
            return
        self._flush_table()
        self._rebuild_table(current.data(0, Qt.UserRole)["type_name"])

    # ------------------------------------------------------------------
    # Row actions: create / delete the reference for checked (or the
    # currently selected) row(s)
    # ------------------------------------------------------------------

    def _checked_rows(self) -> list:
        """Return indices into self._instances whose row checkbox is checked."""
        if not self._has_checkbox_col:
            return []
        rows = []
        for idx in range(len(self._instances)):
            item = self._table.item(self._table_row(idx), self._CHECK_COL)
            if item is not None and item.checkState() == Qt.Checked:
                rows.append(idx)
        return rows

    def _rows_for_action(self) -> list:
        """Checked instance indices, or the one under the cursor if none are checked."""
        rows = self._checked_rows()
        if rows:
            return rows
        idx = self._table.currentRow() - self._instance_row_offset
        return [idx] if 0 <= idx < len(self._instances) else []

    def _on_table_row_changed(self, _current_row, _current_col, _prev_row, _prev_col):
        self._update_row_actions()

    def _on_table_item_changed(self, item: QTableWidgetItem):
        if not self._has_checkbox_col or item.column() != self._CHECK_COL:
            return
        if item.row() == 0:
            self._on_select_all_toggled(item.checkState())
            return
        self._update_row_actions()

    def _on_select_all_toggled(self, state):
        """Row 0's checkbox is a select-all/none toggle for the other rows."""
        self._table.blockSignals(True)
        for idx in range(len(self._instances)):
            cell = self._table.item(self._table_row(idx), self._CHECK_COL)
            if cell is not None:
                cell.setCheckState(state)
        self._table.blockSignals(False)
        self._update_row_actions()

    def _update_row_actions(self):
        rows = [r for r in self._rows_for_action() if 0 <= r < len(self._instances)]
        if not rows:
            self._row_status_lbl.setText("")
            self._create_row_btn.setEnabled(False)
            self._delete_row_btn.setEnabled(False)
            return

        if not self._path:
            # "<class> attributes" root — always present, nothing to
            # create/delete (the Create/Delete buttons are hidden for it).
            if len(rows) == 1:
                inst = self._instances[rows[0]]
                self._row_status_lbl.setText(f"<b>{object_repr(inst)}</b>")
            else:
                self._row_status_lbl.setText(f"<b>{len(rows)} selected</b>")
            return

        set_count = 0
        for row in rows:
            leaf = resolve_path(self._instances[row], self._path, create_missing=False)
            if leaf is not None:
                set_count += 1
        unset_count = len(rows) - set_count

        if len(rows) == 1:
            inst = self._instances[rows[0]]
            state = "set" if set_count else "not set"
            self._row_status_lbl.setText(f"<b>{object_repr(inst)}</b> \u2014 {state}")
        else:
            self._row_status_lbl.setText(
                f"<b>{len(rows)} selected</b> \u2014 {set_count} set, "
                f"{unset_count} not set"
            )
        self._create_row_btn.setEnabled(unset_count > 0)
        self._delete_row_btn.setEnabled(set_count > 0)

    def _on_create_selected_row(self):
        rows = [r for r in self._rows_for_action() if 0 <= r < len(self._instances)]
        if not rows:
            return

        self._flush_table()  # keep already-edited rows before rebuilding
        failed = 0
        for row in rows:
            leaf = resolve_path(self._instances[row], self._path, create_missing=True)
            if leaf is None:
                failed += 1
        if failed:
            QMessageBox.warning(
                self, "Error", f"Could not create the reference for {failed} asset(s)."
            )
        current = self._tree.currentItem()
        if current is not None:
            self._rebuild_table(current.data(0, Qt.UserRole)["type_name"])
        if rows:
            self._table.setCurrentCell(self._table_row(rows[0]), self._asset_col)

    def _on_delete_selected_row(self):
        rows = [r for r in self._rows_for_action() if 0 <= r < len(self._instances)]
        if not rows:
            return

        node_label = self._path[-1]["ref_name"] if self._path else "reference"
        if len(rows) == 1:
            label = object_repr(self._instances[rows[0]])
            msg = f"Remove '{node_label}' from '{label}'?"
        else:
            msg = f"Remove '{node_label}' from {len(rows)} selected assets?"
        if (
            QMessageBox.question(
                self,
                "Confirm delete",
                msg,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        self._flush_table()
        for row in rows:
            clear_path_leaf(self._instances[row], self._path)
        current = self._tree.currentItem()
        if current is not None:
            self._rebuild_table(current.data(0, Qt.UserRole)["type_name"])
        if rows:
            self._table.setCurrentCell(self._table_row(rows[0]), self._asset_col)

    def _flush_table(self):
        for (_row, attr_name), (leaf_obj, widget) in self._cell_targets.items():
            flush_scalar_attr_editor(leaf_obj, attr_name, widget)

    def accept(self):
        self._flush_table()
        super().accept()
