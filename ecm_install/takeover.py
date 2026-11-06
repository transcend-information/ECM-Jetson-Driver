"""Bounded replacement based on current module identity, not private history."""
import gzip
import io
import lzma
import re
from .contracts import _elf_info, digest, require
from .diagnostics import DiagnosticError
from . import jp6_names

MODULE = r'/lib/modules/[A-Za-z0-9._+-]+/(?:[A-Za-z0-9._+-]+/)*[A-Za-z0-9._+-]+\.ko(?:\.(?:gz|xz|zst))?'
CONFIG = r'/(?:etc|run|usr/local/lib|usr/lib|lib)/(?:modules-load\.d|modprobe\.d)/[A-Za-z0-9._+-]+\.conf'


def module_name(path):
    return re.sub(r'\.ko(?:\.(?:gz|xz|zst))?$', '', path.rsplit('/', 1)[1]).replace('-', '_')


def allowed(path):
    return bool(re.fullmatch(MODULE, path) or re.fullmatch(CONFIG, path) or path == '/etc/modules')


def elf(body, path):
    if path.endswith(('.gz', '.xz')):
        reader = gzip.GzipFile if path.endswith('.gz') else lzma.LZMAFile
        with reader(io.BytesIO(body)) as stream:
            body = stream.read(128 * 1024 * 1024 + 1)
        require(len(body) <= 128 * 1024 * 1024, 'Module exceeds decompression bound', 'MODULE_RENAME_CHECK_FAILED')
    require(not path.endswith('.zst'), 'Selected compressed module requires review: ' + path,
            'MODULE_RENAME_CONFLICT')
    return _elf_info(body)


def rewrite_config(text, logical, mapping):
    """Rewrite module operands only; custom commands/continuations need review."""
    out = []
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        active, mark, comment = line.partition('#')
        parts = re.split(r'(\s+)', active)
        indexes = [i for i, value in enumerate(parts) if value and not value.isspace()]
        if not indexes:
            out.append(line); continue
        words = [parts[i] for i in indexes]
        if not any(w.replace('-', '_') in mapping for w in words):
            out.append(line); continue
        direct = logical == '/etc/modules' or '/modules-load.d/' in logical
        if '\\' in active or (not direct and words[0] in ('install', 'remove')):
            raise DiagnosticError('Custom module loader needs review: ' + logical + ':' + str(number),
                                  'MODULE_RENAME_CONFLICT', path=logical, line=number, kind='CUSTOM_LOADER_COMMAND')
        operands = ([0] if direct and len(words) == 1 else
                    [1] if not direct and words[0] in ('options', 'blacklist') else
                    [2] if not direct and words[0] == 'alias' else
                    list(range(1, len(words))) if not direct and words[0] == 'softdep' else [])
        require(bool(operands), 'Ambiguous loader syntax: ' + logical + ':' + str(number), 'MODULE_RENAME_CONFLICT')
        for pos in operands:
            if pos < len(indexes):
                value = words[pos].replace('-', '_')
                if value in mapping:
                    parts[indexes[pos]] = mapping[value]
        # Preserve blacklist of retired names; do not blacklist the new module.
        if not direct and words[0] == 'blacklist':
            out.append(line)
        else:
            out.append(''.join(parts) + mark + comment)
    return ''.join(out)


