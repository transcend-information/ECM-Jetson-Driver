"""Single confirmed child request. No password handling, shell, retry or HOME lookup."""
import json,os,platform,subprocess,signal
from pathlib import Path
from . import r20_storage as S
from .catalog import Catalog
from .contracts import load_json,safe_path,digest,document_digest,verify_manifest,require,token
from .diagnostics import DiagnosticError,RecordedFailure
from .operation_log import Report,read_report,_events,_bounded
from .transaction import Engine
from .system import file_lock
from .system import SystemIO
from . import inventory_policy
from . import session

SCHEMA='ecm-native-install-request-v2'
PACKAGE=Path(__file__).resolve().parents[1]

def identity_inventory(root,workspace=None):
    root=Path(root);result={};total=0
    excluded=Path(workspace) if workspace is not None else None
    if excluded is not None and excluded.is_relative_to(root):
        require(excluded!=root and excluded.relative_to(root).parts[0] not in ('ecm_install','packages','manifests','docs','ecm_env','bootstrap','dependencies'),
            'Workspace cannot exclude distributed code or payload directories','NATIVE_REQUEST_INVALID')
    pending=list(root.iterdir());visited=0
    while pending:
        path=pending.pop();visited+=1
        require(visited<=inventory_policy.MAX_INVENTORY_ENTRIES,'Package directory inventory exceeds bound')
        if excluded is not None and path==excluded:continue
        relative=path.relative_to(root).as_posix()
        safe_path(root,relative)
        if path.is_dir():pending.extend(path.iterdir());continue
        require(path.is_file(),'Non-regular package entry')
        total+=path.stat().st_size
        require(len(result)<inventory_policy.MAX_INVENTORY_FILES and total<=inventory_policy.MAX_INVENTORY_BYTES,'Package inventory exceeds bound')
        result[relative]=digest(path.read_bytes())
    return dict(sorted(result.items()))

def state_fingerprint(workspace):
    path=safe_path(workspace,'state/installed.json')
    try:return {'status':'READ','existed':path.exists(),'sha256':digest(path.read_bytes()) if path.exists() else None}
    except PermissionError:return {'status':'UNREADABLE'}

def make_request(package,workspace,report,key,manifest_sha):
    require(os.geteuid()==os.getuid()==0 and os.getegid()==0,'Authenticated root request required','NATIVE_REQUEST_INVALID')
    bound=session.binding()
    package,workspace=Path(package).absolute(),Path(workspace).absolute()
    require(package!=workspace,'Workspace must not be the package root','NATIVE_REQUEST_INVALID')
    require(key in Catalog(package).selectable_packages(),'Package is outside this distribution')
    path,expected=Catalog(package).manifest(key)
    require(expected==manifest_sha,'Package index changed before authentication')
    manifest,_=verify_manifest(path,key,expected,simulation=False)
    require(Path(report.workspace)==workspace,'Report workspace differs')
    request=dict(schema=SCHEMA,package_root=str(package),package_inventory=identity_inventory(package,workspace),
        package_id=key,manifest=path.relative_to(package).as_posix(),manifest_sha256=expected,
        artifact_set_id=manifest['artifact_set_id'],workspace=str(workspace),report_id=report.id,
        context_sha256=digest((report.directory/'context.json').read_bytes()),
        state_before=state_fingerprint(workspace),confirmed=True,
        owner_uid=0,owner_gid=0,**bound)
    require(request['state_before']['status']=='READ','Root session cannot read required installed state','WORKSPACE_CHANGED')
    target=safe_path(report.directory,'install-request.json');S.atomic_json(target,request)
    return target,digest(target.read_bytes())

def invoke(argv):
    # Authentication happened before Home. Only this owned child receives a
    # cancellation signal; give its transaction recovery time to finish.
    child=subprocess.Popen(argv,shell=False,stdin=subprocess.DEVNULL,start_new_session=True,env=session.child_environment())
    try:return child.wait(timeout=900)
    except (KeyboardInterrupt,subprocess.TimeoutExpired) as exc:
        child.send_signal(signal.SIGINT if isinstance(exc,KeyboardInterrupt) else signal.SIGTERM)
        try:return child.wait(timeout=60)
        except subprocess.TimeoutExpired:
            raise DiagnosticError('Installation child is still recovering; preserve its report and operation lock. No retry was started.',
                'EXECUTOR_INCOMPLETE',child_pid=child.pid)

