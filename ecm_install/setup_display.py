"""Presentation of recorded configurations and passive observations."""
import re
from . import ui, VERSION


def value(item):
    return str(item) if item is not None and item != '' else 'Not verified'


def company_model(item):
    product = item.get('product')
    if item.get('connection') == 'MIPI':
        return {'ECM100': 'TS-ECM100M22', 'ECM300': 'TS-ECM300M22', 'ECM400': 'ECM400 (legacy)'}.get(product, value(product))
    if item.get('connection') == 'GMSL':
        if product == 'ECM300G2F' and item.get('serdes') in ('MAX96717/MAX96724', 'MAX96717/MAX9296'):
            return 'TS-ECM300G2F / TS-ECM301G2F'
        return {'ECM300G2F': 'TS-ECM300G2F', 'ECM400G2F': 'TS-ECM400G2F'}.get(product, value(product))
    return value(product)


def camera_label(current):
    camera = current.get('camera') or {}
    if camera:
        fields = ('sensor', 'connection', 'port', 'serdes')
        return ' / '.join([company_model(camera)] + [str(camera[k]) for k in fields if camera.get(k)]) + ' (recorded)'
    return 'Not configured' if current.get('status') == 'NONE' else 'Recorded setup; identity not verified'


def resolution(manifest, contract=None):
    """Read only the saved package contract; no migration to a newer mode table."""
    size = manifest.get('mode_metadata', {}).get('max_resolution')
    if isinstance(size, list) and len(size) == 2 and all(type(n) is int and 0 < n <= 16384 for n in size):
        return '%d x %d (configured)' % tuple(size)
    modes = (contract or {}).get('modes') or manifest.get('mode_metadata', {}).get('contract', {}).get('modes') or []
    sizes = []
    for mode in modes:
        if mode.get('status') == 'EXCLUDED':
            continue
        mode = mode.get('declared_source_mode') or mode
        if mode.get('status') == 'EXCLUDED' or mode.get('hdr') not in (None, 0) or 'hdr' in str(mode.get('group', '')).lower():
            continue
        output = mode.get('manager_recipe', {}).get('output') or mode.get('output') or mode.get('output_size') or {'width': mode.get('width'), 'height': mode.get('height')}
        if isinstance(output, str):
            match = re.fullmatch(r'(\d+)x(\d+)', output)
            output = {'width': int(match[1]), 'height': int(match[2])} if match else {}
        if isinstance(output, dict):
            w, h = output.get('width'), output.get('height')
            if type(w) is int and type(h) is int and 0 < w <= 16384 and 0 < h <= 16384:
                sizes.append((w * h, w, h))
    if not sizes:
        return 'Not verified'
    _, w, h = max(sizes)
    return '%d x %d (configured; output not verified)' % (w, h)


def install_context(host, current, family):
    return ('Current Camera Setup : ' + camera_label(current),
            'Detected System      : JetPack ' + value(host.get('jetpack')) + ' / L4T ' + value(host.get('l4t')),
            'Package JetPack      : ' + value(family))


def install_page(title, host, current, family):
    ui.begin('Home > Install / Switch Camera > ' + title, VERSION, install_context(host, current, family))


def environment_status(comparisons):
    statuses = {row['status'] for row in comparisons}
    if 'Different' in statuses or 'Warning' in statuses:
        return 'Different'
    if not comparisons or statuses - {'Matched'}:
        return 'Not verified'
    return 'Matched'


def environment_summary(comparisons):
    labels = {'arch': 'Architecture', 'kernel': 'kernel', 'l4t': 'L4T',
              'board': 'board', 'abi_reference': 'ABI'}
    missing = [labels.get(row['check'], row['check']) for row in comparisons if row['status'] == 'Not verified']
    different = [labels.get(row['check'], row['check']) for row in comparisons if row['status'] in ('Different', 'Warning')]
    lines = []
    if different:
        lines.append(' / '.join(different) + ' differs from the package target.')
    if missing:
        lines.append(' / '.join(missing) + ' information is incomplete.')
    if not comparisons:
        lines.append('Environment information is incomplete.')
    if lines:
        lines.append('You may continue. Details are saved in the log.')
    return lines


def show_target(item, family, host, manifest=None, environment=None):
    ui.line('Install Target')
    for label, field in [('Company Model', 'product'), ('Sensor', 'sensor'), ('Connection', 'connection'), ('Port', 'port')]:
        ui.line('  %-16s: %s' % (label, company_model(item) if field == 'product' else value(item.get(field))))
    serdes = item.get('serdes', '').split('/')
    for index, label in enumerate(('SER', 'DES') if item.get('connection') == 'GMSL' else ()):
        setting = 'Not applicable' if item.get('connection') == 'MIPI' else value(serdes[index] if len(serdes) > index else None)
        ui.line('  %-16s: %s' % (label, setting))
    ui.line('  Package JetPack : ' + value(family))
    ui.line('  Package ID      : ' + item['id'])
    ui.line('Current System    : L4T ' + value(host.get('l4t')) + ' / Kernel ' + value(host.get('kernel')))
    target = (manifest or {}).get('target', {})
    ui.line('Package Target    : L4T ' + (target.get('l4t') or 'not declared') + ' / Kernel ' + value(target.get('kernel')))
    ui.line('Environment Match : ' + environment_status(environment or []))
    if manifest is not None:
        for summary in environment_summary(environment or []):
            ui.line('  ' + summary)


def home(host, current, environment, observations, warnings=()):
    ui.begin('Home', VERSION)
    ui.line('Camera Setup      : ' + camera_label(current))
    ui.line('Max. Resolution   : ' + current.get('max_resolution', 'Not verified'))
    status = current.get('status')
    label = ('Installation files recorded' if status in ('VERIFIED_RECORD', 'VERIFIED_LEGACY_RECORD')
             else 'No managed installation recorded' if status == 'NONE' else 'Recorded installation requires review')
    if status=='PARTIAL':
        label='PARTIAL / '+('Boot configuration incomplete' if current.get('incomplete_stage')=='boot_config' else 'Installation incomplete')+'; repair required'
    ui.line('Package Status    : ' + label)
    ui.line('\nSystem')
    for name, item in [('Platform (Device Tree)', host.get('board')), ('Boot Configuration', observations.get('boot_configuration')),
                       ('Power Mode', observations.get('power_mode')), ('JetPack', (str(host['jetpack']) + '.x (L4T family)') if host.get('jetpack') else None),
                       ('L4T', host.get('l4t')), ('Kernel', host.get('kernel'))]:
        ui.line('  %-22s: %s' % (name, value(item)))
    features = environment.get('features', {})
    checks = [features.get(k, {}).get('status') for k in ('bootstrap', 'driver_install')]
    check_label = 'Driver installation dependencies ready' if checks == ['Ready', 'Ready'] else 'Dependencies need attention'
    ui.line('  System Check           : ' + check_label)
    camera_status = observations.get('camera_device', 'Not verified')
    ui.line('  Camera Device          : ' + camera_status)
    notices = list(warnings)
    if current.get('diagnostic'):
        notices.append('Saved setup could not be verified; review installation records in Troubleshoot.')
    for name in ('bootstrap', 'driver_install'):
        feature = features.get(name, {})
        if feature.get('status') not in (None, 'Ready'):
            notices.append(name.replace('_', ' ') + ': ' + '; '.join(feature.get('reasons', [])[:1]))
    for notice in notices:
        ui.line('Notice: ' + notice)
