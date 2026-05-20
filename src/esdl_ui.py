from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit,
    QGroupBox, QCheckBox, QFileDialog,
)
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices

from .esdl_drive.auth import AuthWorker
from .esdl_drive.esdl_drive import ESDLDrive


class ESDLDriveDialog(QDialog):
    """
    Simple dialog that lets the user enter an ESDL Drive resource URL,
    optionally authenticate via Keycloak (PKCE), fetch the ESDL file,
    and parse it into an EnergySystem object.

    on_loaded(energy_system) is called when a file is successfully loaded.
    """

    def __init__(self, parent=None, on_loaded=None):
        super().__init__(parent)
        self.setWindowTitle("Load ESDL from Drive")
        self.setMinimumWidth(540)
        self._token = None
        self._auth_worker = None
        self._drive = ESDLDrive()
        self._on_loaded = on_loaded  # signature: on_loaded(esh, energy_system)
        self._esh = None
        self._energy_system = None
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self):
        root = QVBoxLayout(self)

        # --- ESDL resource URL ---
        resource_group = QGroupBox("ESDL Resource")
        resource_layout = QVBoxLayout(resource_group)
        resource_layout.addWidget(QLabel("ESDL file URL:"))
        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText(
            "http://localhost:9080/store/resource/Users/XXX/file.esdl"
        )
        resource_layout.addWidget(self._url_input)
        root.addWidget(resource_group)

        # --- Authentication ---
        auth_group = QGroupBox("Authentication")
        auth_layout = QVBoxLayout(auth_group)

        self._auth_check = QCheckBox("Requires Keycloak authentication")
        auth_layout.addWidget(self._auth_check)

        kc_row = QHBoxLayout()
        kc_row.addWidget(QLabel("Keycloak realm URL:"))
        self._kc_input = QLineEdit()
        self._kc_input.setPlaceholderText(
            "http://localhost:8080/auth/realms/esdl-mapeditor"
        )
        self._kc_input.setEnabled(False)
        kc_row.addWidget(self._kc_input)
        auth_layout.addLayout(kc_row)

        secret_row = QHBoxLayout()
        secret_row.addWidget(QLabel("Client secret:"))
        self._secret_input = QLineEdit()
        self._secret_input.setPlaceholderText("(leave blank for public clients)")
        self._secret_input.setEchoMode(QLineEdit.Password)
        self._secret_input.setEnabled(False)
        secret_row.addWidget(self._secret_input)
        auth_layout.addLayout(secret_row)

        self._auth_btn = QPushButton("Authenticate in Browser…")
        self._auth_btn.setEnabled(False)
        auth_layout.addWidget(self._auth_btn)

        self._auth_check.toggled.connect(self._kc_input.setEnabled)
        self._auth_check.toggled.connect(self._secret_input.setEnabled)
        self._auth_check.toggled.connect(self._auth_btn.setEnabled)
        self._auth_btn.clicked.connect(self._start_auth)
        root.addWidget(auth_group)

        # --- Status log ---
        self._log_box = QTextEdit()
        self._log_box.setReadOnly(True)
        self._log_box.setFixedHeight(100)
        root.addWidget(self._log_box)

        # --- Save to Drive ---
        save_group = QGroupBox("Save to Drive")
        save_layout = QVBoxLayout(save_group)
        self._save_btn = QPushButton("Sync edits & Save to Drive")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._save_esdl)
        save_layout.addWidget(self._save_btn)
        self._export_btn = QPushButton("Export as .esdl file…")
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export_esdl)
        save_layout.addWidget(self._export_btn)
        root.addWidget(save_group)

        # --- Load button ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._load_btn = QPushButton("Load ESDL")
        self._load_btn.clicked.connect(self._load_esdl)
        btn_row.addWidget(self._load_btn)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _log(self, msg: str):
        self._log_box.append(msg)

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _start_auth(self):
        kc_url = self._kc_input.text().strip()
        if not kc_url:
            self._log("Enter a Keycloak realm URL first.")
            return

        self._set_auth_busy(True)
        self._log("Starting authentication — your browser should open shortly…")

        client_secret = self._secret_input.text().strip() or None
        self._auth_worker = AuthWorker(kc_url, client_secret=client_secret)
        # open_browser is emitted from the worker thread; QDesktopServices
        # must be called on the main thread, so we connect via a slot here.
        self._auth_worker.open_browser.connect(
            lambda url: QDesktopServices.openUrl(QUrl(url))
        )
        self._auth_worker.success.connect(self._on_auth_success)
        self._auth_worker.failed.connect(self._on_auth_failed)
        self._auth_worker.start()

    def _on_auth_success(self, token: str):
        self._token = token
        self._log("Authenticated successfully. You can now load an ESDL file.")
        self._set_auth_busy(False)
        self._auth_btn.setText("Re-authenticate…")

    def _on_auth_failed(self, error: str):
        self._log(f"Authentication failed: {error}")
        self._set_auth_busy(False)

    def _set_auth_busy(self, busy: bool):
        self._auth_btn.setEnabled(not busy)
        self._auth_btn.setText(
            "Waiting for browser login…" if busy else "Authenticate in Browser…"
        )

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def _load_esdl(self):
        url = self._url_input.text().strip()
        if not url:
            self._log("Enter an ESDL URL first.")
            return

        if self._auth_check.isChecked() and self._token is None:
            self._log("Please authenticate first, then click Load.")
            return

        self._log(f"Fetching {url} …")
        self._load_btn.setEnabled(False)

        result = self._drive.get_resource(url, token=self._token)

        self._load_btn.setEnabled(True)

        if 'error' in result:
            self._log(f"Error {result['status']}: {result['error']}")
            return

        esdl_xml = result['content']
        content_type = result.get('content_type', '')
        self._log(f"Received {len(esdl_xml)} bytes  |  Content-Type: {content_type or '(unknown)'}")

        # Guard: if the server returned HTML it's likely an auth redirect
        if 'text/html' in content_type:
            self._log(
                "Server returned HTML instead of ESDL XML — "
                "the resource probably requires authentication.\n"
                f"Preview: {esdl_xml[:300]}"
            )
            return

        self._log("Parsing ESDL…")

        try:
            from esdl.esdl_handler import EnergySystemHandler
            esh = EnergySystemHandler()
            energy_system = esh.load_from_string(esdl_xml)
            self._log(f"Loaded: '{energy_system.name}'")
            self._esh = esh
            self._energy_system = energy_system
            self._save_btn.setEnabled(True)
            self._export_btn.setEnabled(True)
            if callable(self._on_loaded):
                self._on_loaded(esh, energy_system)
        except Exception as exc:
            self._log(f"Failed to parse ESDL: {exc}")

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_esdl(self):
        url = self._url_input.text().strip()
        if not url:
            self._log("No URL to save to.")
            return
        if self._esh is None:
            self._log("No ESDL loaded.")
            return

        # Ask the visualizer (stored on plugin) to sync edits back first
        if callable(getattr(self, '_on_save_sync', None)):
            self._on_save_sync()

        self._log("Serialising ESDL…")
        try:
            xml = self._esh.to_string()
        except Exception as exc:
            self._log(f"Serialisation failed: {exc}")
            return

        self._log(f"Saving to {url} …")
        self._save_btn.setEnabled(False)
        result = self._drive.put_resource(url, xml, token=self._token)
        self._save_btn.setEnabled(True)

        if 'error' in result:
            self._log(f"Save failed {result['status']}: {result['error']}")
        else:
            self._log(f"Saved successfully (HTTP {result['status']}).")

    def _export_esdl(self):
        if self._esh is None:
            self._log("No ESDL loaded.")
            return

        # Suggest a filename derived from the energy system name
        default_name = (self._energy_system.name or "energy_system").replace(" ", "_") + ".esdl"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export ESDL file", default_name, "ESDL files (*.esdl);;XML files (*.xml)"
        )
        if not path:
            return  # user cancelled

        if callable(getattr(self, '_on_save_sync', None)):
            self._on_save_sync()

        try:
            xml = self._esh.to_string()
            with open(path, 'w', encoding='utf-8') as f:
                f.write(xml)
            self._log(f"Exported to {path}")
        except Exception as exc:
            self._log(f"Export failed: {exc}")
