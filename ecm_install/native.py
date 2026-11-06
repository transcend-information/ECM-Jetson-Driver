"""Linux backend reachable only through a fixed, confirmed privileged request.

No compatibility equality gate: target/running kernel differences remain logged.
Mutation is restricted to validated package destinations and managed predecessors.
"""
from contextlib import contextmanager
import os,platform,re,stat,subprocess
from pathlib import Path
from . import r20_storage as S
from .system import SystemIO,file_lock
from .contracts import require,safe_path,digest,document_digest,destinations,verify_manifest,load_json
from .diagnostics import DiagnosticError
from .transaction import EXT,render_extlinux,Engine
from .catalog import Catalog
from . import jp6_names,takeover

def package_destinations(package):
    """Exact fixed destinations for this distribution, shared by native binding."""
    catalog=Catalog(package)
    allowed=set()
    for key in catalog.selectable_packages():
        record=catalog.manifest(key)
        if record is None:continue
        fixed,_=verify_manifest(record[0],key,record[1],simulation=False)
        allowed.update(destinations(fixed,'IDENTITY').values())
    allowed.add('/lib/modules/5.15.148-tegra/updates/drivers/media/i2c/nv_imx415.ko')
    return allowed

def protected(path):
    path=Path(path)
    safe_path(path.parent,path.name)
    for item in [path,*path.parents]:
        if not item.exists():continue
        info=item.lstat()
        if str(item)=='/run/lock' and stat.S_ISDIR(info.st_mode) and info.st_uid==0 and info.st_mode&stat.S_ISVTX:continue
        require(info.st_uid==0 and not info.st_mode&0o022,'Unprotected system path: '+str(item),'NATIVE_DESTINATION_INVALID')
    return path

