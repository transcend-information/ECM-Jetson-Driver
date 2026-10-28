"""Installation-only screens using R20 terminal primitives."""
import argparse
from pathlib import Path
import sys
from . import VERSION, ui
from .catalog import Catalog, PACKAGES, PUBLIC_PACKAGES
from .contracts import verify_manifest, compare_environment, package_evidence, RECEIVED_SCHEMA
from .system import SystemIO
from .workspace import resolve_workspace
from .transaction import Engine
from .runlog import Report, reports, export_report

PACKAGE = Path(__file__).resolve().parents[1]


def create_services(package):
    return SystemIO(), Catalog(package)


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return 'B'


def select_package(host, catalog):
    family = host.get('jetpack')
    while True:
        if family not in ('6','7'):
            ui.begin('Home > Install / Switch Camera > JetPack', VERSION)
            ui.line('Environment: Not verified. Select the intended package family.')
            ui.footer('[6] JetPack 6.x', '[7] JetPack 7.x', '[B] Back')
            family = ask('Select: ')
            if family.upper() == 'B':
                return None
            if family not in ('6','7'):
                continue
        ui.begin('Home > Install / Switch Camera > Camera setup', VERSION)
        ui.line('Package family: JetPack ' + family + '.x (host detection does not identify the camera)')
        keys = [key for key, item in PUBLIC_PACKAGES.items() if item['jetpack'] == family]
        for index, key in enumerate(keys, 1):
            item = PACKAGES[key]
            status = 'Received' if catalog.manifest(key) is not None else 'Awaiting artifacts'
            ui.line('[' + str(index) + '] ' + item.get('display_name', item['product']) + ' / ' + item['sensor'] + ' / ' + item['connection'] + ' / ' + item['port'] + (' / ' + item['serdes'] if 'serdes' in item else '') + ' | ' + status)
        ui.footer('[9] Change JetPack family', '[B] Back')
        value = ask('Select: ')
        if value.upper() == 'B':
            return None
        if value == '9':
            family = None
        elif value.isdigit() and 1 <= int(value) <= len(keys):
            return keys[int(value)-1]


def installation(engine, catalog):
    host = engine.system.observe()
    key = select_package(host, catalog)
    if key is None:
        ui.line('Cancelled. No installation writes.')
        return
    report = Report(engine.workspace, 'install', engine.system)
    try:
        record = catalog.manifest(key)
        item = PACKAGES[key]
        ui.begin('Home > Install / Switch Camera > Confirm installation', VERSION)
        ui.line('Install Target    : ' + item.get('display_name', item['product']) + ' / ' + item['connection'] + ' / JP' + item['jetpack'] + ' / ' + item['port'])
        ui.line('Current System    : ' + str(host.get('l4t')) + ' / Kernel ' + str(host.get('kernel')))
        if record is None:
            ui.line('Package Status    : Awaiting artifacts')
            ui.line('No approved KO / DTBO / tuning set has been received for this package.')
            report.finish('AWAITING_ARTIFACTS', 'No installable artifact set received', package_id=key)
            ui.line('Log: ' + str(report.directory))
            return
        path, expected_digest = record
        manifest, _ = verify_manifest(path, key, expected_digest, simulation=engine.system.simulation)
        environment = compare_environment(host, manifest['target'])
        evidence = package_evidence(manifest)
        report.event('package-verified', package_id=key, artifact_set_id=manifest['artifact_set_id'], manifest_sha256=expected_digest, environment=environment, package_evidence=evidence)
        ui.line('Package Target    : L4T ' + str(manifest['target'].get('l4t') or 'Not verified') + ' / Kernel ' + manifest['target']['kernel'])
        ui.line('Board Target      : ' + str(manifest['target'].get('board') or 'Not verified'))
        ui.line('Driver Version    : ' + str(manifest.get('driver_version') or 'Not verified'))
        ui.line('Overlay Version   : ' + str(manifest.get('overlay_version') or 'Not verified'))
        received = manifest['schema'] == RECEIVED_SCHEMA
        ui.line('Package Status    : ' + ('Received upstream binaries / not release approved' if received else 'SIMULATION / NOT FOR RELEASE' if engine.system.simulation else 'Verified files'))
        ui.line('Artifact Set      : ' + manifest['artifact_set_id'])
        if received:
            ui.line('Source commit     : ' + manifest['source']['commit'])
            ui.line('Build / target ABI / hardware: Not verified; static artifact checks only.')
            if engine.system.simulation:ui.line('Execution         : LOCAL_SIMULATION using received real bytes')
        for comparison in environment:
            ui.line(comparison['check'] + ': ' + comparison['status'] + ' | actual=' + str(comparison['actual']) + ' | expected=' + str(comparison['expected']))
        if any(c['status'] != 'Matched' for c in environment):
            ui.line('Environment differences are recorded. Installation will continue after the normal confirmation.')
        ui.line('1. Back up current files and installed state.')
        ui.line('2. Install this fixed package and prepare the next boot entry.')
        ui.line('3. Save the transaction and recovery evidence. No restart is performed.')
        if ask('Install this camera setup? [Y/N] (Enter = No): ').lower() != 'y':
            report.finish('CANCELLED', 'Installation cancelled before planning', package_id=key)
            ui.line('Cancelled. No installation writes.')
            return
        report.event('normal-install-confirmation', accepted=True, package_id=key)
        def finish(result):
            report.finish('SUCCESS', 'Installation files completed; runtime not verified', transaction_id=result['transaction_id'], package_id=key, environment=environment, package_evidence=evidence)
        result = engine.install(key, path, expected_digest, report.id, on_complete=finish)
        ui.line('[SUCCESS] Installation files completed. Restart required on the target system; runtime not verified.')
        ui.line('Transaction: ' + result['transaction_id'])
        if result.get('history_notices'):
            ui.line('Warning: unrelated history was not verified; preserved and recorded in the transaction log.')
    except Exception as exc:
        try:
            report.finish('FAILED', str(exc), package_id=key)
        except Exception as log_error:
            ui.line('[ERROR] Required report persistence failed: ' + str(log_error))
        ui.line('[ERROR] ' + str(exc))
    ui.line('Report ID: ' + report.id)
    ui.line('Log: ' + str(report.directory))