def launch_install(engine,catalog,report,key,manifest_sha):
    report.stage('authentication')
    if platform.system()!='Linux':
        raise DiagnosticError('Native installation requires Linux; no privileged process was started','NATIVE_PLATFORM_UNSUPPORTED',stage='authentication')
    require(os.geteuid()==0 and session.context() is not None,'Open the authenticated root Manager before installation','NATIVE_REQUEST_INVALID')
    path,expected=make_request(catalog.root,engine.workspace,report,key,manifest_sha)
    selection=load_json(path)
    argv=['/usr/bin/python3','-I','-B',str(Path(catalog.root).absolute()/'ecm_install_exec.py'),
          '--request',str(path),'--sha256',expected]
    report.event('privileged-invocation',argv=argv,attempt=1,execution_identity=session.attribution(),authentication='completed-before-Home')
    execution_error=None
    try:rc=invoke(argv)
    except (OSError,subprocess.TimeoutExpired,KeyboardInterrupt) as exc:rc=None;execution_error=type(exc).__name__+': '+str(exc)
    # Always inspect durable evidence, even after nonzero exit or interruption.
    record=read_report(report.directory)
    report.context=load_json(report.directory/'context.json')
    try:
        events=_events(_bounded(report.directory/'events.jsonl'))
        started=any(e.get('action')=='native-executor-started' for e in events)
    except (ValueError,OSError):started=True
    if started and record.get('status') in ('FAILED','PARTIAL') and isinstance(record.get('diagnostic'),dict):
        detail=record['diagnostic']
        raise RecordedFailure(dict(detail,transaction_id=record.get('transaction_id')))
    if rc==0 and started and record.get('status')=='SUCCESS' and not record.get('record_warnings'):
        try:
            receipt=load_json(safe_path(report.directory,'executor-result.json'))
            bound={'schema':'ecm-native-executor-result-v2','request_sha256':expected,'report_id':report.id,
                'workspace':str(engine.workspace),'package_root':selection['package_root'],'package_id':key,
                'manifest_sha256':manifest_sha,'artifact_set_id':selection['artifact_set_id'],
                'transaction_id':record.get('transaction_id'),'commit_prepared':True,'progress_status':'applied'}
            require(all(receipt.get(k)==v for k,v in bound.items()),'Executor receipt identity differs')
            require(engine.pending() is None,'Installation commit marker remains active','EXECUTOR_INCOMPLETE')
            token(receipt['transaction_id'],'transaction ID')
            result=receipt['result'];state=receipt['installed_state']
            require(result.get('status')=='applied' and result.get('report_id')==report.id and result.get('transaction_id')==receipt['transaction_id'] and
                state.get('transaction_id')==receipt['transaction_id'] and state.get('plan_sha256')==receipt['plan_sha256'] and
                state.get('artifact_set_id')==selection['artifact_set_id'],'Executor receipt state/result differs')
            return result
        except (KeyError,ValueError,OSError) as exc:execution_error='Executor receipt unavailable or invalid: '+str(exc)
    code='EXECUTOR_INCOMPLETE' if started else 'AUTH_OR_EXECUTOR_FAILED'
    raise DiagnosticError('Privileged installation did not return a complete verified receipt; no automatic retry',code,
        stage='authentication',child_returncode=rc,execution_error=execution_error,transaction_id=record.get('transaction_id'),
        observed_status=record.get('status'),transaction_evidence=transaction_evidence(engine.workspace,record.get('transaction_id')))

def transaction_evidence(workspace,tid):
    result={}
    for name in ('state/external-active.json','state/installed.json'):
        try:result[name]=load_json(safe_path(workspace,name))
        except (ValueError,OSError) as exc:result[name]={'unavailable':str(exc)}
    if tid:
        try:
            token(tid,'transaction ID')
            result['progress']=load_json(safe_path(workspace,'backups/'+tid+'/progress.json'))
        except (ValueError,OSError) as exc:result['progress']={'unavailable':str(exc)}
    return result

