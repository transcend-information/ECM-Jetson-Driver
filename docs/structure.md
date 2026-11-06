# Directory Guide

To open the Manager, run `bash ecm_camera_manager.sh` from the extracted installer folder. See the [illustrated startup guide](../README.md#start-the-manager-for-the-first-time).

| Path | Purpose |
| --- | --- |
| [ecm_camera_manager.sh](../ecm_camera_manager.sh) | Starts the Manager. |
| [bootstrap/](../bootstrap/) | Checks the system Python before opening the menu. Includes scripts to plan and apply a confirmed repair of missing system packages. |
| [ecm_camera_manager.py](../ecm_camera_manager.py), [ecm_install/](../ecm_install/) | Run the menus, perform the installation and save its report. |
| [ecm_env/](../ecm_env/), [dependencies/](../dependencies/) | Check installation tools and identify the Ubuntu packages needed for the detected JetPack environment. |
| [manifests/release-index.json](../manifests/release-index.json) | Lists the 12 camera package IDs, their manifest paths and their SHA-256 checksums. |
| [packages/](../packages/) | Holds the camera package manifests and the prebuilt files installed on the Jetson. |
| [docs/resolutions.md](resolutions.md), [docs/images/](images/) | List camera modes and illustrate the startup and installation steps. |
| [CHANGELOG.md](../CHANGELOG.md) | Summarizes installer changes by version. |

## Camera packages

Each directory below contains both a `jp6/` and a `jp7/` package.

| Camera | Connection | Package directory |
| --- | --- | --- |
| TS-ECM100M22 | MIPI | [ecm100/mipi/](../packages/ecm100/mipi/) |
| TS-ECM300M22 | MIPI | [ecm300/mipi/](../packages/ecm300/mipi/) |
| TS-ECM300G2F / TS-ECM301G2F | GMSL, MAX96724 | [ecm300/gmsl/max96717-max96724/](../packages/ecm300/gmsl/max96717-max96724/) |
| TS-ECM300G2F / TS-ECM301G2F | GMSL, MAX9296 | [ecm300/gmsl/max96717-max9296/](../packages/ecm300/gmsl/max96717-max9296/) |
| TS-ECM400G2F | GMSL, MAX96724 | [ecm400/gmsl/max96717-max96724/](../packages/ecm400/gmsl/max96717-max96724/) |
| TS-ECM400G2F | GMSL, MAX9296 | [ecm400/gmsl/max96717-max9296/](../packages/ecm400/gmsl/max96717-max9296/) |

Each [camera package manifest](../packages/ecm100/mipi/jp6/manifest.json) identifies the camera and JetPack target and lists the files under `artifacts/`, including their roles, sizes and SHA-256 checksums. These files include the kernel driver (`.ko`), device-tree overlay (`.dtbo`) and ISP tuning file. JetPack 7 packages also include a NITO tuning file. GMSL packages include drivers for the serializer and deserializer.

The [system dependency manifests](../dependencies/) list the Ubuntu packages that Environment Check uses to plan a repair, including versions, download URLs and checksums.
