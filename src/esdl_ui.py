import os
from datetime import datetime

from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.core import QgsWkbTypes, QgsGeometry
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDockWidget,
    QWidget,
    QWizard,
    QWizardPage,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QGroupBox,
    QFileDialog,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QComboBox,
    QFormLayout,
    QScrollArea,
    QDoubleSpinBox,
    QSpinBox,
    QDialogButtonBox,
    QFrame,
    QListWidget,
)
from qgis.PyQt.QtCore import Qt, pyqtSignal, QVariant
from qgis.PyQt.QtGui import QColor

from .esdl_drive.auth import PasswordAuthWorker
from .esdl_drive.esdl_drive import ESDLDrive
from .esdl_visualize import _geom_to_qgspoint

from esdl.esdl_handler import EnergySystemHandler

# TODO: hide these from the UI
_DEFAULT_DRIVE_URL = os.environ.get("ESDL_DRIVE_URL", "http://localhost:9080")
_DEFAULT_KC_REALM = os.environ.get(
    "ESDL_KC_REALM", "http://localhost:8080/auth/realms/esdl-mapeditor"
)
_DEFAULT_KC_CLIENT = os.environ.get("ESDL_KC_CLIENT", "cdo-mondaine")
_DEFAULT_KC_SECRET = os.environ.get("ESDL_KC_SECRET", "")


class ESDLSourceDialog(QDialog):
    """
    Entry-point dialog: lets the user choose how to load an ESDL —
    from a local file or from ESDL Drive.

    on_loaded(esh, energy_system) is called when a file is successfully loaded.
    """

    def __init__(self, parent=None, on_loaded=None, on_drive_requested=None):
        super().__init__(parent)
        self.setWindowTitle("Load ESDL")
        self.setMinimumWidth(320)
        self._on_loaded = on_loaded
        self._on_drive_requested = on_drive_requested
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)

        root.addWidget(QLabel("Choose how to load an ESDL energy system:"))

        local_btn = QPushButton("Open local .esdl file…")
        local_btn.setMinimumHeight(40)
        local_btn.clicked.connect(self._open_local)
        root.addWidget(local_btn)

        drive_btn = QPushButton("Connect to ESDL Drive…")
        drive_btn.setMinimumHeight(40)
        drive_btn.clicked.connect(self._open_drive)
        root.addWidget(drive_btn)

        cancel_row = QHBoxLayout()
        cancel_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        cancel_row.addWidget(cancel_btn)
        root.addLayout(cancel_row)

    def _open_local(self):
        loader = ESDLFileDialog(parent=self, on_loaded=self._on_loaded)
        if loader.open():
            self.accept()

    def _open_drive(self):
        if not callable(self._on_drive_requested):
            QMessageBox.warning(self, "Not available", "ESDL Drive is not configured.")
            return
        # Dismiss the chooser first, then let the caller open the Drive wizard
        # (caller must hold the reference to keep the non-modal wizard alive).
        self.accept()
        self._on_drive_requested()


class ESDLFileDialog:
    """
    Thin helper that opens a native file-picker, parses the selected .esdl
    file, and calls on_loaded(esh, energy_system).  Not a QDialog — no extra
    window is shown.  Call open() to trigger the flow.
    """

    def __init__(self, parent=None, on_loaded=None):
        self._parent = parent
        self._on_loaded = on_loaded

    def open(self) -> bool:
        """
        Show the file-picker and load the selected file.
        Returns True on success, False if cancelled or on error.
        """
        path, _ = QFileDialog.getOpenFileName(
            self._parent,
            "Open ESDL file",
            "",
            "ESDL files (*.esdl);;XML files (*.xml);;All files (*)",
        )
        if not path:
            return False

        try:
            esh = EnergySystemHandler()
            with open(path, "r", encoding="utf-8") as fh:
                xml = fh.read()
            energy_system = esh.load_from_string(xml)
            if callable(self._on_loaded):
                self._on_loaded(esh, energy_system, source_label=os.path.basename(path))
            return True
        except Exception as exc:
            QMessageBox.critical(self._parent, "Failed to load ESDL", str(exc))
            return False


