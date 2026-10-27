# Directory Guide

The project is named **ECM Jetson Camera Installer**. The local Git working directory is `ecm-camera-installer/`; the extracted archive uses `ecm-jetson-camera-installer-v0.1.0/`. Both contain the same installer version.

See the [README](../README.md) for the purpose, package status, and startup instructions.

```text
ecm-camera-installer/
├── README.md
├── CHANGELOG.md
├── NOTICE.md
├── LICENSE.draft.md
├── THIRD_PARTY_NOTICES.md
├── .gitignore
├── .gitattributes
├── ecm_camera_manager.sh
├── ecm_camera_manager.py
├── ecm_install/
├── manifests/
│   └── release-index.json
├── packages/
│   ├── ecm100/
│   │   └── mipi/jp7/
│   ├── ecm300/
│   │   ├── mipi/jp7/
│   │   └── gmsl/max96717-max96724/jp7/
│   └── ecm400/
│       └── mipi/jp7/
└── docs/
    ├── structure.md
    └── resolutions.md
```

| Path | Contents |
|---|---|
| [ecm_camera_manager.sh](../ecm_camera_manager.sh) | Unified Bash entry point |
| [ecm_camera_manager.py](../ecm_camera_manager.py) | Python entry point |
| [ecm_install/](../ecm_install/) | Menus, installation transactions, backup/restore, and logging |
| [ecm_install/__init__.py](../ecm_install/__init__.py) | Single source of the installer version in code; driver versions remain separate |
| [manifests/release-index.json](../manifests/release-index.json) | Reception status and manifest hashes for eight packages |
| [packages/](../packages/) | Four received JP7 packages grouped by product family, connection, and JetPack version |
| [CHANGELOG.md](../CHANGELOG.md) | Installer version changes and current limitations |
| [NOTICE.md](../NOTICE.md) | Draft company and third-party notices |
| [LICENSE.draft.md](../LICENSE.draft.md) | Installer license framework for company review; not a final license |
| [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) | Artifact provenance and third-party licensing information pending confirmation |
| [.gitignore](../.gitignore) | Excludes locally generated state, logs, backups, and caches |
| [.gitattributes](../.gitattributes) | Uses LF for code and documentation while preserving the original bytes of fixed manifests and artifacts |
| [resolutions.md](resolutions.md) | Mode and resolution reference, with JP6 above JP7 |

## Package Layout

MIPI packages use `packages/<family>/mipi/<jetpack>/`. GMSL packages use `packages/<family>/gmsl/<serializer>-<deserializer>/<jetpack>/`. The serializer comes first in the pair name.

| Product shown in the installer | Connection | Package directory |
|---|---|---|
| ECM100 | MIPI | [ecm100/mipi/jp7/](../packages/ecm100/mipi/jp7/) |
| ECM300 | MIPI | [ecm300/mipi/jp7/](../packages/ecm300/mipi/jp7/) |
| ECM300G2F | GMSL, MAX96717/MAX96724 | [ecm300/gmsl/max96717-max96724/jp7/](../packages/ecm300/gmsl/max96717-max96724/jp7/) |
| ECM400 | MIPI | [ecm400/mipi/jp7/](../packages/ecm400/mipi/jp7/) |

The `ecm300/` directory groups the product family. The GMSL product remains **ECM300G2F** in its manifest, menus, and logs; it is not interchangeable with the ECM300 MIPI package.

Each JetPack package directory contains:

```text
jp7/
├── manifest.json
└── artifacts/
    └── Prebuilt KO, DTBO, ISP, and NITO files
```

Artifact roles, filenames, versions, and hashes are recorded in `manifest.json`. Artifact paths are relative to that manifest. The release index locates each manifest using the existing package ID; moving a package does not rename its ID or change its files' installation destinations on Jetson.

The JetPack directory groups platform versions. Exact kernel, L4T, board, and compatibility information remains in each manifest and must not be inferred from the directory name.

JP6 packages are not yet included. Their status stays pending in the release index without empty artifact directories or placeholder manifests. When complete JP6 packages are received, they will use sibling `jp6/` directories under the same connection or SerDes directory.

## Workspace and Delivery Records

The installation workspace should be separate from the package files and can be selected with `--workspace`. The local `authoring/`, `review/`, `deliveries/`, and VS Code workspace file are outside the repository root. The archive excludes `.git/`, test data, and local operation records.
