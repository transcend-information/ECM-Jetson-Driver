# Changelog

This file tracks installer versions. Sensor drivers and DTBOs retain their existing versions and package identities.

## Unreleased

### Documentation

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
- JP6 binaries are not yet included. Their source specifications remain in the reference table for review.
- The exact L4T version, supported board models, actual images/FPS, and multi-channel synchronization remain to be verified.
- This package does not include preview, photo capture, sensor source code, or client-side compilation.
- License terms and external distribution remain subject to company review. The v0.1.0 version number does not itself indicate release approval.

Keep each delivered archive unchanged. Local review revisions are retained in separate delivery directories and identified by their archive SHA256 values. Once a version is published, subsequent changes must use a new version. Detailed validation evidence is retained outside the package.
