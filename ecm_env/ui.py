"""Driver installation requirements and missing-package repair."""
import json, os, subprocess
from pathlib import Path
from .check import observe, feature_allowed
from . import cache, repair
from ecm_install import VERSION, ui as screen
PACKAGE=Path(__file__).resolve().parents[1]

def collect(*, workspace=None, force=True, reason='manual-recheck', target_profile=None):
    return cache.collect(observe, workspace=workspace, force=force, reason=reason, target_profile=target_profile)

def lines(report):
    names = {'bootstrap': 'Python', 'driver_install': 'Driver installation'}
    output=['Environment Check','']
    if report.get('recheck_failed'):
        output.append('Recheck failed; statuses below are the previous observation. Use Recheck before another repair.')
    for name,label in names.items():output.append('  %-20s: %s'%(label,report['features'][name]['status']))
    for name in ('bootstrap','driver_install'):
        if report['features'][name]['status']!='Ready':
            output.append(names[name]+': '+'; '.join(report['features'][name]['reasons'][:2]))
    output+=['','Ready indicates that the driver installation tools are available.', 'Log: '+report.get('log_path','See environment report')]
    state=report.get('startup_check',{})
    if state:output.append('Check: '+('Saved full check; current system unchanged' if state.get('cache_used') else 'Full read-only check')+' / '+state['status'])
    output.extend(state.get('warnings',[]))
    catalog=report.get('runtime_catalog',{})
    if catalog.get('complete'):
        output.append('Pinned runtime catalog ('+catalog.get('profile','unassigned')+'): '+str(catalog['package_count'])+' packages; metadata verified.')
    elif catalog:output.append('Runtime catalog: '+catalog.get('status','Not verified')+'; '+catalog.get('reason',''))
    output.extend(repair.summary(report))
    output.append('Missing-package review may download temporary files before Y; a healthy system needs no network.')
    return output

def guard(feature, workspace=None):
    report=collect(workspace=workspace,reason='before-'+feature)
    if feature_allowed(report,feature):return True
    print('\n'.join(lines(report)))
    print('This function is unavailable: '+'; '.join(report['features'][feature]['reasons'][:2]))
    print('Use Environment Check for missing-package repair or the recorded manual-action reasons.')
    return False

def menu(ask=input, *, report=None, workspace=None, startup=False):
    report=report or collect(workspace=workspace)
    while True:
        screen.begin('Home > '+('Startup' if startup else 'Troubleshoot')+' > Environment Check',VERSION)
        for line in lines(report):screen.line(line)
        screen.footer('[1] Review online package plan','[2] Install missing packages','[3] Recheck','[4] Details','[B] '+('Continue to Home' if startup else 'Back'))
        try:value=ask('Select: ').strip()
        except (EOFError,KeyboardInterrupt):return report
        if value in ('0','B','b',''):return report
        if value not in ('1','2','3','4'):continue
        if value=='3':report=collect(workspace=workspace,reason='manual-recheck');continue
        if value=='4':
            print(json.dumps(report,indent=2))
            ask('Press Enter to return: ')
            continue
        report=repair.run(report,action='plan' if value=='1' else 'apply',ask=ask,
                          workspace=workspace,package=PACKAGE,collect=collect)


def startup(ask=input, workspace=None):
    report=collect(workspace=workspace,force=False,reason='startup')
    if any(report['features'][name]['status']!='Ready' for name in ('bootstrap','driver_install')):
        return menu(ask,report=report,workspace=workspace,startup=True)
    # Healthy environment goes directly to the normal product menu.
    return report
