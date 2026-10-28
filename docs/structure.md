# Directory Guide

The project is named **ECM Jetson Camera Installer**. The local Git working directory is `ecm-camera-installer/`; the extracted archive uses `ecm-jetson-camera-installer-v0.1.0/`. Both contain the same installer version.

See the [README](../README.md) for the purpose, package status, and startup instructions.

```text
ecm-camera-installer/
├── README.md
├── CHANGELOG.md
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
│       └── gmsl/max96717-max96724/jp7/
└── docs/
    ├── structure.md
    ├── resolutions.md
    └── images/           # README startup screenshots
```

| Path | Contents |
|---|---|
| [ecm_camera_manager.sh](../ecm_camera_manager.sh) | Unified Bash entry point |
| [ecm_camera_manager.py](../ecm_camera_manager.py) | Python entry point |
| [ecm_install/](../ecm_install/) | Menus, installation transactions, backup/restore, and logging |
| [ecm_install/__init__.py](../ecm_install/__init__.py) | Single source of the installer version in code; driver versions remain separate |
| [manifests/release-index.json](../manifests/release-index.json) | Package reception status and manifest hashes |
| [packages/](../packages/) | Four received JP7 packages grouped by product family, connection, and JetPack version |
| [CHANGELOG.md](../CHANGELOG.md) | Installer version changes and current limitations |
| [.gitignore](../.gitignore) | Excludes locally generated state, logs, backups, and caches |
| [.gitattributes](../.gitattributes) | Uses LF for code and documentation while preserving the original bytes of fixed manifests and artifacts |
| [resolutions.md](resolutions.md) | Mode and resolution reference for the included JetPack 7.x packages |
| [images/](images/) | Screenshots showing archive extraction, opening a terminal, and the installer menu |

## Package Layout

MIPI packages use `packages/<family>/mipi/<jetpack>/`. GMSL packages use `packages/<family>/gmsl/<serializer>-<deserializer>/<jetpack>/`. The serializer comes first in the pair name.

| Product shown in the installer | Connection | Package directory |
|---|---|---|
| ECM100 | MIPI | [ecm100/mipi/jp7/](../packages/ecm100/mipi/jp7/) |
| ECM300 | MIPI | [ecm300/mipi/jp7/](../packages/ecm300/mipi/jp7/) |
| ECM300G2F | GMSL, MAX96717/MAX96724 | [ecm300/gmsl/max96717-max96724/jp7/](../packages/ecm300/gmsl/max96717-max96724/jp7/) |
| ECM400G2F | GMSL, MAX96717/MAX96724 | [ecm400/gmsl/max96717-max96724/jp7/](../packages/ecm400/gmsl/max96717-max96724/jp7/) |

The `ecm300/` directory groups the product family. The GMSL product remains **ECM300G2F** in its manifest, menus, and logs; it is not interchangeable with the ECM300 MIPI package.

Each JetPack package directory contains:

```text
jp7/
├── manifest.json
├── evidence/            # Fixed source and artifact receipt, when included
└── artifacts/
    └── Prebuilt KO, DTBO, ISP, and NITO files
```

Artifact roles, filenames, versions, and hashes are recorded in `manifest.json`. Artifact paths are relative to that manifest. The release index locates each manifest using the existing package ID; moving a package does not rename its ID or change its files' installation destinations on Jetson.

### ECM300G2F JP7 artifact set

The GMSL package contains six artifacts from commit `66e13ae803064226ff489a70b0a52c190f87b4af`:

| Role | Package filename under `artifacts/` | Installation filename |
|---|---|---|
| Sensor KO | `ECM300_MAX96724.ko` | `ECM300_MAX96724.ko` |
| Serializer KO | `MAX96717.ko` | `MAX96717.ko` |
| Deserializer KO | `MAX96724.ko` | `MAX96724.ko` |
| Overlay | `tegra234-p3767-camera-p3768-ECM300_MAX96724.dtbo` | `ecm-<transaction-id>.dtbo` |
| Shared ISP | `Transcend_GMSL_ECM300.isp` | `Transcend_GMSL_ECM300.isp` |
| Shared NITO | `Transcend_GMSL_ECM300.nito` | `Transcend_GMSL_ECM300.nito` |

Package filenames and bytes are preserved exactly from the upstream `build_output/` files; no custom JP7 suffixes or serializer/deserializer prefixes are added. The transaction-specific overlay installation path remains unchanged.

KO installation names match the case-sensitive ELF module names. The sensor depends on `tegra-camera`, `MAX96717`, and `MAX96724`. All three KOs declare kernel `6.8.12-1021-tegra`; none contains a module version, so the observed driver version remains unknown. The source README's version label is not substituted for a binary module version.

