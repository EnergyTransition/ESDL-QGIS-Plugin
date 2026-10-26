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

## License

[MIT](LICENSE)