# ESDL QGIS Plugin

Load, visualize, edit, and export [ESDL](https://energytransition.github.io/) energy system files directly in QGIS.

## Status

This project is publicly available as-is. There is no guarantee of future updates or maintenance.

## Requirements

- QGIS **3.44** — the plugin is developed and tested against this version only.
  It has **not** been tested on other QGIS 3.x releases or on QGIS 4.x, and may not work there.
- The [`pyESDL`](https://pypi.org/project/pyESDL/) package (and its dependencies), see [Dependencies](#dependencies) below.

## Installation

Pick one of the following, depending on your situation.

### Option 1: Install from a ZIP file

1. Download or clone this repository and compress the [`src/`](src) folder into a `.zip` file.
2. In QGIS, go to **Plugins → Manage and Install Plugins → Install from ZIP**, then select the file.
3. Install the Python dependencies (see [Dependencies](#dependencies)) before using the plugin.

### Option 2: Install from the official QGIS Plugin Repository

**Not available yet** — the plugin is not currently published on [plugins.qgis.org](https://plugins.qgis.org/). Once it is, installation will be as simple as searching for "ESDL QGIS Plugin" in **Plugins → Manage and Install Plugins**.

### Option 3: Development setup (local development only)

```powershell
# Run once in an elevated PowerShell
$qgisPlugins = "C:\Users\<you>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins"

New-Item -ItemType SymbolicLink -Path "$qgisPlugins\ESDL-QGIS-Plugin" -Target "C:\path\to\ESDL-QGIS-Plugin\src"
```

Then install the dependencies (see below) and restart QGIS.

## Dependencies

The plugin requires `pyESDL` and its dependencies to be importable by QGIS's bundled Python interpreter. Today this means installing them globally into that interpreter using the **OSGeo4W Shell** (shipped with QGIS — search for it in the Start Menu):

```bat
pip install pyESDL>=26.9
```

Alternatively, navigate to the project root directory in the OSGeo4W Shell, and then install with:

```bat
pip install -r src/requirements.txt
```

Recommended: use `pip >= 26.2.2` ([security advisory](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576)).

Restart QGIS afterwards and enable the plugin via **Plugins → Manage and Install Plugins**.

## Project Structure

```text
src/
├── __init__.py             # Plugin entry point — wires the dock, dialogs, importer, and visualizer together
├── esdl_io.py              # Local .esdl file import/export dialogs
├── esdl_model.py           # Pure ESDL domain logic: ports/connections, asset creation, class discovery
├── esdl_ecore.py           # Generic pyecore introspection helpers shared by the ESDL Browser and Table Editor
├── esdl_visualize.py       # Renders the ESDL energy system as QGIS map layers and syncs edits back
├── esdl.png                # Plugin toolbar/menu icon
├── metadata.txt            # QGIS plugin metadata (name, version, author, description, etc.)
├── requirements.txt        # Python dependencies (pyESDL) to install into QGIS's bundled interpreter
├── LICENSE                 # Apache 2.0 license, duplicated here so it ships inside the installable plugin ZIP
├── icons/                  # Per-ESDL-class marker icons used on the map
└── ui/
    ├── intro_dialog.py         # Start screen for loading a local .esdl file
    ├── dock.py                 # Main dock panel: asset/connection actions, ESDL Browser, Table Editor, export, action log
    ├── add_asset_dialog.py     # Add Asset flow dialogs
    ├── connection_dialogs.py   # Add/Delete Connection flow dialogs
    ├── esdl_browser_dialog.py  # Generic ESDL object browser/editor
    ├── table_editor_dialog.py  # Bulk attribute/reference editor across all assets of one type
    └── map_tools.py            # Map canvas tools for geometry capture and asset/port picking
```

## Tutorials

Coming soon

## Contributions

### Bug reports and feature requests

- Open an issue on GitHub with:
  - A descriptive title and clear description
  - A relevant label (`bug`, `enhancement`, `documentation`, etc.)
  - An assignee if you have write access

### Making Changes

- Submit a pull request with your changes
- Follow the existing [project structure](#project-structure) as much as possible
- Include clear commit messages
- Test your changes with QGIS before submitting

### Releases

Maintainers should follow this release workflow:

1. **Version bump**: Update the version in [`src/metadata.txt`](src/metadata.txt) following [semantic versioning](https://semver.org/)
2. **Git tag**: Create a git semantic tag
3. **Release notes**: Create a release on GitHub with a summary of changes

## License

[Apache 2.0](LICENSE)