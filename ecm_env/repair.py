"""Missing-package UI boundary and linked receipts. Password I/O stays native."""
import json
from copy import deepcopy
import os
import subprocess
import uuid
from pathlib import Path
from . import cache


def _online_state(action):
    return Path('/var/log/ecm-environment') if os.geteuid()==0 or action=='apply' else cache._parent(None)


def summary(report):
    plan = report.get('diagnosis', {}).get('repair', {})
    groups = plan.get('recommended_groups', [])
    lines = ['Suggested repair: ' + ('Driver' if groups else 'No automatic missing-package repair')]
    missing=plan.get('missing_packages', [])
    if missing: lines.append('Absent packages: '+', '.join(missing[:6])+(' (+%d; see Details)'%(len(missing)-6) if len(missing)>6 else ''))
    for reason in plan.get('blockers', [])[:2]: lines.append('Repair unavailable: '+reason)
    manual = plan.get('manual_issues', [])
    if manual:
        lines.append('Manual attention: '+manual[0]['reason'])
        lines.append('Installed-but-broken, incompatible or unverified items are not automatically replaced.')
    last = report.get('last_repair')
    if last:
        lines += ['Last repair: '+last['outcome'], 'Repair log: '+last['log']]
    return lines


def run(report, *, action, ask, workspace, package, collect):
    plan = report.get('diagnosis', {}).get('repair', {})
    groups = plan.get('recommended_groups', [])
    operation_id = uuid.uuid4().hex
    details = dict(operation_id=operation_id, recommended_groups=groups, actual_group=None,
                   before_log=report.get('log_path'), before_features=report['features'],
                   diagnosis=report.get('diagnosis'), online_started=False,
                   download_started='no', apply_started='no', system_change='none',
                   recheck='NOT_RUN', confirmation='not_requested', credential_capture=False)
    # If this receipt cannot be allocated, do not start a system operation.
    try:
        log = cache.record_operation(workspace, action=action, feature=None, stage='CHECK',
                                     return_code=None, details=details)
    except (OSError, ValueError) as exc:
        print('Repair not started: cannot save the environment receipt: '+str(exc))
        return report
    entry = json.loads(Path(log).read_text())
    def save(stage, code=None, outcome=None):
        entry.update(stage=stage, return_code=code, details=details)
        if outcome: entry['outcome']=outcome
        cache._atomic(Path(log), entry)
        if outcome:
            report['last_repair'] = dict(outcome=outcome, log=log)
            print('Environment repair: '+outcome+'\nLog: '+log)
    if not plan.get('eligible'):
        save('NOT_ENTERED', outcome='No repair started. '+('; '.join(plan.get('blockers',[])) or
             'No absent repair seed packages. Review functional failures or use Recheck.'))
        return report
    feature = 'driver'
    details['actual_group'] = feature
    state = _online_state(action)
    shell_log = state/('online.'+operation_id)
    details['shell_log'] = str(shell_log)
    command = ['/bin/bash', str(Path(package)/'bootstrap/ecm_online.sh'), '--'+action,
               '--group', feature, '--operation-id', operation_id]
    for provider in plan['missing_packages']:
        command.extend(['--provider', provider])
    if action=='apply' and os.geteuid()!=0:
        if not Path('/usr/bin/sudo').is_file():
            save('SUDO_UNAVAILABLE', outcome='sudo is missing; no online repair started. Administrator assistance is required.')
            return report
        save('AUTHENTICATION')
        try: auth = subprocess.call(['/usr/bin/sudo','-v'])
        except (OSError, KeyboardInterrupt): auth=None
        if auth!=0:
            save('AUTHENTICATION_DECLINED',auth,'Native authentication was not completed; no online repair started.')
            return report
        command=['/usr/bin/sudo','-n','--',*command]
    if action=='apply': cache.invalidate(workspace,'online-repair-requested')
    details['online_started']=True
    details.update(download_started='unknown', apply_started='unknown', system_change='unknown' if action=='apply' else 'none')
    save('ONLINE_REQUESTED')
    try: rc=subprocess.call(command)
    except (OSError,KeyboardInterrupt) as exc:
        rc=None; details['launch_error']=type(exc).__name__+': '+str(exc)
    shell = {}
    try:
        path=shell_log/'result.txt'
        if path.is_symlink() or path.stat().st_size>16384: raise ValueError('Unsafe or oversized online result')
        shell=dict(line.split('=',1) for line in path.read_text().splitlines() if '=' in line)
        if shell.get('operation_id')!=operation_id: raise ValueError('Online result identity differs')
        details['shell_result']=shell
        for key in ('download_started','apply_started','system_change','confirmation'): details[key]=shell.get(key,'unknown')
        reason=shell_log/'stop-reason.txt'
        if not reason.is_symlink() and reason.stat().st_size<=16384: details['stop_reason']=reason.read_text().strip()
    except (OSError, ValueError) as exc:
        details['shell_result_error']=str(exc)
    save('RECHECK',rc)
    try:
        report=collect(workspace=workspace,force=True,reason='online-'+action+'-finished')
        details.update(recheck='COMPLETE',after_log=report.get('log_path'),after_features=report['features'],
                       unresolved=report.get('diagnosis',{}).get('issues',[]))
    except Exception as exc:
        details.update(recheck='FAILED',recheck_error=type(exc).__name__+': '+str(exc))
        # Keep the previous feature observations, but never authorize another
        # missing-only plan from a stale pre-apply package snapshot.
        report=deepcopy(report)
        report['recheck_failed']=details['recheck_error']
        retry=report.setdefault('diagnosis',{}).setdefault('repair',{})
        retry['eligible']=False
        retry.setdefault('blockers',[]).append('Recheck failed; use Recheck before requesting another package operation')
    ready=details['recheck']=='COMPLETE' and all(report['features'][k]['status']=='Ready' for k in ('bootstrap','driver_install'))
    status=shell.get('status','NOT_ENTERED_OR_INTERRUPTED')
    label={'APPLIED_RECHECK_REQUIRED':'Missing packages installed','NO_CHANGE':'NO_CHANGE: no installation',
           'PLAN_READY':'Plan reviewed; no installation','CANCELLED':'Installation cancelled',
           'STOPPED':'Online repair stopped','NOT_ENTERED_OR_INTERRUPTED':'Online entry/completion not confirmed'}.get(status,status)
    outcome=label+'; '+('dependencies Ready after recheck' if ready else 'dependencies unresolved')
    if shell.get('error') not in (None,'NONE'): outcome+='; '+shell['error']
    if details['recheck']=='FAILED': outcome+='; recheck failed: '+details['recheck_error']
    if status=='NO_CHANGE': outcome+=' (no installation; exit 0 is not a repair result)'
    if details.get('stop_reason') and status=='STOPPED': outcome+='; '+details['stop_reason']
    outcome+='; system package changes: '+details['system_change']
    save('FINISHED',rc,outcome)
    return report
