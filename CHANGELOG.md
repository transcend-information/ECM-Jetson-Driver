# Changelog

This file tracks installer versions. Driver and DTBO versions are recorded separately from the installer version.

## Unreleased

### ECM300G2F GMSL / JetPack 7.x

- Replace the complete fixed GMSL artifact set with upstream commit `66e13ae803064226ff489a70b0a52c190f87b4af`: three KOs, one DTBO, one shared ISP, and one shared NITO.
- Preserve the original upstream filenames and exact bytes for all six GMSL artifacts; update manifest paths and the delivery mapping without rebuilding or changing installation destinations.
- Match installation filenames and dependencies to the case-sensitive ELF module names. Keep unobserved module versions as null.
- Use seven Linear modes on all four channels; mode6 is 1920×1080 at 30 FPS. Correct the upstream README discrepancy using effective driver/DTS values and compiled DTBO metadata.
- Record exact Git blob identities, artifact hashes, shared tuning copies, and the source-tree inventory. Source-to-binary provenance, exact BSP/board compatibility, and hardware results remain unverified.
- Update only the JP7 GMSL artifact-role contract. Validate managed 12-to-6 artifact upgrades and restoration using the existing transaction engine and local simulation.

### Documentation

- Limit the public package table, resolution reference, and directory guide to the included JetPack 7.x packages.
- Align the GMSL resolution reference and installation list with the fixed seven-mode artifact set.
- Retain only README and CHANGELOG as root-level documentation. Preserve licensing and provenance drafts outside the repository for company review; embedded source and binary notices are unchanged.
- Use TS ordering model names in the README and resolution tables.
- Label the public GMSL connection as `GMSL (MAX96724)` while retaining the existing package paths and serializer/deserializer configuration.
- Add illustrated startup steps for extracting the archive, opening a terminal, and launching the menu. Identify the menu screenshot as an internal-package example; package availability remains unchanged.

## v0.1.0 — 2026-10-27

### Package Contents

- Organize the installation-only architecture with a unified Bash entry point, home page, package selection, installation confirmation, backup/restore, and installation logs.
- Include four prebuilt JetPack 7.x packages: ECM100/ECM300/ECM400 MIPI and ECM300G2F GMSL.
- Organize packages by product family, connection, and JetPack version, with a serializer/deserializer directory for GMSL. Update index paths and documentation while preserving package IDs, manifests, and artifact contents.
- Preserve package integrity checks, environment-difference warnings, and existing installation-record handling.
- Align the installer version across the program, README, newly generated logs, and archive as v0.1.0. Existing historical records are not rewritten.
- Preserve the existing driver, KO, DTBO, ISP/NITO, and package manifest contents. Installer version changes do not change their versions or trigger a rebuild.
- Add the English README, directory guide, mode/resolution reference, and draft licensing documents for company review.

### Current Status and Limitations

- The four included JP7 packages have local integrity and simulated workflow validation records. This does not establish hardware validation.
- Installation and restore on a real device remain disabled. This package has not been deployed to Jetson.
- Only JetPack 7.x binaries are included in this release.
- The exact L4T version, supported board models, actual images/FPS, and multi-channel synchronization remain to be verified.
- This package does not include preview, photo capture, sensor source code, or client-side compilation.
- License terms and external distribution remain subject to company review. The v0.1.0 version number does not itself indicate release approval.

Keep each delivered archive unchanged. Local review revisions are retained in separate delivery directories and identified by their archive SHA256 values. Once a version is published, subsequent changes must use a new version. Detailed validation evidence is retained outside the package.
