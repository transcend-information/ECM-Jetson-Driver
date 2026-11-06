#!/bin/bash
# Source from the public entry point; no camera, sudo or install on startup.
ecm_bootstrap() {
    local here=$1; shift
    if (( BASH_VERSINFO[0] < 4 )); then printf '%s\n' 'Environment: Missing Bash >= 4.' >&2; return 2; fi
    local tool
    for tool in mkdir mktemp tee; do
        command -v "$tool" >/dev/null || { printf 'Missing bootstrap tool: %s; minimal log unavailable.\n' "$tool" >&2; return 2; }
    done
    umask 077
    local state="${XDG_STATE_HOME:-${HOME:-/tmp}/.local/state}/ecm-camera-manager/environment"
    local logdir
    if [[ $EUID -eq 0 ]]; then
        # Direct sudo/root entry must not infer a replacement /root workspace.
        # Python binds and copies this bounded bootstrap log after validating --workspace.
        logdir=$(mktemp -d /tmp/ecm-root-bootstrap.XXXXXXXX) || return 2
    else
        mkdir -p -- "$state" || { printf '%s\n' 'Cannot create environment log directory.' >&2; return 2; }
        logdir=$(mktemp -d "$state/startup.XXXXXXXX") || return 2
    fi
    printf 'Bootstrap Bash %s\nSystem Python: /usr/bin/python3\n' "$BASH_VERSION" > "$logdir/bootstrap.log"
    if [[ -n ${ECM_PYTHON:-} ]]; then
        local requested
        requested=$(command -v -- "$ECM_PYTHON" 2>/dev/null || true)
        if [[ -z $requested || ! $requested -ef /usr/bin/python3 ]]; then
            printf '%s\n' 'ECM_PYTHON override is not the system Python; this installer uses /usr/bin/python3 for the Manager and installer.' | tee -a "$logdir/bootstrap.log"
        else printf '%s\n' 'ECM_PYTHON resolves to the system Python; compatible.' >> "$logdir/bootstrap.log"; fi
    fi
    while true; do
        local ok=0
        if [[ -x /usr/bin/python3 ]]; then
            if /usr/bin/python3 -I -B -c 'import sys; assert sys.version_info >= (3,10), "Python >= 3.10 is required"; assert sys.prefix == sys.base_prefix, "System Python required"; import argparse,collections,contextlib,copy,ctypes,dataclasses,datetime,fcntl,fractions,gzip,hashlib,io,json,lzma,math,os,pathlib,platform,re,selectors,shlex,shutil,signal,stat,struct,subprocess,tarfile,tempfile,threading,time,traceback,uuid,zlib; print(sys.version)' >> "$logdir/bootstrap.log" 2>&1; then ok=1; fi
        else printf '%s\n' 'Missing executable: /usr/bin/python3' >> "$logdir/bootstrap.log"; fi
        if (( ok )); then
            export ECM_ENV_BOOTSTRAP_LOG="$logdir"
            exec /usr/bin/python3 -I -B "$here/ecm_camera_manager.py" "$@"
        fi
        printf '\nEnvironment Check\n  Python : Missing or incompatible\n  Driver installation : Unavailable until Python is repaired\nLog: %s\n' "$logdir/bootstrap.log"
        local py_status=unknown
        if [[ -x /usr/bin/dpkg-query ]]; then
            py_status=$(/usr/bin/dpkg-query -W -f='${Status}' python3 2>/dev/null || true)
        fi
        printf 'Phase: environment-check\nPython package status: %s\nSystem package changes: none from this check\n' "$py_status" | tee -a "$logdir/bootstrap.log"
        if [[ -x /usr/bin/python3 || ( $py_status != '' && $py_status != 'unknown ok not-installed' && $py_status != 'deinstall ok config-files' ) ]]; then
            printf '%s\n' 'PYTHON_MANUAL_REPAIR_REQUIRED: system Python is installed but failed its check, incompatible, or its package state is unverified.' 'No automatic reinstall, upgrade or downgrade. Review bootstrap.log; Manager and online repair were not entered on this check.' | tee -a "$logdir/bootstrap.log"
            return 2
        fi
        printf '%s\n' 'System Python package is absent. Only a missing-only Driver plan may be reviewed; existing packages are never replaced.'
        printf '[1] Review online Driver runtime plan\n[2] Install missing Driver runtime packages\n[0] Exit\n'
        local choice repair_rc=0
        read -r -p 'Select: ' choice || return 2
        case "$choice" in
            1) /bin/bash "$here/bootstrap/ecm_online.sh" --plan --group driver --provider python3 || repair_rc=$?
               printf 'Online Driver plan returned: %s; no apply requested. See the separately printed environment package log.\n' "$repair_rc" >> "$logdir/bootstrap.log" ;;
            2) printf '%s\n' 'Only absent packages in the exact observed JP6/JP7 profile may be installed. Existing incompatible Python requires administrator repair.'
               if [[ $EUID -eq 0 ]]; then /bin/bash "$here/bootstrap/ecm_online.sh" --apply --group driver --provider python3 || repair_rc=$?
               elif [[ -x /usr/bin/sudo ]]; then
                   if /usr/bin/sudo -v; then /usr/bin/sudo -n -- /bin/bash "$here/bootstrap/ecm_online.sh" --apply --group driver --provider python3 || repair_rc=$?
                   else repair_rc=3; printf '%s\n' 'AUTHENTICATION_DECLINED: no repair started.' | tee -a "$logdir/bootstrap.log"; fi
               else repair_rc=3; printf '%s\n' 'Missing sudo. Ask the administrator to run this same entry as root.' | tee -a "$logdir/bootstrap.log"; fi
               printf 'Online Driver apply boundary returned: %s; review its separate result/log for changes. Python will be rechecked.\n' "$repair_rc" >> "$logdir/bootstrap.log" ;;
            *) return 2 ;;
        esac
    done
}
