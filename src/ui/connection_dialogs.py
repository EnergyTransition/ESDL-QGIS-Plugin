"""
Dialogs for the Add Connection / Delete Connection flows: pick which ports
of two assets to wire together, or pick an existing connection on an asset
to remove.
"""

from esdl import esdl

from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QVBoxLayout,
)
from qgis.PyQt.QtCore import Qt

from ..esdl_model import get_asset_connections, get_connectable_ports


class ConnectPortsDialog(QDialog):
    """
    Lets the user choose which port of Asset A connects to which port of
    Asset B. All ports (InPort and OutPort) for both assets are shown.

    The first selection constrains the second: selecting an OutPort on one
    side disables all OutPorts on the other side, and vice-versa.

    Call *get_selected_ports()* after QDialog.Accepted to retrieve the
    normalized *(out_port, in_port)* pair. Call *get_out_asset()* to find
    which asset owns the OutPort (used for map arrow direction).
    """
 
    def __init__(self, source_asset, target_asset, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect Ports")
        self.setMinimumWidth(280)
        self._source = source_asset
        self._target = target_asset
        self._all_ports_a = []
        self._all_ports_b = []
        self._ok_btn = None
        self._setup_ui()

    def _setup_ui(self):
        out_a, in_a = get_connectable_ports(self._source)
        out_b, in_b = get_connectable_ports(self._target)
        self._all_ports_a = out_a + in_a
        self._all_ports_b = out_b + in_b

        src_name = getattr(self._source, "name", None) or type(self._source).__name__
        tgt_name = getattr(self._target, "name", None) or type(self._target).__name__

        layout = QVBoxLayout(self)

        # Two-column layout: Asset A | ↔ | Asset B
        cols = QHBoxLayout()

        col_a = QVBoxLayout()
        col_a.addWidget(QLabel(f"<b>{src_name}</b>"))
        self._combo_a = QComboBox()
        for p in self._all_ports_a:
            label = f"{getattr(p, 'name', '') or type(p).__name__} [{type(p).__name__}]"
            self._combo_a.addItem(label)
        col_a.addWidget(self._combo_a)
        cols.addLayout(col_a)

        arrow_lbl = QLabel("↔")
        arrow_lbl.setAlignment(Qt.AlignCenter)
        cols.addWidget(arrow_lbl)

        col_b = QVBoxLayout()
        col_b.addWidget(QLabel(f"<b>{tgt_name}</b>"))
        self._combo_b = QComboBox()
        for p in self._all_ports_b:
            label = f"{getattr(p, 'name', '') or type(p).__name__} [{type(p).__name__}]"
            self._combo_b.addItem(label)
        col_b.addWidget(self._combo_b)
        cols.addLayout(col_b)

        layout.addLayout(cols)

        if not self._all_ports_a:
            layout.addWidget(QLabel(f"⚠ '{src_name}' has no ports — add ports first."))
        if not self._all_ports_b:
            layout.addWidget(QLabel(f"⚠ '{tgt_name}' has no ports — add ports first."))

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        self._ok_btn = btn_box.button(QDialogButtonBox.Ok)
        layout.addWidget(btn_box)

        self._combo_a.currentIndexChanged.connect(self._on_combo_a_changed)
        self._combo_b.currentIndexChanged.connect(self._on_update_ok)

        # Trigger initial enable/disable pass
        self._on_combo_a_changed(self._combo_a.currentIndex())

    def _on_combo_a_changed(self, index: int):
        """Disable same-type ports in combo B; update OK button state."""
        if index < 0 or index >= len(self._all_ports_a):
            self._on_update_ok()
            return

        selected_a = self._all_ports_a[index]
        model = self._combo_b.model()
        for i, port_b in enumerate(self._all_ports_b):
            item = model.item(i)
            if item is None:
                continue
            if type(port_b) is type(selected_a):
                item.setFlags(Qt.NoItemFlags)
            else:
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)

        # If the current B selection became disabled, move to the first valid item
        cur = self._combo_b.currentIndex()
        if 0 <= cur < len(self._all_ports_b):
            cur_item = model.item(cur)
            if cur_item is not None and not (cur_item.flags() & Qt.ItemIsEnabled):
                for i in range(len(self._all_ports_b)):
                    it = model.item(i)
                    if it is not None and (it.flags() & Qt.ItemIsEnabled):
                        self._combo_b.setCurrentIndex(i)
                        break

        self._on_update_ok()

    def _on_update_ok(self, _index: int = 0):
        """Enable OK only when a valid OutPort ↔ InPort pair is selected."""
        if self._ok_btn is None:
            return
        if not self._all_ports_a or not self._all_ports_b:
            self._ok_btn.setEnabled(False)
            return
        idx_a = self._combo_a.currentIndex()
        idx_b = self._combo_b.currentIndex()
        if idx_a < 0 or idx_b < 0:
            self._ok_btn.setEnabled(False)
            return
        port_a = self._all_ports_a[idx_a]
        port_b = self._all_ports_b[idx_b]
        valid = (
            isinstance(port_a, esdl.OutPort) and isinstance(port_b, esdl.InPort)
        ) or (isinstance(port_a, esdl.InPort) and isinstance(port_b, esdl.OutPort))
        self._ok_btn.setEnabled(valid)

    def get_selected_ports(self):
        """
        Return *(out_port, in_port)* normalized so out_port is always the
        OutPort regardless of which combo it was selected from.
        Returns *(None, None)* if no valid pair is available.
        """
        if not self._all_ports_a or not self._all_ports_b:
            return None, None
        port_a = self._all_ports_a[self._combo_a.currentIndex()]
        port_b = self._all_ports_b[self._combo_b.currentIndex()]
        if isinstance(port_a, esdl.OutPort):
            return port_a, port_b  # A=out, B=in
        return port_b, port_a  # B=out, A=in

    def get_out_asset(self):
        """
        Return the asset that owns the OutPort (for map arrow direction).
        Must be called after *get_selected_ports()*.
        """
        if not self._all_ports_a:
            return self._source
        port_a = self._all_ports_a[self._combo_a.currentIndex()]
        return self._source if isinstance(port_a, esdl.OutPort) else self._target


