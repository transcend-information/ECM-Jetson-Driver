"""Native-terminal authentication before the root Manager, never password I/O."""
import argparse,json,os,signal,subprocess,tempfile
from pathlib import Path
from . import VERSION,session
from .diagnostics import DiagnosticError

PACKAGE=Path(__file__).resolve().parents[1]

def origin_context():
    return {'bootstrap': os.environ.get('ECM_ENV_BOOTSTRAP_LOG')}

def authenticate(workspace,package=PACKAGE):
    actor=session.operator()
    directory=Path(tempfile.mkdtemp(prefix='ecm-auth-'+str(actor['uid'])+'-'))
    record={'operator':actor,'workspace':str(workspace) if workspace is not None else None,
            'workspace_selection':'explicit' if workspace is not None else 'after-authentication',
            'status':'AUTHENTICATION_PENDING','password_capture':False}
    log=directory/'authentication.json'
    def save():log.write_text(json.dumps(record,indent=2)+'\n');log.chmod(0o600)
    save()
    print('Administrator authentication is required before opening the Manager.',flush=True)
    print('Workspace: '+str(workspace) if workspace is not None else
          'Workspace: existing workspaces will be checked after authentication, using your account home.',flush=True)
    print('The native sudo prompt handles your password. Installation still requires confirmation Y.',flush=True)
    print('Authentication log: '+str(log),flush=True)
    try:
        # Inherited terminal only. No stdin=, password string, -S or output capture.
        checked=subprocess.run(['/usr/bin/sudo','-v'],check=False)
        if checked.returncode:
            record.update(status='AUTHENTICATION_DENIED',returncode=checked.returncode);save()
            print('Authentication did not complete; Manager not opened. Log: '+str(log));return 1
        record['status']='AUTHENTICATED';save()
        argv=['/usr/bin/sudo','-n','--','/usr/bin/python3','-I','-B',str(Path(package)/'ecm_camera_manager.py')]
        if workspace is not None:argv+=['--workspace',str(workspace)]
        argv+=['--operator-context',json.dumps(origin_context(),separators=(',',':'))]
        result=subprocess.run(argv,check=False)
        record.update(status='SESSION_RETURNED' if result.returncode==0 else 'ROOT_SESSION_FAILED',returncode=result.returncode);save()
        return result.returncode
    except KeyboardInterrupt:
        record['status']='AUTHENTICATION_OR_SESSION_CANCELLED';save()
        print('Cancelled. Log: '+str(log));return 130
    except OSError as exc:
        record.update(status='AUTHENTICATION_UNAVAILABLE',error=str(exc));save()
        print('Authentication unavailable. Log: '+str(log));return 2

def main(argv=None):
    parser=argparse.ArgumentParser(description='ECM Camera Manager')
    parser.add_argument('--workspace',help='Explicit existing or new workspace; never inferred from root HOME')
    parser.add_argument('--version',action='store_true')
    parser.add_argument('--operator-context',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args.version:print(VERSION);return 0
    try:
        actor=session.operator()
        if os.geteuid()!=0:
            if args.operator_context is not None:raise ValueError('Operator context is accepted only after native authentication')
            workspace=session.workspace_path(args.workspace,PACKAGE,actor) if args.workspace is not None else None
            return authenticate(workspace)
        value=args.workspace
        if value is None and actor['origin']=='sudo' and actor['uid']>0:
            # Discover under root so a private existing workspace is never mistaken
            # for an absent one. Account lookup, not HOME, binds the original user.
            if not Path(actor['home']).is_absolute():raise ValueError('Operator account home must be absolute')
            from .workspace import resolve_workspace
            value=resolve_workspace(package_dir=PACKAGE,home=actor['home'])['path']
        workspace=session.workspace_path(value,PACKAGE,actor)
        context=origin_context()
        if args.operator_context is not None:
            if len(args.operator_context)>32768:raise ValueError('Operator context exceeds bound')
            context=json.loads(args.operator_context)
            if not isinstance(context,dict):raise ValueError('Invalid operator context')
        from .app import main as manager
        previous=signal.getsignal(signal.SIGTERM)
        def stop(signum,frame):raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM,stop)
        try:
            with session.root_session(workspace,PACKAGE,actor,context) as current:
                print('Manager execution: root; operator: '+actor['name']+' (UID '+str(actor['uid'])+').')
                current['exit_code']=manager(['--workspace',str(workspace)])
                return current['exit_code']
        finally:signal.signal(signal.SIGTERM,previous)
    except KeyboardInterrupt:
        print('Manager interrupted. Review the session log; any recovering installation child retains its own operation lock.');return 130
    except (ValueError,OSError,RuntimeError) as exc:
        print('Manager could not open: '+str(exc));return 2
