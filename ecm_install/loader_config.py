"""Bounded, read-only Linux loader configuration observations.

This API never returns a destination snapshot and never authorizes a write.
All traversal uses pinned directory descriptors; only protected system links
and bounded regular files are accepted. Installation I/O remains unchanged.
"""
import os
import posixpath
import stat
from .contracts import digest
from .diagnostics import DiagnosticError

LIMIT = 256 * 1024
BASES = ('/etc', '/run', '/usr/local/lib', '/usr/lib', '/lib')
CONFIG_DIRS = tuple(base + '/' + kind for base in BASES
                   for kind in ('modules-load.d', 'modprobe.d'))


def _entry(path):
    return (path == '/etc/modules' or
            any(posixpath.dirname(path) == folder and path.endswith('.conf')
                for folder in CONFIG_DIRS))


def _target(path):
    # Package-owned auxiliary configuration may be outside modprobe.d itself.
    return (_entry(path) or any(path.startswith(base + '/') and path.endswith('.conf')
                               for base in ('/lib', '/usr/lib', '/usr/local/lib')))


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read(root, logical):
    """Observe one selected logical config, retaining its parsing semantics."""
    observed = logical
    descriptors = []
    records = []
    links = []

    def fail(reason):
        raise DiagnosticError('Cannot inspect loader configuration ' + logical + ': ' + reason,
                              'MODULE_RENAME_CHECK_FAILED', path=logical,
                              observed_path=observed, reason=reason)

    def protected(info, link=False):
        if info.st_uid != 0 or (not link and info.st_mode & 0o022):
            fail('Path is not root-owned and protected from group/other writes')

    def opened(path, flags, parent=None):
        fd = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        descriptors.append(fd)
        return fd

    def unchanged():
        for parent, name, expected, link in records:
            now = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if _signature(now) != expected:
                fail('Configuration path changed during observation')
            if link is not None and os.readlink(name, dir_fd=parent) != link:
                fail('Configuration link changed during observation')

    try:
        if (not isinstance(logical, str) or not _entry(logical) or
                posixpath.normpath(logical) != logical or '\\' in logical):
            fail('Path is outside the loader configuration inventory')
        rootfd = opened(str(root), os.O_RDONLY | os.O_DIRECTORY)
        protected(os.fstat(rootfd))
        pending = logical
        visited = set()
        while True:
            if pending in visited or len(links) > 8:
                fail('Configuration link loop or depth limit exceeded')
            visited.add(pending)
            if not _target(pending):
                fail('Link target is outside the permitted system configuration paths')
            parts = pending.lstrip('/').split('/')
            if len(parts) > 32:
                fail('Configuration path depth limit exceeded')
            parent = rootfd
            restart = False
            for index, name in enumerate(parts):
                observed = '/' + '/'.join(parts[:index + 1])
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                islink = stat.S_ISLNK(info.st_mode)
                protected(info, islink)
                target = os.readlink(name, dir_fd=parent) if islink else None
                records.append((parent, name, _signature(info), target))
                if islink:
                    if len(links) >= 8:
                        fail('Configuration link depth limit exceeded')
                    leaf = index == len(parts) - 1
                    if not leaf and not (observed == '/lib' and target in ('usr/lib', '/usr/lib')):
                        fail('Linked configuration directory is not the recognized /lib alias')
                    links.append({'path': observed, 'target': target,
                                  'device': info.st_dev, 'inode': info.st_ino,
                                  'uid': info.st_uid, 'gid': info.st_gid})
                    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(observed), target))
                    if not leaf:
                        resolved = posixpath.join(resolved, *parts[index + 1:])
                    if leaf and resolved == '/dev/null':
                        unchanged()
                        return {'path': logical, 'resolved_path': resolved, 'links': links,
                                'masked': True, 'sha256': None, 'body': None}
                    pending = resolved
                    restart = True
                    break
                if index < len(parts) - 1:
                    if not stat.S_ISDIR(info.st_mode):
                        fail('Configuration parent is not a directory')
                    child = opened(name, os.O_RDONLY | os.O_DIRECTORY, parent)
                    if _signature(os.fstat(child)) != _signature(info):
                        fail('Configuration directory changed while opening')
                    parent = child
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > LIMIT:
                    fail('Configuration requires a bounded regular single-link file')
                fd = opened(name, os.O_RDONLY | os.O_NONBLOCK, parent)
                if _signature(os.fstat(fd)) != _signature(info):
                    fail('Configuration file changed while opening')
                chunks = []
                size = 0
                while size <= LIMIT:
                    chunk = os.read(fd, min(65536, LIMIT + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                if size > LIMIT or _signature(os.fstat(fd)) != _signature(info):
                    fail('Configuration grew or changed while reading')
                unchanged()
                body = b''.join(chunks)
                return {'path': logical, 'resolved_path': pending, 'links': links,
                        'masked': False, 'sha256': digest(body), 'body': body,
                        'uid': info.st_uid, 'gid': info.st_gid,
                        'mode': stat.S_IMODE(info.st_mode), 'bytes': len(body),
                        'device': info.st_dev, 'inode': info.st_ino}
            if not restart:
                fail('Configuration did not resolve to a file')
    except OSError as exc:
        fail(type(exc).__name__ + ': ' + str(exc))
    finally:
        for fd in reversed(descriptors):
            os.close(fd)
