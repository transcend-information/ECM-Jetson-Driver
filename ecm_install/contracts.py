"""Strict prebuilt data verification and installer-owned destinations."""
import hashlib
import json
import re
import struct
from pathlib import Path, PurePosixPath
from .catalog import PACKAGES
from .diagnostics import DiagnosticError

RECEIVED_SCHEMA = 'ecm-prebuilt-received-v1'
RECEIVED_AUDIENCE = 'LOCAL_UPSTREAM_BINARY_CANDIDATE'
LOCAL_BUILD_SCHEMA = 'ecm-prebuilt-local-build-v1'
LOCAL_BUILD_AUDIENCE = 'LOCAL_CROSS_BUILD_CANDIDATE'
PUBLIC_SCHEMA = 'ecm-public-prebuilt-v1'
PUBLIC_AUDIENCE = 'PUBLIC_DISTRIBUTION_CANDIDATE'


def is_real_candidate(manifest):
    return manifest.get('schema') in (RECEIVED_SCHEMA, LOCAL_BUILD_SCHEMA, PUBLIC_SCHEMA)


def require(ok, message, code=None):
    if not ok:
        if code:raise DiagnosticError(message, code)
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
    require(digest(path.read_bytes()) == expected_digest, 'Manifest differs from approved index digest', 'MANIFEST_INTEGRITY')
    from .public_contract import validate
    validate(manifest)
    require(manifest.get('package_id') == expected_id and expected_id in PACKAGES, 'Selected package/manifest mismatch')
    expected = PACKAGES[expected_id]
    require(manifest.get('product') == expected['product'] and manifest.get('sensor') == expected['sensor'] and
            manifest.get('connection') == expected['connection'] and manifest.get('jetpack') == expected['jetpack'], 'Manifest product/connection/JetPack mismatch')
    token(manifest.get('artifact_set_id'), 'artifact set ID')
    require(manifest['release_approved'] is False and manifest['runtime_verified'] is False, 'Invalid qualification flags')
    for key in ('driver_version', 'overlay_version'):
        require(manifest[key] is None or isinstance(manifest[key], str) and manifest[key], 'Invalid package version')
    target = manifest['target']
    token(target.get('kernel'), 'target kernel')
    require(target.get('arch') == 'aarch64', 'Package requires aarch64')
    require(all(value is None or isinstance(value, str) and value for value in target.values()), 'Invalid target fields')
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
        require(asset.is_file() and asset.stat().st_size <= 64 * 1024 * 1024, 'Required artifact missing/oversize: ' + role, 'ARTIFACT_MISSING')
        body = asset.read_bytes()
        require(type(item.get('bytes')) is int and len(body) == item['bytes'] and digest(body) == item.get('sha256'), 'Artifact integrity mismatch: ' + role, 'ARTIFACT_INTEGRITY')
        if kind == 'ko':
            module = token(item.get('module_name'), 'module name')
            require(filename == module + '.ko' and module not in modules, 'KO name/identity collision')
            modules.add(module)
            require(item.get('vermagic', '').split(' ')[0] == target['kernel'], 'KO declared ABI contradicts manifest target')
            require(isinstance(item.get('depends'), list), 'KO dependencies must be declared')
            info = _elf_info(body)
            require(info.get('name') == [module] and info.get('vermagic') == [item['vermagic']], 'ELF identity contradicts manifest')
            require(set((info.get('depends') or [''])[0].split(',')) - {''} == set(item['depends']), 'ELF dependencies contradict manifest')
            require('module_version' in item and info.get('version',[]) == ([] if item['module_version'] is None else [item['module_version']]), 'Observed module version contradicts ELF')
            for field, values in item.get('embedded_identity',{}).items():
                require(info.get(field)==values, 'Embedded candidate/source identity contradicts ELF')
        elif kind == 'dtbo':
            require(len(body) >= 40 and struct.unpack_from('>I', body)[0] == 0xd00dfeed and
                    struct.unpack_from('>I', body, 4)[0] == len(body), 'Invalid DTBO header/length')
        contents[role] = body
    if expected['connection'] == 'GMSL':
        require(manifest.get('serdes') == expected['serdes'] and manifest.get('port') == expected['port'],
                'Manifest serializer/deserializer or camera ports differ from selection')
        serializer, deserializer = expected['serdes'].split('/')
        require(artifacts['serializer_ko']['module_name'] == serializer and
                artifacts['deserializer_ko']['module_name'] == deserializer and
                {serializer, deserializer} <= set(artifacts['sensor_ko']['depends']),
                'GMSL driver modules do not match the selected serializer/deserializer')
    for role, item in artifacts.items():
        if item['kind'] == 'ko':
            allowed = modules | set(manifest.get('system_module_dependencies', []))
            require(set(item['depends']) <= allowed, 'Undeclared module provider: ' + role)
    return manifest, contents


def package_evidence(manifest):
    """Report received bytes separately from execution and qualification."""
    from .public_contract import evidence
    return evidence(manifest)


def destinations(manifest, transaction_id):
    token(transaction_id, 'transaction ID')
    result = {}
    for role, item in manifest['artifacts'].items():
        kind = item['kind']
        name = token(item['install_name'], 'install filename')
        if kind == 'ko':
            result[role] = '/lib/modules/' + token(manifest['target']['kernel'], 'kernel') + '/updates/drivers/media/i2c/' + name
        elif kind == 'dtbo':
            from .jp6_names import enabled
            result[role] = '/boot/'+name if enabled(manifest) else '/boot/ecm-' + transaction_id + '.dtbo'
        else:
            preserve_isp = (manifest.get('package_id') in ('ecm300-gmsl-max96724-jp6', 'ecm400-gmsl-max96724-jp6') and
                            ((manifest.get('schema')==PUBLIC_SCHEMA and manifest.get('installation_policy',{}).get('isp_destination')=='PACKAGE_FILENAME') or
                             (manifest.get('reception', {}).get('intake_kind') == 'FIXED_LATEST_UPSTREAM' and
                              manifest.get('installation_policy', {}).get('isp_destination') == 'UPSTREAM_ORIGINAL_NAME')))
            target_name = 'camera_overrides.isp' if role == 'isp' and manifest['jetpack'] == '6' and not preserve_isp else name
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
        status = 'Not verified' if expected is None or value is None else 'Matched' if value == expected else 'Different'
        comparisons.append(dict(check=key, actual=value, expected=expected, status=status,
                                reason='Package target/reference is unknown' if expected is None else 'Observation unavailable' if value is None else 'Matches reference' if value == expected else 'Host differs from package target',
                                value_source='passive host observation', decision='continue', policy_id='environment-warning-v1'))
    return comparisons