All four DTBO camera badges are `Transcend_GMSL_ECM300`, so the channels share one ISP and one NITO installed in `/var/nvidia/nvcam/settings/`. The source-root and `build_output/` copies are byte-identical. KOs are installed under `/lib/modules/<kernel>/updates/drivers/media/i2c/`, and the transaction overlay is stored under `/boot/`.

The package also contains `evidence/upstream-receipt.json`, with the full source-tree file inventory, Git blob identities, hashes, ELF observations, and mode checks. Source files themselves are not distributed. Co-location in one Git commit and matching static metadata do not constitute source-to-binary build proof.

A simulated update from the earlier 12-artifact GMSL package removes only superseded files recorded in the existing managed transaction. Its backups retain the old KOs, overlay, and eight channel-specific tuning files for restore. Unmanaged files are not swept by filename pattern. Real-device installation remains disabled in this version.

The JetPack directory groups platform versions. Exact kernel, L4T, board, and compatibility information remains in each manifest and must not be inferred from the directory name.

This guide documents the four included JetPack 7.x packages shown above.

### ECM400G2F JP7 artifact set

The ECM400G2F package contains six original artifacts from commit `f987e61f1880d4bcf33033235d4d692e210c00ef`:

| Role | Package filename under `artifacts/` | Upstream directory | Installation filename |
|---|---|---|---|
| Sensor KO | `ECM400_MAX96724.ko` | `imx415-gmsl-max96724/build_output/` | `ECM400_MAX96724.ko` |
| Serializer KO | `MAX96717.ko` | `imx415-gmsl-max96724/build_output/` | `MAX96717.ko` |
| Deserializer KO | `MAX96724.ko` | `imx415-gmsl-max96724/build_output/` | `MAX96724.ko` |
| Overlay | `tegra234-p3767-camera-p3768-ECM400_MAX96724.dtbo` | `imx415-gmsl-max96724/build_output/` | `ecm-<transaction-id>.dtbo` |
| ISP | `Transcend_GMSL_ECM400.isp` | `imx415-gmsl-max96724/` | `Transcend_GMSL_ECM400.isp` |
| NITO | `Transcend_GMSL_ECM400.nito` | `imx415-gmsl-max96724/` | `Transcend_GMSL_ECM400.nito` |

The upstream installation script reads ISP/NITO from its own directory (lines 98–106); its output-copy step exports KO/DTBO only (lines 448–471). The source-root ISP matches the `build_output/` copy, but the two NITO files differ despite equal size. This candidate uses the script-selected source-root NITO (Git blob `cf71ef899774b6c02ffe1a50ffdcde0726b02ec2`), with original name and bytes. The alternate output NITO (blob `8c020d50398f82179b1fe7c4bea46fd31a8d1105`) is retained in internal review evidence only. It is not included or treated as equivalent.

KO names match ELF module names. The sensor depends on `tegra-camera`, `MAX96717`, and `MAX96724`; the serializer and deserializer have no additional declared module dependencies. All three KOs declare kernel `6.8.12-1021-tegra` and contain no module version. KOs use `/lib/modules/<kernel>/updates/drivers/media/i2c/`; ISP/NITO use `/var/nvidia/nvcam/settings/`; the transaction overlay uses `/boot/`.

The overlay configures one channel with badge `Transcend_GMSL_ECM400`, six Linear modes, VC0, four lanes, and `serial_c`. See the [mode reference](resolutions.md). Exact board/L4T/ABI and hardware operation remain unverified.

The active JP7 catalog uses the new ID `ecm400-gmsl-max96724-jp7`. The previous `ecm400-mipi-jp7` ID is retained only inside existing transaction histories; it is never aliased to GMSL. Restore uses saved transaction plans and backups. Local simulation verifies an old MIPI installation can switch to this GMSL package, restore the original MIPI files and exact state bytes, then restore the initial system. It also verifies both GMSL packages can switch and restore their own SerDes bytes despite shared installation filenames.

## Workspace and Delivery Records

The installation workspace should be separate from the package files and can be selected with `--workspace`. The local `authoring/`, `review/`, `deliveries/`, and VS Code workspace file are outside the repository root. The archive excludes `.git/`, test data, and local operation records.

Public packaging uses an explicit file allowlist derived from the reviewed candidate and its release index. Pending JP6 packages and residual old ECM400 MIPI working files are excluded. Do not archive an entire development working directory as the public package. The internal ECM400 MIPI JP6 contract is marked legacy-only and excluded from new-install menus and home-page package counts. Saved transaction restore remains available.
