#!/bin/bash
# Missing runtime only; pinned first, authenticated compatible fallback.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
unset PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH APT_CONFIG
umask 077
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
MODE=${1:---plan}; if [[ $# -gt 0 ]]; then shift; fi
GROUP=driver
REQUESTED_PROVIDERS=()
TARGET_PROFILE=auto
OPERATION_ID=
while [[ $# -gt 0 ]]; do
    case "$1" in
        --provider) [[ $# -ge 2 && $2 =~ ^(python3|kmod|psmisc|sudo)$ ]] || exit 2
                    REQUESTED_PROVIDERS+=("$2"); shift 2 ;;
        --group) [[ $# -ge 2 ]] || exit 2; GROUP=$2; shift 2 ;;
        --operation-id) [[ $# -ge 2 ]] || exit 2; OPERATION_ID=$2; shift 2 ;;
        --profile) [[ $# -ge 2 ]] || exit 2; TARGET_PROFILE=$2; shift 2 ;;
        *) printf '%s\n' 'Usage: ecm_online.sh --plan|--apply [--group driver] [--operation-id TOKEN] [--profile ID]'; exit 2 ;;
    esac
done
[[ $MODE == --plan || $MODE == --apply ]] && [[ $GROUP == driver ]] || exit 2
[[ -z $OPERATION_ID || $OPERATION_ID =~ ^[a-f0-9]{32}$ ]] || exit 2
STATE="${XDG_STATE_HOME:-${HOME:-/tmp}/.local/state}/ecm-camera-manager/environment"
if [[ $EUID -eq 0 ]]; then STATE=/var/log/ecm-environment; fi
[[ ! -L $STATE ]] || exit 2
mkdir -p -- "$STATE"
if [[ -n $OPERATION_ID ]]; then
    LOG="$STATE/online.$OPERATION_ID"; mkdir -m 700 -- "$LOG" || exit 2
else LOG=$(mktemp -d "$STATE/online.XXXXXXXX"); fi
TEMP=
STAGE=preflight
ERROR=NONE
RESULT=STARTED
PHASE=environment-check
PLAN_STARTED=no
DOWNLOAD_STARTED=no
APPLY_STARTED=no
SYSTEM_CHANGE=none
CONFIRMATION=not_requested
REASON='Operation started; no installation yet'
phase() { PHASE=$1; printf 'Phase: %s\n' "$PHASE" >> "$LOG/phases.log"; printf 'Phase: %s\n' "$PHASE"; }
printf 'Environment package log: %s\n' "$LOG"
printf '%s\n' 'Online review may download temporary metadata/archives before confirmation; only one final Y authorizes installation.'
finish() {
    local rc=$?
    if [[ $rc -ne 0 && $ERROR == NONE ]]; then ERROR=UNEXPECTED_STAGE_FAILURE; RESULT=STOPPED; REASON='Unexpected stop; inspect the recorded stage'; fi
    printf 'exit=%s\nmode=%s\nfeature=%s\nstage=%s\nerror=%s\nstatus=%s\n' "$rc" "$MODE" "$GROUP" "$STAGE" "$ERROR" "$RESULT" > "$LOG/result.txt"
    printf 'operation_id=%s\nphase=%s\nplan_started=%s\ndownload_started=%s\napply_started=%s\nsystem_change=%s\nconfirmation=%s\nrecheck=NOT_RUN_BY_SHELL\n' "$OPERATION_ID" "$PHASE" "$PLAN_STARTED" "$DOWNLOAD_STARTED" "$APPLY_STARTED" "$SYSTEM_CHANGE" "$CONFIRMATION" >> "$LOG/result.txt"
    printf '%s\n' "$REASON" > "$LOG/stop-reason.txt"
    printf 'Environment result: %s / %s\nPhase: %s / Stage: %s\nSystem package changes: %s\nReason: %s\nLog: %s\n' "$RESULT" "$ERROR" "$PHASE" "$STAGE" "$SYSTEM_CHANGE" "$REASON" "$LOG"
    # Only the freshly allocated private operation directory is disposable.
    if [[ -n $TEMP && $TEMP == /var/tmp/ecm-online.* && -d $TEMP && ! -L $TEMP ]]; then rm -rf -- "$TEMP"; fi
    if [[ $EUID -eq 0 && ${SUDO_UID:-} =~ ^[0-9]+$ && ${SUDO_GID:-} =~ ^[0-9]+$ ]]; then
        chown -R -- "$SUDO_UID:$SUDO_GID" "$LOG"
    fi
}
trap finish EXIT
fail() { ERROR=$1; shift; RESULT=STOPPED; REASON=$*; printf '%s: %s\n' "$ERROR" "$*" >> "$LOG/diagnostic.log"; printf '%s: %s\n' "$ERROR" "$*" >&2; exit 2; }
installed_status() { [[ $1 == 'install ok installed'$'\t'* || $1 == 'hold ok installed'$'\t'* ]]; }
run_stage() {
    local name=$1 rc; shift
    STAGE=$name
    set +e
    "$@" > "$LOG/$name.stdout" 2> "$LOG/$name.stderr"
    rc=$?
    set -e
    printf '%s\n' "$rc" > "$LOG/$name.exit"
    return "$rc"
}
phase environment-check
if [[ $MODE == --apply && $EUID -ne 0 ]]; then fail AUTHORIZATION_REQUIRED 'Use the authenticated Manager or administrator entry; repair was not started'; fi
for tool in apt-get dpkg dpkg-query dpkg-deb sha256sum cp awk grep sed mktemp find sort cmp stat timeout flock; do
    command -v "$tool" >/dev/null || fail BASE_TOOL_MISSING "Missing base tool: $tool; administrator repair required."
done
{ cat /etc/os-release; cat /etc/nv_tegra_release 2>/dev/null || true; uname -m; } > "$LOG/environment.txt"
OS=$(sed -n 's/^ID=//p' /etc/os-release | tr -d '"')
UBUNTU=$(sed -n 's/^VERSION_ID=//p' /etc/os-release | tr -d '"')
ARCH=$(dpkg --print-architecture)
L4T=$(sed -n 's/^# R\([0-9][0-9]*\).*REVISION: \([0-9.][0-9.]*\),.*/r\1.\2/p' /etc/nv_tegra_release 2>/dev/null || true)
PROFILE=
while IFS=$'\t' read -r id os ubuntu arch l4t nvidia packages bundle sums apply; do
    [[ -z $id || $id == \#* ]] && continue
    if [[ $OS == "$os" && $UBUNTU == "$ubuntu" && $ARCH == "$arch" && $L4T == "$l4t" ]]; then
        [[ -z $PROFILE ]] || fail PROFILE_AMBIGUOUS 'Ambiguous observed host'
        [[ $bundle =~ ^dependencies/jp[67]/[a-zA-Z0-9./-]+/arm64$ && $bundle != *..* && $sums =~ ^[a-f0-9]{64}$ ]] || fail METADATA_INVALID 'Invalid profile metadata path'
        NVIDIA_VERSION=$nvidia; PROFILE=$id; BUNDLE="$HERE/$bundle"; EXPECTED_SUMS=$sums; APPLY_ENABLED=$apply
    fi
done < "$HERE/ecm_env/profiles.tsv"
printf 'os=%s\nubuntu=%s\narchitecture=%s\nl4t=%s\nobserved_profile=%s\nselected_target=%s\nfeature=%s\n' "$OS" "$UBUNTU" "$ARCH" "$L4T" "$PROFILE" "$TARGET_PROFILE" "$GROUP" > "$LOG/profile.txt"
[[ -n $PROFILE ]] || fail PROFILE_UNSUPPORTED 'No exact observed Ubuntu / arm64 / L4T profile'
[[ $TARGET_PROFILE == auto || $TARGET_PROFILE == "$PROFILE" ]] || fail PROFILE_MISMATCH 'Selection cannot override the observed host'
[[ $MODE != --apply || $APPLY_ENABLED == 1 ]] || fail APPLY_DISABLED 'Profile allows planning only'
STAGE=metadata
[[ -f $BUNDLE/SHA256SUMS && ! -L $BUNDLE && -z $(find "$BUNDLE" -type l -print -quit) ]] || fail METADATA_INVALID 'Missing metadata or unsafe symlink'
[[ $(sha256sum "$BUNDLE/SHA256SUMS" | awk '{print $1}') == "$EXPECTED_SUMS" ]] || fail METADATA_HASH_MISMATCH 'Metadata inventory differs'
TEMP=$(mktemp -d /var/tmp/ecm-online.XXXXXXXX)
mkdir "$TEMP/repo"
cp -- "$BUNDLE/SHA256SUMS" "$TEMP/repo/SHA256SUMS"
mapfile -t INVENTORY < <(awk '{print $2}' "$TEMP/repo/SHA256SUMS" | sort)
EXPECTED=(Packages manifest.json packages.tsv prerequisites-driver.tsv seeds-driver.txt)
[[ ${INVENTORY[*]} == "${EXPECTED[*]}" ]] || fail METADATA_INVALID 'Incomplete or unexpected metadata inventory'
for file in "${EXPECTED[@]}"; do [[ -f $BUNDLE/$file ]] || fail METADATA_INVALID "Missing $file"; cp -- "$BUNDLE/$file" "$TEMP/repo/$file"; done
[[ $(sha256sum "$TEMP/repo/SHA256SUMS" | awk '{print $1}') == "$EXPECTED_SUMS" ]] || fail METADATA_HASH_MISMATCH 'Copied metadata inventory differs'
(cd "$TEMP/repo" && sha256sum --strict --check SHA256SUMS) > "$LOG/metadata-verification.log" 2>&1 || fail METADATA_HASH_MISMATCH 'Corrupt metadata'
cp -- "$TEMP/repo/manifest.json" "$LOG/runtime-manifest.json"
declare -A SEEN=()
while IFS=$'\t' read -r package version architecture hash filename bytes url extra; do
    [[ $package =~ ^[a-z0-9][a-z0-9+.-]*$ && $version =~ ^[A-Za-z0-9.+:~_-]+$ && ( $architecture == arm64 || $architecture == all ) && $hash =~ ^[0-9a-f]{64}$ && $filename =~ ^debs/[A-Za-z0-9+._%:~-]+\.deb$ && $bytes =~ ^[1-9][0-9]*$ && -z $extra ]] || fail METADATA_INVALID 'Invalid package row'
    [[ $url =~ ^https://(ports\.ubuntu\.com/ubuntu-ports/pool/|repo\.download\.nvidia\.com/jetson/)[A-Za-z0-9+._%:/~-]+\.deb$ && $url != */../* ]] || fail SOURCE_NOT_ALLOWED "Untrusted source for $package"
    [[ -z ${SEEN[$package]+found} ]] || fail METADATA_INVALID "Duplicate package: $package"
    SEEN[$package]=1
done < "$TEMP/repo/packages.tsv"
[[ ${#SEEN[@]} -gt 0 ]] || fail METADATA_INVALID 'Empty package catalog'
STAGE=host-state
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\t${Status}\n' > "$LOG/packages-before.tsv" || fail PACKAGE_STATE_UNAVAILABLE 'Cannot read installed packages'
awk -F '\t' '
    NF != 4 || $1 !~ /^[a-z0-9][a-z0-9+.-]*$/ ||
    $4 !~ /^(unknown|install|hold|deinstall|purge) (ok|reinstreq) (not-installed|config-files|half-installed|unpacked|half-configured|triggers-awaited|triggers-pending|installed)$/ ||
    ($4 ~ / installed$/ && ($2 == "" || $3 == "")) {bad=1}
    END {exit (NR == 0 || bad)}
' "$LOG/packages-before.tsv" || fail PACKAGE_STATE_UNAVAILABLE 'Invalid or empty dpkg-query package list'
sha256sum /var/lib/dpkg/status > "$LOG/status-before.sha256"
dpkg --audit > "$LOG/dpkg-audit.txt" 2>&1 || fail PACKAGE_DATABASE_PARTIAL 'dpkg audit failed'
[[ ! -s $LOG/dpkg-audit.txt ]] || fail PACKAGE_DATABASE_PARTIAL 'Package database needs administrator repair'
# Limit repair to absent providers whose tools are required and unavailable.
cp -- "$TEMP/repo/prerequisites-driver.tsv" "$TEMP/prerequisites-selected.tsv"
: > "$TEMP/seeds-selected.txt"
available() { [[ -x "$1" ]] && "$1" --version >/dev/null 2>&1; }
while read -r provider; do
    if (( ${#REQUESTED_PROVIDERS[@]} )); then
        requested=no
        for p in "${REQUESTED_PROVIDERS[@]}"; do [[ $p != "$provider" ]] || requested=yes; done
        [[ $requested == yes ]] || continue
    fi
    case "$provider" in
        python3) /usr/bin/python3 -I -B -c 'import sys,fcntl,hashlib; assert sys.version_info >= (3,10)' >/dev/null 2>&1 && continue ;;
        kmod) available /usr/sbin/modinfo && available /usr/sbin/depmod && continue ;;
        psmisc) available /usr/bin/fuser && continue ;;
        sudo) [[ $EUID -eq 0 ]] && continue; available /usr/bin/sudo && continue ;;
        *) fail METADATA_INVALID "Unknown driver provider: $provider" ;;
    esac
    status=$(awk -F '\t' -v p="$provider" -v arch="$ARCH" '$1==p && ($3==arch || $3=="all"){print $4}' "$LOG/packages-before.tsv")
    case "$status" in
        ''|'unknown ok not-installed'|'deinstall ok config-files'|'purge ok not-installed') printf '%s\n' "$provider" >> "$TEMP/seeds-selected.txt" ;;
        'install ok installed'|'hold ok installed') ;;
        *) fail PACKAGE_DATABASE_PARTIAL "Package state requires manual review: $provider ($status)" ;;
    esac
done < "$TEMP/repo/seeds-driver.txt"
cp -- "$TEMP/seeds-selected.txt" "$LOG/selected-seeds.txt"
while IFS=$'\t' read -r package version; do
    actual=$(dpkg-query -W -f='${Status}\t${Version}' "$package" 2>/dev/null || true)
    installed_status "$actual" || fail BASE_PREREQUISITE_MISSING "Missing prerequisite: $package"
    [[ $version == - || ${actual#*$'\t'} == "$version" ]] || fail BSP_VERSION_MISMATCH "Fixed BSP prerequisite differs: $package ($actual)"
done < "$TEMP/prerequisites-selected.tsv"
MISSING=()
while read -r package; do
    [[ $package =~ ^[a-z0-9][a-z0-9+.-]*$ && -n ${SEEN[$package]+found} ]] || fail METADATA_INVALID 'Unknown feature seed'
    actual=$(dpkg-query -W -f='${Status}\t${Version}' "$package" 2>/dev/null || true)
    version=$(awk -F '\t' -v p="$package" '$1==p {print $2}' "$TEMP/repo/packages.tsv")
    printf '%s\t%s\t%s\n' "$package" "$actual" "$version" >> "$LOG/seed-versions.tsv"
    MISSING+=("$package=$version")
done < "$TEMP/seeds-selected.txt"
if [[ ${#MISSING[@]} -eq 0 ]]; then
    RESULT=NO_CHANGE
    REASON='No absent seed packages; installed-but-broken or incompatible packages are not reinstalled. Functional recheck is required.'
    printf 'Feature: %s / Profile: %s\nNo missing seed packages; no download or installation. Functional readiness is checked separately.\n' "$GROUP" "$PROFILE" | tee "$LOG/summary.txt"
    exit 0
fi
mkdir -p "$TEMP/lists/partial" "$TEMP/cache/archives/partial" "$TEMP/empty" "$TEMP/downloads"
printf 'deb [trusted=yes] file:%s/repo ./\n' "$TEMP" > "$TEMP/sources.list"
cat > "$TEMP/apt.conf" <<EOF
Dir::Etc::main "/dev/null";
Dir::Etc::parts "$TEMP/empty";
Dir::Etc::sourcelist "$TEMP/sources.list";
Dir::Etc::sourceparts "$TEMP/empty";
Dir::State::lists "$TEMP/lists";
Dir::State::extended_states "$TEMP/extended_states";
Dir::Cache "$TEMP/cache";
Dir::Cache::archives "$TEMP/cache/archives";
Dir::Log "$LOG";
Dir::Cache::pkgcache "";
Dir::Cache::srcpkgcache "";
Dir::Etc::preferences "$TEMP/empty/preferences";
Dir::Etc::preferencesparts "$TEMP/empty";
APT::Sandbox::User "root";
APT::Install-Recommends "false";
APT::Install-Suggests "false";
Acquire::Languages "none";
Acquire::Retries "0";
Acquire::http::Proxy "http://127.0.0.1:9";
Acquire::https::Proxy "http://127.0.0.1:9";
DPkg::Lock::Timeout "0";
EOF
export APT_CONFIG="$TEMP/apt.conf"
phase plan
PLAN_STARTED=yes
run_stage local-index apt-get update || fail LOCAL_INDEX_FAILED 'Could not read the pinned local metadata'
if ! run_stage plan apt-get --simulate --no-remove --no-install-recommends install "${MISSING[@]}"; then
    if grep -Eqi "Version .* was not found|has no installation candidate|Unable to locate package" "$LOG/plan.stderr"; then
        fail PINNED_VERSION_UNAVAILABLE 'Pinned version resolution stopped before download; the existing HTTP 404 fallback was not entered. No version-policy expansion is performed.'
    fi
    fail DEPENDENCY_CONFLICT 'Pinned dependencies cannot be satisfied without changing the existing system; stopped before download'
fi
grep -Eq '^(Remv|Purg) ' "$LOG/plan.stdout" && fail EXISTING_PACKAGE_CHANGE 'Removal plan refused'
grep -Eq '^Inst [^ ]+ \[' "$LOG/plan.stdout" && fail EXISTING_PACKAGE_CHANGE 'Upgrade/downgrade/reinstall refused'
mapfile -t PLANNED < <(awk '/^Inst / {sub(/:arm64$/, "", $2);print $2}' "$LOG/plan.stdout")
[[ ${#PLANNED[@]} -gt 0 ]] || fail PLAN_EMPTY 'No installable missing package plan'
PLANNED_VERSIONS=()
TOTAL_BYTES=0
for package in "${PLANNED[@]}"; do
    row=$(awk -F '\t' -v p="$package" '$1==p {print}' "$TEMP/repo/packages.tsv")
    [[ -n $row ]] || fail PLAN_OUTSIDE_LOCK "Unlisted dependency: $package"
    IFS=$'\t' read -r package version architecture hash filename bytes url <<< "$row"
    [[ $package != nvidia-* ]] && [[ $package != linux-* && $package != cuda-* && $package != libnvidia-* ]] || fail BSP_KERNEL_CHANGE 'BSP/kernel package mutation refused'
    planned_version=$(awk -v p="$package" '$1=="Inst" {n=$2;sub(/:arm64$/, "", n); if(n==p){v=$3;sub(/^\(/,"",v);print v}}' "$LOG/plan.stdout")
    [[ $planned_version == "$version" ]] || fail PLAN_VERSION_MISMATCH "Wrong version in plan: $package"
    printf '%s\n' "$row" >> "$LOG/download-plan.tsv"
    PLANNED_VERSIONS+=("$package=$version"); TOTAL_BYTES=$((TOTAL_BYTES+bytes))
done
[[ -x /usr/lib/apt/apt-helper ]] || fail DOWNLOADER_MISSING 'System apt-helper is required; no additional downloader will be installed'
[[ -s /etc/ssl/certs/ca-certificates.crt ]] || fail TLS_TRUST_UNAVAILABLE 'System HTTPS trust is required; ask the administrator to restore it'
cat > "$TEMP/download.conf" <<EOF
Dir::Etc::main "/dev/null";
Dir::Etc::parts "$TEMP/empty";
APT::Sandbox::User "root";
Acquire::Retries "0";
Acquire::https::Timeout "30";
Acquire::http::Timeout "30";
Acquire::https::AllowRedirect "false";
Acquire::http::AllowRedirect "false";
EOF
source "$HERE/bootstrap/ecm_resolve.sh"
acquire_plan yes
if [[ ${#UNAVAILABLE[@]} -gt 0 ]]; then official_replan; acquire_plan no; fi
{
    printf 'Feature: %s / Observed profile: %s\nMissing packages: %s / Download bytes: %s\n' "$GROUP" "$PROFILE" "${#PLANNED[@]}" "$TOTAL_BYTES"
    awk -F '\t' '{printf "  %s = %s [%s]  %s bytes\n    %s\n",$1,$2,$3,$6,$7}' "$LOG/download-plan.tsv"
    printf 'Existing packages are preserved. Plan and logs: %s\n' "$LOG"
} | tee "$LOG/summary.txt"
if [[ $MODE == --plan ]]; then RESULT=PLAN_READY; REASON='Plan verified; no packages installed. Functional readiness is checked separately.'; printf '%s\n' 'Verified plan only; temporary downloads are removed and no packages are installed.'; exit 0; fi
phase confirmation
STAGE=confirmation
CONFIRMATION=pending
read -r -p 'Install this verified missing-package plan? [y/N]: ' accepted || accepted=
if [[ $accepted != y && $accepted != Y ]]; then RESULT=CANCELLED; CONFIRMATION=declined; REASON='Installation confirmation was declined; no packages installed.'; printf '%s\n' 'Cancelled; no installation. Temporary downloads will be removed.'; exit 0; fi
CONFIRMATION=accepted
STAGE=operation-lock
LOCK_PATH=/run/lock/ecm-camera-manager-install.lock
[[ ! -L $LOCK_PATH ]] || fail INSTALL_BUSY 'Unsafe operation lock'
if [[ ! -e $LOCK_PATH ]]; then (umask 022; set -C; : > "$LOCK_PATH") || fail INSTALL_BUSY 'Cannot create the shared operation lock'; fi
[[ -f $LOCK_PATH && $(stat -c %u:%g:%h "$LOCK_PATH") == 0:0:1 ]] || fail INSTALL_BUSY 'Unexpected operation lock identity'
lock_mode=$(stat -c %a "$LOCK_PATH")
[[ $lock_mode == 600 || $lock_mode == 644 ]] || fail INSTALL_BUSY 'Unexpected operation lock permissions'
exec {REPAIR_LOCK}< "$LOCK_PATH"
flock --exclusive --nonblock "$REPAIR_LOCK" || fail INSTALL_BUSY 'Another install or camera operation is active; close it normally'
STAGE=pre-apply
sha256sum --status --check "$LOG/status-before.sha256" || fail PACKAGE_STATE_CHANGED 'Package state changed after planning; review a fresh plan'
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\t${Status}\n' > "$LOG/packages-preapply.tsv" || fail PACKAGE_STATE_UNAVAILABLE 'Cannot re-read installed packages'
cmp -s "$LOG/packages-before.tsv" "$LOG/packages-preapply.tsv" || fail PACKAGE_STATE_CHANGED 'Installed packages changed after planning'
# Re-solve with exact versions for EVERY planned dependency, still from the
# private metadata only; not just the top-level seeds.
run_stage replan apt-get --simulate --no-remove --no-install-recommends install "${PLANNED_VERSIONS[@]}" || fail DEPENDENCY_CONFLICT 'Plan changed after acquisition'
grep -E '^(Inst|Remv|Purg) ' "$LOG/plan.stdout" | sort > "$TEMP/plan-actions"
grep -E '^(Inst|Remv|Purg) ' "$LOG/replan.stdout" | sort > "$TEMP/replan-actions"
cmp -s "$TEMP/plan-actions" "$TEMP/replan-actions" || fail PLAN_CHANGED 'APT actions changed after acquisition'
cp -- "$LOG/download-plan.tsv" "$TEMP/approved.tsv"
cp -- "$LOG/status-before.sha256" "$TEMP/status-before.sha256"
cat > "$TEMP/check-state.sh" <<'HOOK'
#!/bin/bash
set -euo pipefail
sha256sum --status --check "${0%/*}/status-before.sha256" || { printf '%s\n' 'PACKAGE_STATE_CHANGED: final locked guard' >&2; exit 90; }
HOOK
cat > "$TEMP/check-install.sh" <<'HOOK'
#!/bin/bash
set -euo pipefail
root=${0%/*}
sha256sum --status --check "$root/status-before.sha256" || { printf '%s\n' 'PACKAGE_STATE_CHANGED: final locked archive guard' >&2; exit 90; }
: > "$root/hook-packages"
while read -r path; do
    [[ -f $path && ! -L $path && $path == "$root/cache/archives/"* ]] || exit 91
    [[ $(stat -c %u:%g:%a "$path") == 0:0:600 ]] || exit 91
    package=$(/usr/bin/dpkg-deb -f "$path" Package)
    version=$(/usr/bin/dpkg-deb -f "$path" Version)
    architecture=$(/usr/bin/dpkg-deb -f "$path" Architecture)
    [[ $architecture == arm64 || $architecture == all ]] || exit 92
    status=$(/usr/bin/dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)
    [[ $status == '' || $status == 'unknown ok not-installed' || $status == 'deinstall ok config-files' ]] || exit 93
    [[ $package != nvidia-* ]] && [[ $package != linux-* && $package != cuda-* && $package != libnvidia-* ]] || exit 94
    expected=$(awk -F '\t' -v p="$package" -v v="$version" -v a="$architecture" '$1==p && $2==v && $3==a {print $4}' "$root/approved.tsv")
    [[ -n $expected && $(sha256sum "$path" | awk '{print $1}') == "$expected" ]] || exit 95
    printf '%s\n' "$package" >> "$root/hook-packages"
done
awk -F '\t' '{print $1}' "$root/approved.tsv" | sort > "$root/approved-packages"
sort "$root/hook-packages" > "$root/hook-packages-sorted"
cmp -s "$root/approved-packages" "$root/hook-packages-sorted" || exit 96
HOOK
chmod 700 "$TEMP/check-state.sh" "$TEMP/check-install.sh"
printf '\nDPkg::Pre-Invoke { "%s/check-state.sh"; };\nDPkg::Pre-Install-Pkgs { "%s/check-install.sh"; };\n' "$TEMP" "$TEMP" >> "$TEMP/apt.conf"
phase install
STAGE=apply
APPLY_STARTED=yes
SYSTEM_CHANGE=possible
set +e
DEBIAN_FRONTEND=noninteractive apt-get --yes --no-download --no-remove --no-upgrade --no-install-recommends install "${PLANNED_VERSIONS[@]}" > "$LOG/execute.stdout" 2> "$LOG/execute.stderr"
rc=$?
set -e
printf '%s\n' "$rc" > "$LOG/execute.exit"
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\t${Status}\n' > "$LOG/packages-after.tsv" || true
if [[ $rc -ne 0 ]]; then
    if grep -Eqi 'Could not get lock|Unable to acquire.*lock|locked by another' "$LOG/execute.stderr"; then fail APT_LOCK_BUSY 'APT lock busy; verify package state before retry'; fi
    if grep -Eqi 'Permission denied|are you root' "$LOG/execute.stderr"; then fail APPLY_PERMISSION_DENIED 'APT permission denied; verify package state before retry'; fi
    fail APPLY_FAILED 'Installation stopped; partial state and logs preserved. No automatic retry or rollback.'
fi
SYSTEM_CHANGE=applied
RESULT=APPLIED_RECHECK_REQUIRED
REASON='APT completed; functional readiness must be rechecked separately'
phase recheck
printf '%s\n' 'Missing-package installation completed. Return to Environment Check for a functional recheck; camera output remains unverified.'
