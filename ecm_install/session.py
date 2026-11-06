"""Authenticated root Manager session and original-operator attribution."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import hashlib,json,os,signal,stat,uuid
from .contracts import safe_path,load_json,digest,require,token
from .diagnostics import DiagnosticError
from . import r20_storage as S
from .system import file_lock

SCHEMA='ecm-root-manager-session-v1'
_current=None

def context():return deepcopy(_current) if _current is not None else None

def operator():
    import pwd
    root=os.geteuid()==0
    supplied=[name in os.environ for name in ('SUDO_UID','SUDO_GID','SUDO_USER')]
    require(not root or not any(supplied) or all(supplied),'Incomplete sudo operator identity','NATIVE_REQUEST_INVALID')
    if root and all(supplied):
        uid=int(os.environ['SUDO_UID']);gid=int(os.environ['SUDO_GID']);origin='sudo'
    else:uid=os.getuid();gid=os.getgid();origin='direct-root' if root else 'login-user'
    require(uid>=0 and gid>=0,'Invalid operator identity','NATIVE_REQUEST_INVALID')
    try:account=pwd.getpwuid(uid)
    except KeyError as exc:raise DiagnosticError('Operator account is unavailable','NATIVE_REQUEST_INVALID') from exc
    require(gid==account.pw_gid,'Operator primary group differs','NATIVE_REQUEST_INVALID')
    if root and all(supplied):require(account.pw_name==os.environ['SUDO_USER'],'Sudo operator name differs','NATIVE_REQUEST_INVALID')
    return {'uid':uid,'gid':gid,'name':account.pw_name,'home':account.pw_dir,'origin':origin}

def attribution():
    return {'execution_uid':getattr(os,'geteuid',lambda:None)(),
            'execution_gid':getattr(os,'getegid',lambda:None)(),
            'operator':deepcopy(_current['operator']) if _current else None,
            'session_id':_current['session_id'] if _current else None}

def cache_identity():
    value=attribution();value.pop('session_id')
    return value

def metadata_owner(path):
    """New session metadata is root-owned; existing records keep their owner."""
    p=Path(path).absolute()
    return (0,0) if _current and not p.exists() and p.is_relative_to(Path(_current['workspace'])) else None

def workspace_path(value,package,actor):
    require(value is not None,'Specify --workspace explicitly; root never selects a new /root workspace','WORKSPACE_CHANGED')
    p=Path(os.path.abspath(value));package=Path(package).absolute()
    safe_path(p.parent,p.name)
    require(p!=package and not (p.is_relative_to(package) and p.relative_to(package).parts[0] in
        ('ecm_install','packages','manifests','docs','ecm_env','bootstrap','dependencies')),
        'Workspace cannot replace distributed code or payload','WORKSPACE_CHANGED')
    if p.exists():
        st=p.stat();require(p.is_dir() and (st.st_uid in (0,actor['uid']) or
            (os.geteuid()==0 and actor['origin']=='direct-root')) and not st.st_mode&0o022,
            'Workspace owner or writable permissions differ','WORKSPACE_CHANGED')
    return p

def _save(path,data):
    S._atomic_bytes(path,(json.dumps(data,indent=2)+'\n').encode(),0o600,0,0)

def _bootstrap(directory,actor,value):
    if not value:return None
    source=Path(value).absolute();source=safe_path(source,'bootstrap.log')
    parent=source.parent
    allowed=Path(actor['home'])/'.local/state/ecm-camera-manager/environment'
    require((parent.parent==allowed and parent.name.startswith('startup.')) or
            (parent.parent==Path('/tmp') and parent.name.startswith('ecm-root-bootstrap.')),
            'Bootstrap log is outside its fixed origin','NATIVE_REQUEST_INVALID')
    info=source.stat();pi=parent.stat()
    require(source.is_file() and info.st_nlink==1 and info.st_size<=1024*1024 and
            info.st_uid in (0,actor['uid']) and pi.st_uid in (0,actor['uid']) and not (info.st_mode|pi.st_mode)&0o022,
            'Bootstrap log ownership or bounds differ','NATIVE_REQUEST_INVALID')
    target=directory/'bootstrap.log';S._atomic_bytes(target,source.read_bytes(),0o600,0,0)
    return str(target)

@contextmanager
def private_umask():
    previous=os.umask(0o077)
    try:yield
    finally:os.umask(previous)

@contextmanager
def root_session(workspace,package,actor,origin_context=None):
    with private_umask():
        with _root_session(workspace,package,actor,origin_context) as current:yield current

@contextmanager
def _root_session(workspace,package,actor,origin_context=None):
    global _current
    require(os.geteuid()==os.getuid()==0 and os.getegid()==0,'Root Manager session required','NATIVE_PLATFORM_UNSUPPORTED')
    require(_current is None,'Nested Manager session refused','NATIVE_REQUEST_INVALID')
    workspace=workspace_path(str(workspace),package,actor)
    from .workspace import inspect
    inspect(workspace,explicit=True)
    workspace.mkdir(parents=True,exist_ok=True)
    ws=workspace.stat()
    sid=uuid.uuid4().hex;directory=safe_path(workspace,'logs/sessions/'+sid);directory.mkdir(parents=True,exist_ok=False)
    os.chown(directory,0,0)
    document={'schema':SCHEMA,'session_id':sid,'pid':os.getpid(),'workspace':str(workspace),'package_root':str(Path(package).absolute()),
              'operator':deepcopy(actor),'execution_uid':0,'execution_gid':0,'status':'STARTING',
              'workspace_identity':{'device':ws.st_dev,'inode':ws.st_ino,'uid':ws.st_uid,'gid':ws.st_gid},
              'bootstrap_log':None}
    origin_context=origin_context or {}
    try:
        try:document['bootstrap_log']=_bootstrap(directory,actor,origin_context.get('bootstrap'))
        except (OSError,ValueError) as exc:document['bootstrap_warning']=str(exc)
        _save(directory/'session.json',document)
        print('Manager session log: '+str(directory/'session.json'),flush=True)
        lock=safe_path(workspace,'state/manager-session.lock')
        try:
            with file_lock(lock,owner_uid=0):
                document['status']='RUNNING';_save(directory/'session.json',document)
                _current=dict(document,record=str(directory/'session.json'))
                try:yield _current
                except KeyboardInterrupt:
                    document['status']='INTERRUPTED';raise
                except BaseException as exc:
                    document.update(status='FAILED',error=type(exc).__name__+': '+str(exc));raise
                else:
                    document['exit_code']=_current.get('exit_code',0)
                    document['status']='EXITED' if document['exit_code']==0 else 'FAILED'
                finally:_current=None;_save(directory/'session.json',document)
        except RuntimeError as exc:
            if document['status']=='STARTING':
                document.update(status='SESSION_BUSY',error=str(exc),lock_path=str(lock));_save(directory/'session.json',document)
                raise DiagnosticError('Another Manager is using this workspace. Close it normally; the lock file is retained. Log: '+str(directory/'session.json'),'SESSION_BUSY') from exc
            raise
    finally:_current=None

def binding():
    require(_current is not None and os.geteuid()==0,'Authenticated root Manager session required','NATIVE_REQUEST_INVALID')
    return {'session_id':_current['session_id'],'session_record':_current['record'],
            'session_sha256':digest(Path(_current['record']).read_bytes()),'operator':deepcopy(_current['operator']),
            'workspace_identity':deepcopy(_current['workspace_identity'])}

def validate_binding(request,*,check_parent=False):
    sid=token(request.get('session_id'),'session ID');workspace=Path(request['workspace'])
    path=safe_path(workspace,'logs/sessions/'+sid+'/session.json')
    require(str(path)==request.get('session_record') and digest(path.read_bytes())==request.get('session_sha256'),
            'Root session binding differs','NATIVE_REQUEST_INVALID')
    for item in (path,path.parent):
        st=item.lstat();require(st.st_uid==0 and not st.st_mode&0o022 and (item.is_dir() or st.st_nlink==1),
            'Root session ownership differs','NATIVE_REQUEST_INVALID')
    doc=load_json(path)
    ws=workspace.stat();identity={'device':ws.st_dev,'inode':ws.st_ino,'uid':ws.st_uid,'gid':ws.st_gid}
    require(identity==request.get('workspace_identity')==doc.get('workspace_identity') and not ws.st_mode&0o022,
            'Session workspace identity changed','WORKSPACE_CHANGED')
    require(doc.get('schema')==SCHEMA and doc.get('status')=='RUNNING' and doc.get('session_id')==sid and
            doc.get('workspace')==request['workspace'] and doc.get('package_root')==request['package_root'] and
            doc.get('operator')==request.get('operator') and doc.get('execution_uid')==doc.get('execution_gid')==0,
            'Authenticated session identity differs','NATIVE_REQUEST_INVALID')
    if check_parent:require(doc.get('pid')==os.getppid(),'Executor is not the active Manager child','NATIVE_REQUEST_INVALID')
    return doc

def child_environment():
    actor=binding()['operator']
    env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C','PYTHONDONTWRITEBYTECODE':'1'}
    if actor['origin']=='sudo':env.update(SUDO_UID=str(actor['uid']),SUDO_GID=str(actor['gid']),SUDO_USER=actor['name'])
    return env

@contextmanager
def executor_session(document,record):
    global _current
    require(_current is None,'Executor already has a session','NATIVE_REQUEST_INVALID')
    oldmask=os.umask(0o077);_current=dict(document,record=str(record))
    try:yield
    finally:_current=None;os.umask(oldmask)