def validate_request(path,expected,package,owner):
    path=Path(path).absolute();safe_path(path.parent,path.name)
    request=load_json(path)
    require(digest(path.read_bytes())==expected,'Request digest differs','NATIVE_REQUEST_INVALID')
    require(request.get('schema')==SCHEMA and request.get('confirmed') is True,'Invalid confirmed request','NATIVE_REQUEST_INVALID')
    require((request.get('owner_uid'),request.get('owner_gid'))==owner,'Caller identity differs','NATIVE_REQUEST_INVALID')
    require(owner==(0,0),'Root report owner required','NATIVE_REQUEST_INVALID')
    session.validate_binding(request)
    package=Path(package).absolute();workspace=Path(request['workspace'])
    require(str(package)==request.get('package_root') and workspace.is_absolute() and str(workspace.absolute())==str(workspace),
        'Absolute package/workspace identity differs','NATIVE_REQUEST_INVALID')
    safe_path(workspace.parent,workspace.name)
    require(package!=workspace,'Workspace must not be the package root','NATIVE_REQUEST_INVALID')
    report_id=token(request.get('report_id'),'report ID')
    require(path==safe_path(workspace,'logs/external/'+report_id+'/install-request.json'),'Request outside fixed report','NATIVE_REQUEST_INVALID')
    require(identity_inventory(package,workspace)==request.get('package_inventory'),'Package files changed','NATIVE_REQUEST_INVALID')
    key=request.get('package_id');require(key in Catalog(package).selectable_packages(),'Package is outside this distribution','NATIVE_REQUEST_INVALID')
    entry=Catalog(package).manifest(key);require(entry is not None,'Required artifacts missing','ARTIFACT_MISSING')
    manifest_path,manifest_sha=entry
    require((manifest_path.relative_to(package).as_posix(),manifest_sha)==(request.get('manifest'),request.get('manifest_sha256')),
        'Manifest selection differs','NATIVE_REQUEST_INVALID')
    manifest,contents=verify_manifest(manifest_path,key,manifest_sha,simulation=False)
    require(manifest['artifact_set_id']==request.get('artifact_set_id'),'Artifact set differs','NATIVE_REQUEST_INVALID')
    require(digest((path.parent/'context.json').read_bytes())==request.get('context_sha256'),'Report context changed','NATIVE_REQUEST_INVALID')
    context=load_json(path.parent/'context.json')
    require(context.get('execution_identity',{}).get('operator')==request['operator'] and
            context.get('execution_identity',{}).get('session_id')==request['session_id'],
            'Report session/operator differs','NATIVE_REQUEST_INVALID')
    require(context.get('report_id')==report_id and context.get('package_id')==key and context.get('transaction_id') is None,
        'Report already used or selection differs','NATIVE_REQUEST_INVALID')
    require(not (path.parent/'result.json').exists(),'Operation already has a terminal result','NATIVE_REQUEST_INVALID')
    events=_events(_bounded(path.parent/'events.jsonl'))
    require(any(e.get('action')=='normal-install-confirmation' and e.get('accepted') is True and e.get('package_id')==key for e in events),
        'Normal installation confirmation is missing','NATIVE_REQUEST_INVALID')
    require(not any(e.get('action')=='native-executor-started' for e in events),'Executor request already used','NATIVE_REQUEST_INVALID')
    return request,manifest,contents

def verify_request_owner(path,owner):
    for location in (Path(path),Path(path).parent,Path(path).parent/'context.json'):
        info=location.lstat()
        require((info.st_uid,info.st_gid)==owner and not info.st_mode&0o022 and (location.is_dir() or info.st_nlink==1),
            'Request/report ownership differs','NATIVE_REQUEST_INVALID')

def execute_request(path,expected):
    # The public child has no simulation, arbitrary command, workspace or root override.
    if platform.system()!='Linux' or os.geteuid()!=0:raise DiagnosticError('Privileged Linux executor required','NATIVE_PLATFORM_UNSUPPORTED')
    require(os.getuid()==0 and os.getegid()==0,'Root executor identity required','NATIVE_REQUEST_INVALID')
    owner=(0,0)
    # Establish the root-owned result location before artifact revalidation so
    # early child failures retain their actual code/cause in the same report.
    path=Path(path).absolute();request=load_json(path)
    require(digest(path.read_bytes())==expected and request.get('schema')==SCHEMA,'Invalid request envelope','NATIVE_REQUEST_INVALID')
    require((request.get('owner_uid'),request.get('owner_gid'))==owner and request.get('confirmed') is True,'Caller/confirmation differs','NATIVE_REQUEST_INVALID')
    bound=session.validate_binding(request,check_parent=True)
    require(session.operator()==bound['operator'],'Executor operator environment differs','NATIVE_REQUEST_INVALID')
    workspace=Path(request['workspace']);report_id=token(request['report_id'],'report ID')
    require(workspace.is_absolute() and str(PACKAGE)==request.get('package_root'),'Fixed absolute paths required','NATIVE_REQUEST_INVALID')
    require(path==safe_path(workspace,'logs/external/'+report_id+'/install-request.json'),'Unexpected request location','NATIVE_REQUEST_INVALID')
    require(digest((path.parent/'context.json').read_bytes())==request.get('context_sha256'),'Original report context changed','NATIVE_REQUEST_INVALID')
    require(not (path.parent/'result.json').exists() and not any(e.get('action')=='native-executor-started' for e in _events(_bounded(path.parent/'events.jsonl'))),
        'Request already executed','NATIVE_REQUEST_INVALID')
    verify_request_owner(path,owner)
    from .native import NativeSystemIO
    with session.executor_session(bound,request['session_record']),file_lock(Path(path).parent/'native-request.lock',owner_uid=0):
        try:
            request,manifest,contents=validate_request(path,expected,PACKAGE,owner)
            system=NativeSystemIO(request,manifest,contents)
        except Exception as exc:
            report=Report.resume(workspace,report_id,SystemIO())
            report.event('native-executor-started',validation_failed=True)
            report.fail(exc)
            for item in (report.directory/'context.json',report.directory/'events.jsonl',report.directory/'result.json',report.directory/'summary.txt'):
                safe_path(item.parent,item.name)
                if item.exists():
                    require(item.is_file() and item.stat().st_nlink==1,'Invalid diagnostic publication file','NATIVE_REQUEST_INVALID')
                    os.chown(item,*owner);os.chmod(item,0o600)
            return 2
        return execute_verified(request,system)

