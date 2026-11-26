"""
Entry-point dialog shown when the ESDL QGIS Plugin starts: a short introduction
to what the plugin can do, plus buttons to load a local .esdl file or create a
new, empty one.
"""

from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QPushButton,
)

from ..esdl_io import create_new_esdl, open_esdl_file

_INTRO_TEXT = (
    "This plugin loads or creates an ESDL energy system, visualizes its "
    "assets and connections as QGIS layers, and lets you export your changes "
    "to a .esdl file."
)

_TIP_TEXT = (
    "<b>Tip:</b> For the smoothest experience, edit ESDL "
    "with the ESDL plugin tools where possible. This doesn't apply to styling, "
    "which you can freely do with QGIS's own tools."
)


class ESDLIntroDialog(QDialog):
    """
    Shown on plugin start. After exec_(), check loaded_data: it is set to
    (esh, energy_system, source_label) if a file was loaded or a new energy
    system was created, else None.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ESDL QGIS Plugin")
        self.setMinimumWidth(360)
        self.loaded_data = None
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)

        intro_lbl = QLabel(_INTRO_TEXT)
        intro_lbl.setWordWrap(True)
        text_layout.addWidget(intro_lbl)

        tip_lbl = QLabel(_TIP_TEXT)
        tip_lbl.setWordWrap(True)
        text_layout.addWidget(tip_lbl)

        root.addLayout(text_layout)

        load_btn = QPushButton("Open an ESDL file…")
        load_btn.setMinimumHeight(40)
        load_btn.clicked.connect(self._open_local)
        root.addWidget(load_btn)

        new_btn = QPushButton("Create a new ESDL file")
        new_btn.setMinimumHeight(40)
        new_btn.setToolTip(
            "Start from an empty energy system. Rename it in Browse ESDL and "
            "save it with 'Export as .esdl file'."
        )
        new_btn.clicked.connect(self._create_new)
        root.addWidget(new_btn)

    def _open_local(self):
        loaded = open_esdl_file(parent=self)
        if loaded is not None:
            self.loaded_data = loaded
            self.accept()

    def _create_new(self):
        created = create_new_esdl(parent=self)
        if created is not None:
            self.loaded_data = created
            self.accept()
