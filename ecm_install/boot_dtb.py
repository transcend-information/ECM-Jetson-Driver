"""Explicit JP7 base-DTB selection; never infer a filename from a loose match."""
import re
from .contracts import require, digest
from .binary_formats import fdt_nodes
from . import r20_storage as S

BASE = '/boot/dtb/kernel_tegra234-p3768-0000+p3767-0005-nv-super.dtb'
BASE_SHA = '58762f477f73e0fb5ad2dd3f1c123e834d6d39ad02f985fe469b8880604207cc'
BOARD = 'nvidia,p3768-0000+p3767-0005-super'
PROFILES = {('r39.2.1', BOARD): (BASE, BASE_SHA)}


def boot_path(path):
    require(isinstance(path, str) and re.fullmatch(r'/boot/[A-Za-z0-9_+./-]+\.dtb', path)
            and all(p not in ('.', '..', '') for p in path.split('/')[1:]), 'Invalid base DTB path')
    return path


def strings(body):
    require(isinstance(body, bytes) and body.endswith(b'\0'), 'Invalid DT string list')
    return body.rstrip(b'\0').decode('ascii').split('\0')


def verify_references(base, overlay, board):
    nodes = fdt_nodes(base); patch = fdt_nodes(overlay)
    require(board in strings(nodes['/']['compatible']), 'Base DTB does not match live board')
    require(board in strings(patch['/']['compatible']), 'Overlay does not support live board')
    for path, props in patch.items():
        if path.startswith('/fragment@') and path.count('/') == 1:
            require('target-path' in props, 'Cannot verify overlay phandle target')
            target = strings(props['target-path'])
            require(len(target) == 1 and target[0] in nodes, 'Overlay target missing from base DTB')
    for name in patch.get('/__fixups__', {}):
        target = strings(nodes.get('/__symbols__', {}).get(name, b''))
        require(len(target) == 1 and target[0] in nodes and
                ('phandle' in nodes[target[0]] or 'linux,phandle' in nodes[target[0]]),
                'Overlay symbol missing from base DTB: ' + name)
    return nodes


def select_base(system, manifest, text, overlay):
    """Preserve an explicit JP7 FDT; fill a missing FDT only from reviewed profiles."""
    if str(manifest.get('jetpack')) != '7':
        return None  # Preserve JP6 boot behavior.
    _, fields, _ = S._active_entry(text)
    require(len(fields.get('FDT', [])) <= 1, 'Ambiguous active FDT')
    live = strings(system.path('/sys/firmware/devicetree/base/compatible').read_bytes())
    require(bool(live) and live[0], 'Live board compatible unavailable')
    board = live[0]; l4t = system.observe().get('l4t')
    inherited = fields.get('FDT', [])
    if inherited:
        path = boot_path(inherited[0]); expected = None; source = 'PRESERVED_ACTIVE_FDT'
    else:
        require((l4t, board) in PROFILES,
                'No reviewed base DTB for this JP7 board/BSP; configure a valid explicit FDT')
        path, expected = PROFILES[(l4t, board)]; source = 'REVIEWED_BOARD_BSP_PROFILE'
    snap = system.snapshot(path)
    require(snap['existed'] and snap['body'] is not None, 'Required base DTB unavailable: ' + path)
    body = snap['body']
    require(expected is None or digest(body) == expected, 'Reviewed base DTB hash differs')
    verify_references(body, overlay, board)
    return dict(path=path, sha256=digest(body), bytes=len(body), board=board, l4t=l4t, source=source)


def verify_saved(system, reference):
    if reference is None:
        return
    path = boot_path(reference['path']); snap = system.snapshot(path)
    require(snap['existed'] and snap['sha256'] == reference['sha256'] and
            len(snap['body']) == reference['bytes'], 'Base DTB changed since boot plan')
    require(system.observe().get('l4t') == reference['l4t'] and
            strings(system.path('/sys/firmware/devicetree/base/compatible').read_bytes())[0] == reference['board'],
            'Board/BSP changed since boot plan')


def insert_fdt(text, label, path):
    """Only insert one FDT into the selected existing entry; preserve all other bytes."""
    boot_path(path)
    active, fields, _ = S._active_entry(text)
    require(active == label and not fields.get('FDT'), 'Correction requires selected entry without FDT')
    require(fields.get('OVERLAYS') and not fields.get('FDTOVERLAYS'), 'Expected OVERLAYS directive')
    require(len(fields['OVERLAYS']) == 1, 'Ambiguous overlay directive')
    lines = text.splitlines(keepends=True); current = None; inserted = 0; result = []
    for line in lines:
        match = re.fullmatch(r'\s*LABEL\s+(\S+)\s*', line, re.I)
        if match: current = match[1]
        if current == label and re.match(r'^\s*OVERLAYS\s+', line, re.I):
            indent = re.match(r'^([ \t]*)', line)[1]
            result.append(indent + 'FDT ' + path + ('\r\n' if line.endswith('\r\n') else '\n'))
            inserted += 1
        result.append(line)
    require(inserted == 1, 'Selected overlay directive not unique')
    return ''.join(result)
