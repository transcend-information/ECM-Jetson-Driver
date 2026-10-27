# Third-Party Components and Licensing Information (Draft)

This document records the provenance and observed license markers of artifacts included in installer v0.1.0 for company review. It does not establish that licensing or external redistribution requirements for the complete package have been satisfied.

## Included JP7 Packages

| Package | Artifact types | Fixed source commit | Observed markers |
|---|---|---|---|
| [ECM100 MIPI](packages/ecm100/mipi/jp7/manifest.json) | 1 KO, 1 DTBO, 1 ISP, 1 NITO | `ac20810660da33bfbe03fd27ee464d99ab444094` | KO: `GPL v2`; corresponding DTS: `GPL-2.0-only` |
| [ECM300 MIPI](packages/ecm300/mipi/jp7/manifest.json) | 1 KO, 1 DTBO, 1 ISP, 1 NITO | `f9c50f728c261da4c4812e5d4d227186a62b3fa9` | KO: `GPL v2`; C/H SPDX: `GPL-2.0`; corresponding DTS: `GPL-2.0-only` |
| [ECM400 MIPI](packages/ecm400/mipi/jp7/manifest.json) | 1 KO, 1 DTBO, 1 ISP, 1 NITO | `8c147133107fee51e62181affdbe2e1768e5547e` | KO: `GPL v2`; corresponding DTS: `GPL-2.0-only` |
| [ECM300G2F GMSL](packages/ecm300/gmsl/max96717-max96724/jp7/manifest.json) | 3 KOs, 1 DTBO, 4 ISPs, 4 NITOs | `cf6f7a6a34240c812339688fd7938c3ed7138a18` | Sensor, MAX96717, and MAX96724 KOs: `GPL v2`; SerDes C/H SPDX: `GPL-2.0`; corresponding DTS: `GPL-2.0-only` |

There are 24 artifacts in total: 6 KOs, 4 DTBOs, 7 ISPs, and 7 NITOs. Each package manifest records the filenames, roles, versions, and SHA256 values. A source commit identifies where the source and artifacts were collected; it does not prove that the artifacts were built from that source.

## Observed Information and Pending Checks

- All six KOs contain a `GPL v2` marker in `.modinfo`. This module marker alone does not replace the full source license terms.
- SPDX identifiers and header notices in the corresponding source files must be retained individually. Some mode headers specify GPL version 2 or later; a single set of terms must not be assumed to apply to the entire package.
- All seven ISP files retain NVIDIA copyright markers. The ECM300 MIPI ISP also includes an `Integrator: FRAMOS GmbH` statement. These header details do not independently establish complete ownership or redistribution permission.
- The file inventories reviewed for the four fixed source commits do not list standalone `LICENSE`, `COPYING`, or `NOTICE` files. The full terms, required notices, and any other applicable documents still need to be confirmed.
- The full licensing terms, provenance, and permitted distribution scope for ISP/NITO files remain subject to company review. The KOs' GPL markers must not be extended to ISP/NITO files.
- The company needs to confirm the applicable license texts, how corresponding source will be provided, third-party notices, and the scope of external distribution. This review does not create new license permissions or establish completed compliance.

Existing artifact contents and notices remain unchanged. After formal approval, add the full license texts and required documents according to the confirmed terms. See the separate [draft license framework](LICENSE.draft.md) for the company installer.
