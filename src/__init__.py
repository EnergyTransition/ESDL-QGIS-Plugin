import os
from pathlib import Path

from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon

from .esdl_ui import ESDLSourceDialog, ESDLEditDock, ESDLDriveDialog
from .esdl_visualize import ESDLVisualizer


def classFactory(iface):
    return ESDLPlugin(iface)


class ESDLPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.wd = Path(__file__).parent.absolute()
        self.energy_system = None
        self.visualizer = None
        self._dialog = None
        self._edit_dock = None

    def initGui(self):
        # Create an action (i.e. a button) with Logo
        icon = QIcon(os.path.join(self.wd, "esdl.png"))
        self.action = QAction(icon, "Load ESDL", self.iface.mainWindow())

        self.action.triggered.connect(self.run)

        # Add the action to the toolbar
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        self.iface.removeToolBarIcon(self.action)
        if self._edit_dock is not None:
            self.iface.removeDockWidget(self._edit_dock)
            self._edit_dock = None
        del self.action

    def run(self):
        # NOTE: check multiple ESDL load support?
        def on_loaded(esh, energy_system, source_label="", **_):
            self.energy_system = energy_system
            if self.visualizer is not None:
                self.visualizer.remove_layers()
            self.visualizer = ESDLVisualizer(esh, energy_system)
            layers = self.visualizer.create_and_add_layers()

            # Replace the edit dock (close previous if any)
            if self._edit_dock is not None:
                self.iface.removeDockWidget(self._edit_dock)
            self._edit_dock = ESDLEditDock(
                esh,
                energy_system,
                source_label=source_label,
                parent=self.iface.mainWindow(),
            )
            self._edit_dock.set_sync_callback(self.visualizer.sync_from_layers)
            self.iface.addDockWidget(Qt.RightDockWidgetArea, self._edit_dock)

            # Wire QGIS layer edit signals to the dock's change log
            for layer in layers:
                layer.attributeValueChanged.connect(
                    lambda fid, idx, val, l=layer: self._edit_dock.append_change(
                        f"[{l.name()}] feature {fid}: "
                        f"'{l.fields()[idx].name()}' \u2192 {val!r}"
                    )
                )
                layer.geometryChanged.connect(
                    lambda fid, geom, l=layer: self._edit_dock.append_change(
                        f"[{l.name()}] feature {fid}: geometry moved"
                    )
                )

            self.iface.messageBar().pushMessage(
                "ESDL",
                f"'{energy_system.name}' loaded \u2014 {len(layers)} layer(s) added.",
                level=0,
            )

        def open_drive():
            self._dialog = ESDLDriveDialog(
                parent=self.iface.mainWindow(),
                on_loaded=on_loaded,
            )
            self._dialog.show()  # non-modal: keep open while editing on the map

        self._dialog = None
        chooser = ESDLSourceDialog(
            parent=self.iface.mainWindow(),
            on_loaded=on_loaded,
            on_drive_requested=open_drive,
        )
        chooser.exec()
