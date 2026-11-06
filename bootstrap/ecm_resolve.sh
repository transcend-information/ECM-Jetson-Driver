#!/bin/bash
# Sourced only by ecm_online.sh after verified profile/catalog and host snapshot.
# All mutable state remains in the operation's private TEMP and LOG directories.
allowed_runtime() {
    [[ $1 != nvidia-* ]] &&
    [[ $1 != linux-* && $1 != cuda-* && $1 != libnvidia-* ]]
}
allowed_url() {
    [[ $1 =~ ^https://(ports\.ubuntu\.com/ubuntu-ports/pool/|repo\.download\.nvidia\.com/jetson/)[A-Za-z0-9+._%:/~-]+\.deb$ && $1 != */../* ]]
}
verify_archive() {
    local target=$1 package=$2 version=$3 architecture=$4 hash=$5 bytes=$6 metadata
    STAGE=archive-verification
    [[ -f $target && ! -L $target && $(stat -c %s "$target") == "$bytes" && $(sha256sum "$target" | awk '{print $1}') == "$hash" ]] || fail ARCHIVE_HASH_MISMATCH "Corrupt download: $package"
    metadata=$(dpkg-deb -f "$target" Package Version Architecture | sed 's/^[A-Za-z-]*: //') || fail ARCHIVE_METADATA_INVALID "Unreadable Debian archive: $package"
    [[ $metadata == "$package"$'\n'"$version"$'\n'"$architecture" ]] || fail ARCHIVE_IDENTITY_MISMATCH "Wrong package/version/architecture: $package"
    cp -- "$target" "$TEMP/cache/archives/${package}_${version//:/%3a}_${architecture}.deb"
    printf '%s\t%s\t%s\t%s\t%s\tVERIFIED\n' "$package" "$version" "$architecture" "$hash" "$bytes" >> "$LOG/download-results.tsv"
}
acquire_plan() {
    local allow_fallback=$1 package version architecture hash filename bytes url target code
    UNAVAILABLE=()
    while IFS=$'\t' read -r package version architecture hash filename bytes url; do
        target="$TEMP/downloads/$hash.deb"
        if [[ ! -f $target ]]; then
            printf 'Acquiring %s = %s (%s bytes); no installation yet\n' "$package" "$version" "$bytes"
            phase download
            DOWNLOAD_STARTED=yes
            if ! run_stage "download-$package" timeout 180 env APT_CONFIG="$TEMP/download.conf" /usr/lib/apt/apt-helper download-file "$url" "$target" "SHA256:$hash" "Checksum-FileSize:$bytes"; then
                code=DOWNLOAD_FAILED
                if grep -Eqi '(^|[^0-9])(404|410)([^0-9]|$)' "$LOG/download-$package.stderr"; then code=DOWNLOAD_NOT_FOUND
                elif grep -qi 'Hash Sum mismatch\|Hash mismatch' "$LOG/download-$package.stderr"; then code=DOWNLOAD_HASH_MISMATCH
                elif grep -qi 'certificate\|TLS\|SSL' "$LOG/download-$package.stderr"; then code=TLS_FAILED
                elif grep -qi 'resolve\|connect\|timed out\|network\|timeout' "$LOG/download-$package.stderr" || [[ $(cat "$LOG/download-$package.exit") == 124 ]]; then code=NETWORK_UNAVAILABLE; fi
                if [[ $code == DOWNLOAD_NOT_FOUND && $allow_fallback == yes ]]; then
                    UNAVAILABLE+=("$package")
                    printf '%s\t%s\tHTTP_404_OR_410\t%s\n' "$package" "$version" "$url" >> "$LOG/fallback-triggers.tsv"
                    continue
                fi
                fail "$code" "Acquisition failed: $package; transport/integrity errors never trigger version fallback"
            fi
        fi
        verify_archive "$target" "$package" "$version" "$architecture" "$hash" "$bytes"
    done < "$LOG/download-plan.tsv"
}
official_replan() {
    local suite branch key package version architecture hash bytes filename uri uri_file uri_bytes uri_hash old requested reason row
    local -a requests=()
    STAGE=official-sources
    case "$PROFILE" in
        jp6-r36.4.7) suite=jammy; branch=r36.4 ;;
        jp7-orin-r39.2.1) suite=noble; branch=r39.2 ;;
        *) fail PROFILE_UNSUPPORTED 'No official source mapping for observed profile' ;;
    esac
    command -v apt-cache >/dev/null || fail BASE_TOOL_MISSING 'apt-cache is required for authenticated version resolution'
    key=/usr/share/keyrings/ubuntu-archive-keyring.gpg
    [[ -s $key && ! -L $key ]] || fail REPOSITORY_TRUST_UNAVAILABLE 'Ubuntu archive keyring is missing'
    mkdir -p "$TEMP/official-lists/partial" "$LOG/repository-metadata"
    cp -- "$key" "$TEMP/ubuntu-archive-keyring.gpg"
    : > "$TEMP/official.list"
    for pocket in "$suite" "$suite-updates" "$suite-security"; do
        printf 'deb [arch=arm64 signed-by=%s/ubuntu-archive-keyring.gpg] https://ports.ubuntu.com/ubuntu-ports %s main universe\n' "$TEMP" "$pocket" >> "$TEMP/official.list"
    done
    sed '/Acquire::http::Proxy/d; /Acquire::https::Proxy/d' "$TEMP/apt.conf" > "$TEMP/official.conf"
    cat >> "$TEMP/official.conf" <<EOF
