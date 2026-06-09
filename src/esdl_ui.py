import os
from datetime import datetime

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
)
from qgis.PyQt.QtCore import Qt

from .esdl_drive.auth import PasswordAuthWorker
from .esdl_drive.esdl_drive import ESDLDrive

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
