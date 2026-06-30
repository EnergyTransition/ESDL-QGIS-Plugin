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
_DEFAULT_KC_SECRET = os.environ.get(
    "ESDL_KC_SECRET", "329ab44b-087b-44d2-89b7-da139d32a7b1"
)


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
        self._edit_carriers_callback = None
        self._build_ui(source_label)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self, source_label: str):
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(6, 6, 6, 6)

        name = self._energy_system.name or "(unnamed)"
        info_text = f"<b>{name}</b>"
        if source_label:
            info_text += f"<br><small style='color:grey'>{source_label}</small>"
        info_lbl = QLabel(info_text)
        info_lbl.setWordWrap(True)
        layout.addWidget(info_lbl)

        edit_row = QHBoxLayout()
        self._add_asset_btn = QPushButton("Add Asset…")
        self._add_asset_btn.setToolTip("Click the map to place a new ESDL asset")
        self._add_asset_btn.clicked.connect(self._on_add_asset)
        edit_row.addWidget(self._add_asset_btn)

        self._connect_btn = QPushButton("Connect…")
        self._connect_btn.setToolTip("Click two assets on the map to wire their ports")
        self._connect_btn.clicked.connect(self._on_connect)
        edit_row.addWidget(self._connect_btn)

        self._edit_carriers_btn = QPushButton("Edit Carriers…")
        self._edit_carriers_btn.setToolTip("Add, edit, or delete energy carriers")
        self._edit_carriers_btn.clicked.connect(self._on_edit_carriers)
        edit_row.addWidget(self._edit_carriers_btn)
        layout.addLayout(edit_row)

        layout.addWidget(QLabel("Change log:"))
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        layout.addWidget(self._log)

        self._export_btn = QPushButton("Export as .esdl file…")
        self._export_btn.clicked.connect(self._export)
        layout.addWidget(self._export_btn)

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
        """Register a callable invoked when the user clicks 'Connect'."""
        self._connect_callback = fn

    def set_edit_carriers_callback(self, fn):
        """Register a callable invoked when the user clicks 'Edit Carriers'."""
        self._edit_carriers_callback = fn

    def _on_add_asset(self):
        if callable(self._add_asset_callback):
            self._add_asset_callback()

    def _on_connect(self):
        if callable(self._connect_callback):
            self._connect_callback()

    def _on_edit_carriers(self):
        if callable(self._edit_carriers_callback):
            self._edit_carriers_callback()

    def append_change(self, msg: str):
        """Append a timestamped entry to the change log."""
        ts = datetime.now().strftime("%H:%M:%S")
        self._log.append(f"[{ts}]  {msg}")

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _export(self):
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
            return

        try:
            xml = self._esh.to_string()
            with open(path, "w", encoding="utf-8") as f:
                f.write(xml)
            self.append_change(f"Exported to {path}")
        except Exception as exc:
            self.append_change(f"Export failed: {exc}")


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
# ConnectPortsDialog — port-picker for wiring two assets
# =============================================================================


class ConnectPortsDialog(QDialog):
    """
    Lets the user choose which OutPort of the source asset connects to which
    InPort of the target asset.

    Call *get_selected_ports()* after QDialog.Accepted to retrieve the pair.
    """

    def __init__(self, source_asset, target_asset, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect Ports")
        self._source = source_asset
        self._target = target_asset
        self._out_ports = []
        self._in_ports = []
        self._setup_ui()

    def _setup_ui(self):
        from .esdl_model import get_connectable_ports

        out_ports, _ = get_connectable_ports(self._source)
        _, in_ports = get_connectable_ports(self._target)
        self._out_ports = out_ports
        self._in_ports = in_ports

        src_name = getattr(self._source, "name", None) or type(self._source).__name__
        tgt_name = getattr(self._target, "name", None) or type(self._target).__name__

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(f"<b>{src_name}</b> &nbsp;&#8594;&nbsp; <b>{tgt_name}</b>")
        )

        form = QFormLayout()
        self._out_combo = QComboBox()
        for p in out_ports:
            label = f"{getattr(p, 'name', '') or 'OutPort'} [{type(p).__name__}]"
            self._out_combo.addItem(label)
        form.addRow(f"From — {src_name}:", self._out_combo)

        self._in_combo = QComboBox()
        for p in in_ports:
            label = f"{getattr(p, 'name', '') or 'InPort'} [{type(p).__name__}]"
            self._in_combo.addItem(label)
        form.addRow(f"To — {tgt_name}:", self._in_combo)
        layout.addLayout(form)

        if not out_ports:
            layout.addWidget(
                QLabel(f"⚠ '{src_name}' has no OutPorts — add ports first.")
            )
        if not in_ports:
            layout.addWidget(
                QLabel(f"⚠ '{tgt_name}' has no InPorts — add ports first.")
            )

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        btn_box.button(QDialogButtonBox.Ok).setEnabled(bool(out_ports and in_ports))
        layout.addWidget(btn_box)

    def get_selected_ports(self):
        """Return *(out_port, in_port)* or *(None, None)* if unavailable."""
        if not self._out_ports or not self._in_ports:
            return None, None
        return (
            self._out_ports[self._out_combo.currentIndex()],
            self._in_ports[self._in_combo.currentIndex()],
        )