def collect(system, manifest, contents, old_entries):
    kernel = manifest['target']['kernel']
    selected = {}
    for role, artifact in manifest['artifacts'].items():
        if artifact.get('kind') == 'ko' or artifact.get('module_name'):
            info = _elf_info(contents[role])
            selected[artifact['module_name'].replace('-', '_')] = set(info.get('alias', []))
    observed = system.module_aliases(kernel)
    names = set(selected)
    if jp6_names.enabled(manifest):
        names.add(jp6_names.NAMES[manifest['package_id']][0])
    for alias, name in observed['aliases']:
        if any(alias in aliases for aliases in selected.values()):
            names.add(name)
    owned = {e['destination']: e['sha256'] for e in old_entries}
    files = jp6_names._files(system, kernel, names)
    require(len(files)<=256,'Selected module candidates exceed bounded takeover inventory','MODULE_RENAME_CHECK_FAILED')
    mapping, extra, evidence, issues = {}, [], [], []
    found={row['module'] for row in files}
    for alias,name in observed['aliases']:
        if any(alias in aliases for aliases in selected.values()) and name not in found:
            issues.append(dict(kind='UNRESOLVED_ALIAS_TARGET',module=name,alias=alias,
                               reason='Matching current alias has no safely inspectable module file'))
    for row in files:
        path = row['path']; snap = system.snapshot(path)
        try:
            info = elf(snap['body'], path)
            aliases = set(info.get('alias', []))
            matches = [name for name, expected in selected.items() if aliases & expected]
            require(len(matches) == 1 and aliases <= selected[matches[0]],
                    'Module identity is shared or does not match selected sensor: ' + path,
                    'MODULE_RENAME_CONFLICT')
            require(len(info.get('name', [])) == 1 and
                    info.get('vermagic', [''])[0].split()[0] == kernel,
                    'Module embedded identity/kernel is ambiguous: ' + path, 'MODULE_RENAME_CONFLICT')
            # The two IMX415 GMSL drivers share an OF alias across different deserializers.
            if manifest['connection'] == 'GMSL' and matches[0] == manifest['artifacts']['sensor_ko']['module_name']:
                deserializer = manifest['artifacts']['deserializer_ko']['module_name']
                require(deserializer in info.get('depends', [''])[0].split(','),
                        'Existing GMSL sensor driver uses a different or unknown deserializer: ' + path,
                        'MODULE_RENAME_CONFLICT')
            owner = system.package_owners(path)
            new = matches[0]
            mapping[row['module']] = new
            mapping[info['name'][0].replace('-', '_')] = new
            evidence.append(dict(row, embedded_name=info['name'][0], aliases=sorted(aliases),
                                 vermagic=info['vermagic'][0], package_owners=owner, replacement=new))
            if path not in owned:
                extra.append(dict(destination=path, sha256=snap['sha256'], role='takeover-module'))
        except (ValueError, OSError, IndexError) as exc:
            # Managed predecessors of a different sensor are handled by the
            # verified transaction chain, never inferred from a filename.
            if owned.get(path) != snap['sha256']:
                issues.append(dict(path=path, reason=str(exc), kind='AMBIGUOUS_MODULE_SCOPE'))
    if issues:
        raise DiagnosticError('Cannot safely establish camera module replacement scope',
                              'MODULE_RENAME_CONFLICT', findings=issues)
    sensor = manifest['artifacts']['sensor_ko']['module_name']
    for entry in old_entries:
        if entry['destination'].startswith('/lib/modules/') and entry['sha256'] and entry.get('role')=='sensor_ko':
            old = module_name(entry['destination'])
            if old not in selected:
                # Only a verified predecessor sensor may change sensor family.
                info = elf(system.snapshot(entry['destination'])['body'], entry['destination'])
                if any(a.startswith(('i2c:', 'of:')) for a in info.get('alias', [])):
                    mapping.setdefault(old, sensor)
    if jp6_names.enabled(manifest):
        mapping.setdefault(jp6_names.NAMES[manifest['package_id']][0], sensor)
    mapping = {old: new for old, new in mapping.items() if old != new}
    configs, changes = [], {}
    for logical in jp6_names._configs(system):
        reader = getattr(system, 'loader_config_snapshot', system.snapshot)
        snap = reader(logical)
        configs.append({k: v for k, v in snap.items() if k != 'body'} | {'path': logical})
        if snap.get('masked'):
            continue
        body = snap['body']
        require(body is not None and len(body) <= 256 * 1024, 'Loader config exceeds bound', 'MODULE_RENAME_CHECK_FAILED')
        updated = rewrite_config(body.decode('utf-8'), logical, mapping).encode()
        if updated != body:
            resolved = snap.get('resolved_path', logical)
            require(allowed(resolved), 'Loader link target requires review: ' + resolved, 'MODULE_RENAME_CONFLICT')
            require(resolved not in changes or changes[resolved] == updated,
                    'Conflicting loader semantics for linked file: ' + resolved, 'MODULE_RENAME_CONFLICT')
            changes[resolved] = updated
    return extra, changes, dict(schema='ecm-module-takeover-v1', kernel=kernel,
        alias_scope=observed['scope'], modules=evidence, loader_configs=configs,
        replacements=mapping, config_outputs={p: digest(b) for p, b in sorted(changes.items())},
        provenance_policy='Current embedded identity and aliases; no private history required')


