# Resolution and Mode Reference

The following table lists the modes, resolutions, and FPS settings declared by the included JetPack 7.x packages. All modes are **Linear**. Resolutions are output specification references, and FPS values are settings; they do not establish completed hardware validation. See the [package table in the README](../README.md) for package inclusion and installation status.

## JetPack 7.x

| Model | Connection | Mode | Resolution | FPS |
|---|---|---|---|---|
| TS-ECM100M22 | MIPI | 0/1 | 1920×1080 | 60/30 |
| TS-ECM100M22 | MIPI | 2/3 | 1280×720 | 60/30 |
| TS-ECM300M22 | MIPI | 0/1/2 | 2592×1944 | 80/60/30 |
| TS-ECM300M22 | MIPI | 3/4 | 2560×1440 | 60/30 |
| TS-ECM300M22 | MIPI | 5/6 | 1920×1080 | 60/30 |
| TS-ECM400M22 | MIPI | 0/1 | 3840×2160 | 60/30 |
| TS-ECM400M22 | MIPI | 2/3 | 2560×1440 | 60/30 |
| TS-ECM400M22 | MIPI | 4/5/6 | 1920×1080 | 90/60/30 |
| TS-ECM300G2F | GMSL (MAX96724) | 0 | 2592×1944 | 80 |
| TS-ECM300G2F | GMSL (MAX96724) | 1 | 2592×1944 | 60 |
| TS-ECM300G2F | GMSL (MAX96724) | 2 | 2592×1944 | 30 |
| TS-ECM300G2F | GMSL (MAX96724) | 3 | 2560×1440 | 60 |
| TS-ECM300G2F | GMSL (MAX96724) | 4 | 2560×1440 | 30 |
| TS-ECM300G2F | GMSL (MAX96724) | 5 | 1920×1080 | 60 |
| TS-ECM300G2F | GMSL (MAX96724) | 6 | 1920×1080 | 30 |

Mode and FPS values correspond in the listed order. For example, `0/1` and `60/30` mean mode0 is 60 FPS and mode1 is 30 FPS.

ECM300G2F uses the MAX96717/MAX96724 GMSL package. Channels A/B/C/D share the same seven modes; simultaneous multi-channel capture still requires hardware validation.

The GMSL rows above describe the complete artifact set from commit `66e13ae803064226ff489a70b0a52c190f87b4af`. The effective driver frame-format table agrees with the DTS values and all 28 compiled DTBO mode nodes (seven modes on each of four channels). The earlier 1600×900 and 1280×720 modes are absent from this set.

**Mode6 correction:** the upstream README and a trailing DTS comment say 60 FPS, but the effective driver/DTS values and the compiled DTBO declare 1920×1080 at 30 FPS. This package uses 30 FPS. Matching declarations do not establish that the supplied KOs were built from the reviewed source, nor do they verify actual images, frame rate, or synchronization.

The [package manifest](../packages/ecm300/gmsl/max96717-max96724/jp7/manifest.json) records all per-channel mode nodes. Its [upstream receipt](../packages/ecm300/gmsl/max96717-max96724/jp7/evidence/upstream-receipt.json) records fixed Git blob identities and the shared tuning-file comparison.
