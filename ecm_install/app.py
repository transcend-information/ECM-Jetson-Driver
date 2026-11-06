"""External Manager screens using R20 terminal primitives."""
import argparse
from pathlib import Path
import sys
import json
from . import VERSION, ui
from .catalog import Catalog, PACKAGES
from .contracts import verify_manifest, compare_environment, package_evidence
from .system import SystemIO
from .workspace import resolve_workspace
from .transaction import Engine
from .runlog import Report, reports, emergency_record
from .diagnostics import DiagnosticError, error_detail
from .identity import recorded_identity
from . import setup_display

PACKAGE = Path(__file__).resolve().parents[1]


def create_services(package):
    return SystemIO(), Catalog(package)


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return 'B'


def select_package(host, catalog, current=None, report=None):
    from .selection import select
    return select(host, catalog, ask, current, report)



def installation(engine, catalog):
    from ecm_env.ui import guard
    if not guard('driver_install', workspace=engine.workspace): return
    report = None
    key = None
    try:
        report = Report(engine.workspace, 'install', engine.system)
        report.stage('environment_check')
        host = engine.system.observe()
        report.bind(host=host)
        report.stage('package_check')
        current = recorded_identity(engine)
        report.bind(recorded_current=current)
        key = select_package(host, catalog, current, report)
        if key is None:
            report.finish('CANCELLED', 'Package selection cancelled before transaction creation')
            ui.line('Cancelled. No installation writes.')
            return
        report.bind(package_id=key)
        record = catalog.manifest(key)
        item = PACKAGES[key]
        setup_display.install_page('Confirm installation', host, current, item['jetpack'])
        if record is None:
            setup_display.show_target(item, item['jetpack'], host)
            ui.line('Package Status    : Package unavailable')
            ui.line('The fixed installation set is not included in this package.')
            report.finish('AWAITING_ARTIFACTS', 'No installable artifact set in this package', package_id=key)
            ui.line('Log: ' + str(report.directory))
            return
        path, expected_digest = record
        manifest, _ = verify_manifest(path, key, expected_digest, simulation=engine.system.simulation)
        report.stage('environment_check')
        environment = compare_environment(host, manifest['target'])
        evidence = package_evidence(manifest)
        report.bind(environment=environment, package_evidence=evidence, manifest_sha256=expected_digest)
        report.event('package-verified', package_id=key, artifact_set_id=manifest['artifact_set_id'], manifest_sha256=expected_digest, environment=environment, package_evidence=evidence)
        report.bind(recorded_current=current,selected_identity={'package_id':key,'artifact_set_id':manifest['artifact_set_id'],'manifest_sha256':expected_digest},
                    package_details={'source':manifest.get('source'),'build_reference':manifest.get('build_reference'),
                                     'reference_validation':manifest.get('reference_validation'),'manifest':manifest})
        matches=all(c['status']=='Matched' for c in environment)
        setup_display.show_target(item, item['jetpack'], host, manifest, environment)
        ui.line('Driver Version    : ' + (manifest.get('driver_version') or 'Not declared'))
        ui.line('Package Status    : Precompiled / Package file checksums verified')
        if engine.system.simulation:ui.line('LOCAL_SIMULATION / no host installation')
        if not matches:
            report.event('environment-warning', error_code='ENVIRONMENT_WARNING', comparisons=environment, decision='continue')
        ui.rule()
        ui.line('What happens next')
        ui.line('  1. Back up the current setup and installation records.')
        ui.line('  2. Install the precompiled Driver and tuning files.')
        ui.line('  3. Prepare the next boot configuration.')
        ui.line('  4. Restart the Jetson manually after installation.')
        ui.line('Insufficient original-backup space produces a warning; full restoration may be unavailable.')
        ui.line('Change camera cables only with power disconnected.')
        ui.footer('Nothing is installed until you confirm.', 'Package details are retained in the log.')
        report.stage('confirmation')
        if ask('Install this camera setup? [Y/N] (Enter = No): ').lower() != 'y':
            report.finish('CANCELLED', 'Installation cancelled before planning', package_id=key)
            ui.line('Cancelled. No installation writes.')
            return
        report.event('normal-install-confirmation', accepted=True, package_id=key)
        def finish(result):
            report.finish('SUCCESS', 'Installation files installed. Manual restart required.', transaction_id=result['transaction_id'], package_id=key, environment=environment, package_evidence=evidence, backup_coverage=result.get('backup_coverage'), restoration_complete=result.get('restoration_complete',True))
        if engine.system.simulation:
            result = engine.install(key, path, expected_digest, report.id, on_complete=finish, diagnostics=report)
        else:
            from .privileged import launch_install
            result = launch_install(engine,catalog,report,key,expected_digest)
        label = 'SUCCESS WITH WARNING' if result.get('restoration_complete') is False else 'SUCCESS'
        ui.line('[' + label + '] Installation files installed. Restart the Jetson manually.')
        if result.get('restoration_complete') is False:
            ui.line('Warning: Installation completed with incomplete original backups; full restoration is unavailable.')
        if result.get('history_notices'):
            ui.line('Warning: unrelated history was not verified; preserved and recorded in the transaction log.')
    except Exception as exc:
        show_failure(report, exc, 'install', package_id=key)
    finally:
        if report is not None:
            ui.line('Report ID: ' + report.id)
            ui.line('Log: ' + str(report.emergency_directory or report.directory))
            if report.emergency_directory is None:
                ui.line('View this report in Troubleshoot > Installation Logs.')


