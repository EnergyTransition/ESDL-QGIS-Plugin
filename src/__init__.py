import os
from pathlib import Path

from qgis.PyQt.QtWidgets import QAction, QDialog, QMessageBox
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon

from . import esdl_model
from .esdl_io import export_esdl_file
from .esdl_visualize import ESDLVisualizer, qgs_geom_to_esdl
from .ui.add_asset_dialog import AddAttributesDialog, PickAssetDialog
from .ui.dock import ESDLDock
from .ui.intro_dialog import ESDLIntroDialog
from .ui.map_tools import (
    AssetPickerMapTool,
    LineCaptureMapTool,
    PointCaptureMapTool,
    PolygonCaptureMapTool,
)


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
        self._active_map_tool = None
        self._logged_layer_ids = set()

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
        self._active_map_tool = None
        self._logged_layer_ids.clear()

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
        self._dock.set_add_asset_callback(self._start_add_asset)
        self._dock.set_delete_asset_callback(self._start_delete_asset)
        self._dock.set_close_callback(self._on_dock_closed)
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self._dock)

        for layer in layers:
            self._connect_layer_log_signals(layer)

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
        self._active_map_tool = None
        self._logged_layer_ids.clear()

    def _export(self):
        if self.visualizer is not None:
            self.visualizer.sync_from_layers()
        result = export_esdl_file(self.iface.mainWindow(), self.esh, self.energy_system)
        if result is False:
            self._log_change("Export failed.")
        elif result:
            self._log_change(f"Exported to {result}")
        return bool(result)

    # ------------------------------------------------------------------
    # Action log
    # ------------------------------------------------------------------

    def _log_change(self, msg: str):
        """Append *msg* to the dock's action log, if the dock is open."""
        if self._dock is not None:
            self._dock.append_change(msg)

    def _connect_layer_log_signals(self, layer):
        """
        Hook a layer's attribute/geometry edit signals so direct QGIS
        feature-table edits are recorded in the dock's action log.
        Idempotent: a layer that is already connected is skipped, so this
        is safe to call again for layers created after the initial load
        (e.g. the first asset of a newly-added class).
        """
        if layer is None or layer.id() in self._logged_layer_ids:
            return
        self._logged_layer_ids.add(layer.id())
        layer.attributeValueChanged.connect(
            lambda fid, idx, val, l=layer: self._log_change(
                f"[{l.name()}] feature {fid}: '{l.fields()[idx].name()}' \u2192 {val!r}"
            )
        )
        layer.geometryChanged.connect(
            lambda fid, geom, l=layer: self._log_change(
                f"[{l.name()}] feature {fid}: geometry moved"
            )
        )

    # ------------------------------------------------------------------
    # Add-asset flow
    # ------------------------------------------------------------------

    def _start_add_asset(self):
        """Open the asset-picker dialog, then activate the appropriate map tool."""
        loaded_classes = sorted(self.visualizer.known_classes())
        dlg = PickAssetDialog(loaded_classes, parent=self.iface.mainWindow())
        if dlg.exec_() != QDialog.Accepted:
            return
        pick = dlg.get_data()  # {class_name, name, geom_hint}

        canvas = self.iface.mapCanvas()
        if pick["geom_hint"] == "Line":
            tool = LineCaptureMapTool(canvas)
            hint_msg = "Click vertices on the map; right-click to finish."
        elif pick["geom_hint"] == "Polygon":
            tool = PolygonCaptureMapTool(canvas)
            hint_msg = "Click vertices on the map; right-click to finish."
        else:
            tool = PointCaptureMapTool(canvas)
            hint_msg = "Click the map to place the asset."

        tool.geometry_captured.connect(
            lambda geom: self._on_geometry_captured(geom, pick)
        )
        self._active_map_tool = tool  # keep reference to prevent GC
        canvas.setMapTool(tool)
        self.iface.messageBar().pushMessage(
            "ESDL",
            f"Adding {pick['class_name']} \u2014 {hint_msg}",
            level=0,
            duration=10,
        )

    def _on_geometry_captured(self, qgs_geom, pick: dict):
        """Open the attributes dialog and create the asset in the model."""
        dlg = AddAttributesDialog(pick["class_name"], parent=self.iface.mainWindow())
        if dlg.exec_() != QDialog.Accepted:
            return
        attrs = dlg.get_attrs()

        esdl_geom = qgs_geom_to_esdl(qgs_geom, pick["geom_hint"])
        if esdl_geom is None:
            self.iface.messageBar().pushMessage(
                "ESDL",
                "Could not convert geometry \u2014 asset not added.",
                level=2,
            )
            return

        asset = esdl_model.create_asset_with_geom(
            pick["class_name"], pick["name"], esdl_geom, attrs
        )
        esdl_model.add_default_ports(asset)
        area = self.energy_system.instance[0].area
        esdl_model.attach_to_area(asset, area)
        layer = self.visualizer.add_asset_to_layer(asset, area.name or "")
        self._connect_layer_log_signals(layer)

        self._log_change(f"Added {pick['class_name']} '{pick['name']}'")

    # ------------------------------------------------------------------
    # Delete-asset flow
    # ------------------------------------------------------------------

    def _start_delete_asset(self):
        """Activate the single-click map tool for selecting an asset to delete."""
        tool = AssetPickerMapTool(self.iface.mapCanvas(), self.visualizer)
        tool.asset_selected.connect(self._on_asset_selected_for_delete)
        self._active_map_tool = tool
        self.iface.mapCanvas().setMapTool(tool)
        self.iface.messageBar().pushMessage(
            "ESDL",
            "Click an asset on the map to delete it.",
            level=0,
            duration=8,
        )

    def _on_asset_selected_for_delete(self, asset_id: str):
        """Confirm and delete the selected asset from the model."""
        asset = self.visualizer.get_asset_by_id(asset_id)
        if asset is None:
            return
        asset_name = getattr(asset, "name", None) or asset_id
        reply = QMessageBox.question(
            self.iface.mainWindow(),
            "Delete asset",
            f"Delete '{asset_name}' ({type(asset).__name__})?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        container = asset.eContainer()
        feature = asset.eContainmentFeature()
        if container is not None and feature is not None:
            if feature.many:
                container.eGet(feature).remove(asset)
            else:
                container.eSet(feature, None)
        self.visualizer.refresh_layers()
        self._log_change(f"Deleted {type(asset).__name__} '{asset_name}'")
