"""R20 transaction responsibilities adapted to a prebuilt provider and injected I/O.

Preserves original-before/payload copies, intent-before-write, atomic writes,
state-last commit, reverse rollback and recovery-of-recovery backups. R20 file
primitives preserve file content and ownership.
Immutable plans and a separate progress journal keep resume hashes stable.
"""
from datetime import datetime, timezone
import json
import errno
import os
from pathlib import Path
import re
import uuid
from . import VERSION, r20_storage as S
from .diagnostics import STAGES, DiagnosticError, OperationFailure, error_detail
from .contracts import (require, safe_path, load_json, digest, document_digest,
                        token, destinations, verify_manifest, compare_environment, package_evidence)

from . import jp6_names,takeover
from .commit_record import committed,PROTOCOL

EXT = '/boot/extlinux/extlinux.conf'
TERMINAL = {'applied','restored','rolled_back','cancelled'}
LEGACY_TYPED_SCHEMAS = {3:'imx415-direct-to-formal-v1', 4:'verified-same-pair-reinstall-v1'}


def now():
    return datetime.now(timezone.utc).isoformat()


def public_snapshot(snapshot):
    return {key:value for key, value in snapshot.items() if key != 'body'}


def render_extlinux(text, label, overlay, base_dtb=None):
    """R20 active-first boot rendering, preserving observed kernel/FDT directives.

Reference fingerprints are warnings, never reasons to rewrite observed boot
paths. Ambiguity blocks the concrete operation of constructing a restoreable
boot entry. Existing camera overlay is replaced only when chain-owned.
"""
    _, values, blocks = S._active_entry(text)
    for key in ('LINUX','APPEND'):
        require(len(values.get(key, [])) == 1, 'Cannot construct boot entry: expected one ' + key)
    for key in ('INITRD','FDT','OVERLAYS','FDTOVERLAYS'):
        require(len(values.get(key, [])) <= 1, 'Cannot construct boot entry: ambiguous ' + key)
    require(not (set(values) - {'LINUX','INITRD','FDT','OVERLAYS','FDTOVERLAYS','APPEND','MENU'}), 'Cannot preserve unknown active boot directive')
    require(not (values.get('OVERLAYS') and values.get('FDTOVERLAYS')), 'Cannot determine active overlay directive')
    require(label.casefold() not in {name.casefold() for name in blocks}, 'Boot label collision')
    if base_dtb is not None:
        from .boot_dtb import boot_path
        boot_path(base_dtb)
        require(not values.get('FDT') or values['FDT'] == [base_dtb], 'Cannot replace observed FDT')
        values['FDT'] = [base_dtb]
    args = ' '.join(x for x in values['APPEND'][0].split() if not x.startswith(('ecm_trial=','ecm_manager=')))
    fields = ['LABEL ' + label, '      MENU LABEL ECM Camera Manager ' + label]
    for key in ('LINUX','INITRD','FDT'):
        if key in values:
            fields.append('      ' + key + ' ' + values[key][0])
    fields += ['      OVERLAYS ' + overlay, '      APPEND ' + args + ' ecm_manager=' + label]
    changed = re.sub(r'^[ \t]*DEFAULT[ \t]+\S+[ \t]*\r?$', 'DEFAULT ' + label, text, count=1, flags=re.M | re.I)
    first = re.search(r'^[ \t]*LABEL[ \t]+\S+[ \t]*\r?$', changed, re.M | re.I)
    require(first is not None, 'Cannot construct boot entry: no LABEL')
    return changed[:first.start()].rstrip() + '\n\n' + '\n'.join(fields) + '\n\n' + changed[first.start():]


