import os
from pathlib import Path

from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon

from .esdl_io import export_esdl_file
from .esdl_visualize import ESDLVisualizer
from .ui.dock import ESDLDock
from .ui.intro_dialog import ESDLIntroDialog


def classFactory(iface):
    return ESDLPlugin(iface)


class ESDLPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.wd = Path(__file__).parent.absolute()
        self.esh = None
        self.energy_system = None
        self.visualizer = None
        self._dock = None

    def initGui(self):
        # Create an action (i.e. a button) with Logo
        icon = QIcon(os.path.join(self.wd, "esdl.png"))
        self.action = QAction(icon, "ESDL plugin", self.iface.mainWindow())

        self.action.triggered.connect(self.run)

        # Add the action to the toolbar
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        self.iface.removeToolBarIcon(self.action)
        if self._dock is not None:
            self.iface.removeDockWidget(self._dock)
            self._dock = None
        if self.visualizer is not None:
            self.visualizer.remove_layers()
            self.visualizer = None
        del self.action

    def run(self):
        # Guard: only one ESDL session at a time — require the existing one
        # to be exported/closed before a new one can be loaded.
        if self.energy_system is not None:
            self._prompt_close_existing()
            return
        self._load_new()

    def _prompt_close_existing(self):
        energy_system = self.energy_system
        if energy_system is None:
            return
        reply = QMessageBox.question(
            self.iface.mainWindow(),
            "ESDL already loaded",
            f"'{energy_system.name}' is already loaded. Export and close it "
            "before loading another ESDL.",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if reply == QMessageBox.Cancel:
            return
        if reply == QMessageBox.Save:
            self._export()
        self._close_current()

    def _close_current(self):
        if self._dock is not None:
            self.iface.removeDockWidget(self._dock)
            self._dock = None
        if self.visualizer is not None:
            self.visualizer.remove_layers()
            self.visualizer = None
        self.esh = None
        self.energy_system = None

    def _load_new(self):
        dlg = ESDLIntroDialog(parent=self.iface.mainWindow())
        dlg.exec_()
        if dlg.loaded_data is None:
            return
        esh, energy_system, source_label = dlg.loaded_data
        self.esh = esh
        self.energy_system = energy_system

        self.visualizer = ESDLVisualizer(esh, energy_system)
        layers = self.visualizer.create_and_add_layers()

        self._dock = ESDLDock(
            energy_system,
            source_label=source_label,
            parent=self.iface.mainWindow(),
        )
        self._dock.set_export_callback(self._export)
        self._dock.set_close_callback(self._on_dock_closed)
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self._dock)

        self.iface.messageBar().pushMessage(
            "ESDL",
            f"'{energy_system.name}' loaded \u2014 {len(layers)} layer(s) added.",
            level=0,
        )

    def _on_dock_closed(self):
        """Called after the user confirms closing the ESDL dock (closeEvent already accepted)."""
        if self.visualizer is not None:
            self.visualizer.remove_layers()
            self.visualizer = None
        self._dock = None
        self.esh = None
        self.energy_system = None

    def _export(self):
        if self.visualizer is not None:
            self.visualizer.sync_from_layers()
        export_esdl_file(self.iface.mainWindow(), self.esh, self.energy_system)
