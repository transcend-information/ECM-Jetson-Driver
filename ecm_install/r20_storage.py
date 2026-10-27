"""Pure R20 file/backup primitives, extracted without builder or capture imports."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

def sha(path):
    path = Path(path)
    return _digest(path.read_bytes()) if path.is_file() else None

def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _digest(body):
    return hashlib.sha256(body).hexdigest()


def _sync_dir(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _atomic_bytes(dest, body, mode=0o644, uid=0, gid=0):
    dest = Path(dest)
    require(not dest.is_symlink(), 'Symlink destination refused: ' + str(dest))
    require(not dest.exists() or dest.is_file(), 'Destination is not a regular file: ' + str(dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.ecm-', dir=dest.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        if hasattr(os, 'chown'):
            os.chown(temporary, uid, gid)
        os.replace(temporary, dest)
        _sync_dir(dest.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def atomic_json(path, data):
    body = (json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    # Root apply must leave existing user-owned metadata readable by the normal UI.
    owner = Path(path)
    while not owner.exists():
        owner = owner.parent
    info = owner.stat()
    uid, gid = info.st_uid, info.st_gid
    _atomic_bytes(path, body, 0o600, uid, gid)


def _remove(path):
    path = Path(path)
    require(not path.is_symlink(), 'Symlink removal refused: ' + str(path))
    path.unlink(missing_ok=True)
    if path.parent.exists():
        _sync_dir(path.parent)


def _safe_file(path, parent=None):
    path = Path(path)
    require(not path.is_symlink() and path.is_file(), 'Missing or linked regular file: ' + str(path))
    if parent is not None:
        parent = Path(parent).resolve()
        require(path.resolve().is_relative_to(parent), 'File outside transaction: ' + str(path))
        candidate = path
        while candidate.resolve() != parent:
            require(not candidate.is_symlink(), 'Linked transaction path: ' + str(candidate))
            candidate = candidate.parent
            require(candidate != candidate.parent or candidate.resolve() == parent, 'Invalid transaction path')
    return path.read_bytes()


def _snapshot(path):
    path = Path(path)
    require(not path.is_symlink(), 'Refuse linked destination: ' + str(path))
    if not path.exists():
        return {'existed': False, 'sha256': None, 'mode': 0o644, 'uid': 0, 'gid': 0, 'body': None}
    body = _safe_file(path)
    info = path.stat()
    return {'existed': True, 'sha256': _digest(body), 'mode': info.st_mode & 0o777,
            'uid': info.st_uid, 'gid': info.st_gid, 'body': body}


def _put_snapshot(path, snapshot):
    if snapshot['existed']:
        _atomic_bytes(path, snapshot['body'], snapshot['mode'], snapshot['uid'], snapshot['gid'])
    else:
        _remove(path)
    require(sha(path) == snapshot['sha256'], 'Recovery verification failed: ' + str(path))


def _active_entry(text):
    require('\0' not in text, 'Invalid extlinux content')
    lines = text.splitlines()
    defaults = [m[1] for line in lines if (m := re.fullmatch(r'\s*DEFAULT\s+(\S+)\s*', line, re.I))]
    require(len(defaults) == 1, 'ECM-BOOT-AMBIGUOUS: expected one next-boot DEFAULT')
    blocks, current = {}, None
    for line in lines:
        match = re.fullmatch(r'\s*LABEL\s+(\S+)\s*', line, re.I)
        if match:
            name = match[1]
            require(name.casefold() not in {n.casefold() for n in blocks}, 'ECM-BOOT-AMBIGUOUS: duplicate LABEL')
            blocks[name] = []
            current = name
        elif current is not None:
            blocks[current].append(line)
    require(defaults[0] in blocks, 'ECM-BOOT-AMBIGUOUS: cannot locate default entry')
    fields = {}
    for line in blocks[defaults[0]]:
        match = re.match(r'^\s*([A-Za-z]+)\s+(.+?)\s*$', line)
        if match:
            fields.setdefault(match[1].upper(), []).append(match[2])
    return defaults[0], fields, blocks