# =============================================================================
# CarriersDialog — view / add / edit / delete carriers in EnergySystemInformation
# =============================================================================


class CarriersDialog(QDialog):
    """
    Two-panel dialog for managing ESDL energy carriers.

    Left panel  — QTreeWidget listing carriers grouped by type.
    Right panel — QFormLayout showing editable attributes for the selected carrier.
    Bottom row  — Add / Delete buttons on the left; OK / Cancel on the right.

    Changes are written to the model only when the user clicks OK.
    """

    def __init__(self, energy_system, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Carriers")
        self.setMinimumSize(640, 420)
        self._energy_system = energy_system
        # Working copy: list of dicts {carrier, dirty_attrs}
        # 'carrier' is the live pyESDL object; we only commit on accept().
        self._pending_deletes: set = set()  # carrier ids to remove on OK
        self._attr_widgets: dict = {}  # carrier_id -> {field -> widget}
        self._current_id: str | None = None
        self._setup_ui()
        self._populate_tree()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self):
        from .esdl_model import list_carrier_classes

        self._carrier_classes = list_carrier_classes()

        root = QVBoxLayout(self)
        splitter_row = QHBoxLayout()

        # ---- left: tree ----
        left = QVBoxLayout()
        left.addWidget(QLabel("Carriers:"))
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setMinimumWidth(200)
        self._tree.currentItemChanged.connect(self._on_tree_selection)
        left.addWidget(self._tree)

        btn_row = QHBoxLayout()
        self._add_btn = QPushButton("Add…")
        self._add_btn.clicked.connect(self._on_add)
        self._del_btn = QPushButton("Delete")
        self._del_btn.setEnabled(False)
        self._del_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self._add_btn)
        btn_row.addWidget(self._del_btn)
        btn_row.addStretch()
        left.addLayout(btn_row)

        left_widget = QWidget()
        left_widget.setLayout(left)
        splitter_row.addWidget(left_widget, stretch=2)

        # ---- right: attribute form ----
        right = QVBoxLayout()
        right.addWidget(QLabel("Attributes:"))
        self._form_container = QWidget()
        self._form_layout = QFormLayout(self._form_container)
        self._form_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._form_container)
        right.addWidget(scroll)

        right_widget = QWidget()
        right_widget.setLayout(right)
        splitter_row.addWidget(right_widget, stretch=3)

        root.addLayout(splitter_row)

        # ---- bottom: OK / Cancel ----
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self._commit_and_accept)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

    # ------------------------------------------------------------------
    # Tree population
    # ------------------------------------------------------------------

    def _populate_tree(self):
        """Rebuild the tree from the live energy system carriers."""
        from .esdl_model import get_carriers

        self._tree.clear()
        carriers = get_carriers(self._energy_system)

        # Group by concrete type name
        groups: dict = {}
        for carrier in carriers:
            if carrier.id in self._pending_deletes:
                continue
            type_name = type(carrier).__name__
            if type_name not in groups:
                groups[type_name] = QTreeWidgetItem(self._tree, [type_name])
                groups[type_name].setExpanded(True)
                groups[type_name].setFlags(
                    groups[type_name].flags() & ~Qt.ItemIsSelectable
                )
            label = getattr(carrier, "name", None) or carrier.id
            item = QTreeWidgetItem(groups[type_name], [label])
            item.setData(0, Qt.UserRole, carrier.id)

        self._tree.expandAll()
        # Restore selection if still present
        if self._current_id:
            self._select_carrier_by_id(self._current_id)

    def _select_carrier_by_id(self, carrier_id: str):
        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            group = root.child(i)
            for j in range(group.childCount()):
                item = group.child(j)
                if item.data(0, Qt.UserRole) == carrier_id:
                    self._tree.setCurrentItem(item)
                    return

    # ------------------------------------------------------------------
    # Tree selection → attribute form
    # ------------------------------------------------------------------

    def _on_tree_selection(self, current, _previous):
        """Flush pending edits for the previous item, then build the new form."""
        if _previous is not None:
            prev_id = _previous.data(0, Qt.UserRole)
            if prev_id:
                self._flush_attrs(prev_id)

        carrier_id = current.data(0, Qt.UserRole) if current else None
        self._current_id = carrier_id
        self._del_btn.setEnabled(carrier_id is not None)
        self._clear_form()

        if carrier_id is None:
            return

        carrier = self._find_carrier(carrier_id)
        if carrier is None:
            return

        self._build_attr_form(carrier)

    def _clear_form(self):
        while self._form_layout.rowCount():
            self._form_layout.removeRow(0)

    def _build_attr_form(self, carrier):
        """Populate the right-panel form with the carrier's EAttribute fields."""
        from .esdl_visualize import _get_esdl_attr_fields

        carrier_id = carrier.id
        if carrier_id not in self._attr_widgets:
            self._attr_widgets[carrier_id] = {}
        widgets = self._attr_widgets[carrier_id]

        # Always show id (read-only) and name at the top
        id_edit = QLineEdit(str(carrier.id or ""))
        id_edit.setReadOnly(True)
        id_edit.setStyleSheet("color: grey;")
        self._form_layout.addRow("id:", id_edit)

        name_edit = QLineEdit(str(getattr(carrier, "name", "") or ""))
        widgets["name"] = name_edit
        self._form_layout.addRow("name:", name_edit)

        for field_name, qtype, default, enum_opts in _get_esdl_attr_fields(carrier):
            if field_name in ("id", "name"):
                continue
            if enum_opts is not None:
                w = QComboBox()
                w.addItems(enum_opts)
                val = getattr(carrier, field_name, None)
                str_val = (
                    getattr(val, "name", str(val)) if val is not None else str(default)
                )
                if str_val in enum_opts:
                    w.setCurrentText(str_val)
            elif qtype == QVariant.Int:
                w = QSpinBox()
                w.setRange(-2_147_483_648, 2_147_483_647)
                live = getattr(carrier, field_name, None)
                w.setValue(live if isinstance(live, int) else 0)
            elif qtype == QVariant.Double:
                w = QDoubleSpinBox()
                w.setRange(-1e15, 1e15)
                w.setDecimals(6)
                live = getattr(carrier, field_name, None)
                w.setValue(live if isinstance(live, float) else 0.0)
            else:
                w = QLineEdit()
                live = getattr(carrier, field_name, None)
                w.setText(str(live) if live is not None else "")
            widgets[field_name] = w
            self._form_layout.addRow(field_name + ":", w)

    # ------------------------------------------------------------------
    # Add / Delete
    # ------------------------------------------------------------------

    def _on_add(self):
        """Show a small picker dialog to choose the carrier type and name."""
        dlg = _AddCarrierDialog(self._carrier_classes, parent=self)
        if dlg.exec_() != QDialog.Accepted:
            return
        class_name, name = dlg.get_data()
        from .esdl_model import add_carrier

        new_carrier = add_carrier(self._energy_system, class_name, name)
        self._populate_tree()
        self._select_carrier_by_id(new_carrier.id)

    def _on_delete(self):
        if self._current_id is None:
            return
        carrier = self._find_carrier(self._current_id)
        label = (
            getattr(carrier, "name", None) or self._current_id
            if carrier
            else self._current_id
        )
        reply = QMessageBox.question(
            self,
            "Delete carrier",
            f"Delete carrier '{label}'?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._pending_deletes.add(self._current_id)
        self._attr_widgets.pop(self._current_id, None)
        self._current_id = None
        self._clear_form()
        self._del_btn.setEnabled(False)
        self._populate_tree()

    # ------------------------------------------------------------------
    # Commit on OK
    # ------------------------------------------------------------------

    def _flush_attrs(self, carrier_id: str):
        """Write current widget values back to the carrier object."""
        from .esdl_model import update_carrier_attrs

        carrier = self._find_carrier(carrier_id)
        if carrier is None:
            return
        widgets = self._attr_widgets.get(carrier_id, {})
        attrs = {}
        for field_name, w in widgets.items():
            if isinstance(w, (QSpinBox, QDoubleSpinBox)):
                attrs[field_name] = w.value()
            elif isinstance(w, QComboBox):
                attrs[field_name] = w.currentText()
            else:
                v = w.text().strip()
                attrs[field_name] = v
        update_carrier_attrs(carrier, attrs)

    def _commit_and_accept(self):
        # Flush any unsaved edits in the currently visible form
        if self._current_id:
            self._flush_attrs(self._current_id)
        # Apply pending deletes
        from .esdl_model import remove_carrier

        for cid in self._pending_deletes:
            remove_carrier(self._energy_system, cid)
        self.accept()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_carrier(self, carrier_id: str):
        from .esdl_model import get_carriers

        for c in get_carriers(self._energy_system):
            if c.id == carrier_id:
                return c
        return None


# =============================================================================
# _AddCarrierDialog — internal helper: pick type + name for a new carrier
# =============================================================================


class _AddCarrierDialog(QDialog):
    """Small dialog: choose carrier class and name before creating."""

    def __init__(self, carrier_classes: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Carrier")
        self.setMinimumWidth(320)
        self._carrier_classes = carrier_classes
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._class_combo = QComboBox()
        self._class_combo.addItems(self._carrier_classes)
        form.addRow("Carrier type:", self._class_combo)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. Supply, MainGrid …")
        form.addRow("Name:", self._name_edit)

        layout.addLayout(form)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def get_data(self) -> tuple:
        """Return (class_name, name)."""
        class_name = self._class_combo.currentText()
        name = self._name_edit.text().strip() or class_name
        return class_name, name