Dir::Etc::sourcelist "$TEMP/official.list";
Dir::State::lists "$TEMP/official-lists";
Dir::Etc::trusted "$TEMP/empty/trusted.gpg";
Dir::Etc::trustedparts "$TEMP/empty";
Dir::Etc::preferences "$TEMP/preferences";
Dir::Etc::preferencesparts "$TEMP/empty";
Acquire::AllowInsecureRepositories "false";
Acquire::AllowDowngradeToInsecureRepositories "false";
Acquire::https::AllowRedirect "false";
Acquire::http::AllowRedirect "false";
Acquire::https::Timeout "30";
Acquire::http::Timeout "30";
APT::Get::AllowUnauthenticated "false";
APT::Update::Error-Mode "any";
EOF
    cat > "$TEMP/preferences" <<EOF
Package: nvidia-* linux-* cuda-* libnvidia-*
Pin: version *
Pin-Priority: -1
EOF
    cp -- "$TEMP/official.list" "$TEMP/preferences" "$LOG/repository-metadata/"
    export APT_CONFIG="$TEMP/official.conf"
    phase download
    DOWNLOAD_STARTED=yes
    run_stage signed-index timeout 300 apt-get update || fail SIGNED_INDEX_FAILED 'Official index acquisition/authentication failed; no installation'
    # APT has authenticated Release/InRelease -> Packages. Retain signed release
    # material and hashes of the exact indexes used to derive this transaction.
    find "$TEMP/official-lists" -maxdepth 1 -type f -print0 | sort -z | xargs -0 -r sha256sum > "$LOG/repository-metadata/index-sha256.txt"
    for index in "$TEMP/official-lists/"*InRelease "$TEMP/official-lists/"*Release "$TEMP/official-lists/"*Release.gpg; do
        [[ ! -f $index ]] || cp -- "$index" "$LOG/repository-metadata/"
    done
    for requested in "${MISSING[@]}"; do
        package=${requested%%=*}; version=${requested#*=}; reason=
        for absent in "${UNAVAILABLE[@]}"; do [[ $package != "$absent" ]] || reason=HTTP_404_OR_410; done
        if [[ -z $reason ]] && ! apt-cache show "$requested" > "$TEMP/available" 2> "$TEMP/available.err"; then reason=VERSION_ABSENT_FROM_SIGNED_INDEX; fi
        if [[ -z $reason && ! -s $TEMP/available ]]; then reason=VERSION_ABSENT_FROM_SIGNED_INDEX; fi
        if [[ -n $reason ]]; then
            requests+=("$package")
            printf '%s\t%s\t%s\n' "$package" "$version" "$reason" >> "$LOG/resolution-requests.tsv"
        else requests+=("$requested"); fi
    done
    for package in "${UNAVAILABLE[@]}"; do requests+=("$package"); done
    cp -- "$LOG/plan.stdout" "$LOG/pinned-plan.stdout"
    cp -- "$LOG/download-plan.tsv" "$LOG/pinned-download-plan.tsv"
    phase plan
    run_stage plan apt-get --simulate --no-remove --no-install-recommends install "${requests[@]}" || fail NO_COMPATIBLE_VERSION 'Latest official candidate conflicts with this environment; older versions were not exhaustively searched'
    grep -Eq '^(Remv|Purg) |^Inst [^ ]+ \[' "$LOG/plan.stdout" && fail EXISTING_PACKAGE_CHANGE 'Latest candidate would replace/remove an existing package'
    mapfile -t PLANNED < <(awk '/^Inst / {sub(/:arm64$/, "", $2);print $2}' "$LOG/plan.stdout")
    [[ ${#PLANNED[@]} -gt 0 ]] || fail NO_COMPATIBLE_VERSION 'Latest official candidate produced no installable plan'
    : > "$LOG/download-plan.tsv"; : > "$LOG/resolved-versions.tsv"
    PLANNED_VERSIONS=(); TOTAL_BYTES=0
    for package in "${PLANNED[@]}"; do
        [[ $package =~ ^[a-z0-9][a-z0-9+.-]*$ ]] && allowed_runtime "$package" || fail BSP_KERNEL_CHANGE 'Protected package requested by fallback plan'
        version=$(awk -v p="$package" '$1=="Inst" {n=$2;sub(/:arm64$/, "",n);if(n==p){v=$3;sub(/^\(/,"",v);print v}}' "$LOG/plan.stdout")
        [[ $version =~ ^[A-Za-z0-9.+:~_-]+$ ]] || fail PLAN_VERSION_MISMATCH 'Invalid resolved version'
        run_stage "metadata-$package" apt-cache show "$package=$version" || fail RESOLVED_METADATA_INVALID 'Resolved version is absent from authenticated indexes'
        # APT show may include more than one repository copy. Parse exactly its
        # first stanza and then independently match the URI/hash selected by APT.
        row=$(awk 'BEGIN {RS="";FS="\n"} NR==1 {for(i=1;i<=NF;i++){n=index($i,": ");if(n){k=substr($i,1,n-1);v=substr($i,n+2);f[k]=v}} printf "%s\t%s\t%s\t%s\t%s\t%s",f["Package"],f["Version"],f["Architecture"],f["SHA256"],f["Size"],f["Filename"]}' "$LOG/metadata-$package.stdout")
        IFS=$'\t' read -r name actual architecture hash bytes filename <<< "$row"
        [[ $name == "$package" && $actual == "$version" && ( $architecture == arm64 || $architecture == all ) && $hash =~ ^[a-f0-9]{64}$ && $bytes =~ ^[1-9][0-9]*$ && -n $filename ]] || fail RESOLVED_METADATA_INVALID 'Invalid resolved archive identity'
        run_stage "uri-$package" apt-get --print-uris download "$package=$version" || fail RESOLVED_SOURCE_INVALID 'Cannot resolve an authenticated archive URI'
        row=$(awk '/^\047/ {print;exit}' "$LOG/uri-$package.stdout")
        read -r uri uri_file uri_bytes uri_hash <<< "$row"
        uri=${uri#\'}; uri=${uri%\'}
        allowed_url "$uri" || fail SOURCE_NOT_ALLOWED 'Resolved archive is outside the official source allowlist'
        [[ $uri_bytes == "$bytes" ]] || fail RESOLVED_METADATA_INVALID 'APT URI and authenticated archive size disagree'
        case "$uri_hash" in
            SHA256:*) [[ $uri_hash == "SHA256:$hash" ]] || fail RESOLVED_METADATA_INVALID 'APT URI and authenticated SHA256 disagree' ;;
            SHA512:*)
                strong_hash=$(awk 'BEGIN {RS="";FS="\n"} NR==1 {for(i=1;i<=NF;i++)if($i ~ /^SHA512: /)print substr($i,9)}' "$LOG/metadata-$package.stdout")
                [[ $strong_hash =~ ^[a-f0-9]{128}$ && $uri_hash == "SHA512:$strong_hash" ]] || fail RESOLVED_METADATA_INVALID 'APT URI and authenticated SHA512 disagree' ;;
            *) fail RESOLVED_METADATA_INVALID 'APT URI lacks an accepted authenticated digest' ;;
        esac
        printf '%s\t%s\t%s\t%s\tdebs/%s\t%s\t%s\n' "$package" "$version" "$architecture" "$hash" "${uri##*/}" "$bytes" "$uri" >> "$LOG/download-plan.tsv"
        old=$(awk -F '\t' -v p="$package" '$1==p{print $2}' "$LOG/pinned-download-plan.tsv")
        printf '%s\t%s\t%s\tOFFICIAL_COMPATIBLE_REPLAN\t%s\t%s\n' "$package" "${old:--}" "$version" "$uri" "$hash" >> "$LOG/resolved-versions.tsv"
        PLANNED_VERSIONS+=("$package=$version"); TOTAL_BYTES=$((TOTAL_BYTES+bytes))
    done
    # Use this exact authenticated config during locked replan and final apply.
    cp -- "$TEMP/official.conf" "$TEMP/apt.conf"
    export APT_CONFIG="$TEMP/apt.conf"
}
