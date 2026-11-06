"""Explain passive failures and select missing-only repair groups. No mutation."""
INSTALLED = ('install ok installed', 'hold ok installed')
ABSENT = ('not installed', 'unknown ok not-installed', 'deinstall ok config-files')
TOOL_PACKAGES = {'sudo':'sudo', 'depmod':'kmod', 'modinfo':'kmod',
                 'fuser':'psmisc', 'dpkg-query':'dpkg'}
def package_state(packages, name):
    row = packages.get(name)
    if not row or 'status' not in row: return 'unobserved'
    if row['status'] in INSTALLED: return 'installed'
    if row['status'] in ABSENT: return 'absent'
    return 'partial'


def diagnose(observed, profile, mismatch=None):
    issues = []
    packages = observed.get('packages', {})
    def issue(kind, groups, reason, package=None, check=None):
        issues.append(dict(kind=kind, groups=list(groups), reason=reason,
                           package=package, check=check))
    def failed(package, groups, reason, check):
        state = package_state(packages, package)
        kind = {'absent':'package_absent', 'installed':'installed_but_broken',
                'partial':'package_database_partial', 'unobserved':'observation_failed'}[state]
        issue(kind, groups, reason, package, check)
    py = observed.get('python', {})
    if not py.get('ok'):
        kind = py.get('failure_kind')
        reason = '; '.join(py.get('errors', [])) or 'System Python was not verified'
        if kind == 'incompatible': issue('version_incompatible', ('driver',), reason, 'python3','python')
        else: failed('python3', ('driver',), reason, 'python')
    needed = ('depmod','modinfo','fuser','dpkg-query') if observed.get('effective_uid') == 0 else ('sudo','depmod','modinfo','fuser','dpkg-query')
    for tool in needed:
        if not observed.get('tools', {}).get(tool):
            if tool == 'dpkg-query':
                issue('tool_missing', ('driver',), 'Required executable unavailable: /usr/bin/dpkg-query', 'dpkg', 'tool:dpkg-query')
                continue
            failed(TOOL_PACKAGES[tool], ('driver',),
                   'Required executable unavailable: '+tool, 'tool:'+tool)
    for tool, result in observed.get('tool_checks', {}).items():
        if tool not in needed or result.get('exit') == 0: continue
        groups = ('driver',)
        if result.get('exit') is None: issue('observation_failed', groups, 'Tool observation did not complete: '+tool, TOOL_PACKAGES[tool], 'tool:'+tool)
        else: failed(TOOL_PACKAGES[tool], groups, 'Tool functional check failed: '+tool, 'tool:'+tool)
    package_query = observed.get('package_query', {})
    if observed.get('tools', {}).get('dpkg-query') and package_query.get('valid') is not True:
        issue('observation_failed', ('driver',), package_query.get('reason', 'Package state observation unavailable'), check='package-state')
    if observed.get('system') != 'Linux': issue('not_verified', ('driver',), 'Driver installation requires Linux')
    if mismatch: issue('not_verified', ('driver',), mismatch)
    audit = observed.get('dpkg_audit', {})
    if audit and (audit.get('exit') not in (0, None) or audit.get('stdout','').strip()):
        issue('package_database_partial', ('driver',), 'dpkg database requires administrator repair')
    elif audit and audit.get('exit') is None:
        issue('observation_failed', ('driver',), 'dpkg audit did not complete')

    def feature(group):
        entries = [i for i in issues if group in i['groups']]
        kinds = {i['kind'] for i in entries}
        status = ('Incompatible' if 'version_incompatible' in kinds else
                  'Broken' if kinds & {'installed_but_broken','package_database_partial'} else
                  'Not verified' if kinds & {'observation_failed','not_verified'} else
                  'Missing' if entries else 'Ready')
        return dict(status=status, reasons=[i['reason'] for i in entries], issues=entries)
    catalog = observed.get('runtime_catalog', {})
    seeds = observed.get('repair_seeds', {})
    missing_providers = sorted({i['package'] for i in issues
                                if i['kind'] == 'package_absent' and i['package'] in seeds.get('driver', [])})
    missing = {'driver': missing_providers}
    groups = ['driver'] if missing_providers else []
    blockers = []
    if not profile: blockers.append('No exact host profile; online repair is unavailable')
    if not catalog.get('complete'): blockers.append('Verified dependency metadata is unavailable')
    if mismatch: blockers.append(mismatch)
    if not package_query.get('valid'): blockers.append('Package state must be available before missing-package repair')
    if any(i['kind']=='package_database_partial' for i in issues): blockers.append('Repair the dpkg database manually before online repair')
    for g in groups:
        for name, version in observed.get('repair_prerequisites', {}).get(g, {}).items():
            state = package_state(packages, name)
            if state != 'installed': blockers.append(g+': prerequisite '+name+' is '+state+'; administrator repair required')
            elif version != '-' and packages[name].get('version') != version:
                blockers.append(g+': fixed BSP prerequisite differs: '+name)
    manual = [i for i in issues if i['kind'] != 'package_absent' or
              not any(i['package'] in seeds.get(g,[]) for g in i['groups'])]
    return dict(issues=issues, driver_install=feature('driver'),
                repair=dict(recommended_groups=groups, suggested_order=groups,
                            missing_by_group=missing, missing_packages=sorted({p for g in groups for p in missing[g]}),
                            blockers=list(dict.fromkeys(blockers)), manual_issues=manual,
                            eligible=bool(groups) and not blockers))
