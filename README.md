## Development setup

```
# Run once in an elevated PowerShell
$qgisPlugins = "C:\Users\wangck\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins"
New-Item -ItemType SymbolicLink -Path "$qgisPlugins\esdl_plugin" -Target "C:\Users\wangck\projects\playground\QGIS - DTK\plugin\src"
```


```mermaid
graph TB
    subgraph QGIS["QGIS"]
        TB[Toolbar button]
        LAYERS[Memory Vector Layers\none per ESDL asset class]
        ATTRFORM[Attribute Table / Map\nNative QGIS editing]
    end

    subgraph Plugin["esdl_plugin  (src/)"]
        INIT["__init__.py\nESDLPlugin\n─────────────\nrun()\non_loaded()"]

        subgraph Wizard["esdl_wizard.py  —  ESDLWizard"]
            P1["Page 1 · AuthPage\n─────────────\nusername / password\nconnection settings\n(env var defaults)"]
            P2["Page 2 · BrowsePage\n─────────────\nQTreeWidget\njstree JSON → file tree\nselect .esdl"]
            P3["Page 3 · LoadPage\n─────────────\nfetch + parse ESDL\nchange log\nexport .esdl"]
        end

        VIZ["esdl_visualize.py\nESDLVisualizer\n─────────────\ncreate_and_add_layers()\nsync_from_layers()\nto_xml()"]

        subgraph Drive["esdl_drive/"]
            AUTH["auth.py\nPasswordAuthWorker  ROPC grant\nAuthWorker          PKCE + browser"]
            API["esdl_drive.py\nESDLDrive\n─────────────\nbrowse_cdo()\nget_resource()\nput_resource()"]
        end
    end

    subgraph External["External services"]
        KC["Keycloak\n/protocol/openid-connect/token"]
        ESDLDRIVE["ESDL Drive  :9080\n/store/browse\n/store/resource/…"]
    end

    FILE[".esdl file\n(local export)"]

    TB -->|run| INIT
    INIT -->|show| Wizard
    P1 -->|login| AUTH
    AUTH -->|POST password grant| KC
    KC -->|access_token| AUTH
    AUTH -->|token| P1

    P2 -->|browse_cdo + token| API
    API -->|GET /store/browse| ESDLDRIVE
    ESDLDRIVE -->|jstree JSON| API
    API -->|json| P2

    P3 -->|get_resource + token| API
    API -->|GET /store/resource/…| ESDLDRIVE
    ESDLDRIVE -->|ESDL XML| API
    API -->|content| P3
    P3 -->|esh + energy_system| INIT

    INIT -->|create_and_add_layers| VIZ
    VIZ -->|QgsVectorLayer × N| LAYERS
    LAYERS --> ATTRFORM

    INIT -->|sync_callback| VIZ
    P3 -->|sync_callback on export| VIZ
    VIZ -->|sync_from_layers| LAYERS
    P3 -->|esh.to_string| FILE
```