"""R20 transaction responsibilities adapted to a prebuilt provider and injected I/O.

Preserves original-before/payload copies, intent-before-write, atomic writes,
state-last commit, reverse rollback and recovery-of-recovery backups. R20 file
primitives are imported verbatim; source/build/capture dependencies are absent.
Immutable plans and a separate progress journal keep resume hashes stable.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import uuid
from . import VERSION, r20_storage as S
from .contracts import (require, safe_path, load_json, digest, document_digest,
                        token, destinations, verify_manifest, compare_environment, package_evidence)

EXT = '/boot/extlinux/extlinux.conf'
TERMINAL = {'applied','restored','rolled_back','cancelled'}
LEGACY_TYPED_SCHEMAS = {3:'imx415-direct-to-formal-v1', 4:'verified-same-pair-reinstall-v1'}


def now():
    return datetime.now(timezone.utc).isoformat()


def public_snapshot(snapshot):
    return {key:value for key, value in snapshot.items() if key != 'body'}


def render_extlinux(text, label, overlay):
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
        body = dict(schema=1, timestamp_utc=now(), transaction_id=Path(directory).name, installer_version=VERSION,
                    action=action, qualification='LOCAL_SIMULATION' if self.system.simulation else 'NOT_QUALIFIED', **fields)
        with path.open('ab') as stream:
            stream.write((json.dumps(body, allow_nan=False) + '\n').encode())
            stream.flush(); os.fsync(stream.fileno())

    def progress(self, directory, document):
        self.write_json(safe_path(directory, 'progress.json'), document, 'progress:' + document['status'])

    def pending(self):
        self.history_notices = []
        active = self.wpath('state/external-active.json')
        if active.exists():
            return load_json(active)
        marker = self.wpath('state/recovery-required.json')
        if marker.exists():
            raise RuntimeError('Required transaction protection: existing recovery-required marker needs its owning recovery workflow')
        # Unrelated corrupt historical logs are not a global blocker. Valid
        # unfinished transaction records remain operation protection evidence.
        directory = self.wpath('backups')
        if directory.exists():
            for child in directory.iterdir():
                if not child.is_dir() or child.is_symlink():
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
                            raise RuntimeError('Unfinished typed transaction protection: ' + kind +
                                               ' (' + doc['status'] + ') at ' + str(candidate) +
                                               '; use the original owning inode/quarantine recovery workflow; existing files preserved')
                        owned = doc.get('transaction_id') == child.name and doc.get('schema_version') in (1, 2)
                        if filename == 'progress.json':
                            try:
                                owner = load_json(child / 'plan.json')
                                owned = owner.get('schema') == 'ecm-install-transaction-v1' and owner.get('transaction_id') == child.name
                            except (OSError, ValueError):
                                owned = False
                        if owned:
                            raise RuntimeError('Unfinished transaction protection: ' + str(candidate))
                        self.history_notices.append(dict(status='Not verified', record=str(candidate.relative_to(self.workspace)),
                                                         reason='Record has no recognized transaction identity; required active chain is checked separately', decision='continue'))
        return None

    def _state_snapshot(self):
        return S._snapshot(self.wpath('state/installed.json'))

    def _store_snapshot(self, directory, relative, snapshot):
        result = public_snapshot(snapshot)
        result['file'] = relative if snapshot['existed'] else None
        if snapshot['existed']:
            self.system.checkpoint('backup:' + relative)
            path = safe_path(directory, relative)
            S._atomic_bytes(path, snapshot['body'], 0o600, *self._owner())
            require(digest(path.read_bytes()) == snapshot['sha256'], 'Backup verification failed: ' + relative)
        return result

    def _owner(self):
        return (os.geteuid(), os.getegid()) if hasattr(os, 'geteuid') else (0, 0)

    def _read_snapshot(self, directory, record):
        require(type(record.get('existed')) is bool and all(type(record.get(k)) is int and record[k] >= 0 for k in ('mode','uid','gid')), 'Invalid snapshot metadata')
        require(record['mode'] <= 0o777, 'Invalid snapshot mode')
        body = None
        if record['existed']:
            body = S._safe_file(safe_path(directory, record['file']), directory)
            require(digest(body) == record['sha256'], 'Required backup/payload integrity failure: ' + record['file'])
        else:
            require(record.get('file') is None and record.get('sha256') is None, 'Absent snapshot has content')
        return dict(record, body=body)

    def _new_directory(self):
        tid = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:12]
        path = self.wpath('backups/' + tid)
        path.mkdir(parents=True, exist_ok=False)
        return tid, path

    def _load_contract(self, tid, expected_digest):
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
            require(dest == EXT or re.fullmatch(r'/boot/ecm-[A-Za-z0-9.-]+\.dtbo', dest) or
                    re.fullmatch(r'/lib/modules/[A-Za-z0-9._+-]+/updates/drivers/media/i2c/[A-Za-z0-9._+-]+\.ko', dest) or
                    re.fullmatch(r'/var/nvidia/nvcam/settings/[A-Za-z0-9._+-]+\.(isp|nito)', dest), 'Destination outside installer allowlist')
            self._read_snapshot(directory, entry['before'])
            self._read_snapshot(directory, entry['after'])
        return directory, plan

    def _previous_entries(self, state):
        if state is None:
            return [], None
        if state.get('schema') == 'ecm-external-state-v1':
            directory, plan = self._load_contract(state['transaction_id'], state['plan_sha256'])
            require(load_json(directory / 'progress.json')['status'] == 'applied', 'Required installed transaction is not applied')
            require(plan['package_id'] == state['package_id'], 'Installed package/history mismatch')
            entries = [dict(destination=e['destination'], sha256=e['after']['sha256']) for e in plan['entries']]
            return entries, state.get('overlay')
        # Read-only compatibility for ordinary R20 schema-2 installation records.
        # Typed transition plans need their original inode/quarantine semantics.
        require(not state.get('transition_kind'), 'Cannot plan backup/restore for typed legacy transition: original quarantine/inode recovery adapter is required; existing state preserved')
        planpath = Path(state.get('plan_path', ''))
        require(planpath.is_absolute() and planpath.is_relative_to(self.wpath('backups')), 'Required legacy plan must remain inside this workspace')
        plan = load_json(safe_path(self.workspace, planpath.relative_to(self.workspace).as_posix()))
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
        require(self.pending() is None, 'Unfinished transaction: use Restore / recovery first')
        manifest, contents = verify_manifest(manifest_path, package_id, manifest_digest, simulation=self.system.simulation)
        previous = self.state()
        old_entries, old_overlay = self._previous_entries(previous)
        self._verify_current(old_entries)
        old_state = self._state_snapshot()
        ext = self.system.snapshot(EXT)
        require(ext['existed'], 'Cannot prepare boot backup: extlinux.conf is missing')
        _, fields, _ = S._active_entry(ext['body'].decode())
        overlays = fields.get('OVERLAYS', fields.get('FDTOVERLAYS', []))
        require(overlays == ([old_overlay] if old_overlay else []), 'Cannot replace unowned active overlay: no matching managed backup chain')
        # These are actual boot inputs needed to preserve the observed boot entry,
        # not fingerprints used to reject a different L4T/kernel reference.
        for field in ('LINUX','INITRD','FDT'):
            for path in fields.get(field, []):
                require(path.startswith('/') and self.system.snapshot(path)['existed'], 'Cannot preserve boot entry: required ' + field + ' file unavailable: ' + path)
        tid, directory = self._new_directory()
        routes = destinations(manifest, tid)
        contents['extlinux'] = render_extlinux(ext['body'].decode(), 'ECM-' + tid, routes['overlay']).encode()
        entries = []
        for role, dest in routes.items():
            before = self.system.snapshot(dest)
            body = contents[role]
            after = dict(existed=True, sha256=digest(body), mode=0o644, uid=0, gid=0, body=body)
            entries.append(dict(role=role, destination=dest,
                                before=self._store_snapshot(directory, 'original/' + role, before),
                                after=self._store_snapshot(directory, 'payload/' + role, after)))
        # Switching removes only chain-owned superseded assets; their bytes are
        # backed up in this transaction so restoring the switch restores the pair.
        for index, old in enumerate(old_entries):
            if old['destination'] not in routes.values():
                role = 'superseded-' + str(index)
                entries.append(dict(role=role, destination=old['destination'],
                                    before=self._store_snapshot(directory, 'original/' + role, self.system.snapshot(old['destination'])),
                                    after=dict(existed=False, sha256=None, mode=0o644, uid=0, gid=0, file=None)))
        entries.sort(key=lambda e:e['destination'] == EXT)  # boot activation last
        require(self._state_snapshot()['sha256'] == old_state['sha256'], 'Required installed state changed during plan')
        self._verify_current([dict(destination=e['destination'],**e['before']) for e in entries])
        plan = dict(schema='ecm-install-transaction-v1', transaction_id=tid, action='install',
                    package_id=package_id, report_id=report_id, artifact_set_id=manifest['artifact_set_id'],
                    manifest=manifest, manifest_sha256=manifest_digest, entries=entries, previous_state=previous,
                    state_before=self._store_snapshot(directory, 'original/state', old_state),
                    target=manifest['target'], created_utc=now(), system_root=str(self.system.root),
                    simulation=self.system.simulation, runtime_verified=False, overlay=routes['overlay'],
                    operation='fresh' if previous is None else 'same-pair-reinstall' if previous.get('package_id') == package_id else 'switch-or-update',
                    environment=compare_environment(self.system.observe(), manifest['target']), history_notices=list(self.history_notices))
        return self._save_plan(directory, plan)

    def _save_plan(self, directory, plan):
        self.write_json(directory / 'plan.json', plan, 'plan-save')
        self.progress(directory, {'status':'planned','intent_roles':[]})
        self.event(directory, 'plan-ready', report_id=plan['report_id'], environment=plan['environment'], history_notices=plan.get('history_notices', []), action_kind=plan['action'], runtime_verified=False)
        return plan['transaction_id'], digest((directory / 'plan.json').read_bytes())

    def _command(self, directory, argv):
        result = self.system.command(argv)
        self.event(directory, 'command-result', argv=argv, result=result)
        require(result['rc'] == 0, 'Required command failed: ' + repr(argv) + ': ' + result.get('stderr', ''))
        return result

    def _depmod(self, directory, plan):
        for kernel in self._kernels(plan):
            self._command(directory, ['depmod','-a',kernel])
        self._verify_selection(directory, plan, 'after')

    def _kernels(self, plan):
        return sorted({plan['target']['kernel']} | {e['destination'].split('/')[3] for e in plan['entries'] if e['destination'].startswith('/lib/modules/')})

    def _verify_selection(self, directory, plan, side):
        for entry in plan['entries']:
            if entry['destination'].startswith('/lib/modules/') and entry[side]['existed']:
                kernel = entry['destination'].split('/')[3]
                module = entry['destination'].rsplit('/', 1)[1][:-3]
                selected = self._command(directory, ['modinfo','-k',kernel,'-n',module])
                require(selected['stdout'].strip() == entry['destination'], 'depmod selected another module file: ' + module)

    def _set_state(self, directory, plan, plan_sha):
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
                       action=plan['action'], artifact_set_id=plan['artifact_set_id'], status=progress['status'],
                       runtime_verified=False, qualification='LOCAL_SIMULATION' if self.system.simulation else 'NOT_QUALIFIED',
                       reboot_required=progress['status'] in ('applied','restored'),
                       package_evidence=package_evidence(plan['manifest']),
                       environment=plan['environment'], history_notices=plan.get('history_notices', []), error=progress.get('error'), recovery_errors=progress.get('recovery_errors',[]),
                       events_sha256=digest((directory / 'events.jsonl').read_bytes()))
        self.write_json(directory / 'result.json', receipt, 'result-save')
        return receipt

    def _rollback(self, directory, plan, progress):
        errors = []
        roles = set(progress.get('intent_roles', []))
        for entry in reversed(plan['entries']):
            if entry['role'] not in roles:
                continue
            try:
                self.system.put(entry['destination'], self._read_snapshot(directory, entry['before']))
                self.event(directory, 'rollback-asset-verified', role=entry['role'], destination=entry['destination'])
            except Exception as exc:
                errors.append(str(exc))
        try:
            S._put_snapshot(self.wpath('state/installed.json'), self._read_snapshot(directory, plan['state_before']))
            for kernel in self._kernels(plan):
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
                S._remove(self.wpath('state/external-active.json'))
        except Exception as exc:
            errors.append('Recovery evidence could not be persisted: ' + str(exc))
        return errors

    def apply_plan(self, tid, plan_sha, on_complete=None):
        directory, plan = self._load_contract(tid, plan_sha)
        progress = load_json(directory / 'progress.json')
        require(progress['status'] == 'planned', 'Plan is not new')
        require(self.pending() is None, 'Another transaction requires recovery')
        require(self._state_snapshot()['sha256'] == plan['state_before']['sha256'], 'Required installed state changed since plan')
        self._verify_current([dict(destination=e['destination'],**e['before']) for e in plan['entries']])
        self.system.mutation_preflight()
        # Re-observe differences as warnings; do not compare entire host snapshot
        # for equality. File/state before-values remain transaction preconditions.
        self.event(directory, 'apply-environment', comparisons=compare_environment(self.system.observe(), plan['target']))
        active = dict(transaction_id=tid, plan_sha256=plan_sha)
        self.write_json(self.wpath('state/external-active.json'), active, 'active-marker')
        try:
            progress.update(status='applying')
            self.progress(directory, progress)
            self.event(directory, 'apply-start', report_id=plan['report_id'])
            for entry in plan['entries']:
                progress['intent_roles'].append(entry['role'])
                self.progress(directory, progress)
                self.event(directory, 'asset-write-intent', role=entry['role'], destination=entry['destination'])
                self.system.put(entry['destination'], self._read_snapshot(directory, entry['after']))
                self.event(directory, 'asset-write-verified', role=entry['role'], sha256=entry['after']['sha256'])
            self._depmod(directory, plan)
            self._set_state(directory, plan, plan_sha)
            progress.update(status='applied' if plan['action'] == 'install' else 'restored')
            self.progress(directory, progress)
            self.event(directory, 'apply-complete', runtime_verified=False)
            result = self._receipt(directory, plan, progress)
            if on_complete is not None:
                on_complete(result)
            self.system.checkpoint('active-clear')
            S._remove(self.wpath('state/external-active.json'))
            return result
        except Exception as exc:
            progress['error'] = str(exc)
            errors = self._rollback(directory, plan, progress)
            raise RuntimeError('Operation failed: ' + str(exc) + '; rollback=' + ('recovery required: ' + repr(errors) if errors else 'verified') + '; transaction=' + tid) from exc

    def install(self, package_id, path, manifest_sha, report_id, on_complete=None):
        with self.system.lock(self.workspace):
            tid, plan_sha = self.plan_install(package_id, path, manifest_sha, report_id)
            return self.apply_plan(tid, plan_sha, on_complete)

    def restore_target(self):
        pending = self.pending()
        if pending:
            directory, plan = self._load_contract(pending['transaction_id'], pending['plan_sha256'])
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
                    artifact_set_id=old['artifact_set_id'], manifest=old['manifest'], report_id=report_id,
                    entries=entries, target=old['target'], system_root=str(self.system.root), simulation=self.system.simulation,
                    state_before=self._store_snapshot(directory, 'original/state', self._state_snapshot()),
                    state_after=self._store_snapshot(directory, 'payload/state', self._read_snapshot(old_dir, old['state_before'])),
                    restored_transaction=old['transaction_id'], previous_state=old['previous_state'],
                    environment=compare_environment(self.system.observe(), old['target']), created_utc=now(), runtime_verified=False)
        return self._save_plan(directory, plan)

    def restore(self, report_id, on_complete=None):
        with self.system.lock(self.workspace):
            pending = self.pending()
            if pending:
                directory, plan = self._load_contract(pending['transaction_id'], pending['plan_sha256'])
                progress = load_json(directory / 'progress.json')
                require(progress['status'] in ('applying','recovery_required','planned','applied','restored','rolled_back'), 'Unsupported recovery journal status')
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
