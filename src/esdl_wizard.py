"""
Three-page QWizard for the ESDL Drive plugin.

  Page 1 – Authentication   : username / password  →  Keycloak token
  Page 2 – Browse           : tree of ESDL Drive files  →  user picks one
  Page 3 – Load & Edit      : loads ESDL into QGIS, logs changes, exports

Default connection settings are read from environment variables:
  ESDL_DRIVE_URL   (default: http://localhost:9080)
  ESDL_KC_REALM    (default: http://localhost:8080/auth/realms/esdl-mapeditor)
  ESDL_KC_CLIENT   (default: cdo-mondaine)
  ESDL_KC_SECRET   (default: "")
"""

import os

from qgis.PyQt.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QFileDialog, QGroupBox,
)
from qgis.PyQt.QtCore import Qt

from .esdl_drive.auth import PasswordAuthWorker
from .esdl_drive.esdl_drive import ESDLDrive

_DEFAULT_DRIVE_URL = os.environ.get('ESDL_DRIVE_URL', 'http://localhost:9080')
_DEFAULT_KC_REALM  = os.environ.get('ESDL_KC_REALM',  'http://localhost:8080/auth/realms/esdl-mapeditor')
_DEFAULT_KC_CLIENT = os.environ.get('ESDL_KC_CLIENT', 'cdo-mondaine')
_DEFAULT_KC_SECRET = os.environ.get('ESDL_KC_SECRET', '329ab44b-087b-44d2-89b7-da139d32a7b1')

# ── IDs used by wizard.setField / wizard.field ────────────────────────────────
PAGE_AUTH   = 0
PAGE_BROWSE = 1
PAGE_LOAD   = 2


# =============================================================================
# Wizard
# =============================================================================

class ESDLWizard(QWizard):
    """
    on_loaded(esh, energy_system, on_change) is called after a successful
    ESDL parse. on_change(msg) can be called to append to the change log.
    """

    def __init__(self, parent=None, on_loaded=None):
        super().__init__(parent)
        self.setWindowTitle("Load ESDL from Drive")
        self.setMinimumSize(640, 520)
        self.setOption(QWizard.NoBackButtonOnStartPage, True)

        # Shared state passed between pages
        self.token          = None
        self.drive_url      = _DEFAULT_DRIVE_URL
        self.selected_path  = None   # e.g. "/Users/alice/file.esdl"
        self.esh            = None
        self.energy_system  = None
        self._on_loaded     = on_loaded
        self._drive         = ESDLDrive()

        self._auth_page  = _AuthPage(self)
        self._browse_page = _BrowsePage(self)
        self._load_page  = _LoadPage(self)

        self.addPage(self._auth_page)
        self.addPage(self._browse_page)
        self.addPage(self._load_page)


# =============================================================================
# Page 1 – Authentication
# =============================================================================

class _AuthPage(QWizardPage):

    def __init__(self, wizard: ESDLWizard):
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

        # ── Advanced / connection settings ──────────────────────────────────
        adv = QGroupBox("Connection settings")
        adv_layout = QVBoxLayout(adv)

        self._drive_input = self._row(adv_layout, "ESDL Drive URL:", _DEFAULT_DRIVE_URL)
        self._kc_input    = self._row(adv_layout, "Keycloak realm URL:", _DEFAULT_KC_REALM)
        self._client_input = self._row(adv_layout, "Client ID:", _DEFAULT_KC_CLIENT)
        self._secret_input = self._row(adv_layout, "Client secret:", _DEFAULT_KC_SECRET,
                                       echo=QLineEdit.Password)
        root.addWidget(adv)

        # ── Credentials ─────────────────────────────────────────────────────
        cred = QGroupBox("Credentials")
        cred_layout = QVBoxLayout(cred)
        self._user_input = self._row(cred_layout, "Username:", "")
        self._pass_input = self._row(cred_layout, "Password:", "",
                                     echo=QLineEdit.Password)
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

    # ── QWizardPage protocol ─────────────────────────────────────────────────

    def isComplete(self) -> bool:
        return self._wiz.token is not None

    # ── Auth flow ────────────────────────────────────────────────────────────

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
        self._wiz.drive_url = self._drive_input.text().strip().rstrip('/')
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

    def __init__(self, wizard: ESDLWizard):
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

    # ── QWizardPage protocol ─────────────────────────────────────────────────

    def initializePage(self):
        self._load_tree()

    def isComplete(self) -> bool:
        return self._wiz.selected_path is not None

    # ── Tree logic ───────────────────────────────────────────────────────────

    def _on_selection(self):
        items = self._tree.selectedItems()
        if items:
            path = items[0].data(0, Qt.UserRole)
            if path:  # None means folder
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

        params = {
            'operation': 'get_node',
            'id': '#',
            'depth':3
        }
        result = self._wiz._drive.browse_cdo(token=self._wiz.token, params=params)
        if 'error' in result:
            self._status.setText(f"Error: {result['error']}")
            return

        data = result.get('json', {})
        self._populate(self._tree.invisibleRootItem(), data)
        self._tree.expandAll()
        self._status.setText("Select a .esdl file, then click Next.")

    def _populate(self, parent: QTreeWidgetItem, data):
        """Recursively build the tree from a jstree-format JSON response."""
        if isinstance(data, list):
            for entry in data:
                self._add_entry(parent, entry)
        elif isinstance(data, dict):
            # Single node — recurse into its children list if present
            children = data.get('children')
            if isinstance(children, list):
                for entry in children:
                    self._add_entry(parent, entry)
            else:
                self._add_entry(parent, data)

    def _add_entry(self, parent: QTreeWidgetItem, entry: dict):
        if not isinstance(entry, dict):
            return

        # jstree format uses 'text' for display name and 'id' for the path
        name = (entry.get('text') or entry.get('name')
                or str(entry.get('id', '?')).split('/')[-1] or '?')
        path = entry.get('id') or entry.get('path') or entry.get('uri') or ''
        is_file = name.endswith('.esdl')

        item = QTreeWidgetItem(parent, [name])
        item.setData(0, Qt.UserRole, path if is_file else None)

        # 'children' in jstree is a bool flag when not yet loaded,
        # or a list when depth > 0 pre-loads them.
        children = entry.get('children')
        if isinstance(children, list):
            for child in children:
                self._add_entry(item, child)


