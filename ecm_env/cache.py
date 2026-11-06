"""Host-bound startup observations, never authorization for an operation."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
import tempfile
import time
from . import check
from .profiles import PROFILES, load_profiles, select_observed
from ecm_install import VERSION
from ecm_install import session
from ecm_install.contracts import safe_path, load_json, document_digest

SCHEMA = 'ecm-environment-startup-v4-driver'
MAX_AGE_SECONDS = 86400
PACKAGE = Path(__file__).resolve().parents[1]


def _hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def fingerprint(package=PACKAGE):
    """Small passive probes plus content hashes; no GI import, sudo or camera."""
    fields = {'schema': SCHEMA, 'check_schema': check.INTERFACE_VERSION,
              'installer_version': VERSION, 'system': platform.system(),
              'kernel': platform.release(), 'machine_arch': platform.machine()}
    fields['execution_identity'] = session.cache_identity()
    errors = []
    observed = {}
    def required(name, path):
        try:
            value = Path(path).read_bytes()
            if not value.strip(): raise ValueError('empty identity')
            fields[name] = hashlib.sha256(value).hexdigest()
            return value
        except (OSError, ValueError) as exc: errors.append(name + ': ' + str(exc))
    required('machine_identity', '/etc/machine-id')
    # A cloned machine-id or workspace must not inherit another boot's Ready.
    required('boot_identity', '/proc/sys/kernel/random/boot_id')
    os_release = required('os_release', '/etc/os-release')
    l4t_release = required('l4t_release', '/etc/nv_tegra_release')
    if os_release:
        for line in os_release.decode('utf-8', errors='replace').splitlines():
            if line.startswith('VERSION_ID='): observed['ubuntu'] = line.split('=',1)[1].strip('"')
            if line.startswith('ID='): observed['os_id'] = line.split('=',1)[1].strip('"')
    if l4t_release:
        match = re.search(r'R(\d+).*REVISION:\s*([\d.]+)', l4t_release.decode('utf-8', errors='replace'))
        if match: observed['l4t'] = 'r'+match[1]+'.'+match[2]
    try:
        python = Path(check.PYTHON).resolve(strict=True)
        fields['python'] = {'path': str(python), 'sha256': _hash(python),
                            'process': sys.executable, 'version': sys.version,
                            'prefix': sys.prefix, 'base_prefix': sys.base_prefix}
    except OSError as exc: errors.append('system_python: ' + str(exc))
    query = check.run(['/usr/bin/dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${Architecture}\t${Status}\n'])
    rows = query['stdout'].splitlines()
    if query['exit'] != 0 or not rows: errors.append('dpkg package state unavailable')
    elif any(len(r.split('\t')) != 4 or r.split('\t')[-1] not in
             ('install ok installed', 'deinstall ok config-files', 'hold ok installed') for r in rows):
        errors.append('dpkg package state is partial or pending')
    fields['dpkg_state'] = document_digest(sorted(rows))
    try:
        stamp = Path('/var/lib/dpkg/status').stat()
        fields['dpkg_status_file'] = [stamp.st_ino, stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns]
    except OSError as exc: errors.append('dpkg status file: ' + str(exc))
    try:
        updates = Path('/var/lib/dpkg/updates')
        if not updates.is_dir() or any(p.name.isdigit() for p in updates.iterdir()):
            errors.append('dpkg updates unavailable or pending')
    except OSError as exc: errors.append('dpkg updates: ' + str(exc))
    arch = check.run(['/usr/bin/dpkg', '--print-architecture'])
    if arch['exit'] != 0 or not arch['stdout'].strip(): errors.append('dpkg architecture unavailable')
    fields['architecture'] = arch['stdout'].strip()
    if arch['exit'] == 0: observed['architecture'] = fields['architecture']
    profile = select_observed(observed)
    fields['observed_profile'] = profile['id'] if profile else None
    fields['dependency_manifest_path'] = profile['bundle']+'/manifest.json' if profile else None
    fields['dependency_manifest'] = None
    fields['dependency_source_pin'] = profile['sums'] if profile else None
    if profile is None: errors.append('No exact observed host environment profile')
    fields['tools'] = {}
    for name, value in check.TOOLS.items():
        path = Path(value)
        try:
            stat = path.stat()
            fields['tools'][name] = [str(path.resolve()), stat.st_size, stat.st_mtime_ns,
                                      stat.st_ctime_ns, os.access(path, os.X_OK)]
        except OSError: fields['tools'][name] = None
    try:
        root = Path(package).absolute()
        roots = [root / name for name in ('bootstrap','ecm_env','ecm_install','dependencies','manifests','packages')]
        entries = list(root.glob('*.py')) + list(root.glob('*.sh'))
        for path in roots:
            if not path.is_dir(): raise ValueError('Required product directory missing: ' + path.name)
            entries.extend(p for p in path.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
        files = {}
        for path in sorted(set(entries)):
            rel = path.relative_to(root).as_posix()
            safe_path(root, rel)
            files[rel] = _hash(path)
        fields['package_content'] = document_digest(files)
        fields['profile_definitions'] = _hash(safe_path(root, 'ecm_env/profiles.tsv'))
        fields['loaded_profile_definitions'] = document_digest(PROFILES)
        if load_profiles() != PROFILES:
            raise ValueError('Environment profile definitions changed; restart to reload them')
        if profile:
            manifest = safe_path(root, fields['dependency_manifest_path'])
            fields['dependency_manifest'] = _hash(manifest)
    except (OSError, ValueError) as exc: errors.append('package content: ' + str(exc))
    return {'complete': not errors, 'fields': fields, 'errors': errors,
            'sha256': document_digest(fields)}


def _atomic(path, document):
    safe_path(path.parent, path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.environment-', dir=path.parent)
    try:
        if session.context() is not None:os.fchown(fd,0,0)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(document, stream, indent=2, allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def _parent(workspace):
    if workspace is None and session.context():workspace=session.context()['workspace']
    if workspace is not None: return safe_path(workspace, 'logs/environment')
    return safe_path(Path(os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state'))), 'ecm-camera-manager/environment')


def _eligible(report):
    try:
        return (report.get('selected_target_profile') is None
                and report['system'] == 'Linux' and report['features'] == check.summarize(report)
                and all(report['features'][k]['status'] == 'Ready' for k in ('bootstrap','driver_install'))
                and report['runtime_catalog'].get('manifest_sha256') is not None
                and report['runtime_catalog'].get('complete') is True)
    except (KeyError, TypeError, ValueError): return False


def _reuse(entry, current, now):
    try:
        return (current['complete'] and entry['schema'] == SCHEMA and entry['status'] == 'Passed'
                and entry['fingerprint'] == current and 0 <= now-entry['checked_epoch'] <= MAX_AGE_SECONDS
                and document_digest(entry['report']) == entry['report_sha256'] and _eligible(entry['report']))
    except (KeyError, TypeError, ValueError): return False


def _bootstrap(report, directory):
    report.pop('bootstrap_log', None)
    report.pop('bootstrap_association', None)
    current=session.context()
    value = current.get('bootstrap_log') if current else os.environ.get('ECM_ENV_BOOTSTRAP_LOG')
    if not value: return
    report['bootstrap_log'] = value
    try:
        allowed = Path(current['record']).parent if current else _parent(None)
        path = Path(value).absolute() if current else Path(value).absolute() / 'bootstrap.log'
        relative = path.relative_to(allowed.absolute()).as_posix()
        path = safe_path(allowed, relative)
        if not path.is_file() or path.stat().st_size > 1024*1024: raise ValueError('Bootstrap log unavailable or too large')
        body = path.read_bytes()
        target = safe_path(directory, 'bootstrap.log')
        with target.open('xb') as stream: stream.write(body)
        report['bootstrap_association'] = {'source': str(path), 'copy': str(target),
                                            'sha256': hashlib.sha256(body).hexdigest()}
    except (OSError, ValueError) as exc:
        report['bootstrap_association'] = {'status': 'Unavailable', 'reason': str(exc)}


def collect(observer, *, workspace=None, force=False, reason='startup', package=PACKAGE, target_profile=None):
    # Startup is host-only. An explicit target always gets a fresh observation
    # and never leaves a target-specific Passed record for the next startup.
    if target_profile is not None: force = True
    def observe_now():
        return observer() if target_profile is None else observer(target_profile=target_profile)
    current = fingerprint(package)
    warnings = []
    cached = {}
    cache_path = None
    cache_writable = False
    if workspace is not None:
        try:
            cache_path = safe_path(workspace, 'state/environment/startup.json')
            if cache_path.exists():
                try: cached = load_json(cache_path)
                except (ValueError, OSError) as exc: warnings.append('Invalid environment record: ' + str(exc))
            # Retire old Passed before any forced or fallible observation. Even
            # reuse requires a writable state record, so failures cannot retain it.
            _atomic(cache_path, {'schema': SCHEMA, 'status': 'Checking', 'reason': reason})
            cache_writable = True
        except (OSError, ValueError) as exc: warnings.append('Environment cache unavailable: ' + str(exc))
    used = not force and cache_writable and _reuse(cached, current, time.time())
    if used:
        report = deepcopy(cached['report'])
        checked_epoch = cached['checked_epoch']
    else:
        try: report = observe_now()
        except Exception as exc:
            if cache_writable: _atomic(cache_path, {'schema': SCHEMA, 'status': 'Failed', 'reason': type(exc).__name__})
            raise
        checked_epoch = time.time()
    after = fingerprint(package)
    if used and current != after:
        # A change during the light check must never display the old observation.
        report = observe_now()
        used = False
        checked_epoch = time.time()
        current = after
        after = fingerprint(package)
    report = deepcopy(report)
    report['execution_identity'] = session.attribution()
    report['features'] = check.summarize(report)
    profile = select_observed(report['environment'])
    report['diagnosis'] = check.diagnose(report, profile, check.target_mismatch(profile, report.get('selected_target_profile')))
    stable = current == after and current['complete']
    report['startup_check'] = {'schema': SCHEMA, 'cache_used': used, 'reason': reason,
        'fingerprint': after, 'stable': stable, 'full_checked_at': datetime.fromtimestamp(checked_epoch,timezone.utc).isoformat(),
        'status': 'Passed' if stable and _eligible(report) else 'Needs attention', 'warnings': warnings}
    report['collected_at'] = datetime.now(timezone.utc).isoformat()
    try:
        parent = _parent(workspace); parent.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix='check-', dir=parent))
        if session.context() is not None:os.chown(directory,0,0)
        report['log_path'] = str(directory/'environment.json')
        _bootstrap(report, directory)
        _atomic(directory/'environment.json', report)
    except (OSError, ValueError) as exc:
        warnings.append('Environment log unavailable: ' + str(exc))
        report['startup_check']['status'] = 'Needs attention'
        report['log_path'] = 'Unavailable; environment observation is not cached'
    if cache_writable:
        entry = {'schema': SCHEMA, 'status': report['startup_check']['status'],
                 'fingerprint': after, 'checked_epoch': checked_epoch,
                 'report': report, 'report_sha256': document_digest(report)}
        try: _atomic(cache_path, entry)
        except (OSError, ValueError) as exc:
            warnings.append('Environment record unavailable: ' + str(exc))
            report['startup_check']['status'] = 'Needs attention'
    return report


def invalidate(workspace, reason):
    if workspace is not None:
        path = safe_path(workspace, 'state/environment/startup.json')
        _atomic(path, {'schema': SCHEMA, 'status': 'Invalidated', 'reason': reason})


def record_operation(workspace, *, action, feature, stage, return_code, details=None):
    """Record the UI/authorization boundary without capturing terminal input."""
    parent=_parent(workspace);parent.mkdir(parents=True,exist_ok=True)
    directory=Path(tempfile.mkdtemp(prefix='online-entry-',dir=parent))
    _atomic(directory/'result.json',dict(schema='ecm-online-entry-v1',action=action,feature=feature,
        stage=stage,return_code=return_code,execution_identity=session.attribution(),
        recorded_at=datetime.now(timezone.utc).isoformat(),credential_capture=False,details=details or {}))
    return str(directory/'result.json')