def restoration(engine):
    report = Report(engine.workspace, 'restore', engine.system)
    try:
        target = engine.restore_target()
        ui.begin('Home > Backups / Restore', VERSION)
        ui.line('[1] ' + target['kind'].title() + ' transaction ' + target['transaction_id'] + ' / ' + target['package_id'])
        ui.line('Only the latest active transaction is selectable. Older backups remain preserved.')
        ui.footer('[B] Back')
        if ask('Select backup: ') != '1':
            report.finish('CANCELLED', 'Restore cancelled before planning')
            return
        ui.line('Restore original bytes, permissions and recorded absence; preserve unrelated files and history.')
        if ask('Restore this backup? [Y/N] (Enter = No): ').lower() != 'y':
            report.finish('CANCELLED', 'Restore confirmation declined')
            return
        report.event('normal-restore-confirmation', accepted=True, target=target)
        def finish(result):
            report.finish('SUCCESS', 'Restore/recovery files completed; runtime not verified', transaction_id=result['transaction_id'], package_evidence=result.get('package_evidence'))
        result = engine.restore(report.id, on_complete=finish)
        ui.line('[SUCCESS] Restore / recovery completed. No restart was performed.')
    except Exception as exc:
        report.finish('FAILED', str(exc))
        ui.line('[ERROR] ' + str(exc))
    ui.line('Report ID: ' + report.id)
    ui.line('Log: ' + str(report.directory))


def log_menu(engine):
    records, warnings = reports(engine.workspace)
    ui.begin('Home > Installation logs', VERSION)
    for warning in warnings:
        ui.line(warning)
    for index, (_, record) in enumerate(records, 1):
        ui.line('[' + str(index) + '] ' + record['report_id'] + ' / ' + record['operation'] + ' / ' + record['status'])
    ui.footer('[B] Back')
    value = ask('View report: ')
    if value.isdigit() and 1 <= int(value) <= len(records):
        directory, record = records[int(value)-1]
        ui.line(record['message'])
        ui.line('Log: ' + str(directory))
        if ask('Export installation summary? [Y/N] (Enter = No): ').lower() == 'y':
            destination, sha = export_report(engine.workspace, directory)
            ui.line('Export: ' + destination)
            ui.line('SHA256: ' + sha)


def main(argv=None):
    parser = argparse.ArgumentParser(description='ECM installation-only local candidate')
    parser.add_argument('--workspace')
    parser.add_argument('--version', action='store_true')
    args = parser.parse_args(argv)
    if args.version:
        print(VERSION)
        return 0
    try:
        system, catalog = create_services(PACKAGE)
        record = resolve_workspace(args.workspace, PACKAGE)
        engine = Engine(record['path'], system)
        while True:
            host = system.observe()
            ui.begin('Home', VERSION)
            ui.line('System: JetPack ' + str(host.get('jetpack') or 'Not verified') + ' / L4T ' + str(host.get('l4t') or 'Not verified'))
            ui.line('Kernel: ' + str(host.get('kernel')))
            try:
                installed = engine.state()
                ui.line('Camera Setup: ' + (installed.get('package_id', installed.get('profile_id', 'Recorded legacy setup')) if installed else 'Not selected'))
                ui.line('Installed State: ' + (installed.get('status', 'Recorded; not runtime verified') if installed else 'No managed installation recorded'))
            except (OSError, ValueError) as exc:
                ui.line('Installed State: Not verified (' + str(exc) + ')')
            ui.line('Camera Device: Not verified; no active probe or capture is performed')
            ui.line('Workspace: ' + str(engine.workspace))
            received = [k for k in PUBLIC_PACKAGES if catalog.manifest(k) is not None]
            ui.line('Package Status: ' + str(len(received)) + ' received / ' + str(len(PUBLIC_PACKAGES)-len(received)) + ' awaiting; target / hardware Not verified')
            # for key in received:
            #     item=PACKAGES[key]
            #     ui.line('Received: ' + item['product'] + ' / ' + item['connection'] + ' / JP' + item['jetpack'])
            if system.simulation:
                ui.line('LOCAL_SIMULATION / NOT FOR RELEASE / no hardware verification')
            for warning in record['warnings']:
                ui.line('Warning: ' + warning)
            ui.footer('[1] Install / Switch Camera', '[2] Backups / Restore', '[3] Installation logs', '[0] Exit')
            value = ask('Select: ')
            if value in ('0','B','b'):
                return 0
            try:
                if value == '1': installation(engine, catalog)
                elif value == '2': restoration(engine)
                elif value == '3': log_menu(engine)
            except Exception as exc:
                ui.line('[ERROR] ' + str(exc))
    except (ValueError, OSError, RuntimeError) as exc:
        print('[ERROR] ' + str(exc), file=sys.stderr)
        return 2
