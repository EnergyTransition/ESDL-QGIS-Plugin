import os
from pathlib import Path

from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon


def classFactory(iface):
    return ESDLPlugin(iface)

class ESDLPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.wd = Path(__file__).parent.absolute()
        self.energy_system = None
        self.visualizer = None
        self._dialog = None

    def initGui(self):
        # Create an action (i.e. a button) with Logo
        icon = QIcon(os.path.join(self.wd, 'esdl.png'))
        self.action = QAction(icon, 'Load ESDL from Drive', self.iface.mainWindow())

        self.action.triggered.connect(self.run)
        
        # Add the action to the toolbar
        self.iface.addToolBarIcon(self.action)

      
    def unload(self):
        self.iface.removeToolBarIcon(self.action)
        del self.action

    def run(self):
        from .esdl_wizard import ESDLWizard
        from .esdl_visualize import ESDLVisualizer

        def on_loaded(esh, energy_system, on_change=None):
            self.energy_system = energy_system
            if self.visualizer is not None:
                self.visualizer.remove_layers()
            self.visualizer = ESDLVisualizer(esh, energy_system)
            layers = self.visualizer.create_and_add_layers()
            # Give the wizard a way to sync edits before export/save
            self._dialog.sync_callback = self.visualizer.sync_from_layers

            # Wire QGIS layer edit signals to the change log
            if callable(on_change):
                for layer in layers:
                    layer.attributeValueChanged.connect(
                        lambda fid, idx, val, l=layer: on_change(
                            f"[{l.name()}] feature {fid}: "
                            f"'{l.fields()[idx].name()}' → {val!r}"
                        )
                    )
                    layer.geometryChanged.connect(
                        lambda fid, geom, l=layer: on_change(
                            f"[{l.name()}] feature {fid}: geometry moved"
                        )
                    )

            self.iface.messageBar().pushMessage(
                "ESDL",
                f"'{energy_system.name}' loaded — {len(layers)} layer(s) added.",
                level=0,
            )

        self._dialog = ESDLWizard(
            parent=self.iface.mainWindow(),
            on_loaded=on_loaded,
        )
        self._dialog.show()  # non-modal: keep open while editing

