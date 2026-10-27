"""Persistent installation reports, separate from pre-existing image/history logs."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid
from . import VERSION, r20_storage as S
from .contracts import load_json, safe_path, digest
from .system import file_lock


class Report:
    def __init__(self, workspace, operation, system):
        self.workspace, self.system = Path(workspace), system
        self.workspace.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime('%Y%m%d')
        with file_lock(safe_path(self.workspace, 'state/external-report.lock')):
            directory = safe_path(self.workspace, 'state/external-runlog')
            directory.mkdir(parents=True, exist_ok=True)
            origin_path = safe_path(directory, 'origin.json')
            if not origin_path.exists():
                S.atomic_json(origin_path, {'origin_instance_id':str(uuid.uuid4())})
            self.origin = load_json(origin_path)['origin_instance_id']
            uuid.UUID(self.origin)
            counter = safe_path(directory, day + '.json')
            previous = load_json(counter)['last_sequence'] if counter.exists() else 0
            if type(previous) is not int or previous < 0:
                raise ValueError('Cannot allocate required report sequence: invalid external counter')
            sequence = previous + 1
            S.atomic_json(counter, {'last_sequence':sequence})
        self.id = day + '-' + str(sequence).zfill(4) + '-' + self.origin[:8]
        self.directory = safe_path(self.workspace, 'logs/external/' + self.id)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.context = dict(report_id=self.id, origin_instance_id=self.origin, sequence=sequence,
                            operation=operation, candidate=VERSION, installer_version=VERSION,
                            qualification='LOCAL_SIMULATION' if system.simulation else 'NOT_QUALIFIED', runtime_verified=False)
        S.atomic_json(self.directory / 'context.json', self.context)
        self.event('report-start')

    def event(self, action, **fields):
        self.system.checkpoint('report:' + action)
        path = safe_path(self.directory, 'events.jsonl')
        with path.open('ab') as stream:
            stream.write((json.dumps(dict(report_id=self.id, timestamp_utc=datetime.now(timezone.utc).isoformat(), installer_version=VERSION, action=action, **fields), allow_nan=False) + '\n').encode())
            stream.flush(); os.fsync(stream.fileno())

    def finish(self, status, message, **fields):
        self.event('report-result', status=status, message=message, **fields)
        result = dict(self.context, status=status, message=message, **fields)
        self.system.checkpoint('report:result-save')
        S.atomic_json(self.directory / 'result.json', result)
        S._atomic_bytes(self.directory / 'summary.txt', (json.dumps(result, indent=2) + '\n').encode(), 0o600,
                        os.geteuid() if hasattr(os,'geteuid') else 0, os.getegid() if hasattr(os,'getegid') else 0)
        return result


def reports(workspace):
    root = safe_path(workspace, 'logs/external')
    if not root.exists():
        return [], []
    result, warnings = [], []
    for directory in sorted(root.iterdir(), reverse=True)[:100]:
        try:
            record = load_json(safe_path(directory, 'result.json'))
            result.append((directory, record))
        except (ValueError, OSError) as exc:
            warnings.append('Warning: unrelated old report unavailable: ' + directory.name + ': ' + str(exc))
    return result, warnings


def export_report(workspace, directory):
    directory = Path(directory)
    root = safe_path(workspace, 'logs/external')
    if directory.parent != root:
        raise ValueError('Export only selected installer reports')
    body = S._safe_file(safe_path(directory, 'summary.txt'), directory)
    destination = safe_path(workspace, 'reports/' + directory.name + '-install-summary.txt')
    S._atomic_bytes(destination, body, 0o600, os.geteuid() if hasattr(os,'geteuid') else 0, os.getegid() if hasattr(os,'getegid') else 0)
    return str(destination), digest(body)
