"""
Dialogs for the Add Asset flow: pick an asset class/name, then fill in its
optional ESDL attributes. Geometry is captured separately via the map tools
in ui.map_tools between these two steps.
"""

from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QScrollArea,
    QWidget,
    QDialogButtonBox,
)
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor, QStandardItemModel, QStandardItem

from ..esdl_model import geom_hint_for_class, list_asset_classes_grouped, make_temp_asset


class PickAssetDialog(QDialog):
    """
    Shows a class combobox grouped by ESDL capability (Consumer, Producer,
    Storage, Conversion, Transport, Other), with classes already present in
    the loaded energy system listed first under "In session". The geometry
    draw mode is derived automatically from esdl_model.geom_hint_for_class().
    """

    def __init__(self, loaded_classes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add ESDL Asset")
        self.setMinimumWidth(380)
        self._loaded_classes = list(loaded_classes)
        self._setup_ui()

    def _setup_ui(self):
        groups = list_asset_classes_grouped()
        loaded_set = set(self._loaded_classes)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._class_combo = QComboBox()
        model = QStandardItemModel(self._class_combo)

        _first_idx = [None]  # mutable container for nonlocal-style capture

        def _add_header(text):
            item = QStandardItem(text)
            item.setFlags(Qt.NoItemFlags)
            item.setForeground(QColor("#2D3C6B"))
            font = item.font()
            font.setBold(True)
            font.setPointSize(font.pointSize() + 2)
            item.setFont(font)
            model.appendRow(item)

        def _add_class(name):
            item = QStandardItem(name)
            model.appendRow(item)
            if _first_idx[0] is None:
                _first_idx[0] = model.rowCount() - 1

        if self._loaded_classes:
            _add_header("In session")
            for cls_name in sorted(self._loaded_classes):
                _add_class(cls_name)

        for group_name, class_names in groups.items():
            remaining = [c for c in class_names if c not in loaded_set]
            if not remaining:
                continue
            _add_header(group_name)
            for cls_name in remaining:
                _add_class(cls_name)

        self._class_combo.setModel(model)
        self._class_combo.currentTextChanged.connect(self._on_class_changed)
        form.addRow("Asset class:", self._class_combo)

        self._name_edit = QLineEdit()
        form.addRow("Name:", self._name_edit)

        self._geom_label = QLabel()
        form.addRow("Draw mode:", self._geom_label)

        layout.addLayout(form)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        if _first_idx[0] is not None:
            self._class_combo.setCurrentIndex(_first_idx[0])
            self._on_class_changed(self._class_combo.currentText())

    def _on_class_changed(self, class_name: str):
        # Skip group-header items (non-selectable)
        if not class_name or not class_name[0].isalpha():
            return
        hint = geom_hint_for_class(class_name)
        labels = {
            "Point": "Click once on the map",
            "Line": "Click vertices, right-click to finish",
            "Polygon": "Click vertices, right-click to finish",
        }
        self._geom_label.setText(labels.get(hint, hint))

    def get_data(self) -> dict:
        """Return dict with keys: class_name, name, geom_hint."""
        class_name = self._class_combo.currentText()
        return {
            "class_name": class_name,
            "name": self._name_edit.text().strip() or class_name,
            "geom_hint": geom_hint_for_class(class_name),
        }


class AddAttributesDialog(QDialog):
    """
    Shows a scrollable form of ESDL EAttribute fields for the given class.
    Attributes can also be edited later in the QGIS feature table.
    """

    def __init__(self, class_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Attributes — {class_name}")
        self.setMinimumWidth(400)
        self._class_name = class_name
        self._attr_widgets = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        note = QLabel(
            "<small style='color:grey'>Optional — you can also edit these "
            "later with Edit Asset dialog.</small>"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self._attr_container = QWidget()
        self._attr_form = QFormLayout(self._attr_container)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._attr_container)
        scroll.setMinimumHeight(160)
        layout.addWidget(scroll)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._populate_fields()

    def _populate_fields(self):
        from ..esdl_visualize import _get_esdl_attr_fields

        asset = make_temp_asset(self._class_name)
        if asset is None:
            return
        for field_name, qtype, default, enum_opts in _get_esdl_attr_fields(asset):
            if enum_opts is not None:
                w = QComboBox()
                w.addItems(enum_opts)
                if default in enum_opts:
                    w.setCurrentText(default)
            elif qtype == QVariant.Int:
                w = QSpinBox()
                w.setRange(-2_147_483_648, 2_147_483_647)
                if isinstance(default, int):
                    w.setValue(default)
            elif qtype == QVariant.Double:
                w = QDoubleSpinBox()
                w.setRange(-1e15, 1e15)
                w.setDecimals(6)
                if isinstance(default, float):
                    w.setValue(default)
            else:
                w = QLineEdit()
                w.setText(str(default) if default else "")
            self._attr_widgets[field_name] = w
            self._attr_form.addRow(field_name + ":", w)

    def get_attrs(self) -> dict:
        """Return dict of field_name -> value (skipping blank strings)."""
        attrs = {}
        for field_name, w in self._attr_widgets.items():
            if isinstance(w, (QSpinBox, QDoubleSpinBox)):
                attrs[field_name] = w.value()
            elif isinstance(w, QComboBox):
                attrs[field_name] = w.currentText()
            else:
                v = w.text().strip()
                if v:
                    attrs[field_name] = v
        return attrs
