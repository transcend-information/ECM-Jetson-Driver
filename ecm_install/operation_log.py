"""Durable operation diagnostics."""
from datetime import datetime, timezone
import json, os, tempfile, uuid
from pathlib import Path
from . import VERSION, r20_storage as S
from .contracts import load_json, safe_path, digest, token
from .system import file_lock
from .diagnostics import SCHEMA, REVISION, STAGES, CODES, DiagnosticError, error_detail

def now(): return datetime.now(timezone.utc).isoformat()

def emergency_record(operation, exc, *, context=None, primary=None):
    """User-local fallback only; inability to log never authorizes installation."""
    data=dict(context or {})
    data.setdefault('report_id',datetime.now(timezone.utc).strftime('%Y%m%d')+'-emergency-'+uuid.uuid4().hex[:12])
    detail=primary or error_detail(exc,'startup')
    data.update(schema=SCHEMA,revision=REVISION,operation=operation,installer_version=VERSION,status=detail.get('outcome','FAILED'),
        timestamp_utc=now(),transaction_id=data.get('transaction_id'),runtime_verified=False,diagnostic=detail,
        stage=detail['stage'],error_code=detail['error_code'],next_action=detail['next_action'],message=detail['message'],
        persistence_error={'type':type(exc).__name__,'message':str(exc)},storage='USER_LOCAL_EMERGENCY')
    directory=Path(tempfile.mkdtemp(prefix='ecm-diagnostics-emergency-'))
    S.atomic_json(directory/'result.json',data)
    return directory,data

