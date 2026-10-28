# Resolution and Mode Reference

The following tables list the modes, resolutions, and FPS settings for each model. All modes are **Linear**. Resolutions are output specification references, and FPS values are settings; they do not establish completed hardware validation. See the [package table in the README](../README.md) for package inclusion and installation status.

## JetPack 6.x

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
| TS-ECM300G2F | GMSL (MAX96724) | 0 | 2560×1440 | 30 |
| TS-ECM300G2F | GMSL (MAX96724) | 1 | 1920×1080 | 30 |
| TS-ECM300G2F | GMSL (MAX96724) | 2 | 1600×900 | 30 |
| TS-ECM300G2F | GMSL (MAX96724) | 3 | 1280×720 | 30 |

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
| TS-ECM300G2F | GMSL (MAX96724) | 0 | 2560×1440 | 30 |
| TS-ECM300G2F | GMSL (MAX96724) | 1 | 1920×1080 | 30 |
| TS-ECM300G2F | GMSL (MAX96724) | 2 | 1600×900 | 30 |
| TS-ECM300G2F | GMSL (MAX96724) | 3 | 1280×720 | 30 |

Mode and FPS values correspond in the listed order. For example, `0/1` and `60/30` mean mode0 is 60 FPS and mode1 is 30 FPS.

ECM300G2F uses the MAX96717/MAX96724 GMSL package. Channels A/B/C/D share the same four modes; simultaneous multi-channel capture still requires hardware validation.