class NativeSystemIO(SystemIO):
    def __init__(self,request,manifest,contents):
        require(platform.system()=='Linux' and os.geteuid()==0,'Privileged Linux executor required','NATIVE_PLATFORM_UNSUPPORTED')
        super().__init__('/')
        self.request,self.manifest,self.contents=request,manifest,contents
        self.workspace=Path(request['workspace']);self.package=Path(request['package_root'])
        self.owner=(request['owner_uid'],request['owner_gid'])
        require(self.workspace.is_dir(),'Fixed workspace disappeared','WORKSPACE_CHANGED')
        safe_path(self.workspace.parent,self.workspace.name)
        info=self.workspace.stat()
        require({'device':info.st_dev,'inode':info.st_ino,'uid':info.st_uid,'gid':info.st_gid}==request['workspace_identity'],
            'Workspace differs from authenticated session','WORKSPACE_CHANGED')
        require(info.st_uid in (0,request['operator']['uid']) or request['operator']['origin']=='direct-root',
            'Workspace belongs to another user','WORKSPACE_CHANGED')
        require(not info.st_mode&0o022,'Workspace permits unrelated writers','WORKSPACE_CHANGED')
        self.workspace_identity=(info.st_dev,info.st_ino)
        self.locked=False;self.allowed={};self.directory=None
        self.lib_alias=None
        lib=Path('/lib')
        if lib.is_symlink():
            st=lib.lstat()
            require(st.st_uid==0 and os.readlink(lib) in ('usr/lib','/usr/lib'),'Unrecognized /lib alias','NATIVE_DESTINATION_INVALID')
            protected(Path('/usr/lib'));self.lib_alias=(st.st_dev,st.st_ino,os.readlink(lib))
        else:protected(lib)
        self.executables={}
        for name,path in [('depmod','/usr/sbin/depmod'),('modinfo','/usr/sbin/modinfo'),('fuser','/usr/bin/fuser'),('dpkg-query','/usr/bin/dpkg-query')]:
            entry=Path(path);resolved=entry.resolve(strict=True);protected(resolved)
            require(resolved.is_file() and os.access(resolved,os.X_OK),'Required system command unavailable: '+path)
            self.executables[name]=(path,str(resolved),digest(resolved.read_bytes()))
        # Validate fixed catalogue destinations; cleanup additionally requires the verified predecessor chain.
        self.known_destinations=package_destinations(self.package)

    def path(self,logical):
        require(isinstance(logical,str) and logical.startswith('/'),'Absolute system path required','NATIVE_DESTINATION_INVALID')
        if logical.startswith('/lib/') and self.lib_alias is not None:
            st=Path('/lib').lstat()
            require((st.st_dev,st.st_ino,os.readlink('/lib'))==self.lib_alias,'Pinned /lib alias changed','NATIVE_DESTINATION_INVALID')
            logical='/usr/lib/'+logical[len('/lib/'):]
        return safe_path(self.root,logical[1:])

    def snapshot(self,logical):
        path=self.path(logical)
        if path.exists():
            info=path.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size<=128*1024*1024,
                'Native snapshot requires a bounded regular single-link file: '+logical,'NATIVE_DESTINATION_INVALID')
        return S._snapshot(path)

    def loader_config_snapshot(self,logical):
        from .loader_config import read
        return read(self.root,logical)

    def _workspace_check(self):
        safe_path(self.workspace.parent,self.workspace.name);info=self.workspace.stat()
        require((info.st_dev,info.st_ino)==self.workspace_identity,'Fixed workspace changed','WORKSPACE_CHANGED')

    def _run(self,name,args,timeout=60):
        path,resolved,expected=self.executables[name]
        require(str(Path(path).resolve())==resolved and digest(protected(Path(resolved)).read_bytes())==expected,
            'System executable changed: '+name,'NATIVE_DESTINATION_INVALID')
        # Kernel indexes are public system data; keep the Manager's private
        # umask unchanged and set 022 only inside the depmod child process.
        process_options={'umask':0o022} if name=='depmod' else {}
        result=subprocess.run([path,*args],stdin=subprocess.DEVNULL,capture_output=True,text=True,errors='replace',
            shell=False,timeout=timeout,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'},**process_options)
        return {'rc':result.returncode,'stdout':result.stdout,'stderr':result.stderr}

    def _camera_idle(self):
        for device in sorted(Path('/dev').glob('video[0-9]*')):
            result=self._run('fuser',[str(device)],10)
            if result['rc']!=1 or result['stdout'].strip() or result['stderr'].strip():
                raise DiagnosticError('Camera busy or ownership unavailable: '+str(device),'CAMERA_BUSY',command=['fuser',str(device)],result=result)
        for proc in Path('/proc').glob('[0-9]*'):
            if proc.name==str(os.getpid()):continue
            try:args=(proc/'cmdline').read_bytes()[:16384].split(b'\0')
            except FileNotFoundError:continue
            names=[Path(arg.decode('utf-8','replace')).name for arg in args]
            if any(name in ('gst-launch-1.0', 'gst_worker.py', 'ecm_runtime.py') for name in names):
                raise DiagnosticError('Camera client is active: PID '+proc.name,'CAMERA_BUSY',pid=proc.name)

    def module_aliases(self,kernel):
        require(self.locked,'Rename alias check requires installation locks','NATIVE_REQUEST_INVALID')
        if not self.path('/lib/modules/'+kernel).exists():
            return {'scope':'TARGET_MODULE_TREE_ABSENT','aliases':[]}
        result=self._run('depmod',['-n','-a',kernel])
        require(result['rc']==0 and len(result['stdout'])<=16*1024*1024,'Cannot obtain current module aliases without writing indexes','MODULE_RENAME_CHECK_FAILED')
        return {'scope':'CURRENT_DEPMOD_DRY_RUN','aliases':jp6_names.aliases_from_text(result['stdout'])}

    def mutation_preflight(self):
        require(self.locked and os.geteuid()==0,'Privileged operation locks are required','NATIVE_REQUEST_INVALID')
        self._workspace_check();self._camera_idle()

    def package_owners(self,logical):
        owners=set()
        for path in sorted({logical,str(self.path(logical))}):
            result=self._run('dpkg-query',['-S','--',path])
            require(result['rc'] in (0,1),'Cannot establish module package ownership: '+logical,'MODULE_RENAME_CHECK_FAILED')
            if result['rc']==0:
                require(len(result['stdout'])<=256*1024,'Package ownership output exceeds bound','MODULE_RENAME_CHECK_FAILED')
                owners.update(line.rsplit(': ',1)[0] for line in result['stdout'].splitlines() if ': ' in line)
        return sorted(owners)

    @contextmanager
    def lock(self,workspace):
        require(Path(workspace)==self.workspace,'Workspace differs from confirmed request','WORKSPACE_CHANGED')
        self._workspace_check()
        with file_lock(protected(Path('/run/lock/ecm-camera-manager-install.lock')),owner_uid=0,public_read=True):
            with file_lock(safe_path(self.workspace,'state/camera-operation.lock'),owner_uid=0,public_read=True):
                self.locked=True
                try:
                    self.mutation_preflight()
                    from .privileged import state_fingerprint,identity_inventory
                    expected=self.request['state_before']
                    require(expected.get('status')=='READ' and state_fingerprint(self.workspace)==expected,
                        'Installed state changed after confirmation','WORKSPACE_CHANGED')
                    require(identity_inventory(self.package,self.workspace)==self.request['package_inventory'],'Package changed after authentication','NATIVE_REQUEST_INVALID')
                    verify_manifest(self.package/self.request['manifest'],self.request['package_id'],self.request['manifest_sha256'],simulation=False)
                    yield
                finally:self.locked=False

    def bind_transaction(self,directory,plan):
        require(self.locked and plan['action']=='install','Only confirmed installation is enabled','NATIVE_REQUEST_INVALID')
        require(plan['package_id']==self.request['package_id'] and plan['report_id']==self.request['report_id'] and
            plan['manifest_sha256']==self.request['manifest_sha256'] and document_digest(plan['manifest'])==document_digest(self.manifest),
            'Transaction does not match the confirmed package','NATIVE_REQUEST_INVALID')
        require(Path(directory).parent==self.workspace/'backups','Unexpected transaction directory','NATIVE_REQUEST_INVALID')
        routes=destinations(self.manifest,plan['transaction_id'])
        rows={e['role']:e for e in plan['entries']}
        require(len(rows)==len(plan['entries']) and set(routes)<=set(rows),'Invalid transaction role set','NATIVE_DESTINATION_INVALID')
        expected=dict(self.contents)
        legacy_destinations={};verified=[]
        previous=plan.get('previous_state')
        verifier=Engine(self.workspace,self)
        require(previous==verifier.state(),'Installed state changed during binding','TRANSACTION_INCOMPLETE')
        repair=verifier.repair_context(plan['package_id'],plan['manifest_sha256'])
        require(document_digest(repair)==document_digest(plan.get('repair_of')),'Repair source changed after planning','TRANSACTION_INCOMPLETE')
        verified,old_overlay,history_gap=(repair['entries'],repair['overlay'],None) if repair else verifier.installation_predecessor(previous)
        require(document_digest(history_gap)==document_digest(plan.get('history_gap')),
                'Historical evidence changed after planning','TRANSACTION_INCOMPLETE')
        extra_entries,loader_changes,checked=takeover.collect(self,self.manifest,self.contents,verified)
        before=self.snapshot(EXT)
        _,fields,_=S._active_entry(before['body'].decode())
        overlays=fields.get('OVERLAYS',fields.get('FDTOVERLAYS',[]))
        checked['active_overlay']=takeover.check_overlay(self,self.contents,overlays,old_overlay)
        require(document_digest(checked)==document_digest(plan.get('takeover')),
                'Module/loader inventory changed after planning','MODULE_RENAME_CONFLICT')
        verified+=extra_entries
        legacy_destinations={e['destination']:e['sha256'] for e in verified}
        cleanup={e['destination']:e['sha256'] for e in verified
                 if e['destination'] not in routes.values() and
                 (not e['destination'].startswith('/lib/modules/') or e.get('role') in (None,'sensor_ko','takeover-module')) and
                 not (takeover.allowed(e['destination']) and not e['destination'].startswith('/lib/modules/'))}
        before=self.snapshot(EXT)
        from .boot_dtb import select_base
        base = select_base(self,self.manifest,before['body'].decode(),self.contents['overlay'])
        require(base == plan.get('boot_base_dtb'),'Base DTB selection changed after planning','NATIVE_DESTINATION_INVALID')
        expected['extlinux']=takeover.boot_text(render_extlinux(before['body'].decode(),'ECM-'+plan['transaction_id'],routes['overlay'],base['path'] if base else None),checked['replacements']).encode()
        for index,(logical,body) in enumerate(sorted(loader_changes.items())):
            role='loader-'+str(index);routes[role]=logical;expected[role]=body
        allowed={}
        for role,row in rows.items():
            logical=row['destination'];physical=protected(self.path(logical))
            current=self.snapshot(logical)
            require(all(current.get(k)==row['before'].get(k) for k in ('existed','sha256','mode','uid','gid')),
                'Native before-state changed: '+logical,'NATIVE_DESTINATION_INVALID')
            if role in routes:
                require(logical==routes[role] and row['after']['sha256']==digest(expected[role]),'Native destination/payload mismatch','NATIVE_DESTINATION_INVALID')
                desired=dict(row['after'],body=expected[role])
                metadata=tuple(current[k] for k in ('mode','uid','gid')) if role.startswith('loader-') else (0o644,0,0)
                require(desired['existed'] and (desired['mode'],desired['uid'],desired['gid'])==metadata,
                    'Native new-file metadata differs','NATIVE_DESTINATION_INVALID')
            else:
                require(role.startswith('superseded-') and not row['after']['existed'] and
                    logical in cleanup and cleanup[logical]==row['before']['sha256'],
                    'Cleanup destination outside managed package roles','NATIVE_DESTINATION_INVALID')
                desired=dict(existed=False,sha256=None,mode=0o644,uid=0,gid=0,body=None)
            require(logical not in allowed,'Duplicate native destination','NATIVE_DESTINATION_INVALID')
            allowed[logical]=(current,desired)
        self.allowed=allowed;self.directory=Path(directory)

    def put(self,logical,desired):
        require(self.locked and logical in self.allowed,'Unbound native destination','NATIVE_DESTINATION_INVALID')
        self._workspace_check();physical=protected(self.path(logical))
        # Reuse immutable in-memory bytes, never a re-read user-supplied payload.
        matches=[value for value in self.allowed[logical] if all(value.get(k)==desired.get(k) for k in ('existed','sha256','mode','uid','gid'))]
        require(bool(matches),'Snapshot outside this transaction','NATIVE_DESTINATION_INVALID')
        value=matches[-1]
        require(value['body'] is None or digest(value['body'])==value['sha256'],'Native payload integrity changed','NATIVE_DESTINATION_INVALID')
        current=self.snapshot(logical)
        require(any(all(current.get(k)==candidate.get(k) for k in ('existed','sha256','mode','uid','gid')) for candidate in self.allowed[logical]),
            'Destination changed outside transaction','NATIVE_DESTINATION_INVALID')
        S._put_snapshot(physical,value)

    def command(self,argv):
        require(self.locked and self.directory is not None,'Unbound native command','NATIVE_REQUEST_INVALID')
        modules={(p.split('/')[3],takeover.module_name(p)) for p in self.allowed if p.startswith('/lib/modules/')}
        allowed=([['depmod','-a',kernel] for kernel in {self.manifest['target']['kernel']}|{k for k,m in modules}]+
                 [['modinfo','-k',kernel,'-n',module] for kernel,module in modules])
        require(argv in allowed,'Command is outside this transaction','NATIVE_REQUEST_INVALID')
        return self._run(argv[0],argv[1:])

    def selection_matches(self,actual,expected):
        return actual==expected or bool(self.lib_alias and expected.startswith('/lib/') and
            actual=='/usr/lib/'+expected[len('/lib/'):] and self.path(actual)==self.path(expected))

    def publish_diagnostics(self,report):
        self._workspace_check()
        require(report.directory==self.workspace/'logs/external'/self.request['report_id'],'Unexpected report publication','NATIVE_REQUEST_INVALID')
        uid,gid=self.owner
        for path in [report.directory,*report.directory.iterdir()]:
            safe_path(path.parent,path.name)
            require(path.is_file() or path==report.directory,'Unexpected nested report entry','NATIVE_REQUEST_INVALID')
            require(path==report.directory or path.stat().st_nlink==1,'Linked report file refused','NATIVE_REQUEST_INVALID')
            os.chown(path,uid,gid);os.chmod(path,0o700 if path.is_dir() else 0o600)
        # Publish only this operation's metadata. Old history and binary backups
        # retain their original ownership and permissions.
        tid=report.context.get('transaction_id')
        if tid:
            directory=safe_path(self.workspace,'backups/'+tid)
            owner=load_json(directory/'operation.json')
            require(owner.get('report_id')==report.id,'Transaction is not owned by this operation','NATIVE_REQUEST_INVALID')
            os.chown(directory,0,gid);os.chmod(directory,0o750)
            for name in ('operation.json','plan.json','progress.json','result.json','events.jsonl','committed.json'):
                path=safe_path(directory,name)
                if path.exists():os.chown(path,0,gid);os.chmod(path,0o640)
