"""
Minimal QGIS dock panel shown after an ESDL file is loaded.
"""

from datetime import datetime

from qgis.PyQt.QtWidgets import (
    QDockWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QMessageBox,
)


class ESDLDock(QDockWidget):
    def __init__(self, energy_system, source_label="", parent=None):
        super().__init__("ESDL QGIS Plugin", parent)
        self.setObjectName("ESDLDock")  # lets QGIS remember dock position
        self._energy_system = energy_system
        self._add_asset_callback = None
        self._edit_asset_callback = None
        self._delete_asset_callback = None
        self._connect_callback = None
        self._disconnect_callback = None
        self._browse_esdl_callback = None
        self._table_editor_callback = None
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

        self._edit_asset_btn = QPushButton("Edit Asset")
        self._edit_asset_btn.setToolTip("Click an asset on the map to open it in the ESDL Browser")
        self._edit_asset_btn.clicked.connect(self._on_edit_asset)
        asset_row.addWidget(self._edit_asset_btn)

        self._delete_asset_btn = QPushButton("Delete Asset")
        self._delete_asset_btn.setToolTip("Click an asset on the map to delete it")
        self._delete_asset_btn.clicked.connect(self._on_delete_asset)
        asset_row.addWidget(self._delete_asset_btn)
        layout.addLayout(asset_row)

        layout.addSpacing(8)

        conn_row = QHBoxLayout()
        conn_row.setSpacing(4)
        self._connect_btn = QPushButton("Add Connection")
        self._connect_btn.setToolTip("Click two assets on the map to wire their ports")
        self._connect_btn.clicked.connect(self._on_connect)
        conn_row.addWidget(self._connect_btn)

        self._disconnect_btn = QPushButton("Delete Connection")
        self._disconnect_btn.setToolTip(
            "Click an asset on the map to remove one of its connections"
        )
        self._disconnect_btn.clicked.connect(self._on_disconnect)
        conn_row.addWidget(self._disconnect_btn)
        layout.addLayout(conn_row)

        layout.addSpacing(8)

        self._browse_esdl_btn = QPushButton("Browse ESDL")
        self._browse_esdl_btn.setToolTip("Inspect and edit ESDL object attributes and references")
        self._browse_esdl_btn.clicked.connect(self._on_browse_esdl)
        layout.addWidget(self._browse_esdl_btn)

        self._table_editor_btn = QPushButton("Table Editor")
        self._table_editor_btn.setToolTip(
            "Edit asset attributes and configured references (e.g. "
            "costInformation) across all assets of the same type in one table"
        )
        self._table_editor_btn.clicked.connect(self._on_table_editor)
        layout.addWidget(self._table_editor_btn)

        layout.addSpacing(8)

        self._export_btn = QPushButton("Export as .esdl file")
        self._export_btn.clicked.connect(self._on_export)
        layout.addWidget(self._export_btn)

        layout.addSpacing(8)

        layout.addWidget(QLabel("Action log"))
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        layout.addWidget(self._log)

        self.setWidget(inner)

    def set_add_asset_callback(self, fn):
        """Register a callable invoked when the user clicks 'Add Asset'."""
        self._add_asset_callback = fn

    def set_edit_asset_callback(self, fn):
        """Register a callable invoked when the user clicks 'Edit Asset'."""
        self._edit_asset_callback = fn

    def set_delete_asset_callback(self, fn):
        """Register a callable invoked when the user clicks 'Delete Asset'."""
        self._delete_asset_callback = fn

    def set_connect_callback(self, fn):
        """Register a callable invoked when the user clicks 'Add Connection'."""
        self._connect_callback = fn

    def set_disconnect_callback(self, fn):
        """Register a callable invoked when the user clicks 'Delete Connection'."""
        self._disconnect_callback = fn

    def set_export_callback(self, fn):
        """Register a callable invoked when the user clicks 'Export as .esdl file'."""
        self._export_callback = fn

    def set_browse_esdl_callback(self, fn):
        """Register a callable invoked when the user clicks 'Browse ESDL'."""
        self._browse_esdl_callback = fn

    def set_table_editor_callback(self, fn):
        """Register a callable invoked when the user clicks 'Table Editor'."""
        self._table_editor_callback = fn

    def set_close_callback(self, fn):
        """Register a callable invoked after the user confirms closing this dock."""
        self._close_callback = fn

    def _on_add_asset(self):
        if callable(self._add_asset_callback):
            self._add_asset_callback()

    def _on_edit_asset(self):
        if callable(self._edit_asset_callback):
            self._edit_asset_callback()

    def _on_delete_asset(self):
        if callable(self._delete_asset_callback):
            self._delete_asset_callback()

    def _on_connect(self):
        if callable(self._connect_callback):
            self._connect_callback()

    def _on_disconnect(self):
        if callable(self._disconnect_callback):
            self._disconnect_callback()

    def _on_browse_esdl(self):
        if callable(self._browse_esdl_callback):
            self._browse_esdl_callback()

    def _on_table_editor(self):
        if callable(self._table_editor_callback):
            self._table_editor_callback()

    def _on_export(self):
        if callable(self._export_callback):
            self._export_callback()

    def append_change(self, msg: str):
        """Append a timestamped entry to the action log."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log.append(f"[{ts}]  {msg}")

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
