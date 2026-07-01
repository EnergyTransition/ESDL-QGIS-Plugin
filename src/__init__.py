import os
from pathlib import Path

from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon

from .esdl_ui import ESDLSourceDialog, ESDLEditDock, ESDLDriveDialog
from .esdl_visualize import ESDLVisualizer
from . import esdl_model


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
        self._active_map_tool = None

    def initGui(self):
        # Create an action (i.e. a button) with Logo
        icon = QIcon(os.path.join(self.wd, "esdl.png"))
        self.action = QAction(icon, "ESDL plugin", self.iface.mainWindow())

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
            self._edit_dock.set_add_asset_callback(self._start_add_asset)
            self._edit_dock.set_connect_callback(self._start_connect)
            self._edit_dock.set_disconnect_callback(self._start_disconnect)
            self._edit_dock.set_refresh_layers_callback(self.visualizer.refresh_layers)
            self._edit_dock.set_edit_asset_callback(self._start_edit_asset)
            self._edit_dock.set_delete_asset_callback(self._start_delete_asset)
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

    # ------------------------------------------------------------------
    # Add-asset flow
    # ------------------------------------------------------------------

    def _start_add_asset(self):
        """Open the asset-picker dialog, then activate the appropriate map tool."""
        from qgis.PyQt.QtWidgets import QDialog
        from .esdl_ui import (
            PickAssetDialog,
            PointCaptureMapTool,
            LineCaptureMapTool,
            PolygonCaptureMapTool,
        )

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
            f"Adding {pick['class_name']} — {hint_msg}",
            level=0,
            duration=10,
        )

    def _on_geometry_captured(self, qgs_geom, pick: dict):
        """Open the attributes dialog and create the asset in the model."""
        from qgis.PyQt.QtWidgets import QDialog
        from .esdl_ui import AddAttributesDialog
        from .esdl_visualize import qgs_geom_to_esdl

        dlg = AddAttributesDialog(pick["class_name"], parent=self.iface.mainWindow())
        if dlg.exec_() != QDialog.Accepted:
            return
        attrs = dlg.get_attrs()

        esdl_geom = qgs_geom_to_esdl(qgs_geom, pick["geom_hint"])
        if esdl_geom is None:
            self.iface.messageBar().pushMessage(
                "ESDL",
                "Could not convert geometry — asset not added.",
                level=2,
            )
            return

        asset = esdl_model.create_asset_with_geom(
            pick["class_name"], pick["name"], esdl_geom, attrs
        )
        esdl_model.add_default_ports(asset)
        area = self.energy_system.instance[0].area
        esdl_model.attach_to_area(asset, area)
        self.visualizer.add_asset_to_layer(asset, area.name or "")

        if self._edit_dock:
            self._edit_dock.append_change(
                f"Added {pick['class_name']} '{pick['name']}'"
            )

    # ------------------------------------------------------------------
    # Connect-assets flow
    # ------------------------------------------------------------------

    def _start_connect(self):
        """Activate the two-click rubber-band tool for connecting asset ports."""
        from .esdl_ui import ConnectAssetsMapTool

        tool = ConnectAssetsMapTool(self.iface.mapCanvas(), self.visualizer)
        tool.assets_selected.connect(self._on_assets_selected)
        self.iface.mapCanvas().setMapTool(tool)
        self.iface.messageBar().pushMessage(
            "ESDL",
            "Click the source asset, then the target asset.",
            level=0,
            duration=8,
        )

    # ------------------------------------------------------------------
    # Disconnect flow
    # ------------------------------------------------------------------

    def _start_disconnect(self):
        """Activate the single-click map tool for selecting an asset to disconnect."""
        from .esdl_ui import EditAssetMapTool

        tool = EditAssetMapTool(self.iface.mapCanvas(), self.visualizer)
        tool.asset_selected.connect(self._on_asset_selected_for_disconnect)
        self._active_map_tool = tool
        self.iface.mapCanvas().setMapTool(tool)
        self.iface.messageBar().pushMessage(
            "ESDL",
            "Click an asset on the map to remove one of its connections.",
            level=0,
            duration=8,
        )

    def _on_asset_selected_for_disconnect(self, asset_id: str):
        """Open DisconnectDialog and remove the chosen connection."""
        from qgis.PyQt.QtWidgets import QDialog
        from .esdl_ui import DisconnectDialog

        asset = self.visualizer.get_asset_by_id(asset_id)
        if asset is None:
            return

        dlg = DisconnectDialog(asset, parent=self.iface.mainWindow())
        if dlg.exec_() != QDialog.Accepted:
            return

        out_port, in_port = dlg.get_selected_connection()
        if out_port is None or in_port is None:
            return

        out_asset, in_asset = dlg.get_connection_assets()
        esdl_model.disconnect_ports(out_port, in_port)
        self.visualizer.refresh_layers()

        out_name = getattr(out_asset, "name", None) or (
            type(out_asset).__name__ if out_asset else "?"
        )
        in_name = getattr(in_asset, "name", None) or (
            type(in_asset).__name__ if in_asset else "?"
        )
        if self._edit_dock:
            self._edit_dock.append_change(
                f"Disconnected '{out_name}' \u2194 '{in_name}'"
            )

    def _on_assets_selected(self, source_id: str, target_id: str):
        """Open the ConnectPortsDialog and wire the chosen ports."""
        from qgis.PyQt.QtWidgets import QDialog
        from .esdl_ui import ConnectPortsDialog

        src = self.visualizer.get_asset_by_id(source_id)
        tgt = self.visualizer.get_asset_by_id(target_id)
        if src is None or tgt is None:
            return

        dlg = ConnectPortsDialog(src, tgt, parent=self.iface.mainWindow())
        if dlg.exec_() != QDialog.Accepted:
            return

        out_port, in_port = dlg.get_selected_ports()
        if out_port is None or in_port is None:
            return

        out_asset = dlg.get_out_asset()
        in_asset = tgt if out_asset is src else src

        esdl_model.connect_ports(out_port, in_port)
        self.visualizer.add_connection_to_layer(out_asset, in_asset)

        out_name = getattr(out_asset, "name", None) or type(out_asset).__name__
        in_name = getattr(in_asset, "name", None) or type(in_asset).__name__
        if self._edit_dock:
            self._edit_dock.append_change(f"Connected '{out_name}' \u2194 '{in_name}'")

    # ------------------------------------------------------------------
    # Edit-asset flow
    # ------------------------------------------------------------------

    def _start_edit_asset(self):
        """Activate the single-click map tool for selecting an asset to edit."""
        from .esdl_ui import EditAssetMapTool

        tool = EditAssetMapTool(self.iface.mapCanvas(), self.visualizer)
        tool.asset_selected.connect(self._on_asset_selected_for_edit)
        self._active_map_tool = tool
        self.iface.mapCanvas().setMapTool(tool)
        self.iface.messageBar().pushMessage(
            "ESDL",
            "Click an asset on the map to open it in the ESDL Browser.",
            level=0,
            duration=8,
        )

    def _on_asset_selected_for_edit(self, asset_id: str):
        """Open the ESDL Browser navigated directly to the selected asset."""
        from .esdl_ui import EsdlBrowserDialog

        asset = self.visualizer.get_asset_by_id(asset_id)
        if asset is None:
            return
        dlg = EsdlBrowserDialog(
            self.energy_system,
            initial_obj=asset,
            parent=self.iface.mainWindow(),
        )
        dlg.exec_()
        asset_name = getattr(asset, "name", None) or asset_id
        if self._edit_dock:
            self._edit_dock.append_change(f"Edited '{asset_name}' in ESDL Browser")
        self.visualizer.refresh_layers()

    # ------------------------------------------------------------------
    # Delete-asset flow
    # ------------------------------------------------------------------

    def _start_delete_asset(self):
        """Activate the single-click map tool for selecting an asset to delete."""
        from .esdl_ui import EditAssetMapTool

        tool = EditAssetMapTool(self.iface.mapCanvas(), self.visualizer)
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
        from qgis.PyQt.QtWidgets import QMessageBox

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
        if self._edit_dock:
            self._edit_dock.append_change(
                f"Deleted {type(asset).__name__} '{asset_name}'"
            )
        self.visualizer.refresh_layers()
