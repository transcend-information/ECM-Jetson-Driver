#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "${ECM_PYTHON:-python3}" -I -B "$HERE/ecm_camera_manager.py" "$@"
