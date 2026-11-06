"""Explicit camera selection; never infer camera identity or start an operation."""
from . import ui, setup_display


def choose(title, host, current, family, rows, ask, *, selected_family=None):
    message = None
    while True:
        setup_display.install_page(title, host, current, family)
        if selected_family:
            ui.line('Selected family: ' + selected_family)
        for index, (_, label) in enumerate(rows, 1):
            ui.line('[%d] %s' % (index, label))
        if message:
            ui.line(message)
        ui.footer('[9] Change package JetPack', '[B] Back', 'Selection does not install files.')
        value = ask('Select: ').strip()
        if value.lower() in ('', '0', 'b'):
            return None
        if value == '9':
            return 'change-family'
        if value.isascii() and value.isdecimal() and len(value) <= 6 and 1 <= int(value) <= len(rows):
            return rows[int(value) - 1][0]
        message = 'Invalid selection. Enter a displayed number or B to go back.'


def select(host, catalog, ask, current=None, report=None):
    current = current or {'status': 'NONE'}
    family = host.get('jetpack')
    models = [('ECM100', 'GC2093'), ('ECM300', 'IMX675'), ('ECM400', 'IMX415')]
    selectable = catalog.selectable_packages()
    while True:
        if family not in ('6', '7'):
            message = None
            while True:
                setup_display.install_page('Select JetPack family', host, current, None)
                ui.line('Select the package family. Detected system details remain unchanged.')
                if message:
                    ui.line(message)
                ui.footer('[6] JetPack 6.x', '[7] JetPack 7.x', '[B] Back')
                family = ask('Select: ').strip()
                if family.lower() in ('', '0', 'b'):
                    return None
                if family in ('6', '7'):
                    break
                message = 'Invalid selection. Enter 6, 7 or B.'
        packages = {k: v for k, v in selectable.items() if v['jetpack'] == family}
        rows = [(sensor, ('ECM300 / ECM301' if product == 'ECM300' else product) + ' (' + sensor + ')')
                for product, sensor in models if any(p['sensor'] == sensor for p in packages.values())]
        sensor = choose('Select camera model', host, current, family, rows, ask)
        if sensor is None:
            return None
        if sensor == 'change-family':
            family = None
            continue
        matching = {k: v for k, v in packages.items() if v['sensor'] == sensor}
        selected_family = next(label.split(' (')[0] for key, label in rows if key == sensor)
        while True:
            connections = []
            # ECM100 GMSL is not open; only approved catalog connections are shown.
            for connection in ('MIPI', 'GMSL'):
                items = [p for p in matching.values() if p['connection'] == connection]
                if items:
                    names = list(dict.fromkeys(setup_display.company_model(p) for p in items))
                    label = connection if sensor == 'GC2093' else connection + ' | ' + ', '.join(names)
                    connections.append((connection, label))
            connection = choose('Select connection', host, current, family, connections, ask,
                                selected_family=selected_family)
            if connection is None:
                break
            if connection == 'change-family':
                family = None
                break
            options = {k: v for k, v in matching.items() if v['connection'] == connection}
            if connection == 'GMSL':
                rows = [(k, item['serdes'].replace('/', ' / ') + ' | Ports ' + item['port'] +
                         ' | ' + ('Included' if catalog.manifest(k) is not None else 'Awaiting artifacts'))
                        for k, item in options.items()]
                selected = choose('Select serializer / deserializer', host, current, family, rows, ask,
                                  selected_family=selected_family)
                if selected is None:
                    continue
                if selected == 'change-family':
                    family = None
                    break
            else:
                if len(options) != 1:
                    raise ValueError('MIPI selection must identify exactly one package')
                selected = next(iter(options))
            item = options[selected]
            if report is not None:
                report.event('package-selection', package_id=selected,
                             detected_jetpack=host.get('jetpack'), package_jetpack=family,
                             product=item['product'], sensor=sensor, connection=connection,
                             serdes=item.get('serdes'), port=item['port'], selection_scope='USER_SELECTED_NOT_HARDWARE_DETECTED')
            return selected
