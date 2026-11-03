"""
Minimal QGIS dock panel shown after an ESDL file is loaded.
"""

from qgis.PyQt.QtWidgets import (
    QDockWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QMessageBox,
)


class ESDLDock(QDockWidget):
    def __init__(self, energy_system, source_label="", parent=None):
        super().__init__("ESDL", parent)
        self.setObjectName("ESDLDock")  # lets QGIS remember dock position
        self._energy_system = energy_system
        self._add_asset_callback = None
        self._delete_asset_callback = None
        self._export_callback = None
        self._close_callback = None
        self._build_ui(source_label)

    def _build_ui(self, source_label: str):
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        name = self._energy_system.name or "(unnamed)"
        info_text = f"<b>{name}</b>"
        if source_label:
            info_text += f"<br><small style='color:grey'>{source_label}</small>"
        info_lbl = QLabel(info_text)
        info_lbl.setWordWrap(True)
        layout.addWidget(info_lbl)

        layout.addSpacing(8)

        asset_row = QHBoxLayout()
        asset_row.setSpacing(4)
        self._add_asset_btn = QPushButton("Add Asset")
        self._add_asset_btn.setToolTip("Click the map to place a new ESDL asset")
        self._add_asset_btn.clicked.connect(self._on_add_asset)
        asset_row.addWidget(self._add_asset_btn)

        self._delete_asset_btn = QPushButton("Delete Asset")
        self._delete_asset_btn.setToolTip("Click an asset on the map to delete it")
        self._delete_asset_btn.clicked.connect(self._on_delete_asset)
        asset_row.addWidget(self._delete_asset_btn)
        layout.addLayout(asset_row)

        layout.addSpacing(8)

        self._export_btn = QPushButton("Export as .esdl file")
        self._export_btn.clicked.connect(self._on_export)
        layout.addWidget(self._export_btn)

        layout.addStretch()
        self.setWidget(inner)

    def set_add_asset_callback(self, fn):
        """Register a callable invoked when the user clicks 'Add Asset'."""
        self._add_asset_callback = fn

    def set_delete_asset_callback(self, fn):
        """Register a callable invoked when the user clicks 'Delete Asset'."""
        self._delete_asset_callback = fn

    def set_export_callback(self, fn):
        """Register a callable invoked when the user clicks 'Export as .esdl file'."""
        self._export_callback = fn

    def set_close_callback(self, fn):
        """Register a callable invoked after the user confirms closing this dock."""
        self._close_callback = fn

    def _on_add_asset(self):
        if callable(self._add_asset_callback):
            self._add_asset_callback()

    def _on_delete_asset(self):
        if callable(self._delete_asset_callback):
            self._delete_asset_callback()

    def _on_export(self):
        if callable(self._export_callback):
            self._export_callback()

    def closeEvent(self, event):
        """Closing this dock also closes the loaded ESDL — confirm first."""
        reply = QMessageBox.question(
            self,
            "Close ESDL",
            "Closing this panel will close the currently loaded ESDL.\n"
            "Export as .esdl file before closing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if reply == QMessageBox.Cancel:
            event.ignore()
            return
        if reply == QMessageBox.Save:
            self._on_export()
        event.accept()
        if callable(self._close_callback):
            self._close_callback()