def execute_verified(request,system):
    """Internal dependency-injection seam; CLI never accepts a test backend."""
    report=Report.resume(request['workspace'],request['report_id'],system)
    engine=Engine(request['workspace'],system)
    try:
        report.event('native-executor-started',package_id=request['package_id'],workspace=request['workspace'])
        def completed(result):
            report.finish('SUCCESS','Installation files installed. Manual restart required.',
                transaction_id=result['transaction_id'],package_id=request['package_id'],
                backup_coverage=result.get('backup_coverage'),restoration_complete=result.get('restoration_complete',True))
        engine.install(request['package_id'],Path(request['package_root'])/request['manifest'],request['manifest_sha256'],
            report.id,on_complete=completed,diagnostics=report,
            on_committed=lambda result:write_executor_receipt(engine,report,request,result))
        return 0
    except (Exception,KeyboardInterrupt) as exc:
        record=report.fail(exc)
        try:
            receipt={k:request[k] for k in ('workspace','package_root','package_id','manifest_sha256','artifact_set_id','report_id')}
            receipt.update(schema='ecm-native-executor-result-v2',status=record['status'],
                request_sha256=digest((report.directory/'install-request.json').read_bytes()),
                transaction_id=record.get('transaction_id'),diagnostic=record.get('diagnostic'),
                installed_state=engine.state(),active_marker_cleared=not engine.wpath('state/external-active.json').exists(),
                runtime_verified=False,reboot_required=False)
            system.checkpoint('executor-result-save')
            S.atomic_json(safe_path(report.directory,'executor-result.json'),receipt)
        except Exception as persistence:
            try:
                stale=safe_path(report.directory,'executor-result.json')
                if stale.exists():stale.unlink()
            except Exception:pass
            print('Executor failure receipt unavailable: '+str(persistence),flush=True)
        return 2
    finally:
        system.publish_diagnostics(report)

def write_executor_receipt(engine,report,request,result):
    """Prepare the receipt before the final active-marker commit gate.

    Parent reconciliation also requires the gate to be clear. A crash or failed
    receipt cannot make a provisional success appear committed on reopening.
    """
    tid=result['transaction_id'];directory=engine.wpath('backups/'+token(tid,'transaction ID'))
    try:
        plan=load_json(directory/'plan.json');plan_sha=digest((directory/'plan.json').read_bytes())
        state=load_json(engine.wpath('state/installed.json'));progress=load_json(directory/'progress.json')
        require(plan.get('report_id')==report.id and plan.get('manifest_sha256')==request['manifest_sha256'] and
            state.get('transaction_id')==tid and state.get('plan_sha256')==plan_sha and progress.get('status')=='applied' and
            load_json(engine.wpath('state/external-active.json')).get('transaction_id')==tid,'Prepared transaction evidence differs')
        receipt={k:request[k] for k in ('workspace','package_root','package_id','manifest_sha256','artifact_set_id','report_id')}
        receipt.update(schema='ecm-native-executor-result-v2',request_sha256=digest((report.directory/'install-request.json').read_bytes()),
            transaction_id=tid,plan_sha256=plan_sha,installed_state=state,result=result,progress_status='applied',
            commit_prepared=True,commit_gate='state/external-active.json must be absent',runtime_verified=False,postboot_status='NOT_COLLECTED')
        engine.system.checkpoint('executor-result-save')
        S._atomic_bytes(safe_path(report.directory,'executor-result.json'),
            (json.dumps(receipt,indent=2)+'\n').encode(),0o600,request['owner_uid'],request['owner_gid'])
        report.event('native-commit-receipt',transaction_id=tid,receipt_sha256=digest((report.directory/'executor-result.json').read_bytes()))
    except Exception as exc:
        raise DiagnosticError('Files committed but executor receipt could not be completed: '+str(exc),
            'EXECUTOR_INCOMPLETE',stage='result',transaction_id=tid,files_status=result.get('status')) from exc