class ESDLEditDock(QDockWidget):
    """
    Dockable QGIS panel that shows a change log and export button after an
    ESDL is loaded — used by both the local-file and ESDL Drive flows.

    Add to the QGIS sidebar with:
        iface.addDockWidget(Qt.RightDockWidgetArea, dock)

    The panel is a standard QDockWidget: the float/pin toggle is the small
    button in its title bar (drag to dock, double-click to float).
    """

    def __init__(self, esh, energy_system, source_label="", parent=None):
        super().__init__("ESDL Editor", parent)
        self.setObjectName("ESDLEditDock")  # lets QGIS remember dock position
        self._esh = esh
        self._energy_system = energy_system
        self._sync_callback = None
        self._add_asset_callback = None
        self._connect_callback = None
        self._disconnect_callback = None
        self._refresh_layers_callback = None
        self._edit_asset_callback = None
        self._delete_asset_callback = None
        self._close_callback = None
        self._build_ui(source_label)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self, source_label: str):
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Energy system info header
        name = self._energy_system.name or "(unnamed)"
        info_text = f"<b>{name}</b>"
        if source_label:
            info_text += f"<br><small style='color:grey'>{source_label}</small>"
        info_lbl = QLabel(info_text)
        info_lbl.setWordWrap(True)
        layout.addWidget(info_lbl)

        layout.addSpacing(8)

        # --- [Add Asset] [Edit Asset] [Delete Asset] ---
        asset_row = QHBoxLayout()
        asset_row.setSpacing(4)
        self._add_asset_btn = QPushButton("Add Asset")
        self._add_asset_btn.setToolTip("Click the map to place a new ESDL asset")
        self._add_asset_btn.clicked.connect(self._on_add_asset)
        asset_row.addWidget(self._add_asset_btn)

        self._edit_asset_btn = QPushButton("Edit Asset")
        self._edit_asset_btn.setToolTip(
            "Click an asset on the map to open it in the ESDL Browser"
        )
        self._edit_asset_btn.clicked.connect(self._on_edit_asset)
        asset_row.addWidget(self._edit_asset_btn)

        self._delete_asset_btn = QPushButton("Delete Asset")
        self._delete_asset_btn.setToolTip("Click an asset on the map to delete it")
        self._delete_asset_btn.clicked.connect(self._on_delete_asset)
        asset_row.addWidget(self._delete_asset_btn)
        layout.addLayout(asset_row)

        layout.addSpacing(8)

        # --- [Add Connection] [Delete Connection] ---
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

        # --- [Browse ESDL] ---
        self._browse_esdl_btn = QPushButton("Browse ESDL")
        self._browse_esdl_btn.setToolTip(
            "Inspect and edit ESDL object attributes and references"
        )
        self._browse_esdl_btn.clicked.connect(self._on_browse_esdl)
        layout.addWidget(self._browse_esdl_btn)

        layout.addSpacing(8)

        # --- [Export as .esdl file] ---
        self._export_btn = QPushButton("Export as .esdl file")
        self._export_btn.clicked.connect(self._export)
        layout.addWidget(self._export_btn)

        layout.addSpacing(8)

        # --- Change log ---
        layout.addWidget(QLabel("Change log"))
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        layout.addWidget(self._log)

        self.setWidget(inner)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_sync_callback(self, fn):
        """Register a callable that syncs QGIS layer edits back to the ESH."""
        self._sync_callback = fn

    def set_add_asset_callback(self, fn):
        """Register a callable invoked when the user clicks 'Add Asset'."""
        self._add_asset_callback = fn

    def set_connect_callback(self, fn):
        """Register a callable invoked when the user clicks 'Add Connection'."""
        self._connect_callback = fn

    def set_disconnect_callback(self, fn):
        """Register a callable invoked when the user clicks 'Delete Connection'."""
        self._disconnect_callback = fn

    def set_refresh_layers_callback(self, fn):
        """Register a callable that refreshes QGIS layers after model changes."""
        self._refresh_layers_callback = fn

    def set_edit_asset_callback(self, fn):
        """Register a callable invoked when the user clicks 'Edit Asset'."""
        self._edit_asset_callback = fn

    def set_delete_asset_callback(self, fn):
        """Register a callable invoked when the user clicks 'Delete Asset'."""
        self._delete_asset_callback = fn

    def set_close_callback(self, fn):
        """Register a callable invoked after the dock is confirmed closed."""
        self._close_callback = fn

    def _on_add_asset(self):
        if callable(self._add_asset_callback):
            self._add_asset_callback()

    def _on_connect(self):
        if callable(self._connect_callback):
            self._connect_callback()

    def _on_disconnect(self):
        if callable(self._disconnect_callback):
            self._disconnect_callback()

    def _on_edit_asset(self):
        if callable(self._edit_asset_callback):
            self._edit_asset_callback()

    def _on_delete_asset(self):
        if callable(self._delete_asset_callback):
            self._delete_asset_callback()

    def _on_browse_esdl(self):
        dlg = EsdlBrowserDialog(self._energy_system, parent=self)
        dlg.exec_()
        if callable(self._refresh_layers_callback):
            self._refresh_layers_callback()

    def append_change(self, msg: str):
        """Append a timestamped entry to the change log."""
        ts = datetime.now().strftime("%H:%M:%S")
        self._log.append(f"[{ts}]  {msg}")

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _export(self) -> bool:
        """Sync and save to a .esdl file.  Returns True if saved, False if cancelled or failed."""
        if callable(self._sync_callback):
            self._sync_callback()

        default_name = (self._energy_system.name or "energy_system").replace(
            " ", "_"
        ) + ".esdl"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export ESDL file",
            default_name,
            "ESDL files (*.esdl);;XML files (*.xml)",
        )
        if not path:
            return False

        try:
            xml = self._esh.to_string()
            with open(path, "w", encoding="utf-8") as f:
                f.write(xml)
            self.append_change(f"Exported to {path}")
            return True
        except Exception as exc:
            self.append_change(f"Export failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        """Warn about unsaved changes before closing the dock."""
        if self._log.toPlainText().strip():
            reply = QMessageBox.question(
                self,
                "Unsaved changes",
                "You have unsaved changes in this ESDL session.\n"
                "Export as .esdl before closing?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if reply == QMessageBox.Cancel:
                event.ignore()
                return
            if reply == QMessageBox.Save:
                saved = self._export()
                if not saved:
                    # User cancelled the file picker — keep the dock open
                    event.ignore()
                    return
        event.accept()
        if callable(self._close_callback):
            self._close_callback()


class ESDLDriveDialog(QWizard):
    """
    Three-page wizard for loading ESDL from ESDL Drive.

      Page 1 – Authentication : username / password → Keycloak token
      Page 2 – Browse         : tree of ESDL Drive files → user picks one
      Page 3 – Load           : fetches ESDL, opens ESDL Editor dock in sidebar

    on_loaded(esh, energy_system, source_label) is called after a successful parse.
    Default connection settings are read from module-level _DEFAULT_* constants
    (overridable via environment variables ESDL_DRIVE_URL, ESDL_KC_REALM,
    ESDL_KC_CLIENT, ESDL_KC_SECRET).
    """

    def __init__(self, parent=None, on_loaded=None):
        super().__init__(parent)
        self.setWindowTitle("Load ESDL from Drive")
        self.setMinimumSize(640, 520)
        self.setOption(QWizard.NoBackButtonOnStartPage, True)

        self.token = None
        self.drive_url = _DEFAULT_DRIVE_URL
        self.selected_path = None
        self.esh = None
        self.energy_system = None
        self._on_loaded = on_loaded
        self._drive = ESDLDrive()

        self.addPage(_AuthPage(self))
        self.addPage(_BrowsePage(self))
        self.addPage(_LoadPage(self))


# =============================================================================
# Page 1 – Authentication
# =============================================================================


class _AuthPage(QWizardPage):

    def __init__(self, wizard: ESDLDriveDialog):
        super().__init__()
        self._wiz = wizard
        self._worker = None
        self.setTitle("Step 1 of 3 – Authentication")
        self.setSubTitle(
            "Enter your ESDL Drive credentials. "
            "Connection settings default to environment variables."
        )
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)

        adv = QGroupBox("Connection settings")
        adv_layout = QVBoxLayout(adv)
        self._drive_input = self._row(adv_layout, "ESDL Drive URL:", _DEFAULT_DRIVE_URL)
        self._kc_input = self._row(adv_layout, "Keycloak realm URL:", _DEFAULT_KC_REALM)
        self._client_input = self._row(adv_layout, "Client ID:", _DEFAULT_KC_CLIENT)
        self._secret_input = self._row(
            adv_layout, "Client secret:", _DEFAULT_KC_SECRET, echo=QLineEdit.Password
        )
        root.addWidget(adv)

        cred = QGroupBox("Credentials")
        cred_layout = QVBoxLayout(cred)
        self._user_input = self._row(cred_layout, "Username:", "")
        self._pass_input = self._row(
            cred_layout, "Password:", "", echo=QLineEdit.Password
        )
        self._login_btn = QPushButton("Login")
        self._login_btn.clicked.connect(self._login)
        cred_layout.addWidget(self._login_btn)
        root.addWidget(cred)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        root.addWidget(self._status)
        root.addStretch()

    @staticmethod
    def _row(layout, label: str, default: str, echo=None) -> QLineEdit:
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setFixedWidth(150)
        row.addWidget(lbl)
        edit = QLineEdit(default)
        if echo is not None:
            edit.setEchoMode(echo)
        row.addWidget(edit)
        layout.addLayout(row)
        return edit

    def isComplete(self) -> bool:
        return self._wiz.token is not None

    def _login(self):
        username = self._user_input.text().strip()
        password = self._pass_input.text()
        if not username or not password:
            self._status.setText("Enter username and password.")
            return
        self._login_btn.setEnabled(False)
        self._status.setText("Logging in…")
        self._worker = PasswordAuthWorker(
            keycloak_realm_url=self._kc_input.text().strip(),
            username=username,
            password=password,
            client_id=self._client_input.text().strip() or _DEFAULT_KC_CLIENT,
            client_secret=self._secret_input.text().strip() or None,
        )
        self._worker.success.connect(self._on_success)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_success(self, token: str):
        self._wiz.token = token
        self._wiz.drive_url = self._drive_input.text().strip().rstrip("/")
        self._wiz._drive = ESDLDrive(base_url=self._wiz.drive_url)
        self._status.setText("Authenticated successfully.")
        self._login_btn.setEnabled(True)
        self.completeChanged.emit()

    def _on_failed(self, error: str):
        self._status.setText(f"Login failed: {error}")
        self._login_btn.setEnabled(True)


# =============================================================================
# Page 2 – Browse
# =============================================================================


class _BrowsePage(QWizardPage):

    def __init__(self, wizard: ESDLDriveDialog):
        super().__init__()
        self._wiz = wizard
        self.setTitle("Step 2 of 3 – Select ESDL File")
        self.setSubTitle(
            "Browse the ESDL Drive repository and select a .esdl file to load."
        )
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("ESDL Drive contents:"))
        top.addStretch()
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._load_tree)
        top.addWidget(self._refresh_btn)
        root.addLayout(top)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabel("Path")
        self._tree.itemSelectionChanged.connect(self._on_selection)
        root.addWidget(self._tree)
        self._status = QLabel("Waiting for browse results…")
        root.addWidget(self._status)

    def initializePage(self):
        self._load_tree()

    def isComplete(self) -> bool:
        return self._wiz.selected_path is not None

    def _on_selection(self):
        items = self._tree.selectedItems()
        if items:
            path = items[0].data(0, Qt.UserRole)
            if path:
                self._wiz.selected_path = path
                self._status.setText(f"Selected: {path}")
                self.completeChanged.emit()
                return
        self._wiz.selected_path = None
        self.completeChanged.emit()

    def _load_tree(self):
        self._tree.clear()
        self._wiz.selected_path = None
        self._status.setText("Loading…")
        self.completeChanged.emit()
        result = self._wiz._drive.browse_cdo(
            token=self._wiz.token,
            params={"operation": "get_node", "id": "#", "depth": 3},
        )
        if "error" in result:
            self._status.setText(f"Error: {result['error']}")
            return
        self._populate(self._tree.invisibleRootItem(), result.get("json", {}))
        self._tree.expandAll()
        self._status.setText("Select a .esdl file, then click Next.")

    def _populate(self, parent: QTreeWidgetItem, data):
        if isinstance(data, list):
            for entry in data:
                self._add_entry(parent, entry)
        elif isinstance(data, dict):
            children = data.get("children")
            if isinstance(children, list):
                for entry in children:
                    self._add_entry(parent, entry)
            else:
                self._add_entry(parent, data)

    def _add_entry(self, parent: QTreeWidgetItem, entry: dict):
        if not isinstance(entry, dict):
            return
        name = (
            entry.get("text")
            or entry.get("name")
            or str(entry.get("id", "?")).split("/")[-1]
            or "?"
        )
        path = entry.get("id") or entry.get("path") or entry.get("uri") or ""
        is_file = name.endswith(".esdl")
        item = QTreeWidgetItem(parent, [name])
        item.setData(0, Qt.UserRole, path if is_file else None)
        children = entry.get("children")
        if isinstance(children, list):
            for child in children:
                self._add_entry(item, child)


# =============================================================================
# Page 3 – Load
# =============================================================================


class _LoadPage(QWizardPage):

    def __init__(self, wizard: ESDLDriveDialog):
        super().__init__()
        self._wiz = wizard
        self.setTitle("Step 3 of 3 – Load")
        self.setSubTitle(
            "Loading the selected ESDL file into QGIS. "
            "The ESDL Editor panel will open for change tracking and export."
        )
        self.setFinalPage(True)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        self._info = QLabel("")
        self._info.setWordWrap(True)
        root.addWidget(self._info)
        root.addStretch()

    def initializePage(self):
        self._load_esdl()

    def _load_esdl(self):
        path = self._wiz.selected_path
        if not path:
            self._info.setText("No file selected.")
            return

        resource_url = f"{self._wiz.drive_url}/store/resource{path}"
        self._info.setText(f"Fetching {resource_url} …")

        result = self._wiz._drive.get_resource(resource_url, token=self._wiz.token)
        if "error" in result:
            self._info.setText(f"Fetch error: {result['error']}")
            return
        if "text/html" in result.get("content_type", ""):
            self._info.setText(
                "Server returned HTML — authentication may have expired."
            )
            return

        try:
            esh = EnergySystemHandler()
            es = esh.load_from_string(result["content"])
            self._wiz.esh = esh
            self._wiz.energy_system = es
            self._info.setText(
                f"✓ '{es.name}' loaded.\n"
                "The ESDL Editor panel is open in the sidebar — "
                "edit features on the map, export when done."
            )
            if callable(self._wiz._on_loaded):
                self._wiz._on_loaded(esh, es, source_label=path)
        except Exception as exc:
            self._info.setText(f"Parse error: {exc}")


# =============================================================================
# Helper
# =============================================================================


def _make_temp_asset(class_name: str):
    """
    Instantiate a no-arg pyESDL asset of *class_name* for attribute
    introspection only.  Returns None if the class cannot be instantiated.
    """
    try:
        from esdl import esdl

        cls = getattr(esdl, class_name)
        return cls()
    except Exception:
        return None


# =============================================================================
# Geometry-capture map tools
# =============================================================================


class PointCaptureMapTool(QgsMapTool):
    """Single left-click captures a point geometry."""

    geometry_captured = pyqtSignal(object)  # QgsGeometry

    def __init__(self, canvas):
        super().__init__(canvas)

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            pt = self.toMapCoordinates(event.pos())
            self.geometry_captured.emit(QgsGeometry.fromPointXY(pt))
            self.canvas().unsetMapTool(self)


class LineCaptureMapTool(QgsMapTool):
    """Left-click adds vertices; right-click finishes and emits a LineString."""

    geometry_captured = pyqtSignal(object)  # QgsGeometry

    def __init__(self, canvas):
        super().__init__(canvas)
        self._points = []
        self._rubber_band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self._rubber_band.setColor(QColor(0, 120, 215))
        self._rubber_band.setWidth(2)

    def canvasPressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._points.append(self.toMapCoordinates(event.pos()))
        elif event.button() == Qt.RightButton:
            self._finish()

    def canvasMoveEvent(self, event):
        if not self._points:
            return
        cur = self.toMapCoordinates(event.pos())
        self._rubber_band.reset(QgsWkbTypes.LineGeometry)
        for p in self._points:
            self._rubber_band.addPoint(p, False)
        self._rubber_band.addPoint(cur, True)

    def _finish(self):
        if len(self._points) >= 2:
            geom = QgsGeometry.fromPolylineXY(self._points)
            self._rubber_band.reset()
            self.canvas().unsetMapTool(self)
            self.geometry_captured.emit(geom)

    def deactivated(self):
        self._rubber_band.reset()
        self._points = []


class PolygonCaptureMapTool(QgsMapTool):
    """Left-click adds vertices; right-click finishes and emits a Polygon."""

    geometry_captured = pyqtSignal(object)  # QgsGeometry

    def __init__(self, canvas):
        super().__init__(canvas)
        self._points = []
        self._rubber_band = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
        self._rubber_band.setColor(QColor(0, 120, 215))
        self._rubber_band.setFillColor(QColor(0, 120, 215, 40))
        self._rubber_band.setWidth(2)

    def canvasPressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._points.append(self.toMapCoordinates(event.pos()))
        elif event.button() == Qt.RightButton:
            self._finish()

    def canvasMoveEvent(self, event):
        if not self._points:
            return
        cur = self.toMapCoordinates(event.pos())
        self._rubber_band.reset(QgsWkbTypes.PolygonGeometry)
        for p in self._points:
            self._rubber_band.addPoint(p, False)
        self._rubber_band.addPoint(cur, True)

    def _finish(self):
        if len(self._points) >= 3:
            geom = QgsGeometry.fromPolygonXY([self._points])
            self._rubber_band.reset()
            self.canvas().unsetMapTool(self)
            self.geometry_captured.emit(geom)

    def deactivated(self):
        self._rubber_band.reset()
        self._points = []


# =============================================================================
# PickAssetDialog — step 1: choose asset class and name (new add-asset flow)
# =============================================================================


class PickAssetDialog(QDialog):
    """
    First step of the dialog-first add-asset flow.

    Shows a class combobox, a name field, and the geometry draw mode is
    derived automatically from esdl_model.geom_hint_for_class().
    """

    def __init__(self, loaded_classes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add ESDL Asset")
        self.setMinimumWidth(380)
        self._loaded_classes = list(loaded_classes)
        self._setup_ui()

    def _setup_ui(self):
        from .esdl_model import list_asset_classes

        all_classes = list_asset_classes()
        loaded_set = set(self._loaded_classes)
        ordered = sorted(self._loaded_classes) + [
            c for c in all_classes if c not in loaded_set
        ]

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._class_combo = QComboBox()
        self._class_combo.addItems(ordered)
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

        if ordered:
            self._on_class_changed(ordered[0])

    def _on_class_changed(self, class_name: str):
        from .esdl_model import geom_hint_for_class

        hint = geom_hint_for_class(class_name)
        labels = {
            "Point": "Click once on the map",
            "Line": "Click vertices, right-click to finish",
            "Polygon": "Click vertices, right-click to finish",
        }
        self._geom_label.setText(labels.get(hint, hint))

    def get_data(self) -> dict:
        """Return dict with keys: class_name, name, geom_hint."""
        from .esdl_model import geom_hint_for_class

        class_name = self._class_combo.currentText()
        return {
            "class_name": class_name,
            "name": self._name_edit.text().strip() or class_name,
            "geom_hint": geom_hint_for_class(class_name),
        }


# =============================================================================
# Geometry-capture — step 2: capture geometry draw by the user
# =============================================================================

# =============================================================================
# AddAttributesDialog — step 3: fill attributes after geometry draw
# =============================================================================


class AddAttributesDialog(QDialog):
    """
    Third step of the dialog-first add-asset flow.

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
            "later in the QGIS feature table.</small>"
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

    # TODO: review
    def _populate_fields(self):
        from .esdl_visualize import _get_esdl_attr_fields

        asset = _make_temp_asset(self._class_name)
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


# =============================================================================
# ConnectAssetsMapTool — two-click tool that selects a source then target asset
# =============================================================================


class ConnectAssetsMapTool(QgsMapTool):
    """
    Two-step click tool for connecting asset ports.

    1. First click  — identifies the source asset (highlighted by rubber band).
    2. Second click — identifies the target asset; emits *assets_selected* with
                      the two asset IDs, then deactivates.

    Requires a reference to an ESDLVisualizer for spatial feature lookup.
    """

    assets_selected = pyqtSignal(str, str)  # source_id, target_id

    def __init__(self, canvas, visualizer):
        super().__init__(canvas)
        self._visualizer = visualizer
        self._source_id = None
        self._rubber_band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self._rubber_band.setColor(QColor(255, 140, 0))
        self._rubber_band.setWidth(2)

    def _tolerance(self) -> float:
        return self.canvas().mapUnitsPerPixel() * 10

    def canvasReleaseEvent(self, event):
        point = self.toMapCoordinates(event.pos())
        asset_id = self._visualizer.find_asset_id_near(point, self._tolerance())
        if asset_id is None:
            return
        if self._source_id is None:
            self._source_id = asset_id
        elif asset_id != self._source_id:
            self._rubber_band.reset()
            self.assets_selected.emit(self._source_id, asset_id)
            self.canvas().unsetMapTool(self)

    def canvasMoveEvent(self, event):
        if self._source_id is None:
            return
        src_asset = self._visualizer.get_asset_by_id(self._source_id)
        if src_asset is None or src_asset.geometry is None:
            return
        src_pt = _geom_to_qgspoint(src_asset.geometry)
        if src_pt is None:
            return
        cur_pt = self.toMapCoordinates(event.pos())
        self._rubber_band.reset(QgsWkbTypes.LineGeometry)
        self._rubber_band.addPoint(src_pt)
        self._rubber_band.addPoint(cur_pt)

    def deactivated(self):
        self._rubber_band.reset()
        self._source_id = None


# =============================================================================
# EditAssetMapTool — single-click tool that selects an asset for editing
# =============================================================================


class EditAssetMapTool(QgsMapTool):
    """
    Single left-click identifies the nearest ESDL asset and emits
    *asset_selected* with its id string so the caller can open the
    ESDL Browser at that object.  Right-click cancels.
    """

    asset_selected = pyqtSignal(str)  # asset_id

    def __init__(self, canvas, visualizer):
        super().__init__(canvas)
        self._visualizer = visualizer

    def _tolerance(self) -> float:
        return self.canvas().mapUnitsPerPixel() * 10

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            point = self.toMapCoordinates(event.pos())
            asset_id = self._visualizer.find_asset_id_near(point, self._tolerance())
            if asset_id is not None:
                self.asset_selected.emit(asset_id)
                self.canvas().unsetMapTool(self)
        elif event.button() == Qt.RightButton:
            self.canvas().unsetMapTool(self)


# =============================================================================
# ConnectPortsDialog — port-picker for wiring two assets
# =============================================================================


class ConnectPortsDialog(QDialog):
    """
    Lets the user choose which port of Asset A connects to which port of
    Asset B.  All ports (InPort and OutPort) for both assets are shown.

    The first selection constrains the second: selecting an OutPort on one
    side disables all OutPorts on the other side, and vice-versa.

    Call *get_selected_ports()* after QDialog.Accepted to retrieve the
    normalized *(out_port, in_port)* pair.  Call *get_out_asset()* to find
    which asset owns the OutPort (used for map arrow direction).
    """

    def __init__(self, source_asset, target_asset, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect Ports")
        self._source = source_asset
        self._target = target_asset
        self._all_ports_a = []
        self._all_ports_b = []
        self._ok_btn = None
        self._setup_ui()

    def _setup_ui(self):
        from .esdl_model import get_connectable_ports

        out_a, in_a = get_connectable_ports(self._source)
        out_b, in_b = get_connectable_ports(self._target)
        self._all_ports_a = out_a + in_a
        self._all_ports_b = out_b + in_b

        src_name = getattr(self._source, "name", None) or type(self._source).__name__
        tgt_name = getattr(self._target, "name", None) or type(self._target).__name__

        layout = QVBoxLayout(self)
        # layout.addWidget(QLabel(f"<b>{src_name}</b> &nbsp;↔&nbsp; <b>{tgt_name}</b>"))
        # layout.addSpacing(6)

        # Two-column layout: Asset A | ↔ | Asset B
        cols = QHBoxLayout()

        col_a = QVBoxLayout()
        col_a.addWidget(QLabel(f"<b>{src_name}</b>"))
        self._combo_a = QComboBox()
        for p in self._all_ports_a:
            label = f"{getattr(p, 'name', '') or type(p).__name__} [{type(p).__name__}]"
            self._combo_a.addItem(label)
        col_a.addWidget(self._combo_a)
        cols.addLayout(col_a)

        arrow_lbl = QLabel("↔")
        arrow_lbl.setAlignment(Qt.AlignCenter)
        cols.addWidget(arrow_lbl)

        col_b = QVBoxLayout()
        col_b.addWidget(QLabel(f"<b>{tgt_name}</b>"))
        self._combo_b = QComboBox()
        for p in self._all_ports_b:
            label = f"{getattr(p, 'name', '') or type(p).__name__} [{type(p).__name__}]"
            self._combo_b.addItem(label)
        col_b.addWidget(self._combo_b)
        cols.addLayout(col_b)

        layout.addLayout(cols)

        if not self._all_ports_a:
            layout.addWidget(QLabel(f"⚠ '{src_name}' has no ports — add ports first."))
        if not self._all_ports_b:
            layout.addWidget(QLabel(f"⚠ '{tgt_name}' has no ports — add ports first."))

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        self._ok_btn = btn_box.button(QDialogButtonBox.Ok)
        layout.addWidget(btn_box)

        self._combo_a.currentIndexChanged.connect(self._on_combo_a_changed)
        self._combo_b.currentIndexChanged.connect(self._on_update_ok)

        # Trigger initial enable/disable pass
        self._on_combo_a_changed(self._combo_a.currentIndex())

    def _on_combo_a_changed(self, index: int):
        """Disable same-type ports in combo B; update OK button state."""
        if index < 0 or index >= len(self._all_ports_a):
            self._on_update_ok()
            return

        selected_a = self._all_ports_a[index]
        model = self._combo_b.model()
        for i, port_b in enumerate(self._all_ports_b):
            item = model.item(i)
            if item is None:
                continue
            if type(port_b) is type(selected_a):
                item.setFlags(Qt.NoItemFlags)
            else:
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)

        # If the current B selection became disabled, move to the first valid item
        cur = self._combo_b.currentIndex()
        if 0 <= cur < len(self._all_ports_b):
            cur_item = model.item(cur)
            if cur_item is not None and not (cur_item.flags() & Qt.ItemIsEnabled):
                for i in range(len(self._all_ports_b)):
                    it = model.item(i)
                    if it is not None and (it.flags() & Qt.ItemIsEnabled):
                        self._combo_b.setCurrentIndex(i)
                        break

        self._on_update_ok()

    def _on_update_ok(self, _index: int = 0):
        """Enable OK only when a valid OutPort ↔ InPort pair is selected."""
        if self._ok_btn is None:
            return
        if not self._all_ports_a or not self._all_ports_b:
            self._ok_btn.setEnabled(False)
            return
        idx_a = self._combo_a.currentIndex()
        idx_b = self._combo_b.currentIndex()
        if idx_a < 0 or idx_b < 0:
            self._ok_btn.setEnabled(False)
            return
        from esdl import esdl

        port_a = self._all_ports_a[idx_a]
        port_b = self._all_ports_b[idx_b]
        valid = (
            isinstance(port_a, esdl.OutPort) and isinstance(port_b, esdl.InPort)
        ) or (isinstance(port_a, esdl.InPort) and isinstance(port_b, esdl.OutPort))
        self._ok_btn.setEnabled(valid)

    def get_selected_ports(self):
        """
        Return *(out_port, in_port)* normalized so out_port is always the
        OutPort regardless of which combo it was selected from.
        Returns *(None, None)* if no valid pair is available.
        """
        if not self._all_ports_a or not self._all_ports_b:
            return None, None
        from esdl import esdl

        port_a = self._all_ports_a[self._combo_a.currentIndex()]
        port_b = self._all_ports_b[self._combo_b.currentIndex()]
        if isinstance(port_a, esdl.OutPort):
            return port_a, port_b  # A=out, B=in
        return port_b, port_a  # B=out, A=in

    def get_out_asset(self):
        """
        Return the asset that owns the OutPort (for map arrow direction).
        Must be called after *get_selected_ports()*.
        """
        if not self._all_ports_a:
            return self._source
        from esdl import esdl

        port_a = self._all_ports_a[self._combo_a.currentIndex()]
        return self._source if isinstance(port_a, esdl.OutPort) else self._target


# =============================================================================
# DisconnectDialog — pick an existing connection on an asset to remove
# =============================================================================


class DisconnectDialog(QDialog):
    """
    Shows all connections that involve *asset* (both outgoing and incoming)
    and lets the user select one to remove.

    Call *get_selected_connection()* after QDialog.Accepted to retrieve the
    *(out_port, in_port)* pair to disconnect.
    """

    def __init__(self, asset, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Delete Connection")
        self.setMinimumWidth(440)
        self._asset = asset
        self._connections = []  # [(out_port, in_port, out_asset, in_asset), ...]
        self._ok_btn = None
        self._setup_ui()

    def _setup_ui(self):
        from .esdl_model import get_asset_connections

        self._connections = get_asset_connections(self._asset)
        asset_name = getattr(self._asset, "name", None) or type(self._asset).__name__

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Connections of <b>{asset_name}</b>:"))

        self._list = QListWidget()
        for out_port, in_port, out_asset, in_asset in self._connections:
            out_port_name = getattr(out_port, "name", None) or "OutPort"
            in_port_name = getattr(in_port, "name", None) or "InPort"
            out_asset_name = (
                getattr(out_asset, "name", None) or type(out_asset).__name__
                if out_asset
                else "?"
            )
            in_asset_name = (
                getattr(in_asset, "name", None) or type(in_asset).__name__
                if in_asset
                else "?"
            )
            self._list.addItem(
                f"{out_asset_name} [{out_port_name}]  \u2194  {in_asset_name} [{in_port_name}]"
            )

        if not self._connections:
            layout.addWidget(QLabel(f"\u26a0 '{asset_name}' has no connections."))
        else:
            layout.addWidget(self._list)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        self._ok_btn = btn_box.button(QDialogButtonBox.Ok)
        self._ok_btn.setEnabled(False)
        layout.addWidget(btn_box)

        self._list.currentRowChanged.connect(self._on_row_changed)

    def _on_row_changed(self, row: int):
        if self._ok_btn is not None:
            self._ok_btn.setEnabled(row >= 0)

    def get_selected_connection(self):
        """Return *(out_port, in_port)* of the selected connection, or *(None, None)*."""
        row = self._list.currentRow()
        if row < 0 or row >= len(self._connections):
            return None, None
        out_port, in_port, _, _ = self._connections[row]
        return out_port, in_port

    def get_connection_assets(self):
        """Return *(out_asset, in_asset)* of the selected connection, or *(None, None)*."""
        row = self._list.currentRow()
        if row < 0 or row >= len(self._connections):
            return None, None
        _, _, out_asset, in_asset = self._connections[row]
        return out_asset, in_asset


# =============================================================================
# EsdlBrowserDialog — generic ESDL object browser / editor
# =============================================================================


class EsdlBrowserDialog(QDialog):
    """
    Generic ESDL object browser and editor.

    Opens at the EnergySystem root.  From there the user can:
      * View and edit EAttributes (scalars, enums, booleans).
      * Navigate into any EReference by clicking its value link.
      * Go back through the navigation history.
      * Add new containment children (type chosen from a picker dialog).
      * Delete containment children.
      * Assign cross-references via a candidate-picker dialog.

    Attribute edits are flushed to the live pyESDL model on each navigation
    step and when the user clicks OK.  Clicking Cancel closes the dialog
    without reverting changes already flushed during navigation.
    """

    def __init__(self, energy_system, initial_obj=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ESDL Browser")
        self.setMinimumSize(820, 580)
        self._root = energy_system
        self._obj = energy_system
        self._history = []  # back-navigation stack
        self._current_attr_widgets = {}  # attr_name → (widget, attr_meta)
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
        self._back_btn.setEnabled(bool(self._history))
        self._rebuild_content()

    def _go_back(self):
        if not self._history:
            return
        self._flush_attrs()
        self._obj = self._history.pop()
        self._current_attr_widgets = {}
        self._back_btn.setEnabled(bool(self._history))
        self._rebuild_content()

    def accept(self):
        self._flush_attrs()
        super().accept()

    # ------------------------------------------------------------------
    # Attribute flush (write widget values back to the model)
    # ------------------------------------------------------------------

    def _flush_attrs(self):
        obj = self._obj
        for attr_name, (widget, _meta) in list(self._current_attr_widgets.items()):
            try:
                feature = obj.eClass.findEStructuralFeature(attr_name)
                if feature is None:
                    continue
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

        from .esdl_ecore import get_asset_attributes, get_asset_references

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
                    ", ".join(str(v) for v in val)
                    if isinstance(val, list)
                    else str(val or "")
                )
                w = QLineEdit(display)
                w.setReadOnly(True)
                w.setStyleSheet("color: #888;")
                w.setToolTip("List-typed attribute — read-only in this prototype")
            elif "options" in attr:
                w = QComboBox()
                w.blockSignals(True)
                w.addItems(attr["options"])
                cur = str(attr.get("value") or attr.get("default") or "").lower()
                for opt in attr["options"]:
                    if opt.lower() == cur:
                        w.setCurrentText(opt)
                        break
                w.blockSignals(False)
                self._current_attr_widgets[name] = (w, attr)
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
                self._current_attr_widgets[name] = (w, attr)
            elif attr["type"] in ("EDouble", "EFloat"):
                w = QDoubleSpinBox()
                w.setRange(-1e15, 1e15)
                w.setDecimals(6)
                w.blockSignals(True)
                try:
                    v = attr.get("value")
                    w.setValue(float(v) if v not in (None, "") else 0.0)
                except (ValueError, TypeError):
                    pass
                w.blockSignals(False)
                self._current_attr_widgets[name] = (w, attr)
            else:
                w = QLineEdit()
                v = attr.get("value")
                w.setText(str(v) if v is not None else "")
                self._current_attr_widgets[name] = (w, attr)

            form.addRow(lbl, w)

        parent_layout.addWidget(box)

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
        from pyecore.ecore import ECollection
        from .esdl_ecore import object_repr

        name = ref["name"]
        many = ref["many"]
        containment = ref["containment"]
        eopposite = ref["eopposite"]

        feature = parent_obj.eClass.findEStructuralFeature(name)
        if feature is None:
            return QWidget()

        raw = parent_obj.eGet(feature)
        items = (
            list(raw)
            if isinstance(raw, ECollection)
            else ([raw] if raw is not None else [])
        )

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
                btn.clicked.connect(
                    lambda checked=False, o=item_obj: self._navigate_to(o)
                )
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
                    lambda checked=False, o=parent_obj, r=ref: self._add_containment(
                        o, r
                    )
                )
                for item_obj in items:
                    del_btn = QPushButton("×")
                    del_btn.setFixedSize(26, 22)
                    del_btn.setStyleSheet("color: #c00; font-weight: bold;")
                    del_btn.setToolTip("Delete this item")
                    del_btn.clicked.connect(
                        lambda checked=False, o=item_obj, po=parent_obj, r=ref: self._delete_containment(
                            o, po, r
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
                        lambda checked=False, o=parent_obj, r=ref: self._clear_xref(
                            o, r
                        )
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
            import uuid as _uuid
            from .esdl_ecore import instantiate_type

            new_obj = instantiate_type(type_name)
            if hasattr(new_obj, "id"):
                new_obj.id = str(_uuid.uuid4())
            if hasattr(new_obj, "name"):
                new_obj.name = f"New{type_name}"
            feature = parent_obj.eClass.findEStructuralFeature(ref["name"])
            if ref["many"]:
                parent_obj.eGet(ref["name"]).append(new_obj)
            else:
                parent_obj.eSet(feature, new_obj)
            self._rebuild_content()
        except Exception as exc:
            QMessageBox.warning(self, "Error", f"Could not create {type_name}: {exc}")

    def _delete_containment(self, item_obj, parent_obj, ref):
        from .esdl_ecore import object_repr

        label = object_repr(item_obj)
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
        from .esdl_ecore import find_reachable_of_types

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
            self._rebuild_content()
        except Exception as exc:
            QMessageBox.warning(self, "Error", f"Could not set reference: {exc}")

    def _clear_xref(self, parent_obj, ref):
        try:
            feature = parent_obj.eClass.findEStructuralFeature(ref["name"])
            parent_obj.eSet(feature, None)
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
        from .esdl_ecore import object_repr

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