class Report:
    @classmethod
    def resume(cls, workspace, report_id, system):
        token(report_id,'report ID')
        self=cls.__new__(cls)
        self.workspace,self.system=Path(workspace),system
        self.directory=safe_path(self.workspace,'logs/external/'+report_id)
        self.context=load_json(self.directory/'context.json')
        if self.context.get('report_id')!=report_id or self.context.get('operation')!='install':
            raise DiagnosticError('Executor report identity differs','NATIVE_REQUEST_INVALID',stage='authentication')
        self.id=report_id;self.origin=self.context['origin_instance_id']
        self.stage_name='authentication';self.active=False;self.emergency_directory=None
        return self

    def __init__(self,workspace,operation,system):
        self.workspace,self.system=Path(workspace),system
        self.stage_name='startup';self.active=False;self.emergency_directory=None
        self.workspace.mkdir(parents=True,exist_ok=True)
        day=datetime.now(timezone.utc).strftime('%Y%m%d')
        with file_lock(safe_path(self.workspace,'state/external-report.lock')):
            directory=safe_path(self.workspace,'state/external-runlog');directory.mkdir(parents=True,exist_ok=True)
            origin_path=safe_path(directory,'origin.json')
            if not origin_path.exists():S.atomic_json(origin_path,{'origin_instance_id':str(uuid.uuid4())})
            self.origin=load_json(origin_path)['origin_instance_id'];uuid.UUID(self.origin)
            counter=safe_path(directory,day+'.json')
            previous=load_json(counter)['last_sequence'] if counter.exists() else 0
            if type(previous) is not int or previous<0:raise ValueError('Cannot allocate required report sequence')
            sequence=previous+1;S.atomic_json(counter,{'last_sequence':sequence})
        self.id=day+'-'+str(sequence).zfill(4)+'-'+self.origin[:8]
        self.directory=safe_path(self.workspace,'logs/external/'+self.id);self.directory.mkdir(parents=True,exist_ok=False)
        self.context=dict(schema=SCHEMA,revision=REVISION,report_id=self.id,origin_instance_id=self.origin,sequence=sequence,
            operation=operation,candidate=VERSION,installer_version=VERSION,started_utc=now(),transaction_id=None,
            qualification='LOCAL_SIMULATION' if system.simulation else 'NOT_QUALIFIED',runtime_verified=False,
            postboot_status='NOT_COLLECTED',probe_evidence=[])
        from .session import attribution,context
        if context() is not None:os.chown(self.directory,0,0)
        self.context['execution_identity']=attribution()
        self.bind();self.event('report-start')

    def bind(self,**fields):
        self.context.update(fields)
        try:S.atomic_json(safe_path(self.directory,'context.json'),self.context)
        except Exception as exc:raise DiagnosticError(str(exc),'LOG_WRITE_FAILED',stage=self.stage_name) from exc

    def event(self,action,**fields):
        try:
            self.system.checkpoint('report:'+action)
            entry=dict(fields,schema=SCHEMA,revision=REVISION,report_id=self.id,
                transaction_id=fields.get('transaction_id',self.context.get('transaction_id')),
                timestamp_utc=now(),installer_version=VERSION,action=action,stage=fields.get('stage',self.stage_name))
            with safe_path(self.directory,'events.jsonl').open('ab') as stream:
                stream.write((json.dumps(entry,allow_nan=False)+'\n').encode());stream.flush();os.fsync(stream.fileno())
        except Exception as exc:raise DiagnosticError(str(exc),'LOG_WRITE_FAILED',stage=self.stage_name) from exc

    def stage(self,name,**fields):
        if name not in STAGES:raise ValueError('Unknown diagnostic stage')
        if self.active and self.stage_name==name:return
        if self.active:self.event('stage-status',status='COMPLETED')
        self.stage_name=name;self.active=True;self.event('stage-status',status='RUNNING',**fields)

    def stage_failed(self,detail):
        self.event('stage-status',status='FAILED',stage=detail['stage'],diagnostic=detail,error_code=detail['error_code'])
        self.active=False

    def finish(self,status,message,**fields):
        code=fields.pop('error_code','OK' if status=='SUCCESS' else 'USER_CANCELLED' if status=='CANCELLED' else
                        'AWAITING_ARTIFACTS' if status=='AWAITING_ARTIFACTS' else STAGES[self.stage_name][1])
        if code not in CODES:raise ValueError('Unknown diagnostic code')
        result=dict(self.context,**fields)
        result.update(status=status,message=message,stage=fields.get('stage',self.stage_name),error_code=code,
                      next_action=fields.get('next_action',CODES[code]),completed_utc=now(),runtime_verified=False)
        if status=='SUCCESS' and self.context['operation']=='install':
            result.update(installation_status='FILES_INSTALLED_REBOOT_REQUIRED',postboot_status='NOT_COLLECTED',commit_status='PREPARED')
        if self.active:self.event('stage-status',status='COMPLETED' if status=='SUCCESS' else status)
        self.active=False
        self.event('report-result',**{k:v for k,v in result.items() if k not in ('schema','revision','installer_version','report_id')})
        try:
            self.system.checkpoint('report:result-save')
            S._atomic_bytes(safe_path(self.directory,'summary.txt'),summary(result).encode(),0o600,
                os.geteuid() if hasattr(os,'geteuid') else 0,os.getegid() if hasattr(os,'getegid') else 0)
            S.atomic_json(safe_path(self.directory,'result.json'),result)
        except Exception as exc:raise DiagnosticError(str(exc),'LOG_WRITE_FAILED',stage=self.stage_name) from exc
        return result

    def fail(self,exc,**fields):
        detail=error_detail(exc,self.stage_name)
        fields.update(diagnostic=detail,stage=detail['stage'],error_code=detail['error_code'],next_action=detail['next_action'])
        if detail.get('transaction_id'):fields['transaction_id']=detail['transaction_id']
        outcome=detail.get('outcome','FAILED')
        fields.update(installation_status=outcome,reboot_required=False,
                      completed_roles=detail.get('completed_roles',detail.get('context',{}).get('completed_roles',[])),
                      file_results=detail.get('file_results',[]))
        live=getattr(self,'install_progress',None)
        if live is not None:live.failure_detail=detail
        try:
            result=self.finish(outcome,detail['message'],**fields)
            if live is not None:live.failure_saved(True)
            return result
        except Exception as persistence:
            # A prior provisional success cannot survive as the terminal file
            # when a later required commit/log step has failed.
            try:
                path=safe_path(self.directory,'result.json')
                if path.exists() and load_json(path).get('status')=='SUCCESS':path.unlink()
            except Exception:pass
            self.emergency_directory,result=emergency_record(self.context['operation'],persistence,
                context=dict(self.context,**fields),primary=detail)
            if live is not None:
                try:live.failure_saved(False,str(self.emergency_directory))
                except Exception:pass
            return result

def summary(record):
    return '\n'.join(['ECM operation diagnostics','Report: '+str(record.get('report_id','Unknown')),
        'Operation: '+str(record.get('operation','Unknown')),'Transaction: '+str(record.get('transaction_id') or 'Not created / not recorded'),
        'Status: '+str(record.get('status','INCOMPLETE')),'Stage / code: '+str(record.get('stage','LEGACY_NOT_RECORDED'))+' / '+str(record.get('error_code','LEGACY_NOT_RECORDED')),
        'Message: '+str(record.get('message','No terminal result collected')),'Next: '+str(record.get('next_action','Review available records.')),
        ])+'\n'

def _bounded(path):
    if path.stat().st_size>16*1024*1024:raise ValueError('Diagnostic file exceeds 16 MiB export limit')
    return S._safe_file(path,path.parent)