class DisconnectDialog(QDialog):
    """
    Shows all connections that involve *asset* (both outgoing and incoming)
    and lets the user select one to remove.

    Call *get_selected_connection()* after QDialog.Accepted to retrieve the
    *(out_port, in_port)* pair to disconnect.
    """

    def __init__(self, asset, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Delete Connection")
        self.setMinimumWidth(440)
        self._asset = asset
        self._connections = []  # [(out_port, in_port, out_asset, in_asset), ...]
        self._ok_btn = None
        self._setup_ui()

    def _setup_ui(self):
        self._connections = get_asset_connections(self._asset)
        asset_name = getattr(self._asset, "name", None) or type(self._asset).__name__

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Connections of <b>{asset_name}</b>:"))

        self._list = QListWidget()
        for out_port, in_port, out_asset, in_asset in self._connections:
            out_port_name = getattr(out_port, "name", None) or "OutPort"
            in_port_name = getattr(in_port, "name", None) or "InPort"
            out_asset_name = (
                getattr(out_asset, "name", None) or type(out_asset).__name__
                if out_asset
                else "?"
            )
            in_asset_name = (
                getattr(in_asset, "name", None) or type(in_asset).__name__
                if in_asset
                else "?"
            )
            self._list.addItem(
                f"{out_asset_name} [{out_port_name}]  ↔  {in_asset_name} [{in_port_name}]"
            )

        if not self._connections:
            layout.addWidget(QLabel(f"⚠ '{asset_name}' has no connections."))
        else:
            layout.addWidget(self._list)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        self._ok_btn = btn_box.button(QDialogButtonBox.Ok)
        self._ok_btn.setEnabled(False)
        layout.addWidget(btn_box)

        self._list.currentRowChanged.connect(self._on_row_changed)

    def _on_row_changed(self, row: int):
        if self._ok_btn is not None:
            self._ok_btn.setEnabled(row >= 0)

    def get_selected_connection(self):
        """Return *(out_port, in_port)* of the selected connection, or *(None, None)*."""
        row = self._list.currentRow()
        if row < 0 or row >= len(self._connections):
            return None, None
        out_port, in_port, _, _ = self._connections[row]
        return out_port, in_port

    def get_connection_assets(self):
        """Return *(out_asset, in_asset)* of the selected connection, or *(None, None)*."""
        row = self._list.currentRow()
        if row < 0 or row >= len(self._connections):
            return None, None
        _, _, out_asset, in_asset = self._connections[row]
        return out_asset, in_asset