class Engine:
    def __init__(self, workspace, system):
        self.workspace = Path(workspace).absolute()
        safe_path(self.workspace.parent, self.workspace.name)
        self.system = system
        self.history_notices = []
        self.stage_name = 'transaction_precheck'
        self.operation_report_id = None
        self.diagnostics = None
        self.recovery_diagnostic_errors = None
        self.live = None

    def step(self, number):
        if self.live is not None:self.live.start(number)

    def stage(self, name):
        self.stage_name = name
        if self.diagnostics is not None:
            self.diagnostics.stage(name)

    def wpath(self, relative):
        return safe_path(self.workspace, relative)

    def state(self):
        path = self.wpath('state/installed.json')
        return load_json(path) if path.exists() else None

    def write_json(self, path, value, stage):
        self.system.checkpoint(stage)
        safe_path(self.workspace, Path(path).relative_to(self.workspace).as_posix())
        S.atomic_json(path, value)

    def event(self, directory, action, **fields):
        self.system.checkpoint('event:' + action)
        path = safe_path(directory, 'events.jsonl')
        body = dict(fields, schema=2, timestamp_utc=now(), transaction_id=Path(directory).name, installer_version=VERSION,
                    report_id=fields.get('report_id', self.operation_report_id), stage=self.stage_name,
                    action=action, qualification='LOCAL_SIMULATION' if self.system.simulation else 'NOT_QUALIFIED')
        with path.open('ab') as stream:
            stream.write((json.dumps(body, allow_nan=False) + '\n').encode())
            stream.flush(); os.fsync(stream.fileno())
        if self.diagnostics is not None:
            self.diagnostics.event(action, **dict(fields, transaction_id=Path(directory).name))

    def progress(self, directory, document):
        self.write_json(safe_path(directory, 'progress.json'), document, 'progress:' + document['status'])

    def _repair_completed(self, tid):
        seen=set()
        for _ in range(64):
            require(tid not in seen,'Repair completion chain contains a cycle','TRANSACTION_INCOMPLETE')
            seen.add(tid)
            link=self.wpath('state/repair-links/'+tid+'.json')
            if not link.exists():return False
            fixed=load_json(link)
            directory,successor=self._load_contract(fixed['transaction_id'],fixed['plan_sha256'])
            require(successor.get('repair_of',{}).get('transaction_id')==tid,
                    'Repair completion link identity differs','TRANSACTION_INCOMPLETE')
            if load_json(directory/'progress.json')['status']=='applied' and committed(directory,successor):return True
            tid=fixed['transaction_id']
        raise DiagnosticError('Repair completion chain exceeds bound','TRANSACTION_INCOMPLETE')

    def pending(self):
        self.history_notices = []
        active = self.wpath('state/external-active.json')
        if active.exists():
            return load_json(active)
        marker = self.wpath('state/recovery-required.json')
        if marker.exists():
            raise DiagnosticError('Required transaction protection: existing recovery-required marker needs its owning recovery workflow', 'TRANSACTION_INCOMPLETE')
        if self.wpath('state/capture-recovery-required.json').exists():
            raise DiagnosticError('An earlier operation has an unresolved recovery hold. Review its original record before installing.', 'TRANSACTION_INCOMPLETE')
        # Unrelated corrupt historical logs are not a global blocker. Valid
        # unfinished transaction records remain operation protection evidence.
        directory = self.wpath('backups')
        if directory.exists():
            for child in directory.iterdir():
                if not child.is_dir() or child.is_symlink():
                    continue
                link=self.wpath('state/repair-links/'+child.name+'.json')
                if link.exists():
                    require(self._repair_completed(child.name),
                            'Repair completion link is incomplete','TRANSACTION_INCOMPLETE')
                    continue
                for filename in ('plan.json','journal.json','progress.json'):
                    candidate = child / filename
                    if not candidate.exists():
                        continue
                    try:
                        doc = load_json(candidate)
                    except (ValueError, OSError) as exc:
                        self.history_notices.append(dict(status='Not verified', record=str(candidate.relative_to(self.workspace)),
                                                         reason='Unrelated historical record unreadable; required current chain is checked separately: ' + str(exc), decision='continue'))
                        continue
                    if filename=='progress.json' and doc.get('status') in ('applied','restored'):
                        try:
                            owner=load_json(child/'plan.json')
                            if owner.get('schema')=='ecm-install-transaction-v1' and owner.get('transaction_id')==child.name and not committed(child,owner):
                                return dict(transaction_id=child.name,plan_sha256=digest((child/'plan.json').read_bytes()),status='commit_incomplete')
                        except (ValueError,OSError):pass  # Unrelated history cannot authorize or block writes.
                    if doc.get('status') in ('applying','restoring','recovery_required','uninstalling'):
                        # R20 persists these typed operations in plan.json before
                        # committing installed state, and while restoring its
                        # predecessor. The current state cannot identify every
                        # interruption window. Recognize the owning record, but
                        # never reinterpret its inode/quarantine recovery here.
                        schema = doc.get('schema_version')
                        kind = LEGACY_TYPED_SCHEMAS.get(schema) if type(schema) is int else None
                        transition = doc.get('transition')
                        typed = (filename == 'plan.json' and kind is not None and
                                 isinstance(transition, dict) and transition.get('kind') == kind and
                                 doc.get('transaction_id') == child.name and
                                 re.fullmatch(r'[A-Za-z0-9-]+', child.name))
                        if typed:
                            raise DiagnosticError('Unfinished typed transaction protection: ' + kind +
                                               ' (' + doc['status'] + ') at ' + str(candidate) +
                                               '; use the original owning inode/quarantine recovery workflow; existing files preserved', 'LEGACY_UNSUPPORTED')
                        owned = doc.get('transaction_id') == child.name and doc.get('schema_version') in (1, 2)
                        if filename == 'progress.json':
                            try:
                                owner = load_json(child / 'plan.json')
                                owned = owner.get('schema') == 'ecm-install-transaction-v1' and owner.get('transaction_id') == child.name
                            except (OSError, ValueError):
                                owned = False
                        if owned:
                            raise DiagnosticError('Unfinished transaction protection: ' + str(candidate), 'TRANSACTION_INCOMPLETE')
                        self.history_notices.append(dict(status='Not verified', record=str(candidate.relative_to(self.workspace)),
                                                         reason='Record has no recognized transaction identity; required active chain is checked separately', decision='continue'))
        return None

    def _state_snapshot(self):
        return S._snapshot(self.wpath('state/installed.json'))

    def _store_snapshot(self, directory, relative, snapshot):
        result = public_snapshot(snapshot)
        result['file'] = relative if snapshot['existed'] else None
        result['backup_status'] = 'SAVED' if snapshot['existed'] else 'NOT_NEEDED'
        result['restorable'] = True
        if snapshot['existed']:
            try:
                self.system.checkpoint('backup:' + relative)
                path = safe_path(directory, relative)
                S._atomic_bytes(path, snapshot['body'], 0o600, *self._owner())
                require(digest(path.read_bytes()) == snapshot['sha256'], 'Backup verification failed: ' + relative)
            except OSError as exc:
                # Only old system-file copies are optional. Payload, plan,
                # state metadata and diagnostic writes remain mandatory.
                if exc.errno != errno.ENOSPC or not relative.startswith('original/') or relative == 'original/state':
                    raise
                result.update(file=None, backup_status='SKIPPED_ENOSPC', restorable=False,
                              backup_error={'errno':exc.errno,'message':str(exc)})
                self.event(directory, 'backup-warning', path=relative, error_code='BACKUP_INCOMPLETE',
                           sha256=snapshot['sha256'], restorable=False, reason=str(exc))
                if self.live is not None:self.live.warning=True
                print('Warning: original backup skipped (insufficient space): '+relative+
                      '; full restoration is unavailable.', flush=True)
                return result
        self.event(directory, 'snapshot-stored', path=relative, existed=snapshot['existed'], sha256=snapshot['sha256'])
        return result

    def _owner(self):
        return (os.geteuid(), os.getegid()) if hasattr(os, 'geteuid') else (0, 0)

    def _read_snapshot(self, directory, record, missing=None):
        require(type(record.get('existed')) is bool and all(type(record.get(k)) is int and record[k] >= 0 for k in ('mode','uid','gid')), 'Invalid snapshot metadata')
        require(record['mode'] <= 0o777, 'Invalid snapshot mode')
        body = None
        if record['existed']:
            require(record.get('restorable', True), 'Original backup was skipped; full restoration unavailable', 'BACKUP_INCOMPLETE')
            require(isinstance(record.get('file'),str), 'Invalid snapshot file reference')
            require(isinstance(record.get('sha256'),str) and re.fullmatch(r'[0-9a-f]{64}',record['sha256']), 'Invalid snapshot digest')
            path = safe_path(directory, record['file'])
            if missing is not None:
                try:path.stat()
                except FileNotFoundError:
                    missing.append(record['file'])
                    return dict(record,body=None)
            body = S._safe_file(path, directory)
            require(digest(body) == record['sha256'], 'Required backup/payload integrity failure: ' + record['file'])
        else:
            require(record.get('file') is None and record.get('sha256') is None, 'Absent snapshot has content')
        return dict(record, body=body)

    def _new_directory(self):
        tid = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:12]
        path = self.wpath('backups/' + tid)
        path.mkdir(parents=True, exist_ok=False)
        S.atomic_json(path / 'operation.json', dict(schema='ecm-operation-owner-v1', transaction_id=tid,
                                                   report_id=self.operation_report_id, created_utc=now()))
        if self.diagnostics is not None:
            self.diagnostics.bind(transaction_id=tid)
        return tid, path

    def _load_contract(self, tid, expected_digest, missing=None):
        token(tid, 'transaction ID')
        directory = self.wpath('backups/' + tid)
        path = safe_path(directory, 'plan.json')
        plan = load_json(path)
        require(digest(path.read_bytes()) == expected_digest, 'Required transaction plan digest mismatch')
        require(plan.get('schema') == 'ecm-install-transaction-v1' and plan.get('transaction_id') == tid, 'Transaction identity mismatch')
        require(plan['simulation'] == self.system.simulation, 'Transaction belongs to a different execution class')
        require(plan['system_root'] == str(self.system.root), 'Transaction belongs to a different system root')
        seen = set()
        for entry in plan['entries']:
            dest = entry['destination']
            require(dest not in seen, 'Duplicate transaction destination')
            seen.add(dest)
            self.system.path(dest)
            # Reconstructed installer-owned locations, never arbitrary user-supplied destinations.
            require(dest == EXT or dest in jp6_names.OVERLAYS or re.fullmatch(r'/boot/ecm-[A-Za-z0-9.-]+\.dtbo', dest) or
                    re.fullmatch(r'/lib/modules/[A-Za-z0-9._+-]+/updates/drivers/media/i2c/[A-Za-z0-9._+-]+\.ko', dest) or
                    re.fullmatch(r'/var/nvidia/nvcam/settings/[A-Za-z0-9._+-]+\.(isp|nito)', dest) or
                    (plan.get('takeover') and takeover.allowed(dest)), 'Destination outside installer allowlist')
            before=entry['before']
            if before.get('restorable') is False:
                require(before.get('backup_status')=='SKIPPED_ENOSPC' and before.get('file') is None and
                        before.get('existed') is True and re.fullmatch(r'[0-9a-f]{64}',before.get('sha256','')),
                        'Invalid skipped-backup metadata')
            else:self._read_snapshot(directory, before, missing)
            self._read_snapshot(directory, entry['after'], missing)
        self.verify_legacy_attachment(directory, plan, missing)
        return directory, plan

    @staticmethod
    def verify_legacy_attachment(directory, plan, missing=None, *, read_display=False):
        """Validate an older immutable plan's optional sidecar as opaque bytes."""
        ref = plan.get('capture_contract')
        if ref is None:
            return
        require(isinstance(ref, dict) and ref.get('path') == 'capture-contract.json' and
                ref.get('manifest_sha256') == plan['manifest_sha256'] and
                ref.get('anchor') in ('MANIFEST_SIDECAR', 'EXACT_ORIGINAL_MANIFEST_REGISTRY') and
                re.fullmatch(r'[0-9a-f]{64}', str(ref.get('sha256', ''))) and
                type(ref.get('bytes')) is int and 0 < ref['bytes'] <= 1024 * 1024,
                'Invalid legacy attachment reference')
        if ref['anchor'] == 'MANIFEST_SIDECAR':
            require(plan['manifest'].get('capture_contract_sha256') == ref['sha256'], 'Legacy attachment anchor differs')
        path = safe_path(directory, ref['path'])
        if missing is not None and not path.exists():
            missing.append(ref['path'])
            return
        require(path.is_file() and path.stat().st_size == ref['bytes'] and
                digest(path.read_bytes()) == ref['sha256'], 'Legacy attachment integrity mismatch')
        if read_display:
            document = load_json(path)
            require(isinstance(document, dict) and document.get('package_id') == plan['package_id'],
                    'Legacy attachment package differs')
            return document

    def installation_predecessor(self, state):
        """Missing completed history is advisory, never authority for cleanup.

        Only this install preflight can tolerate genuinely absent history files.
        Existing corrupt, contradictory or unfinished evidence still blocks;
        restore and identity continue to use the strict contract reader.
        """
        if state is None or state.get('schema')!='ecm-external-state-v1':
            entries,overlay=self._previous_entries(state)
            return entries,overlay,None
        try:
            tid=token(state['transaction_id'],'transaction ID')
            expected=state['plan_sha256']
            require(isinstance(expected,str) and re.fullmatch(r'[0-9a-f]{64}',expected),'Invalid installed plan digest')
            token(state['package_id'],'package ID');token(state['artifact_set_id'],'artifact set ID')
            require(state.get('status') in (None,'installed_reboot_required'),'Installed state is not completed')
            require(state.get('simulation',self.system.simulation)==self.system.simulation,'Installed execution class differs')
            directory=self.wpath('backups/'+tid);missing=[];available={};docs={}
            for name in ('plan.json','progress.json','result.json','operation.json','committed.json','journal.json'):
                path=safe_path(directory,name)
                try:path.stat()
                except FileNotFoundError:continue
                docs[name]=load_json(path);available[name]=digest(path.read_bytes())
            for name,doc in docs.items():
                if 'transaction_id' in doc:require(doc['transaction_id']==tid,'Historical transaction identity differs: '+name)
                if 'plan_sha256' in doc:require(doc['plan_sha256']==expected,'Historical plan identity differs: '+name)
                for field in ('package_id','artifact_set_id'):
                    if field in doc:require(doc[field]==state[field],'Historical package identity differs: '+name)
            for name in ('progress.json','result.json','journal.json'):
                if name in docs:require(docs[name].get('status')=='applied','Historical transaction is not completed: '+name)
            if 'operation.json' in docs:
                require(docs['operation.json'].get('schema')=='ecm-operation-owner-v1' and
                        docs['operation.json'].get('transaction_id')==tid,'Historical operation owner differs')
            if 'committed.json' in docs:
                proof=docs['committed.json']
                require(proof.get('transaction_id')==tid and proof.get('plan_sha256')==expected,'Historical commit proof differs')
            if 'plan.json' not in docs:
                missing.append('plan.json')
                # A prepared result without its final marker is an interruption,
                # even when the immutable plan itself has subsequently vanished.
                require(docs.get('result.json',{}).get('commit_status')!='PREPARED' or 'committed.json' in docs,
                        'Prepared historical transaction has no completion proof')
                if 'progress.json' not in docs:missing.append('progress.json')
            else:
                _,plan=self._load_contract(tid,expected,missing)
                require(plan['action']=='install' and plan['package_id']==state['package_id'] and
                        plan['artifact_set_id']==state['artifact_set_id'] and plan.get('overlay')==state.get('overlay'),
                        'Installed package/history mismatch')
                require(committed(directory,plan),'Required installed transaction commit is incomplete')
                if 'progress.json' not in docs:missing.append('progress.json')
                self._read_snapshot(directory,plan['state_before'],missing)
                if not missing:
                    return self._previous_entries(state)+(None,)
            # Include hashes of every remaining file, including logs, so native
            # binding notices a changed history gap without modifying old data.
            if directory.exists():
                total=0
                for count,path in enumerate(directory.rglob('*'),1):
                    require(count<=4096,'Historical file inventory exceeds bound')
                    relative=path.relative_to(directory).as_posix();safe_path(directory,relative)
                    if path.is_file():
                        size=path.stat().st_size;total+=size
                        require(size<=32*1024*1024 and total<=128*1024*1024,'Historical file bytes exceed bound')
                        available[relative]=digest(S._safe_file(path,directory))
            gap=dict(transaction_id=tid,plan_sha256=expected,missing=sorted(set(missing)),
                     available_sha256=available,decision='REIDENTIFY_FROM_CURRENT_FILES',
                     historical_ownership_trusted=False)
            return [],None,gap
        except (KeyError,TypeError,ValueError,OSError,RuntimeError) as exc:
            raise DiagnosticError(str(exc),'TRANSACTION_INCOMPLETE') from exc

    def repair_context(self, package_id, manifest_sha):
        pending=self.pending()
        state=self.state()
        if pending is None and state and state.get('status')=='partial':
            pending={'transaction_id':state['transaction_id'],'plan_sha256':state['plan_sha256']}
        if pending is None:return None
        directory,plan=self._load_contract(pending['transaction_id'],pending['plan_sha256'])
        require(plan['action']=='install' and plan['package_id']==package_id and plan['manifest_sha256']==manifest_sha,
                'Incomplete installation: select the same fixed package to repair', 'TRANSACTION_INCOMPLETE')
        progress=load_json(directory/'progress.json')
        require(progress['status'] in ('planned','applying','partial','applied','recovery_required'),
                'This transaction cannot be resumed by installation', 'TRANSACTION_INCOMPLETE')
        entries=[]
        for entry in plan['entries']:
            current=self.system.snapshot(entry['destination'])
            require(any(all(current.get(k)==side.get(k) for k in ('existed','sha256','mode','uid','gid'))
                        for side in (entry['before'],entry['after'])),
                    'Incomplete installation file changed outside transaction: '+entry['destination'], 'TRANSACTION_INCOMPLETE')
            entries.append(dict(destination=entry['destination'],sha256=current['sha256'],role=entry['role']))
        _,fields,_=S._active_entry(self.system.snapshot(EXT)['body'].decode())
        overlays=fields.get('OVERLAYS',fields.get('FDTOVERLAYS',[]))
        require(len(overlays)<=1,'Incomplete boot selection is ambiguous','TRANSACTION_INCOMPLETE')
        return dict(transaction_id=plan['transaction_id'],plan_sha256=pending['plan_sha256'],
                    entries=entries,overlay=overlays[0] if overlays else None,
                    previous_status=progress['status'],package_id=package_id,manifest_sha256=manifest_sha)

    def _previous_entries(self, state):
        if state is None:
            return [], None
        if state.get('schema') == 'ecm-external-state-v1':
            try:
                directory, plan = self._load_contract(state['transaction_id'], state['plan_sha256'])
                require(load_json(directory / 'progress.json')['status'] == 'applied', 'Required installed transaction is not applied')
                require(committed(directory,plan),'Required installed transaction commit is incomplete')
                require(plan['package_id'] == state['package_id'] and plan['artifact_set_id'] == state['artifact_set_id'] and plan.get('overlay') == state.get('overlay'), 'Installed package/history mismatch')
            except (KeyError,ValueError,OSError,RuntimeError) as exc:
                raise DiagnosticError(str(exc),'TRANSACTION_INCOMPLETE') from exc
            entries = [dict(destination=e['destination'], sha256=e['after']['sha256'], role=e['role']) for e in plan['entries']]
            return entries, state.get('overlay')
        # Read-only compatibility for ordinary R20 schema-2 installation records.
        # Typed transition plans need their original inode/quarantine semantics.
        require(not state.get('transition_kind'), 'Cannot plan backup/restore for typed legacy transition: original quarantine/inode recovery adapter is required; existing state preserved', 'LEGACY_UNSUPPORTED')
        planpath = Path(state.get('plan_path', ''))
        require(planpath.is_absolute() and planpath.is_relative_to(self.wpath('backups')), 'Required legacy plan must remain inside this workspace')
        plan = load_json(safe_path(self.workspace, planpath.relative_to(self.workspace).as_posix()))
        require(not plan.get('transition') and plan.get('schema_version') not in LEGACY_TYPED_SCHEMAS, 'Typed legacy plan requires its original recovery workflow', 'LEGACY_UNSUPPORTED')
        from .legacy import ordinary_entries
        return ordinary_entries(self.workspace, planpath, state, plan)

    def _verify_current(self, entries):
        for entry in entries:
            current = self.system.snapshot(entry['destination'])
            require(current['sha256'] == entry['sha256'], 'Cannot establish current transaction before-state: ' + entry['destination'])
            if entry.get('bytes') is not None:
                require(current['body'] is not None and len(current['body']) == entry['bytes'], 'Required legacy artifact byte count mismatch: ' + entry['destination'])
            for key in ('mode','uid','gid'):
                if key in entry and current['existed']:
                    require(current[key] == entry[key], 'Required destination metadata changed (' + key + '): ' + entry['destination'])

    def plan_install(self, package_id, manifest_path, manifest_digest, report_id):
        self.operation_report_id = report_id
        repair=self.repair_context(package_id,manifest_digest)
        manifest, contents = verify_manifest(manifest_path, package_id, manifest_digest, simulation=self.system.simulation)

        previous = self.state()
        old_entries,old_overlay,history_gap=(repair['entries'],repair['overlay'],None) if repair else self.installation_predecessor(previous)
        if history_gap:
            notice=dict(status='Warning',record='backups/'+history_gap['transaction_id'],
                        reason='Completed installation history is missing; re-identifying affected files from the selected package.',
                        decision='continue',missing=history_gap['missing'])
            self.history_notices.append(notice)
            if self.diagnostics is not None:self.diagnostics.event('history-gap-warning',**notice)
            if self.live is not None:self.live.warning=True
            print('Warning: completed installation history is missing; re-identifying affected files. Existing state and logs are preserved.',flush=True)
        self._verify_current(old_entries)
        extra_entries,loader_changes,takeover_check=takeover.collect(self.system,manifest,contents,old_entries)
        old_entries=old_entries+extra_entries
        old_state = self._state_snapshot()
        ext = self.system.snapshot(EXT)
        require(ext['existed'], 'Cannot prepare boot backup: extlinux.conf is missing')
        _, fields, _ = S._active_entry(ext['body'].decode())
        overlays = fields.get('OVERLAYS', fields.get('FDTOVERLAYS', []))
        takeover_check['active_overlay']=takeover.check_overlay(self.system,contents,overlays,old_overlay)
        # These are actual boot inputs needed to preserve the observed boot entry,
        # not fingerprints used to reject a different L4T/kernel reference.
        for field in ('LINUX','INITRD','FDT'):
            for path in fields.get(field, []):
                require(path.startswith('/') and self.system.snapshot(path)['existed'], 'Cannot preserve boot entry: required ' + field + ' file unavailable: ' + path)
        from .boot_dtb import select_base
        boot_base_dtb = select_base(self.system, manifest, ext['body'].decode(), contents['overlay'])
        self.step(2)
        self.stage('backup')
        tid, directory = self._new_directory()
        routes = destinations(manifest, tid)
        contents['extlinux'] = takeover.boot_text(render_extlinux(ext['body'].decode(), 'ECM-' + tid, routes['overlay'], boot_base_dtb['path'] if boot_base_dtb else None), takeover_check['replacements']).encode()
        entries = []
        for role, dest in routes.items():
            before = self.system.snapshot(dest)
            body = contents[role]
            after = dict(existed=True, sha256=digest(body), mode=0o644, uid=0, gid=0, body=body)
            entries.append(dict(role=role, destination=dest,
                                before=self._store_snapshot(directory, 'original/' + role, before),
                                after=self._store_snapshot(directory, 'payload/' + role, after)))
        for index,(dest,body) in enumerate(sorted(loader_changes.items())):
            role='loader-'+str(index);routes[role]=dest;contents[role]=body
            before=self.system.snapshot(dest)
            after=dict(before,body=body,sha256=digest(body))
            entries.append(dict(role=role,destination=dest,
                before=self._store_snapshot(directory,'original/'+role,before),
                after=self._store_snapshot(directory,'payload/'+role,after)))
        # Switching removes only chain-owned superseded assets; their bytes are
        # backed up in this transaction so restoring the switch restores the pair.
        for index, old in enumerate(old_entries):
            if old['destination'] not in routes.values():
                if old['destination'].startswith('/lib/modules/') and old.get('role') not in (None,'sensor_ko','takeover-module'):
                    continue  # Preserve predecessor shared helpers outside selected replacement roles.
                if takeover.allowed(old['destination']) and not old['destination'].startswith('/lib/modules/'):
                    continue  # A loader config is never a superseded package asset.
                role = 'superseded-' + str(index)
                entries.append(dict(role=role, destination=old['destination'],
                                    before=self._store_snapshot(directory, 'original/' + role, self.system.snapshot(old['destination'])),
                                    after=dict(existed=False, sha256=None, mode=0o644, uid=0, gid=0, file=None)))
        entries.sort(key=lambda e: 3 if e['destination']==EXT else 0 if e['role'].startswith(('superseded-','loader-')) else 1)
        require(self._state_snapshot()['sha256'] == old_state['sha256'], 'Required installed state changed during plan')
        self._verify_current([dict(destination=e['destination'],**e['before']) for e in entries])
        plan = dict(schema='ecm-install-transaction-v1', transaction_id=tid, action='install',
                    commit_protocol=PROTOCOL,
                    package_id=package_id, report_id=report_id, artifact_set_id=manifest['artifact_set_id'],
                    manifest=manifest, manifest_sha256=manifest_digest, entries=entries, previous_state=previous,
                    state_before=self._store_snapshot(directory, 'original/state', old_state),
                    target=manifest['target'], created_utc=now(), system_root=str(self.system.root),
                    simulation=self.system.simulation, runtime_verified=False, overlay=routes['overlay'],
                    operation='repair' if repair else 'history_reidentified' if history_gap else self.install_kind(previous,package_id,manifest_digest),
                    history_gap=history_gap,
                    repair_of=repair,
                    installation_boot=self.system.boot_identity(),
                    environment=compare_environment(self.system.observe(), manifest['target']), history_notices=list(self.history_notices))
        plan['boot_base_dtb'] = boot_base_dtb
        plan['takeover']=takeover_check
        plan['backup_coverage']=[dict(destination=e['destination'],role=e['role'],
            status=e['before'].get('backup_status','SAVED'),restorable=e['before'].get('restorable',True)) for e in entries]
        return self._save_plan(directory, plan)

    def install_kind(self, previous, package_id, manifest_sha):
        if previous is None:return 'fresh'
        if previous.get('package_id') != package_id:return 'switch'
        _,old=self._load_contract(previous['transaction_id'],previous['plan_sha256'])
        return 'reinstall' if old['manifest_sha256']==manifest_sha else 'update'

    def _save_plan(self, directory, plan):
        self.write_json(directory / 'plan.json', plan, 'plan-save')
        self.progress(directory, {'status':'planned','intent_roles':[]})
        self.event(directory, 'plan-ready', report_id=plan['report_id'], environment=plan['environment'], history_notices=plan.get('history_notices', []), action_kind=plan['action'], runtime_verified=False)
        return plan['transaction_id'], digest((directory / 'plan.json').read_bytes())

    def _command(self, directory, argv):
        try:
            result = self.system.command(argv)
        except Exception as exc:
            try:self.event(directory, 'command-exception', argv=argv, diagnostic=error_detail(exc, self.stage_name))
            except Exception:pass  # Preserve the command exception as the primary cause.
            raise
        failure = None if result['rc'] == 0 else DiagnosticError('Required command failed: ' + repr(argv) + ': ' + result.get('stderr', ''),
            STAGES[self.stage_name][1], stage=self.stage_name, argv=argv, result=result)
        try:self.event(directory, 'command-result', argv=argv, result=result)
        except Exception as log_error:
            if self.recovery_diagnostic_errors is not None:
                self.recovery_diagnostic_errors.append('Command log unavailable: ' + str(log_error))
            elif failure is None:raise
            if failure is not None:
                failure.diagnostic_context['log_error'] = str(log_error)
                raise failure from log_error
        if failure is not None:raise failure
        return result

    def _depmod(self, directory, plan):
        self.stage('depmod')
        for kernel in self._kernels(plan):
            self._command(directory, ['depmod','-a',kernel])
        self._verify_selection(directory, plan, 'after')

    def _kernels(self, plan):
        return sorted({plan['target']['kernel']} | {e['destination'].split('/')[3] for e in plan['entries'] if e['destination'].startswith('/lib/modules/')})

    def _verify_selection(self, directory, plan, side):
        if side == 'after':self.stage('module_path_check')
        for entry in plan['entries']:
            if entry['destination'].startswith('/lib/modules/') and entry[side]['existed']:
                kernel = entry['destination'].split('/')[3]
                module = takeover.module_name(entry['destination'])
                selected = self._command(directory, ['modinfo','-k',kernel,'-n',module])
                if not self.system.selection_matches(selected['stdout'].strip(), entry['destination']):
                    raise DiagnosticError('depmod selected another module file: ' + module, 'MODULE_PATH_MISMATCH',
                        stage=self.stage_name, expected=entry['destination'], actual=selected['stdout'].strip())

    def _set_state(self, directory, plan, plan_sha):
        self.stage('result')
        self.system.checkpoint('state-commit')
        path = self.wpath('state/installed.json')
        if plan['action'] == 'restore':
            S._put_snapshot(path, self._read_snapshot(directory, plan['state_after']))
        else:
            state = dict(schema='ecm-external-state-v1', transaction_id=plan['transaction_id'], plan_sha256=plan_sha,
                         package_id=plan['package_id'], artifact_set_id=plan['artifact_set_id'], overlay=plan['overlay'],
                         runtime_verified=False, status='installed_reboot_required', simulation=self.system.simulation)
            S.atomic_json(path, state)

    def _receipt(self, directory, plan, progress):
        receipt = dict(transaction_id=plan['transaction_id'], report_id=plan['report_id'], package_id=plan['package_id'], installer_version=VERSION,
                       plan_sha256=digest((directory / 'plan.json').read_bytes()),
                       action=plan['action'], artifact_set_id=plan['artifact_set_id'], status=progress['status'],
                       runtime_verified=False, qualification='LOCAL_SIMULATION' if self.system.simulation else 'NOT_QUALIFIED',
                       reboot_required=progress['status'] in ('applied','restored'),
                       package_evidence=package_evidence(plan['manifest']),
                       environment=plan['environment'], history_notices=plan.get('history_notices', []), error=progress.get('error'), recovery_errors=progress.get('recovery_errors',[]),
                       diagnostic_errors=progress.get('diagnostic_errors',[]),
                       stage=progress.get('diagnostic', {}).get('stage', self.stage_name), diagnostic=progress.get('diagnostic'),
                       error_code=progress.get('diagnostic', {}).get('error_code', 'OK'), postboot_status='NOT_COLLECTED',
                       events_sha256=digest((directory / 'events.jsonl').read_bytes()),
                       backup_coverage=plan.get('backup_coverage',[]),
                       restoration_complete=all(e['before'].get('restorable',True) for e in plan['entries']),
                       completed_roles=progress.get('completed_roles',[]),file_results=progress.get('file_results',[]),
                       installation_status='PARTIAL' if progress['status']=='partial' else progress['status'])
        if plan.get('commit_protocol'):
            receipt['commit_protocol']=plan['commit_protocol']
            receipt['commit_status']='PREPARED' if progress['status'] in ('applied','restored') else 'INCOMPLETE'
        self.write_json(directory / 'result.json', receipt, 'result-save')
        return receipt

    def _rollback(self, directory, plan, progress):
        errors = []
        diagnostic_errors = progress.setdefault('diagnostic_errors', [])
        self.recovery_diagnostic_errors = diagnostic_errors
        roles = set(progress.get('intent_roles', []))
        for entry in reversed(plan['entries']):
            if entry['role'] not in roles:
                continue
            try:
                self.system.put(entry['destination'], self._read_snapshot(directory, entry['before']))
            except Exception as exc:
                errors.append(str(exc))
                continue
            try:self.event(directory, 'rollback-asset-verified', role=entry['role'], destination=entry['destination'])
            except Exception as exc:diagnostic_errors.append('Recovery asset log unavailable: ' + str(exc))
        try:
            S._put_snapshot(self.wpath('state/installed.json'), self._read_snapshot(directory, plan['state_before']))
            for kernel in self._kernels(plan):
                if self.system.path('/lib/modules/'+kernel).exists():
                    self._command(directory, ['depmod','-a',kernel])
            self._verify_selection(directory, plan, 'before')
        except Exception as exc:
            errors.append(str(exc))
        progress.update(status='recovery_required' if errors else 'rolled_back', recovery_errors=errors)
        try:
            self.progress(directory, progress)
            self.event(directory, 'rollback-complete', errors=errors)
            self._receipt(directory, plan, progress)
            if not errors:
                if plan.get('repair_of'):
                    S.atomic_json(self.wpath('state/external-active.json'), plan['repair_of'])
                else:S._remove(self.wpath('state/external-active.json'))
        except Exception as exc:
            diagnostic_errors.append('Recovery evidence could not be persisted: ' + str(exc))
        self.recovery_diagnostic_errors = None
        return errors

    def _partial(self, directory, plan, plan_sha, progress, detail):
        progress.update(status='partial', diagnostic=detail, error=detail['message'],
                        runtime_verified=False, reboot_required=False)
        errors=progress.setdefault('diagnostic_errors',[])
        try:
            S.atomic_json(self.wpath('state/external-active.json'),dict(transaction_id=plan['transaction_id'],
                          plan_sha256=plan_sha,status='partial',diagnostic=detail,package_id=plan['package_id']))
            S._remove(safe_path(directory,'committed.json'))
        except Exception as exc:errors.append('Early partial marker: '+str(exc))
        rows=[]
        for entry in plan['entries']:
            try:
                current=self.system.snapshot(entry['destination'])
                status=('AFTER' if current['sha256']==entry['after']['sha256'] else
                        'BEFORE' if current['sha256']==entry['before']['sha256'] else 'CHANGED')
                rows.append(dict(role=entry['role'],destination=entry['destination'],status=status,
                                 sha256=current['sha256'],restorable=entry['before'].get('restorable',True)))
            except Exception as exc:rows.append(dict(role=entry['role'],destination=entry['destination'],status='UNREADABLE',reason=str(exc)))
        progress['file_results']=rows
        active=dict(transaction_id=plan['transaction_id'],plan_sha256=plan_sha,status='partial',diagnostic=detail,
                    package_id=plan['package_id'],file_results=rows,runtime_verified=False)
        state=dict(schema='ecm-external-state-v1',transaction_id=plan['transaction_id'],plan_sha256=plan_sha,
                   package_id=plan['package_id'],artifact_set_id=plan['artifact_set_id'],overlay=plan.get('overlay'),
                   status='partial',installation_status='PARTIAL',runtime_verified=False,reboot_required=False,
                   incomplete_stage=detail['stage'],simulation=self.system.simulation)
        # Independent durable indicators: any remaining active marker prevents
        # Home and report readers from presenting a successful installation.
        for label,write in (
            ('active marker',lambda:S.atomic_json(self.wpath('state/external-active.json'),active)),
            ('installed state',lambda:S.atomic_json(self.wpath('state/installed.json'),state)),
            ('progress',lambda:self.progress(directory,progress)),
            ('failure event',lambda:self.event(directory,'installation-partial',diagnostic=detail,file_results=rows)),
            ('receipt',lambda:self._receipt(directory,plan,progress))):
            try:write()
            except Exception as exc:errors.append(label+': '+str(exc))
        return dict(detail,transaction_id=plan['transaction_id'],outcome='PARTIAL',
                    recovery_status='NEW_FILES_RETAINED',file_results=rows,
                    completed_roles=progress.get('completed_roles',[]),diagnostic_errors=errors,
                    next_action='Select the same fixed package to repair the incomplete installation.')

    def apply_plan(self, tid, plan_sha, on_complete=None, on_committed=None):
        directory, plan = self._load_contract(tid, plan_sha)
        progress = load_json(directory / 'progress.json')
        require(progress['status'] == 'planned', 'Plan is not new')
        pending=self.pending()
        require(pending is None or (plan.get('repair_of') and pending['transaction_id']==plan['repair_of']['transaction_id']),
                'Another transaction requires recovery')
        require(self._state_snapshot()['sha256'] == plan['state_before']['sha256'], 'Required installed state changed since plan')
        self._verify_current([dict(destination=e['destination'],**e['before']) for e in plan['entries']])
        from .boot_dtb import verify_saved
        verify_saved(self.system, plan.get('boot_base_dtb'))
        self.system.mutation_preflight()
        self.system.bind_transaction(directory, plan)
        self.event(directory, 'apply-environment', comparisons=compare_environment(self.system.observe(), plan['target']))
        active = dict(transaction_id=tid, plan_sha256=plan_sha, status='applying')
        self.write_json(self.wpath('state/external-active.json'), active, 'active-marker')
        entry=None
        try:
            progress.update(status='applying',completed_roles=[])
            self.progress(directory, progress)
            self.event(directory, 'apply-start', report_id=plan['report_id'])
            self.step(3);self.stage('takeover')
            # Cleanup is journalled removal from the active module tree; old
            # bytes, when backed up, remain in this transaction's original/.
            boot_cleanup=[e for e in plan['entries'] if e['role'].startswith('superseded-') and e['destination'].startswith('/boot/')]
            groups=[(3,'takeover',[e for e in plan['entries'] if e['role'].startswith(('superseded-','loader-')) and e not in boot_cleanup]),
                    (4,'file_write',[e for e in plan['entries'] if e['destination']!=EXT and not e['role'].startswith(('superseded-','loader-'))]),
                    (5,'boot_config',[e for e in plan['entries'] if e['destination']==EXT]+boot_cleanup)]
            for number,stage,entries in groups:
                self.step(number);self.stage(stage)
                for entry in entries:
                    progress['intent_roles'].append(entry['role'])
                    self.progress(directory, progress)
                    self.event(directory, 'asset-write-intent', role=entry['role'], destination=entry['destination'], expected_sha256=entry['after']['sha256'])
                    self.system.put(entry['destination'], self._read_snapshot(directory, entry['after']))
                    self._verify_current([dict(destination=entry['destination'],**entry['after'])])
                    progress['completed_roles'].append(entry['role'])
                    self.progress(directory,progress)
                    self.event(directory, 'asset-write-verified', role=entry['role'], destination=entry['destination'], sha256=entry['after']['sha256'])
            entry=None
            self.step(6);self._depmod(directory, plan)
            self.step(7);self._set_state(directory, plan, plan_sha)
            progress.update(status='applied' if plan['action'] == 'install' else 'restored')
            self.progress(directory, progress)
            self.event(directory, 'apply-complete', runtime_verified=False)
            result = self._receipt(directory, plan, progress)
            if on_complete is not None:on_complete(result)
            if plan.get('repair_of'):
                self.write_json(self.wpath('state/repair-links/'+plan['repair_of']['transaction_id']+'.json'),
                                dict(transaction_id=tid,plan_sha256=plan_sha),'repair-link-save')
            if on_committed is not None:on_committed(result)
            if self.live is not None:self.live.prepare_commit()
            # The active marker is the final commit gate. No required result,
            # executor receipt or log write is left after its removal.
            self.system.checkpoint('active-clear')
            active_path=self.wpath('state/external-active.json')
            os.replace(active_path,safe_path(directory,'committed.json'))
            S._sync_dir(directory);S._sync_dir(active_path.parent)
            if self.live is not None:self.live.committed()
            return result
        except (Exception,KeyboardInterrupt) as exc:
            primary = error_detail(exc, self.stage_name, destination=entry['destination'] if entry else None,
                                   role=entry['role'] if entry else None, completed_roles=progress.get('completed_roles',[]))
            if isinstance(exc,KeyboardInterrupt):primary.update(error_code='OPERATION_INTERRUPTED')
            progress['error'],progress['diagnostic']=str(exc),primary
            if self.live is not None:self.live.fail(primary)
            diagnostic_errors = progress.setdefault('diagnostic_errors', [])
            try:
                if self.diagnostics is not None:self.diagnostics.stage_failed(primary)
            except Exception as error:diagnostic_errors.append('Failure-stage log unavailable: '+str(error))
            missing=any(not e['before'].get('restorable',True) and e['role'] in progress['intent_roles'] for e in plan['entries'])
            if plan['action']=='install' and (self.stage_name in ('boot_config','depmod','module_path_check','result') or missing):
                from .diagnostics import RecordedFailure
                raise RecordedFailure(self._partial(directory,plan,plan_sha,progress,primary)) from exc
            errors=[]
            try:
                self.stage('recovery')
                errors.extend(self._rollback(directory,plan,progress))
            except Exception as recovery_error:errors.append('Recovery raised: '+str(recovery_error))
            finally:self.recovery_diagnostic_errors=None
            raise OperationFailure(primary,tid,errors,diagnostic_errors) from exc

    def install(self, package_id, path, manifest_sha, report_id, on_complete=None, diagnostics=None, on_committed=None):
        from .install_progress import InstallProgress
        self.diagnostics,self.operation_report_id=diagnostics,report_id
        self.live=InstallProgress(diagnostics)
        if diagnostics is not None:diagnostics.install_progress=self.live
        try:
            self.step(1);self.stage('transaction_precheck')
            with self.system.lock(self.workspace):
                tid,plan_sha=self.plan_install(package_id,path,manifest_sha,report_id)
                return self.apply_plan(tid,plan_sha,on_complete,on_committed)
        except (Exception,KeyboardInterrupt) as exc:
            if not self.live.failure_mode:self.live.fail(error_detail(exc,self.stage_name))
            raise
        finally:self.live=None

    def restore_target(self):
        pending = self.pending()
        if pending:
            directory, plan = self._load_contract(pending['transaction_id'], pending['plan_sha256'])
            require(all(e['before'].get('restorable',True) for e in plan['entries']),
                    'Original backups are incomplete; select the same package to repair', 'BACKUP_INCOMPLETE')
            return dict(kind='recovery', transaction_id=plan['transaction_id'], package_id=plan['package_id'])
        state = self.state()
        require(state is not None, 'No managed backup to restore')
        require(state.get('schema') == 'ecm-external-state-v1', 'Restore of a legacy transaction requires its reviewed legacy recovery adapter; existing workspace is preserved')
        directory, plan = self._load_contract(state['transaction_id'], state['plan_sha256'])
        return dict(kind='restore', transaction_id=plan['transaction_id'], package_id=plan['package_id'], previous=plan['previous_state'])

    def plan_restore(self, report_id):
        state = self.state()
        require(state and state.get('schema') == 'ecm-external-state-v1', 'No compatible installed transaction to restore')
        old_dir, old = self._load_contract(state['transaction_id'], state['plan_sha256'])
        require(load_json(old_dir / 'progress.json')['status'] == 'applied', 'Installed transaction is not applied')
        require(committed(old_dir,old),'Installed transaction commit is incomplete','TRANSACTION_INCOMPLETE')
        require(all(e['before'].get('restorable',True) for e in old['entries']) and not old.get('repair_of'),
                'Full original restoration is unavailable; preserve the completed repair and history', 'BACKUP_INCOMPLETE')
        self._verify_current([dict(destination=e['destination'],sha256=e['after']['sha256']) for e in old['entries']])
        # A restoration is another journalled transaction, including rollback of
        # restoration itself. Its payloads do not depend on the unpacked package.
        tid, directory = self._new_directory()
        entries = []
        for entry in old['entries']:
            entries.append(dict(role=entry['role'], destination=entry['destination'],
                                before=self._store_snapshot(directory, 'original/' + entry['role'], self.system.snapshot(entry['destination'])),
                                after=self._store_snapshot(directory, 'payload/' + entry['role'], self._read_snapshot(old_dir, entry['before']))))
        plan = dict(schema='ecm-install-transaction-v1', transaction_id=tid, action='restore', package_id=old['package_id'],
                    commit_protocol=PROTOCOL,
                    artifact_set_id=old['artifact_set_id'], manifest=old['manifest'], report_id=report_id,
                    takeover=old.get('takeover'),
                    entries=entries, target=old['target'], system_root=str(self.system.root), simulation=self.system.simulation,
                    state_before=self._store_snapshot(directory, 'original/state', self._state_snapshot()),
                    state_after=self._store_snapshot(directory, 'payload/state', self._read_snapshot(old_dir, old['state_before'])),
                    restored_transaction=old['transaction_id'], previous_state=old['previous_state'],
                    environment=compare_environment(self.system.observe(), old['target']), created_utc=now(), runtime_verified=False)
        return self._save_plan(directory, plan)

    def restore(self, report_id, on_complete=None, diagnostics=None):
        self.diagnostics = diagnostics
        self.operation_report_id = report_id
        self.stage('transaction_precheck')
        with self.system.lock(self.workspace):
            pending = self.pending()
            if pending:
                directory, plan = self._load_contract(pending['transaction_id'], pending['plan_sha256'])
                progress = load_json(directory / 'progress.json')
                require(all(e['before'].get('restorable',True) for e in plan['entries']),
                        'Original backups are incomplete; select the same package to repair', 'BACKUP_INCOMPLETE')
                require(progress['status'] in ('applying','partial','recovery_required','planned','applied','restored','rolled_back'), 'Unsupported recovery journal status')
                # Roll back every persisted intent, including a crash after state
                # commit and before clearing the active marker. Idempotent.
                errors = self._rollback(directory, plan, progress)
                require(not errors, 'Recovery incomplete: ' + repr(errors))
                result = self._receipt(directory, plan, progress)
                if on_complete is not None:
                    on_complete(result)
                return result
            tid, plan_sha = self.plan_restore(report_id)
            return self.apply_plan(tid, plan_sha, on_complete)
