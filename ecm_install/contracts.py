"""Strict prebuilt data verification and installer-owned destinations."""
import hashlib
import json
import re
import struct
from pathlib import Path, PurePosixPath
from .catalog import PACKAGES

RECEIVED_SCHEMA = 'ecm-prebuilt-received-v1'
RECEIVED_AUDIENCE = 'LOCAL_UPSTREAM_BINARY_CANDIDATE'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'Duplicate JSON field: ' + key)
        result[key] = value
    return result


def load_json(path):
    path = Path(path)
    require(not path.is_symlink() and path.is_file(), 'Required JSON missing or linked: ' + str(path))
    require(path.stat().st_size <= 4 * 1024 * 1024, 'JSON exceeds size bound')
    body = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Invalid JSON constant')))
    require(isinstance(body, dict), 'JSON object required')
    return body


def digest(body):
    return hashlib.sha256(body).hexdigest()


def document_digest(document):
    return digest(json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())


def safe_path(root, relative):
    root = Path(root).absolute()
    text = str(relative)
    p = PurePosixPath(text)
    require(text and not p.is_absolute() and '\\' not in text and ':' not in text and
            all(part not in ('', '.', '..') for part in text.split('/')), 'Unsafe relative path: ' + text)
    path = root.joinpath(*p.parts)
    for item in [root, *path.parents, path]:
        if item.exists() or item.is_symlink():
            info = item.lstat()
            require(not item.is_symlink() and not getattr(info, 'st_file_attributes', 0) & 1024,
                    'Linked/reparse path refused: ' + str(item))
    require(path.resolve().is_relative_to(root.resolve()), 'Path escaped root')
    return path


def token(value, label):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,127}', value), 'Invalid ' + label)
    return value


def _elf_info(body):
    require(len(body) >= 64 and body[:6] == b'\x7fELF\x02\x01', 'KO must be ELF64 little endian')
    require(struct.unpack_from('<HH', body, 16) == (1, 183), 'KO must be aarch64 relocatable ELF')
    shoff = struct.unpack_from('<Q', body, 40)[0]
    entsize, count, strindex = struct.unpack_from('<HHH', body, 58)
    require(entsize == 64 and 0 < count < 8192 and strindex < count and shoff + count * entsize <= len(body), 'Invalid ELF section table')
    sections = [struct.unpack_from('<IIQQQQIIQQ', body, shoff + i * 64) for i in range(count)]
    names_sec = sections[strindex]
    require(names_sec[4] + names_sec[5] <= len(body), 'Invalid ELF section names')
    names = body[names_sec[4]:names_sec[4]+names_sec[5]]
    info = {}
    for section in sections:
        require(section[0] < len(names), 'Invalid ELF section name')
        name = names[section[0]:].split(b'\0', 1)[0]
        if name == b'.modinfo':
            require(section[4] + section[5] <= len(body), 'Invalid .modinfo')
            for item in body[section[4]:section[4]+section[5]].split(b'\0'):
                if b'=' in item:
                    key, value = item.decode('utf-8').split('=', 1)
                    info.setdefault(key, []).append(value)
    return info


