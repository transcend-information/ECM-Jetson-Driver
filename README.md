# ECM Jetson Camera Installer

Version: **v0.1.0**

## Purpose and Current Status

ECM Jetson Camera Installer provides a unified installation entry point for NVIDIA Jetson camera driver packages, including package selection, installation confirmation, backup and restore, and installation logs.

The installer uses prebuilt `.ko`, `.dtbo`, and supporting ISP/NITO files. Users do not need to compile drivers. This package provides installation-related functions only; it does not include preview, photo capture, or sensor source code.

Four JetPack 7.x packages are currently included and have passed local simulated installation, backup/restore, and package-switching checks. The home and package confirmation pages are available for review. **Installation and restore on a real device are not yet enabled, and board validation is still pending.**

## Models and Packages

| Model | Sensor | Connection | JetPack 6.x | JetPack 7.x |
|---|---|---|---|---|
| ECM100 | GC2093 | MIPI | Pending inclusion | Included |
| ECM300 | IMX675 | MIPI | Pending inclusion | Included |
| ECM400 | IMX415 | MIPI | Pending inclusion | Included |
| ECM300G2F | IMX675 | GMSL (MAX96717/MAX96724) | Pending inclusion | Included |

“Included” means the package files are present and have passed integrity checks and local simulation. It does not mean that installation or image capture has been verified on a real device. Packages marked “Pending inclusion” cannot currently be installed.

Packages are organized by product family, connection, and JetPack version. GMSL packages also identify the serializer/deserializer pair. For example, ECM300 MIPI uses `packages/ecm300/mipi/jp7/`, while ECM300G2F uses `packages/ecm300/gmsl/max96717-max96724/jp7/`. Both belong to the ECM300 product family; their package identities remain separate. See the [directory guide](docs/structure.md).

The included JP7 binaries identify their target kernel as `6.8.12-1021-tegra`. The exact L4T version, supported board models, and hardware compatibility remain to be confirmed; the JetPack major version alone is not sufficient to establish compatibility.

### Resolutions and FPS

The following specifications are declared by the included **JetPack 7.x** packages. All listed modes are **Linear**; HDR is outside the scope of this package. FPS values are mode settings; actual frames and frame rates will continue to be validated on hardware.

| Model | Connection | Resolutions and FPS |
|---|---|---|
| ECM100 | MIPI | 1920×1080 @ 60/30 FPS; 1280×720 @ 60/30 FPS |
| ECM300 | MIPI | 2592×1944 @ 80/60/30 FPS; 2560×1440 @ 60/30 FPS; 1920×1080 @ 60/30 FPS |
| ECM400 | MIPI | 3840×2160 @ 60/30 FPS; 2560×1440 @ 60/30 FPS; 1920×1080 @ 90/60/30 FPS |
| ECM300G2F | GMSL | 2560×1440, 1920×1080, 1600×900, and 1280×720, all at 30 FPS |

The GMSL specifications apply to each camera channel. Simultaneous capture across multiple channels will continue to be validated. Drivers, mode numbers, and capture processing must be checked against the corresponding package for each JetPack version.

The target output specifications for the local JP6 MIPI versions have been documented, but their binaries are not yet included in this installer. ECM400 JP6 still uses downstream cropping, so its sensor receiver dimensions must not be confused with the output specifications above. See the [resolution and mode reference](docs/resolutions.md). This package does not include preview code that performs capture or cropping.

## Requirements and Startup

### Requirements

- A Linux/AArch64 environment on NVIDIA Jetson.
- Bash and Python 3.10 or later. The program uses the Python standard library.
- The complete installer directory, including the program, package index, and binaries under `packages/`.
- A writable workspace for installation logs, backups, and reports.

### Open the Menu

Open a terminal in the root directory of the extracted package and run:

```bash
bash ecm_camera_manager.sh
```

To display the installer version, run `bash ecm_camera_manager.sh --version`. The archive is named `ecm-jetson-camera-installer-v0.1.0.tar.gz`.

The program automatically checks the workspace. If it finds exactly one recognized existing workspace, it reuses its records without moving or merging old data. If no existing workspace is found, the default is `$HOME/ECM_Camera_Workspace`.

If multiple workspaces are detected, use `--workspace` to select the directory to reuse. Replace the following path with the actual location:

```bash
bash ecm_camera_manager.sh --workspace "/path/to/existing-workspace"
```

### Workflow

1. Open the home page to review the current environment and package status.
2. Select **Install / Switch Camera**. The program detects JetPack from the environment and offers manual selection when detection is unavailable.
3. Select the model and its MIPI/GMSL package.
4. Review the selected package, environment differences, and planned actions on the installation confirmation page.

The current version allows review through the installation confirmation page; writing to a real device is not yet enabled. Environment differences produce warnings and are logged. Missing or corrupted package files stop the operation. The program does not reboot automatically.

Installation logs are stored in `<workspace>/logs/external/`, and exported summaries are stored in `<workspace>/reports/`. Select **Installation logs** on the home page to view records.

See the [directory guide](docs/structure.md) for the purpose of each folder.

See the [CHANGELOG](CHANGELOG.md) for version changes. The [notice](NOTICE.md), [draft company license framework](LICENSE.draft.md), and [third-party notices](THIRD_PARTY_NOTICES.md) are for internal review. Final terms and external distribution remain subject to company approval.