def check_overlay(system, contents, overlays, managed):
    if overlays==([managed] if managed else []):
        return dict(scope='VERIFIED_PREDECESSOR_OR_NO_ACTIVE_OVERLAY',paths=overlays)
    require(len(overlays)==1,'Multiple active overlays need explicit scope review','UNOWNED_OVERLAY')
    path=overlays[0]
    require(bool(re.fullmatch(r'/boot/[A-Za-z0-9._+-]+\.dtbo',path)),
            'Active overlay path is outside the camera overlay scope: '+path,'UNOWNED_OVERLAY')
    body=system.snapshot(path)['body']
    require(body is not None,'Active overlay file is unavailable: '+path,'UNOWNED_OVERLAY')
    if body==contents['overlay']:
        return dict(scope='EXACT_SELECTED_OVERLAY',path=path,sha256=digest(body),preserved=True)
    from .binary_formats import fdt_nodes
    before,after=fdt_nodes(body),fdt_nodes(contents['overlay'])
    # A same-scope camera overlay can be replaced without a private history.
    # Additional nodes/properties or fragment targets remain genuine ambiguity.
    require(set(before)<=set(after) and all(set(props)<=set(after[node]) for node,props in before.items()),
            'Active overlay changes nodes outside selected camera scope: '+path,'UNOWNED_OVERLAY')
    for node,props in before.items():
        for key in ('target','target-path','compatible'):
            require(key not in props or props[key]==after[node].get(key),
                    'Active overlay target/compatible differs: '+path+' '+node,'UNOWNED_OVERLAY')
    aliases=set(_elf_info(contents['sensor_ko']).get('alias',[]))
    compatibles={c.decode('ascii') for props in before.values() for c in props.get('compatible',b'').split(b'\0') if c}
    require(any('of:N*T*C'+c in aliases or 'of:N*T*C'+c+'C*' in aliases for c in compatibles),
            'Active overlay has no selected sensor identity: '+path,'UNOWNED_OVERLAY')
    return dict(scope='SELECTED_CAMERA_OVERLAY_STRUCTURE',path=path,sha256=digest(body),preserved=True)


def boot_text(text, mapping):
    """Update module operands in APPEND, preserving unrelated arguments."""
    def replace(match):
        key, value = match.group(1), match.group(2)
        if key.replace('-', '_') in ('modules_load', 'rd.modules_load'):
            value = ','.join(mapping.get(x.replace('-', '_'), x) for x in value.split(','))
        elif '.' in key:
            module, option = key.split('.', 1)
            key = mapping.get(module.replace('-', '_'), module) + '.' + option
        return key + '=' + value
    from . import r20_storage as S
    active,_,_=S._active_entry(text)
    output=[];current=None
    for line in text.split('\n'):
        match=re.match(r'\s*LABEL\s+(\S+)',line,re.I)
        if match:current=match.group(1)
        if current==active and line.lstrip().upper().startswith('APPEND '):
            line=re.sub(r'([^\s=]+)=([^\s]+)',replace,line)
        output.append(line)
    return '\n'.join(output)