def _events(body, warnings=None):
    result=[]
    for number,line in enumerate(body.decode('utf-8').splitlines(),1):
        if not line.strip():continue
        try:
            event=json.loads(line)
            if not isinstance(event,dict):raise ValueError('JSON event must be an object')
            result.append(event)
        except ValueError as exc:
            message='line '+str(number)+': '+str(exc)
            if warnings is None:raise ValueError(message) from exc
            warnings.append(message)
    return result

def read_report(directory):
    record={};warnings=[]
    for name in ('context.json','result.json'):
        try:record.update(load_json(safe_path(directory,name)))
        except (ValueError,OSError) as exc:warnings.append(name+': '+str(exc))
    try:
        event_warnings=[]
        terminal=[e for e in _events(_bounded(safe_path(directory,'events.jsonl')),event_warnings) if e.get('action')=='report-result']
        warnings.extend('events.jsonl: '+w for w in event_warnings)
        if terminal and terminal[-1].get('schema')==SCHEMA:
            if record.get('status')!=terminal[-1].get('status'):warnings.append('Terminal event/result status differs')
            record.update(terminal[-1])
    except (ValueError,OSError,UnicodeError) as exc:warnings.append('events.jsonl: '+str(exc))
    record.setdefault('report_id',Path(directory).name);record.setdefault('operation','legacy');record.setdefault('status','INCOMPLETE')
    record.setdefault('message','No complete terminal result; inspect available records');record['record_warnings']=warnings
    original = {k: record.get(k) for k in ('status', 'stage', 'error_code', 'message', 'next_action')}
    if record.get('operation') in ('install','restore') and record.get('status')=='SUCCESS':
        try:
            workspace=Path(directory).parents[2]
            tid=token(record.get('transaction_id'),'transaction ID')
            transaction=safe_path(workspace,'backups/'+tid)
            from .commit_record import committed
            plan=load_json(transaction/'plan.json')
            # Historical restore reports without this protocol keep their old
            # semantics. New ordinary restores require the same final gate as
            # installs; recovery is separately identified by its callback.
            if record['operation']=='install' or plan.get('commit_protocol'):
                progress=load_json(safe_path(transaction,'progress.json'))
                active=safe_path(workspace,'state/external-active.json')
                incomplete=active.exists() and load_json(active).get('transaction_id')==tid
                recovery=record['operation']=='restore' and (record.get('transaction_outcome')=='rolled_back' or
                    (record.get('transaction_outcome') is None and progress.get('status')=='rolled_back' and
                     (plan.get('action')=='install' or plan.get('report_id')!=record['report_id'])))
                if recovery:
                    complete=progress.get('status')=='rolled_back' and not progress.get('recovery_errors') and not incomplete
                else:
                    expected='applied' if record['operation']=='install' else 'restored'
                    complete=(plan.get('action')==record['operation'] and progress.get('status')==expected and
                              not incomplete and committed(transaction,plan))
                if record['operation'] == 'install' and (plan.get('report_id') != record['report_id'] or
                        plan.get('transaction_id') != tid or plan.get('package_id') != record.get('package_id')):
                    raise ValueError('Installation report and saved transaction identity differ')
                if not complete:
                    record.update(status='PARTIAL',installation_status='PARTIAL',reboot_required=False,
                                  commit_status='INCOMPLETE',message='Transaction completion is unverified; inspect transaction and recovery before use.')
                    warnings.append('Success record is superseded by incomplete transaction state')
                else:record['commit_status']='RECOVERY_COMPLETED' if recovery else 'COMMITTED'
        except (OSError, ValueError, KeyError, TypeError) as exc:
            record.update(status='INCOMPLETE',installation_status='INCOMPLETE',reboot_required=False,commit_status='INCOMPLETE')
            warnings.append('Transaction proof unavailable: ' + str(exc))
        if warnings:
            status = 'PARTIAL' if record['status'] == 'PARTIAL' else 'INCOMPLETE'
            record.update(recorded_result=original, status=status, stage='record_review',
                          error_code='TRANSACTION_INCOMPLETE', reboot_required=False,
                          installation_status=status, commit_status='INCOMPLETE',
                          message='The saved evidence does not establish installation completion.',
                          next_action='Review the original report and its transaction files.')
    record['record_integrity']='INCOMPLETE' if warnings else 'COMPLETE'
    return record

def reports(workspace):
    root=safe_path(workspace,'logs/external')
    if not root.exists():return [],[]
    result=[];warnings=[]
    for directory in sorted(root.iterdir(),reverse=True)[:100]:
        try:
            safe_path(root,directory.name)
            if directory.is_dir():result.append((directory,read_report(directory)))
        except (ValueError,OSError) as exc:warnings.append('Warning: report unavailable: '+directory.name+': '+str(exc))
    return result,warnings