# =============================================================================
# Page 3 – Load & Edit
# =============================================================================

class _LoadPage(QWizardPage):

    def __init__(self, wizard: ESDLWizard):
        super().__init__()
        self._wiz = wizard
        self.setTitle("Step 3 of 3 – Load & Edit")
        self.setSubTitle(
            "The ESDL file is loaded into QGIS as map layers. "
            "Edit features directly on the map. Changes appear in the log below."
        )
        self.setFinalPage(True)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)

        self._info = QLabel("")
        self._info.setWordWrap(True)
        root.addWidget(self._info)

        # Change log
        log_group = QGroupBox("Change log")
        log_layout = QVBoxLayout(log_group)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        log_layout.addWidget(self._log)
        root.addWidget(log_group)

        # Export
        export_row = QHBoxLayout()
        export_row.addStretch()
        self._export_btn = QPushButton("Export as .esdl file…")
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export)
        export_row.addWidget(self._export_btn)
        root.addLayout(export_row)

    # ── QWizardPage protocol ─────────────────────────────────────────────────

    def initializePage(self):
        self._load_esdl()

    # ── Load ─────────────────────────────────────────────────────────────────

    def _load_esdl(self):
        path = self._wiz.selected_path
        if not path:
            self._info.setText("No file selected.")
            return

        resource_url = f"{self._wiz.drive_url}/store/resource{path}"
        self._info.setText(f"Fetching {resource_url} …")

        result = self._wiz._drive.get_resource(resource_url, token=self._wiz.token)
        if 'error' in result:
            self._info.setText(f"Fetch error: {result['error']}")
            return

        if 'text/html' in result.get('content_type', ''):
            self._info.setText("Server returned HTML — authentication may have expired.")
            return

        try:
            from esdl.esdl_handler import EnergySystemHandler
            esh = EnergySystemHandler()
            es  = esh.load_from_string(result['content'])
            self._wiz.esh = esh
            self._wiz.energy_system = es
            self._info.setText(
                f"Loaded '{es.name}'. Edit features on the map, then export when done."
            )
            self._export_btn.setEnabled(True)
            self._append_log(f"ESDL loaded: '{es.name}' from {path}")
            if callable(self._wiz._on_loaded):
                self._wiz._on_loaded(esh, es, on_change=self._append_log)
        except Exception as exc:
            self._info.setText(f"Parse error: {exc}")

    # ── Change log ───────────────────────────────────────────────────────────

    def _append_log(self, message: str):
        from datetime import datetime
        ts = datetime.now().strftime('%H:%M:%S')
        self._log.append(f"[{ts}]  {message}")

    # ── Export ───────────────────────────────────────────────────────────────

    def _export(self):
        esh = self._wiz.esh
        es  = self._wiz.energy_system
        if esh is None:
            return

        default_name = (es.name or "energy_system").replace(" ", "_") + ".esdl"
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Export ESDL file", default_name, "ESDL files (*.esdl);;All files (*)"
        )
        if not save_path:
            return

        try:
            sync = getattr(self._wiz, 'sync_callback', None)
            if callable(sync):
                sync()
            xml = esh.to_string()
            with open(save_path, 'w', encoding='utf-8') as f:
                f.write(xml)
            self._append_log(f"Exported to {save_path}")
        except Exception as exc:
            self._append_log(f"Export failed: {exc}")
