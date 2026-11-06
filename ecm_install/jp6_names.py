"""Explicit JP6 MIPI names and read-only checks for unmanaged old loaders."""
from pathlib import Path
import os,re
from .contracts import require,_elf_info,digest
from .diagnostics import DiagnosticError

POLICY='ECM_MODEL_JP6_V1'
NAMES={
    'ecm100-mipi-jp6':('gc2093','ECM100_JP6'),
    'ecm300-mipi-jp6':('nv_imx675','ECM300_JP6'),
    'ecm400-mipi-jp6':('nv_imx415','ECM400_JP6'),
}
OVERLAYS={'/boot/tegra234-p3767-camera-p3768-'+new+'.dtbo' for old,new in NAMES.values()}

def enabled(manifest):
    return (manifest.get('schema')=='ecm-public-prebuilt-v1' and
            manifest.get('installation_policy',{}).get('module_naming')==POLICY)

def validate(manifest):
    require(manifest.get('package_id') in NAMES,'Company naming is limited to the three JP6 MIPI packages')
    old,new=NAMES[manifest['package_id']]
    require(manifest['target']['kernel']=='5.15.148-tegra','Company-named JP6 kernel differs')
    ko=manifest['artifacts']['sensor_ko'];dt=manifest['artifacts']['overlay']
    require(ko['module_name']==new and ko['install_name']==new+'.ko' and Path(ko['path']).name==new+'.ko','Company module name/filenames differ')
    expected='tegra234-p3767-camera-p3768-'+new+'.dtbo'
    require(dt['install_name']==expected and Path(dt['path']).name==expected,'Company overlay filenames differ')

def aliases_from_text(text):
    result=[]
    for line in text.splitlines():
        fields=line.split()
        if len(fields)==3 and fields[0]=='alias':result.append((fields[1],fields[2].replace('-','_')))
    return result

def _files(system,kernel,names):
    root=system.path('/lib/modules/'+kernel)
    if not root.exists():return []
    result=[];entries=0
    def unreadable(error):
        raise DiagnosticError('Cannot inspect target module directory: '+str(error.filename),'MODULE_RENAME_CHECK_FAILED',path=str(error.filename)) from error
    for base,dirs,files in os.walk(root,followlinks=False,onerror=unreadable):
        entries+=len(dirs)+len(files)
        require(entries<=100000,'Module tree exceeds bounded rename check','MODULE_RENAME_CHECK_FAILED')
        # build/source links are normal read-only kernel header references.
        dirs[:]=[n for n in dirs if not (Path(base)/n).is_symlink()]
        for name in files:
            stem=re.sub(r'\.ko(?:\.(?:gz|xz|zst))?$','',name)
            if stem==name:continue
            stem=stem.replace('-','_')
            if stem not in names:continue
            p=Path(base)/name;logical='/lib/modules/'+kernel+'/'+p.relative_to(root).as_posix()
            snap=system.snapshot(logical)
            result.append({'path':logical,'sha256':snap['sha256'],'module':stem})
    return sorted(result,key=lambda x:x['path'])

def _configs(system):
    selected=[]
    for kind in ('modules-load.d','modprobe.d'):
        names={}
        for base in ('/etc','/run','/usr/local/lib','/usr/lib','/lib'):
            logical=base+'/'+kind;root=system.path(logical)
            if not root.exists():continue
            require(root.is_dir(),'Loader configuration directory is not a directory','MODULE_RENAME_CHECK_FAILED')
            for p in sorted(root.glob('*.conf')):
                names.setdefault(p.name,(logical+'/'+p.name,p))
        for logical,p in names.values():
            # Native reads validate masks and linked files before inspection.
            # Preserve each logical path: loader types have different grammars.
            if not hasattr(system,'loader_config_snapshot') and p.is_symlink() and os.readlink(p)=='/dev/null':continue
            selected.append(logical)
    modules=system.path('/etc')/'modules'
    if modules.exists() or modules.is_symlink():selected.append('/etc/modules')
    require(len(selected)<=512,'Loader configuration inventory exceeds bound','MODULE_RENAME_CHECK_FAILED')
    return sorted(selected)

def loader_references(text,logical,obsolete):
    """Check module operands, not comments, descriptive values or inactive files."""
    obsolete={name.replace('-','_') for name in obsolete}
    rows=[];pending='';start=0
    for number,line in enumerate(text.splitlines(),1):
        active=line.split('#',1)[0].strip()
        if not pending:start=number
        continued=active.endswith('\\')
        pending+=active[:-1]+' ' if continued else active
        if continued:continue
        fields=pending.split();pending=''
        if not fields:continue
        if '/modprobe.d/' not in logical:
            referenced={fields[0].lstrip('-')} if len(fields)==1 else set()
        elif fields[0]=='blacklist':continue
        elif fields[0]=='options':referenced=set(fields[1:2])
        elif fields[0]=='alias':referenced=set(fields[1:3])
        elif fields[0]=='softdep':referenced=set(fields[1:])
        elif fields[0] in ('install','remove'):
            referenced=set(re.findall(r'[A-Za-z0-9_-]+',' '.join(fields[1:])))
        else:continue
        if {name.replace('-','_') for name in referenced} & obsolete:rows.append(start)
    require(not pending,'Incomplete loader continuation: '+logical,'MODULE_RENAME_CHECK_FAILED')
    return rows

