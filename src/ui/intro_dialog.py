"""
Entry-point dialog shown when the ESDL plugin starts: a short introduction
to what the plugin can do, plus a button to load a local .esdl file.
"""

from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QPushButton,
)

from ..esdl_io import open_esdl_file

_INTRO_TEXT = (
    "This plugin loads an ESDL energy system file, visualizes its assets "
    "and connections as QGIS layers, and lets you export your changes back "
    "to a .esdl file."
)


class ESDLIntroDialog(QDialog):
    """
    Shown on plugin start. After exec_(), check loaded_data: it is set to
    (esh, energy_system, source_label) if a file was loaded, else None.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ESDL plugin")
        self.setMinimumWidth(360)
        self.loaded_data = None
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)

        intro_lbl = QLabel(_INTRO_TEXT)
        intro_lbl.setWordWrap(True)
        root.addWidget(intro_lbl)

        load_btn = QPushButton("Open local .esdl file…")
        load_btn.setMinimumHeight(40)
        load_btn.clicked.connect(self._open_local)
        root.addWidget(load_btn)

    def _open_local(self):
        loaded = open_esdl_file(parent=self)
        if loaded is not None:
            self.loaded_data = loaded
            self.accept()
