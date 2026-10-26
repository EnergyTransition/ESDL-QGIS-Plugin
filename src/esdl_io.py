"""
Local-file import/export for ESDL energy system files.
"""

import os

from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox

from esdl.esdl_handler import EnergySystemHandler


def open_esdl_file(parent=None):
    """
    Show a native file-picker and parse the selected .esdl file.

    Returns (esh, energy_system, source_label), or None if the user
    cancelled or the file could not be parsed.
    """
    path, _ = QFileDialog.getOpenFileName(
        parent,
        "Open ESDL file",
        "",
        "ESDL files (*.esdl);;XML files (*.xml);;All files (*)",
    )
    if not path:
        return None

    try:
        esh = EnergySystemHandler()
        with open(path, "r", encoding="utf-8") as fh:
            xml = fh.read()
        energy_system = esh.load_from_string(xml)
        return esh, energy_system, os.path.basename(path)
    except Exception as exc:
        QMessageBox.critical(parent, "Failed to load ESDL", str(exc))
        return None


def export_esdl_file(parent, esh, energy_system) -> bool:
    """
    Show a native save-picker and write *esh*'s current state to a .esdl file.

    Returns True if saved, False if the user cancelled or the save failed.
    """
    default_name = (energy_system.name or "energy_system").replace(" ", "_") + ".esdl"
    path, _ = QFileDialog.getSaveFileName(
        parent,
        "Export ESDL file",
        default_name,
        "ESDL files (*.esdl);;XML files (*.xml)",
    )
    if not path:
        return False

    try:
        xml = esh.to_string()
        with open(path, "w", encoding="utf-8") as f:
            f.write(xml)
        return True
    except Exception as exc:
        QMessageBox.critical(parent, "Failed to export ESDL", str(exc))
        return False
