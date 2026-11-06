"""Read-only functional readiness. Ready means dependencies present, never frames."""
import hashlib, importlib, json, os, platform, re, subprocess, sys
from pathlib import Path
from . import INTERFACE_VERSION
from .profiles import select_observed, target_mismatch
from .catalog import inspect as inspect_catalog
from .diagnosis import diagnose, TOOL_PACKAGES

PYTHON = '/usr/bin/python3'
STANDARD_LIBRARY = ('argparse','collections','contextlib','copy','ctypes','dataclasses',
    'datetime','fcntl','fractions','gzip','hashlib','io','json','lzma','math','os',
    'pathlib','platform','re','selectors','shlex','shutil','signal','stat','struct',
    'subprocess','tarfile','tempfile','threading','time','traceback','uuid','zlib')
TOOLS = {'sudo':'/usr/bin/sudo','depmod':'/usr/sbin/depmod','modinfo':'/usr/sbin/modinfo',
    'fuser':'/usr/bin/fuser','dpkg-query':'/usr/bin/dpkg-query',
    'dpkg-deb':'/usr/bin/dpkg-deb','apt-get':'/usr/bin/apt-get'}
def item(status, reasons=(), **extra):
    return dict(status=status, reasons=list(reasons), **extra)

def summarize(observed):
    """Pure policy, shared by actual observation and explicitly labelled tests."""
    python = observed['python']
    bootstrap = item('Ready' if python['ok'] else 'Missing', python.get('errors', []))
    profile=select_observed(observed['environment'])
    diagnostic=diagnose(observed, profile, target_mismatch(profile,observed.get('selected_target_profile')))
    driver=diagnostic['driver_install']
    if not python['ok']:
        py_issues=[i for i in diagnostic['issues'] if i.get('check')=='python']
        bootstrap=item('Incompatible' if any(i['kind']=='version_incompatible' for i in py_issues) else
                       'Broken' if any(i['kind']=='installed_but_broken' for i in py_issues) else
                       'Missing' if any(i['kind']=='package_absent' for i in py_issues) else 'Not verified', python.get('errors',[]))
    return {'bootstrap': bootstrap, 'driver_install': driver}