def show_failure(report, exc, operation, **fields):
    detail = error_detail(exc, report.stage_name if report else 'startup')
    try:
        if report is not None:
            record = report.fail(exc, **fields)
        else:
            directory, record = emergency_record(operation, exc, context=fields, primary=detail)
            ui.line('Emergency log: ' + str(directory))
    except Exception as persistence:
        record = dict(status='FAILED', operation=operation, transaction_id=None,
                      diagnostic=detail, log_available=False, persistence_error=str(persistence))
        print(json.dumps(record), file=sys.stderr)
        ui.line('Log persistence unavailable; diagnostic record emitted to stderr.')
    outcome=detail.get('outcome','FAILED')
    ui.line('['+outcome+'] ' + detail['stage'] + ' / ' + detail['error_code'])
    if outcome=='PARTIAL':
        ui.line('Boot configuration incomplete.' if detail['stage']=='boot_config' else 'Installation incomplete.')
        ui.line('New files already written are retained. Keep the Jetson running.')
    if detail.get('recovery_status') == 'FILES_RESTORED':
        ui.line('Earlier file changes were rolled back.')
    context=detail.get('context',{})
    if context.get('destination'):ui.line('Destination: '+context['destination'])
    completed=detail.get('completed_roles',context.get('completed_roles',[]))
    if completed:ui.line('Writes completed before failure: '+', '.join(completed))
    ui.line('Cause: ' + detail['message'].replace('\n', ' ')[:180])
    ui.line('Next: ' + detail['next_action'])


def log_menu(engine):
    records, warnings = reports(engine.workspace)
    records = [(p, r) for p, r in records if r.get('operation') == 'install']
    ui.begin('Home > Troubleshoot > Installation Logs', VERSION)
    for warning in warnings:
        ui.line(warning)
    for index, (_, record) in enumerate(records, 1):
        label = record['status']
        if label == 'SUCCESS' and record.get('restoration_complete') is False:
            label = 'SUCCESS WITH WARNING'
        ui.line('[%d] %s / install / %s' % (index, record['report_id'], label))
    if not records:
        ui.line('No saved installation reports.')
    ui.footer('[0] Back')
    value = ask('View report: ')
    if value.isascii() and value.isdigit() and len(value) <= 6 and 1 <= int(value) <= len(records):
        directory, record = records[int(value)-1]
        if 'recorded_result' in record:
            ui.line('Originally recorded: ' + str(record['recorded_result']['status']))
            ui.line('Current evidence: ' + record['status'])
        if record['status'] == 'SUCCESS':
            ui.line('Installation files installed. Manual restart required.')
            if record.get('restoration_complete') is False:
                ui.line('Some original files could not be backed up.')
        else:
            ui.line(str(record['message']))
            ui.line('Stage / code: ' + str(record.get('stage')) + ' / ' + str(record.get('error_code')))
        for warning in record.get('record_warnings', []):
            ui.line('Evidence: ' + warning)
        ui.line('Log: ' + str(directory))
        ask('Press Enter to return: ')


def troubleshoot_menu(engine):
    environment_report = None
    while True:
        ui.begin('Home > Troubleshoot', VERSION)
        ui.line('Installation Logs   View saved installation reports and error details.')
        ui.line('Environment Check   Check system requirements for driver installation.')
        ui.footer('[1] Installation Logs', '[2] Environment Check', '[B] Back')
        value = ask('Select: ')
        if value.lower() in ('0', 'b'):
            return environment_report
        if value == '1':
            log_menu(engine)
        elif value == '2':
            from ecm_env.ui import menu
            environment_report = menu(ask, workspace=engine.workspace)


def main(argv=None):
    parser = argparse.ArgumentParser(description='ECM Camera Manager local review candidate')
    parser.add_argument('--workspace')
    parser.add_argument('--version', action='store_true')
    args = parser.parse_args(argv)
    if args.version:
        print(VERSION)
        return 0
    from ecm_env.ui import startup
    try:
        system, catalog = create_services(PACKAGE)
        record = resolve_workspace(args.workspace, PACKAGE)
        engine = Engine(record['path'], system)
        environment_report = startup(ask,workspace=engine.workspace)
        while True:
            host = system.observe()
            current=recorded_identity(engine)
            observations=system.home_observe()
            setup_display.home(host,current,environment_report,observations,record['warnings'])
            from . import r20_storage as storage
            try:
                storage.atomic_json(engine.wpath('logs/home/latest.json'),
                    {'host':host,'recorded_current':current,'observations':observations,
                     'environment_check':environment_report.get('startup_check',{}),
                     'execution_identity':environment_report.get('execution_identity',{})})
            except OSError as exc:
                ui.line('Notice: Home observation log unavailable: ' + str(exc))
            if system.simulation:
                ui.line('LOCAL_SIMULATION / NOT FOR RELEASE / no hardware verification')
            ui.footer('[1] Install / Switch Camera', '[2] Troubleshoot', '[0] Exit')
            value = ask('Select: ')
            if value in ('0','B','b'):
                return 0
            try:
                if value == '1':
                    installation(engine, catalog)
                    ask('Press Enter to return: ')
                elif value == '2':
                    updated=troubleshoot_menu(engine)
                    if updated:environment_report=updated
            except Exception as exc:
                ui.line('[ERROR] ' + str(exc))
    except (ValueError, OSError, RuntimeError) as exc:
        show_failure(None, exc, 'startup')
        return 2