def verify_manifest(path, expected_id, expected_digest, *, simulation=False):
    path = Path(path)
    manifest = load_json(path)
    require(digest(path.read_bytes()) == expected_digest, 'Manifest differs from approved index digest')
    received = manifest.get('schema') == RECEIVED_SCHEMA
    require(received or manifest.get('schema') == 'ecm-prebuilt-v1', 'Unsupported prebuilt schema')
    require(manifest.get('package_id') == expected_id and expected_id in PACKAGES, 'Selected package/manifest mismatch')
    expected = PACKAGES[expected_id]
    require(manifest.get('product') == expected['product'] and manifest.get('sensor') == expected['sensor'] and
            manifest.get('connection') == expected['connection'] and manifest.get('jetpack') == expected['jetpack'], 'Manifest product/connection/JetPack mismatch')
    audience = manifest.get('audience')
    require(audience == (RECEIVED_AUDIENCE if received else 'SIMULATION_NOT_FOR_RELEASE' if simulation else 'RELEASE'), 'Simulation/release boundary violation')
    token(manifest.get('artifact_set_id'), 'artifact set ID')
    if received:
        require(manifest.get('release_approved') is False and manifest.get('runtime_verified') is False, 'Received candidate is not a release/runtime approval')
        require(all(k in manifest and (manifest[k] is None or isinstance(manifest[k],str) and manifest[k]) for k in ('driver_version','overlay_version')), 'Observed versions must be strings or null')
        require(all(manifest.get('build_evidence',{}).get(k)=='NOT_VERIFIED' for k in
                    ('source_to_binary_proof','exact_build_target','target_crc_compatibility','hardware_validation')), 'Explicit unverified build/target/hardware evidence required')
    else:
        require(all(isinstance(manifest.get(k), str) and manifest[k] for k in ('driver_version','overlay_version')), 'Candidate versions required')
    target = manifest.get('target', {})
    fields = ('kernel','arch') if received else ('kernel','l4t','board','arch')
    require(all(isinstance(target.get(k), str) and target[k] and target[k] != 'UNCONFIGURED' for k in fields), 'Observed artifact target required' if received else 'Exact artifact build target required')
    if received:
        require(all(k in target and (target[k] is None or isinstance(target[k],str) and target[k] and target[k]!='UNCONFIGURED') for k in ('l4t','board')), 'Unknown target fields must be explicit null')
    token(target['kernel'], 'build kernel')
    require(target['arch'] == 'aarch64', 'Manifest architecture must match artifact ELF')
    source = manifest.get('source', {})
    require(re.fullmatch(r'[0-9a-f]{64}', source.get('snapshot_sha256', '')), 'Final source snapshot required')
    require(source.get('commit') is None or re.fullmatch(r'[0-9a-f]{40,64}', source['commit']), 'Full source commit required')
    if received:
        require(isinstance(source.get('commit'),str) and re.fullmatch(r'[0-9a-f]{40}',source['commit']) and
                source.get('snapshot_kind')=='REVIEWED_GIT_TREE' and source.get('co_located_in_commit') is True, 'Fixed upstream receipt identity required')
    artifacts = manifest.get('artifacts', {})
    require(set(artifacts) == set(expected['roles']), 'Required artifact roles missing or unexpected')
    contents, filenames, modules = {}, set(), set()
    for role, kind in expected['roles'].items():
        item = artifacts[role]
        require(item.get('kind') == kind, 'Artifact kind mismatch: ' + role)
        filename = token(item.get('install_name'), 'install filename')
        require(filename not in filenames, 'Duplicate install filename')
        filenames.add(filename)
        require(filename.endswith('.' + kind), 'Artifact suffix mismatch')
        asset = safe_path(path.parent, item.get('path', ''))
        require(asset.is_file() and asset.stat().st_size <= 64 * 1024 * 1024, 'Required artifact missing/oversize: ' + role)
        body = asset.read_bytes()
        require(type(item.get('bytes')) is int and len(body) == item['bytes'] and digest(body) == item.get('sha256'), 'Artifact integrity mismatch: ' + role)
        if received:
            upstream=item.get('upstream',{})
            require(upstream.get('commit')==source['commit'] and isinstance(upstream.get('git_path'),str) and upstream['git_path'], 'Artifact upstream commit/path missing: '+role)
            require(hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()==upstream.get('git_blob'), 'Artifact Git blob identity mismatch: '+role)
        if kind == 'ko':
            module = token(item.get('module_name'), 'module name')
            require(filename == module + '.ko' and module not in modules, 'KO name/identity collision')
            modules.add(module)
            require(item.get('vermagic', '').split(' ')[0] == target['kernel'], 'KO declared ABI contradicts manifest target')
            require(isinstance(item.get('depends'), list), 'KO dependencies must be declared')
            # Sandbox I/O never disables validation of received real binaries.
            if received or not simulation:
                info = _elf_info(body)
                require(info.get('name') == [module] and info.get('vermagic') == [item['vermagic']], 'ELF identity contradicts manifest')
                require(set((info.get('depends') or [''])[0].split(',')) - {''} == set(item['depends']), 'ELF dependencies contradict manifest')
                if received:
                    require('module_version' in item and info.get('version',[]) == ([] if item['module_version'] is None else [item['module_version']]), 'Observed module version contradicts ELF')
        elif kind == 'dtbo' and (received or not simulation):
            require(len(body) >= 40 and struct.unpack_from('>I', body)[0] == 0xd00dfeed and
                    struct.unpack_from('>I', body, 4)[0] == len(body), 'Invalid DTBO header/length')
        contents[role] = body
    for role, item in artifacts.items():
        if item['kind'] == 'ko':
            allowed = modules | set(manifest.get('system_module_dependencies', []))
            require(set(item['depends']) <= allowed, 'Undeclared module provider: ' + role)
    return manifest, contents


def package_evidence(manifest):
    """Report received bytes separately from execution and qualification."""
    return dict(artifact_audience=manifest['audience'],manifest_schema=manifest['schema'],
                source_commit=manifest['source'].get('commit'),source_snapshot_sha256=manifest['source']['snapshot_sha256'],
                driver_version=manifest.get('driver_version'),overlay_version=manifest.get('overlay_version'),
                target=manifest['target'],build_evidence=manifest.get('build_evidence',{'scope':'SIMULATION_FIXTURE' if manifest['audience']=='SIMULATION_NOT_FOR_RELEASE' else 'NOT_VERIFIED'}),
                release_approved=False,native_install_verified=False,hardware_verified=False)


def destinations(manifest, transaction_id):
    token(transaction_id, 'transaction ID')
    result = {}
    for role, item in manifest['artifacts'].items():
        kind = item['kind']
        name = token(item['install_name'], 'install filename')
        if kind == 'ko':
            result[role] = '/lib/modules/' + token(manifest['target']['kernel'], 'kernel') + '/updates/drivers/media/i2c/' + name
        elif kind == 'dtbo':
            result[role] = '/boot/ecm-' + transaction_id + '.dtbo'
        else:
            target_name = 'camera_overrides.isp' if role == 'isp' and manifest['jetpack'] == '6' else name
            result[role] = '/var/nvidia/nvcam/settings/' + target_name
    require(len(set(result.values())) == len(result), 'Destination collision')
    result['extlinux'] = '/boot/extlinux/extlinux.conf'
    return result


def compare_environment(actual, target):
    comparisons = []
    for key in ('arch','l4t','kernel','board','abi_reference'):
        expected = target.get(key)
        if key not in target:
            continue
        value = actual.get(key)
        status = 'Not verified' if expected is None or value is None else 'Matched' if value == expected else 'Warning'
        comparisons.append(dict(check=key, actual=value, expected=expected, status=status,
                                reason='Package target/reference is unknown' if expected is None else 'Observation unavailable' if value is None else 'Matches reference' if value == expected else 'Host differs from package target',
                                value_source='passive host observation', decision='continue', policy_id='environment-warning-v1'))
    return comparisons