def run(argv, timeout=12):
    try:
        r=subprocess.run(argv,stdin=subprocess.DEVNULL,capture_output=True,text=True,
            errors='replace',timeout=timeout,env=dict(os.environ,LC_ALL='C'),shell=False)
        return {'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
    except (OSError,subprocess.TimeoutExpired) as exc:
        return {'exit':None,'stdout':'','stderr':type(exc).__name__+': '+str(exc)}

def system_python():
    errors=[]
    try:
        if not os.path.samefile(sys.executable,PYTHON): errors.append('This process is not the system /usr/bin/python3')
    except OSError:errors.append('System /usr/bin/python3 is missing')
    if sys.version_info<(3,10):errors.append('Python >= 3.10 is required')
    if sys.prefix!=sys.base_prefix:errors.append('Virtual environments are not used for the privileged installer')
    for name in STANDARD_LIBRARY:
        try:importlib.import_module(name)
        except (ImportError,OSError) as exc:errors.append(name+': '+str(exc))
    return dict(ok=not errors,errors=errors,executable=sys.executable,version=sys.version,
                failure_kind='incompatible' if sys.version_info<(3,10) or sys.prefix!=sys.base_prefix else 'functional' if errors else None)

def require_system_python():
    result=system_python()
    if not result['ok']:
        raise RuntimeError('Use bash ecm_camera_manager.sh with system Python: '+'; '.join(result['errors']))

def package_observation(query, names, host_arch):
    if not host_arch:
        return {}, {'valid': False, 'reason': 'dpkg did not report the host package architecture'}
    if query['exit'] != 0:
        reason = (query['stderr'] or 'no diagnostic output').strip()[:180]
        return {}, {'valid': False, 'reason': 'dpkg-query did not complete: ' + reason}
    packages = {name: {'status': 'not installed'} for name in names}
    rows = query['stdout'].splitlines()
    if not rows:
        return {}, {'valid': False, 'reason': 'dpkg-query returned an empty package list'}
    state_pattern = (r'(unknown|install|hold|deinstall|purge) (ok|reinstreq) '
                     r'(not-installed|config-files|half-installed|unpacked|half-configured|triggers-awaited|triggers-pending|installed)')
    for line in rows:
        fields = line.split('\t')
        if len(fields) != 4:
            return {}, {'valid': False, 'reason': 'dpkg-query returned a malformed package row'}
        name, version, architecture, status = fields
        if (not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*', name) or
                not re.fullmatch(state_pattern, status) or
                (status.endswith(' installed') and not (version and architecture))):
            return {}, {'valid': False, 'reason': 'dpkg-query returned invalid package metadata'}
        if name in names and architecture in (host_arch, 'all'):
            packages[name] = dict(version=version, architecture=architecture, status=status)
    return packages, {'valid': True, 'rows': len(rows)}


def observe(target_profile=None):
    """Read the host and the driver installation tool requirements."""
    env={};commands=[]
    try:
        content=Path('/etc/os-release').read_text()
        for line in content.splitlines():
            if line.startswith('VERSION_ID='):env['ubuntu']=line.split('=',1)[1].strip('"')
            if line.startswith('ID='):env['os_id']=line.split('=',1)[1].strip('"')
    except OSError:pass
    try:
        text=Path('/etc/nv_tegra_release').read_text()
        match=re.search(r'R(\d+).*REVISION:\s*([\d.]+)',text)
        if match:env['l4t']='r'+match[1]+'.'+match[2]
    except OSError:pass
    arch=run(['/usr/bin/dpkg','--print-architecture']);commands.append(dict(command='dpkg-architecture',**arch))
    if arch['exit']==0:env['architecture']=arch['stdout'].strip()
    profile=select_observed(env)
    names = {'python3', *TOOL_PACKAGES.values()}
    seeds={}; prerequisites={}
    catalog={'status':'Unavailable','complete':False,'reason':'No exact observed host profile; target choice cannot override the host'}
    if profile:
        try:
            catalog=inspect_catalog(profile)
            directory=Path(catalog['path'])
            for group in ('driver',):
                seeds[group]=(directory/('seeds-'+group+'.txt')).read_text().splitlines()
                prerequisites[group]=dict(line.split('\t') for line in (directory/('prerequisites-'+group+'.tsv')).read_text().splitlines())
                names.update(seeds[group]); names.update(prerequisites[group])
        except (OSError,ValueError,KeyError,TypeError) as exc:
            catalog={'status':'Unavailable','complete':False,'reason':str(exc)}
            seeds={}; prerequisites={}
    packages={}
    query=run(['/usr/bin/dpkg-query','-W','-f=${Package}\t${Version}\t${Architecture}\t${Status}\n'])
    commands.append(dict(command='runtime-package-state',**query))
    packages, package_query = package_observation(query, names, env.get('architecture'))
    audit=run(['/usr/bin/dpkg','--audit']); commands.append(dict(command='dpkg-audit',**audit))
    py=system_python()
    tools={n:Path(p).is_file() and os.access(p,os.X_OK) for n,p in TOOLS.items()}
    tool_checks={n:run([TOOLS[n],'--version']) for n in TOOL_PACKAGES if tools.get(n)}
    result={'schema':INTERFACE_VERSION,'scope':'PASSIVE_ENVIRONMENT_ONLY','system':platform.system(),
        'python':py,'environment':env,'observed_profile':profile['id'] if profile else None,'effective_uid':os.geteuid(),
        'selected_target_profile':target_profile,
        'tools':tools,'tool_checks':tool_checks,
        'packages':packages,'package_query':package_query,'repair_seeds':seeds,'repair_prerequisites':prerequisites,'dpkg_audit':audit,'runtime_catalog':catalog,'commands':commands}
    result['features']=summarize(result)
    result['diagnosis']=diagnose(result,profile,target_mismatch(profile,target_profile))
    return result


def feature_allowed(report,name):
    return report['features'][name]['status'] == 'Ready'
