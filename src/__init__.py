import os
from pathlib import Path

from qgis.PyQt.QtWidgets import QAction, QDialog, QMessageBox, QPushButton
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt import sip
from qgis.core import Qgis

from . import esdl_model
from .esdl_io import export_esdl_file
from .esdl_visualize import ESDLVisualizer, qgs_geom_to_esdl
from .ui.add_asset_dialog import AddAttributesDialog, PickAssetDialog
from .ui.connection_dialogs import ConnectPortsDialog, DisconnectDialog
from .ui.dock import ESDLDock
from .ui.esdl_browser_dialog import EsdlBrowserDialog
from .ui.intro_dialog import ESDLIntroDialog
from .ui.table_editor_dialog import TableEditorDialog
from .ui.map_tools import (
    AssetPickerMapTool,
    CanvasCancelFilter,
    ConnectAssetsMapTool,
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
        # Active Move Asset edit session: {"layer", "asset_name", "tools", "msg_item"}
        self._move_session = None
        # Switch between QGIS's Vertex Tool and Move Feature tool
        self._switching_move_tool = False

    def initGui(self):
        # Create an action (i.e. a button) with Logo
        icon = QIcon(os.path.join(self.wd, "esdl.png"))
        self.action = QAction(icon, "ESDL QGIS Plugin dev", self.iface.mainWindow())

        self.action.triggered.connect(self.run)

        # Add the action to the toolbar
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        self._finish_move_session()
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
        self._finish_move_session()
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
        self._dock.set_edit_asset_callback(self._start_edit_asset)
        self._dock.set_move_asset_callback(self._start_move_asset)
        self._dock.set_delete_asset_callback(self._start_delete_asset)
        self._dock.set_connect_callback(self._start_connect)
        self._dock.set_disconnect_callback(self._start_disconnect)
        self._dock.set_browse_esdl_callback(self._start_browse_esdl)
        self._dock.set_table_editor_callback(self._start_table_editor)
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
        self._finish_move_session()
        if self.visualizer is not None:
            self.visualizer.remove_layers()
            self.visualizer = None
        self._dock = None
        self.esh = None
        self.energy_system = None
        self._active_map_tool = None
        self._logged_layer_ids.clear()

    def _export(self):
        self._finish_move_session()
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
        Hook a layer's edit signals so QGIS feature edits (Move Asset, or the
        attribute table) are synced to the model and recorded in the dock's
        action log. Only committed edits are logged, so dragging, undo and
        cancelled sessions don't flood the log.
        Idempotent: a layer that is already connected is skipped, so this
        is safe to call again for layers created after the initial load
        (e.g. the first asset of a newly-added class).
        """
        if layer is None or layer.id() in self._logged_layer_ids:
            return
        self._logged_layer_ids.add(layer.id())
        layer.committedAttributeValuesChanges.connect(
            lambda _layer_id, changes, lyr=layer: self._log_committed_attributes(lyr, changes)
        )
        layer.committedGeometriesChanges.connect(
            lambda _layer_id, changes, lyr=layer: self._log_committed_geometries(lyr, changes)
        )
        # Sync the model on every (uncommitted) geometry edit, not only on
        # commit: connection lines then follow the asset while it is dragged,
        # and undo/redo/roll-back re-emit this signal, restoring the model.
        layer.geometryChanged.connect(
            lambda fid, geom, lyr=layer: self._on_layer_geometry_changed(lyr, fid, geom)
        )

    @staticmethod
    def _feature_label(layer, fid) -> str:
        feat = layer.getFeature(fid)
        name = feat["name"] if feat.isValid() else None
        return f"[{layer.name()}] '{name}'" if name else f"[{layer.name()}] feature {fid}"

    def _log_committed_attributes(self, layer, changes):
        """Log attribute edits saved to *layer*; *changes* maps fid -> {field_idx: value}."""
        fields = layer.fields()
        for fid, values in changes.items():
            parts = ", ".join(
                f"'{fields[idx].name()}' \u2192 {val!r}" for idx, val in values.items()
            )
            self._log_change(f"{self._feature_label(layer, fid)}: {parts}")

    def _log_committed_geometries(self, layer, changes):
        """Log geometry edits saved to *layer*; *changes* maps fid -> QgsGeometry."""
        for fid in changes:
            self._log_change(f"{self._feature_label(layer, fid)}: geometry moved")

    def _on_layer_geometry_changed(self, layer, fid, geom):
        """Write an edited feature geometry into the model and redraw connections."""
        if self.visualizer is None:
            return
        if self.visualizer.update_asset_geometry(layer, fid, geom) is not None:
            self.visualizer.refresh_connections()

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

        tool.geometry_captured.connect(lambda geom: self._on_geometry_captured(geom, pick))
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
    # Edit-asset flow
    # ------------------------------------------------------------------

    def _start_edit_asset(self):
        """Activate the single-click map tool for selecting an asset to edit."""
        tool = AssetPickerMapTool(self.iface.mapCanvas(), self.visualizer)
        tool.asset_selected.connect(self._on_asset_selected_for_edit)
        self._active_map_tool = tool
        self.iface.mapCanvas().setMapTool(tool)
        self.iface.messageBar().pushMessage(
            "ESDL",
            "Click an asset on the map to open it in the ESDL Browser. "
            "Right-click cancels.",
            level=0,
            duration=8,
        )

    def _on_asset_selected_for_edit(self, asset_id: str):
        """Open the ESDL Browser navigated directly to the selected asset."""
        asset = self.visualizer.get_asset_by_id(asset_id)
        if asset is None:
            return
        dlg = EsdlBrowserDialog(
            self.energy_system,
            initial_obj=asset,
            parent=self.iface.mainWindow(),
        )
        dlg.exec_()
        for change in dlg.changes:
            self._log_change(change)
        self.visualizer.refresh_layers()

    # ------------------------------------------------------------------
    # Move-asset flow
    # ------------------------------------------------------------------

    def _start_move_asset(self):
        """Activate the single-click map tool for selecting an asset to move."""
        tool = AssetPickerMapTool(self.iface.mapCanvas(), self.visualizer)
        tool.asset_selected.connect(self._on_asset_selected_for_move)
        self._active_map_tool = tool
        self.iface.mapCanvas().setMapTool(tool)
        self.iface.messageBar().pushMessage(
            "ESDL",
            "Hover to highlight an asset, click it to move it. Esc or right-click cancels.",
            level=0,
            duration=8,
        )

    def _on_asset_selected_for_move(self, asset_id: str):
        """
        Put the picked asset's layer in edit mode and hand over to QGIS's
        built-in Vertex Tool (or Move Feature). Geometry edits are synced to
        the model live via _on_layer_geometry_changed; the edit session is
        committed when the user leaves these tools (see _finish_move_session).
        """
        self._finish_move_session()
        layer, fid = self.visualizer.find_feature_for_asset(asset_id)
        asset = self.visualizer.get_asset_by_id(asset_id)
        if layer is None or fid is None or asset is None:
            return
        if not layer.isEditable() and not layer.startEditing():
            self.iface.messageBar().pushMessage(
                "ESDL", f"Layer '{layer.name()}' cannot be edited.", level=2
            )
            return

        asset_name = getattr(asset, "name", None) or asset_id
        self.iface.setActiveLayer(layer)
        layer.selectByIds([fid])
        self._move_session = {
            "layer": layer,
            "asset_name": asset_name,
            "tools": set(),
            "msg_item": None,
            "cancel_filter": None,
        }
        self._activate_move_tool(whole_asset=False)
        canvas = self.iface.mapCanvas()
        canvas.mapToolSet.connect(self._on_map_tool_set_during_move)
        # Esc / right-click are intercepted before QGIS's tools see them.
        cancel_filter = CanvasCancelFilter(canvas)
        cancel_filter.cancel_requested.connect(lambda: self._finish_move_session(revert=True))
        canvas.installEventFilter(cancel_filter)
        canvas.viewport().installEventFilter(cancel_filter)
        self._move_session["cancel_filter"] = cancel_filter
        self._move_session["msg_item"] = self._push_move_message()

    def _move_tool_actions(self):
        return (self.iface.actionVertexToolActiveLayer(), self.iface.actionMoveFeature())

    def _activate_move_tool(self, whole_asset: bool):
        """Switch between QGIS's Vertex Tool and Move Feature tool."""
        if self._move_session is None:
            return
        vertex_action, move_action = self._move_tool_actions()
        self._switching_move_tool = True
        try:
            (move_action if whole_asset else vertex_action).trigger()
        finally:
            self._switching_move_tool = False
        tool = self.iface.mapCanvas().mapTool()
        if tool is not None:
            self._move_session["tools"].add(tool)

    def _push_move_message(self):
        """Show a message bar with tool-switch and Done buttons for the move session."""
        bar = self.iface.messageBar()
        widget = bar.createMessage(
            "ESDL",
            f"Drag a vertex to move it, or switch to "
            "'Move whole asset'. Esc or right-click cancels.",
        )
        buttons = (
            ("Vertex tool", lambda: self._activate_move_tool(whole_asset=False)),
            ("Move whole asset", lambda: self._activate_move_tool(whole_asset=True)),
            ("Done", self._finish_move_session),
            ("Cancel", lambda: self._finish_move_session(revert=True)),
        )
        for label, slot in buttons:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _checked=False, fn=slot: fn())
            widget.layout().addWidget(btn)
        return bar.pushWidget(widget, Qgis.Info)

    def _on_map_tool_set_during_move(self, new_tool, _old_tool=None):
        """End the move session as soon as the user switches to an unrelated tool."""
        if self._switching_move_tool or self._move_session is None:
            return
        if new_tool is not None:
            if new_tool in self._move_session["tools"]:
                return
            if new_tool.action() in self._move_tool_actions():
                self._move_session["tools"].add(new_tool)
                return
        self._finish_move_session()

    def _finish_move_session(self, revert: bool = False):
        """
        End the move session: restore the Pan tool, then commit the edits
        (or roll them back when *revert* is True) and clear the selection.
        """
        session = self._move_session
        if session is None:
            return
        self._move_session = None
        canvas = self.iface.mapCanvas()
        try:
            canvas.mapToolSet.disconnect(self._on_map_tool_set_during_move)
        except TypeError:
            pass
        cancel_filter = session["cancel_filter"]
        if cancel_filter is not None and not sip.isdeleted(cancel_filter):
            canvas.removeEventFilter(cancel_filter)
            canvas.viewport().removeEventFilter(cancel_filter)
            cancel_filter.deleteLater()

        # Leave QGIS's editing tool first so an in-progress drag is dropped
        # before the edit buffer is committed or rolled back.
        if canvas.mapTool() in session["tools"]:
            self.iface.actionPan().trigger()

        layer = session["layer"]
        if not sip.isdeleted(layer):
            # Roll-back re-emits geometryChanged, which restores the model too.
            # Saved edits are logged by the layer's committed* signals.
            if revert:
                if layer.isEditable():
                    layer.rollBack()
            elif layer.isEditable() and not layer.commitChanges():
                errors = "; ".join(layer.commitErrors())
                layer.rollBack()
                self._log_change(f"Move of '{session['asset_name']}' reverted: {errors}")
            layer.removeSelection()

        msg_item = session["msg_item"]
        if msg_item is not None and not sip.isdeleted(msg_item):
            self.iface.messageBar().popWidget(msg_item)

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
            "Click an asset on the map to delete it. Right-click cancels.",
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

    # ------------------------------------------------------------------
    # Add-connection flow
    # ------------------------------------------------------------------

    def _start_connect(self):
        """Activate the two-click rubber-band tool for connecting asset ports."""
        tool = ConnectAssetsMapTool(self.iface.mapCanvas(), self.visualizer)
        tool.assets_selected.connect(self._on_assets_selected)
        self._active_map_tool = tool
        self.iface.mapCanvas().setMapTool(tool)
        self.iface.messageBar().pushMessage(
            "ESDL",
            "Click the source asset, then the target asset. Right-click undoes the "
            "source pick (or cancels if none is picked); Esc cancels.",
            level=0,
            duration=8,
        )

    def _on_assets_selected(self, source_id: str, target_id: str):
        """Open the ConnectPortsDialog and wire the chosen ports."""
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
        self._log_change(f"Connected '{out_name}' \u2194 '{in_name}'")

    # ------------------------------------------------------------------
    # Delete-connection flow
    # ------------------------------------------------------------------

    def _start_disconnect(self):
        """Activate the single-click map tool for selecting an asset to disconnect."""
        tool = AssetPickerMapTool(self.iface.mapCanvas(), self.visualizer)
        tool.asset_selected.connect(self._on_asset_selected_for_disconnect)
        self._active_map_tool = tool
        self.iface.mapCanvas().setMapTool(tool)
        self.iface.messageBar().pushMessage(
            "ESDL",
            "Click an asset on the map to remove one of its connections. "
            "Right-click cancels.",
            level=0,
            duration=8,
        )

    def _on_asset_selected_for_disconnect(self, asset_id: str):
        """Open DisconnectDialog and remove the chosen connection."""
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
        in_name = getattr(in_asset, "name", None) or (type(in_asset).__name__ if in_asset else "?")
        self._log_change(f"Disconnected '{out_name}' \u2194 '{in_name}'")

    # ------------------------------------------------------------------
    # Browse-ESDL flow
    # ------------------------------------------------------------------

    def _start_browse_esdl(self):
        """Open the ESDL Browser dialog, log user changes, and refresh layers."""
        self._finish_move_session()
        dlg = EsdlBrowserDialog(self.energy_system, parent=self.iface.mainWindow())
        dlg.exec_()
        # dlg.changes reflects edits already flushed to the model (attribute
        # edits are flushed on navigation; containment/xref edits apply
        # immediately) — logged regardless of Accept/Cancel for that reason.
        for change in dlg.changes:
            self._log_change(change)
        self.visualizer.refresh_layers()

    # ------------------------------------------------------------------
    # Table Editor flow
    # ------------------------------------------------------------------

    def _start_table_editor(self):
        """Open the Table Editor dialog, log on accept, and refresh layers."""
        self._finish_move_session()
        dlg = TableEditorDialog(self.energy_system, parent=self.iface.mainWindow())
        if dlg.exec_() == QDialog.Accepted:
            self._log_change("Edited asset(s) via Table Editor")
        self.visualizer.refresh_layers()