def _check(system,manifest,contents,old_entries):
    if not enabled(manifest):return None
    old,new=NAMES[manifest['package_id']];kernel=manifest['target']['kernel']
    owned={e['destination']:e['sha256'] for e in old_entries if e.get('sha256')}
    managed_names={Path(p).name[:-3] for p in owned if p.startswith('/lib/modules/'+kernel+'/') and p.endswith('.ko')}
    aliases=set(_elf_info(contents['sensor_ko']).get('alias',[]))
    observed=system.module_aliases(kernel)
    matching=sorted(set((a,m) for a,m in observed['aliases'] if a in aliases))
    relevant={old,new}|{m for a,m in matching}
    files=_files(system,kernel,relevant)
    new_path='/lib/modules/'+kernel+'/updates/drivers/media/i2c/'+new+'.ko'
    problems=[]
    for item in files:
        if item['path']==new_path and (item['sha256']==digest(contents['sensor_ko']) or owned.get(new_path)==item['sha256']):continue
        if owned.get(item['path'])!=item['sha256']:
            problems.append({'kind':'UNMANAGED_MODULE','path':item['path'],'module':item['module']})
    for alias,name in matching:
        if name not in {old,new}|managed_names:
            problems.append({'kind':'UNMANAGED_ALIAS','path':next((f['path'] for f in files if f['module']==name),'/lib/modules/'+kernel),'alias':alias,'module':name})
    overlay_path='/boot/'+manifest['artifacts']['overlay']['install_name']
    overlay=system.snapshot(overlay_path)
    if overlay['existed'] and overlay['sha256']!=digest(contents['overlay']) and owned.get(overlay_path)!=overlay['sha256']:
        problems.append({'kind':'UNMANAGED_DESTINATION','path':overlay_path})
    # Old chain-owned modules of a different selected sensor are also removed;
    # explicit loaders must not keep requesting those superseded modules.
    obsolete=({old}|managed_names)-{new}
    configs=[]
    for logical in _configs(system):
        reader=getattr(system,'loader_config_snapshot',system.snapshot)
        snapshot=reader(logical)
        configs.append({k:v for k,v in snapshot.items() if k!='body'} if 'resolved_path' in snapshot else
                       {'path':logical,'sha256':snapshot['sha256']})
        if snapshot.get('masked'):continue
        body=snapshot['body']
        require(body is not None and len(body)<=256*1024,'Loader configuration unavailable/oversize','MODULE_RENAME_CHECK_FAILED')
        try:text=body.decode('utf-8')
        except UnicodeError as exc:
            raise DiagnosticError('Cannot decode loader configuration: '+logical,'MODULE_RENAME_CHECK_FAILED',path=logical) from exc
        for number in loader_references(text,logical,obsolete):
            problems.append({'kind':'UNMANAGED_LOADER_REFERENCE','path':logical,'line':number})
    from . import r20_storage as S
    ext=system.snapshot('/boot/extlinux/extlinux.conf')
    if ext['existed']:
        _,fields,_=S._active_entry(ext['body'].decode())
        for value in fields.get('APPEND',[]):
            for token in value.split():
                key,separator,argument=token.partition('=')
                key=key.replace('-','_');argument=argument.replace('-','_')
                if ((key in ('modules_load','rd.modules_load') and set(argument.split(',')) & obsolete) or
                        any(key.startswith(name+'.') for name in obsolete)):
                    problems.append({'kind':'ACTIVE_BOOT_ARGUMENT','path':'/boot/extlinux/extlinux.conf','argument':key})
    if problems:raise DiagnosticError('JP6 module rename requires review of unowned modules or old loader references; existing files preserved','MODULE_RENAME_CONFLICT',findings=problems)
    return {'schema':'ecm-jp6-name-transition-v1','old_module':old,'new_module':new,'alias_observation':observed['scope'],'matching_aliases':matching,'module_files':files,'loader_configs':configs,'managed_predecessors':sorted(owned),'action':'Only verified transaction-owned superseded files are removed; reboot remains manual'}


def check(system,manifest,contents,old_entries):
    try:return _check(system,manifest,contents,old_entries)
    except (OSError,UnicodeError) as exc:
        raise DiagnosticError('Cannot complete the JP6 module/loader inventory','MODULE_RENAME_CHECK_FAILED',path=str(getattr(exc,'filename',None)),cause=str(exc)) from exc
