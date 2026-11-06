# Resolution and Mode Reference

The JetPack 6 and JetPack 7 packages declare the same modes for each camera configuration below. The table lists the resolution and maximum frame rate for each mode in the packaged device-tree overlay. Actual frame rates depend on the running system.

The GMSL rows apply to both the MAX96724 and MAX9296 deserializers. For each model, the two packages declare the same modes. See the [package table](../README.md) for the JetPack 6 and JetPack 7 package IDs.

| Camera and connection | Mode IDs | Resolution | Maximum FPS, in mode order |
| --- | --- | --- | --- |
| TS-ECM100M22 · MIPI | 0 / 1 | 1920 × 1080 | 60 / 30 |
| TS-ECM100M22 · MIPI | 2 / 3 | 1280 × 720 | 60 / 30 |
| TS-ECM300M22 · MIPI or TS-ECM300G2F / TS-ECM301G2F · GMSL | 0 / 1 / 2 | 2592 × 1944 | 80 / 60 / 30 |
| TS-ECM300M22 · MIPI or TS-ECM300G2F / TS-ECM301G2F · GMSL | 3 / 4 | 2560 × 1440 | 60 / 30 |
| TS-ECM300M22 · MIPI or TS-ECM300G2F / TS-ECM301G2F · GMSL | 5 / 6 | 1920 × 1080 | 60 / 30 |
| TS-ECM400G2F · GMSL | 0 | 3840 × 2160 | 30 |
| TS-ECM400G2F · GMSL | 1 / 2 | 2560 × 1440 | 60 / 30 |
| TS-ECM400G2F · GMSL | 3 / 4 / 5 | 1920 × 1080 | 90 / 60 / 30 |

For example, the TS-ECM100M22 declares up to 60 FPS in mode 0 and 30 FPS in mode 1 at 1920 × 1080. TS-ECM300G2F and TS-ECM301G2F share the package for the selected deserializer and JetPack version.
