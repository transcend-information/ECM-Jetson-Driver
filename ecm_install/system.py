"""System I/O boundary. This unqualified local candidate never mutates a real host."""
from contextlib import contextmanager
import os
from pathlib import Path
import platform
import re
import stat
from .contracts import safe_path
from . import r20_storage as S
from .diagnostics import NativeWritesDisabled


@contextmanager
def file_lock(path, owner_uid=None, *, read_only=False, public_read=False):
    """R20 nonblocking OS lock semantics; crash releases the OS lock."""
    path = Path(path)
    if not read_only:path.parent.mkdir(parents=True, exist_ok=True)
    safe_path(path.parent, path.name)
    flags=os.O_RDONLY if read_only else os.O_CREAT|os.O_RDWR
    fd=os.open(path,flags|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_CLOEXEC',0),0o644 if public_read else 0o600)
    info=os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or (owner_uid is not None and (info.st_uid!=owner_uid or info.st_mode&0o022)):
        os.close(fd);raise ValueError('Operation lock must be a regular single-link file')
    stream = os.fdopen(fd,'rb' if read_only else 'r+b')
    try:
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                stream.write(b'0'); stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if public_read:
                if os.geteuid()!=0 or info.st_uid!=0:raise PermissionError('Only the root lock owner may publish read access')
                os.fchmod(stream.fileno(),0o644)
    except OSError as error:
        stream.close()
        raise RuntimeError('Required operation lock is busy: ' + str(path)) from error
    try:
        yield
    finally:
        stream.close()


class SystemIO:
    simulation = False
    def __init__(self, root='/'):
        self.root = Path(root).absolute()

    def path(self, logical):
        if not logical.startswith('/'):
            raise ValueError('Absolute system destination required')
        return safe_path(self.root, logical[1:])

    def checkpoint(self, stage):
        pass

    def boot_identity(self):
        from uuid import UUID
        source = '/proc/sys/kernel/random/boot_id'
        try:
            path = self.path(source)
            with path.open('rb') as stream: body = stream.read(129)
            value = str(UUID(body.decode('ascii').strip()))
            return {'status':'AVAILABLE','value':value,'source':source}
        except PermissionError as exc: status = 'PERMISSION_DENIED'; reason = str(exc)
        except FileNotFoundError as exc: status = 'MISSING'; reason = str(exc)
        except (OSError,ValueError,UnicodeError) as exc: status = 'UNAVAILABLE'; reason = str(exc)
        return {'status':status,'value':None,'source':source,'reason':reason}

    def snapshot(self, logical):
        self.checkpoint('snapshot:' + logical)
        return S._snapshot(self.path(logical))

    def put(self, logical, snapshot):
        self.checkpoint('write:' + logical)
        S._put_snapshot(self.path(logical), snapshot)

    def observe(self):
        result = {'arch':platform.machine(), 'kernel':platform.release(), 'l4t':None, 'board':None, 'jetpack':None}
        try:
            content = self.path('/etc/nv_tegra_release').read_text()
            match = re.search(r'R(\d+).*REVISION:\s*([\d.]+)', content)
            if match:
                result['l4t'] = 'r' + match[1] + '.' + match[2]
                result['jetpack'] = {'36':'6', '38':'7', '39':'7'}.get(match[1])
        except (OSError, ValueError):
            pass
        try:
            result['board'] = self.path('/sys/firmware/devicetree/base/model').read_bytes().rstrip(b'\0').decode()
        except (OSError, ValueError):
            pass
        return result

    def home_observe(self):
        """Read configuration and registered nodes; never open a camera."""
        result = {'boot_configuration': None, 'power_mode': None,
                  'camera_device': 'Not verified', 'sources': {}, 'errors': []}
        compatible = '/sys/firmware/devicetree/base/compatible'
        try:
            raw = self.path(compatible).read_bytes().decode()
            if raw:
                result['boot_configuration'] = ('Super (Device Tree)' if re.search(
                    r'p3768-\d{4}\+p3767-\d{4}-super(?:\0|$)', raw) else 'Not identified as Super (Device Tree)')
                result['sources']['boot_configuration'] = compatible
        except (OSError, ValueError, UnicodeError) as exc:
            result['errors'].append({'source': compatible, 'error': type(exc).__name__})
        nodes = '/sys/class/video4linux'
        try:
            folder = self.path(nodes)
            names = sorted(p.name for p in folder.iterdir() if re.fullmatch(r'video\d+', p.name))
            result['camera_device'] = (str(len(names)) + ' video node(s); sensor not verified') if names else 'No registered video nodes'
            result['sources']['camera_device'] = {'path': nodes, 'nodes': names}
        except (OSError, ValueError) as exc:
            result['errors'].append({'source': nodes, 'error': type(exc).__name__})
        # This read-only vendor query is separate from the mutation command API.
        if not self.simulation and platform.system() == 'Linux' and self.root == Path('/'):
            import subprocess
            for command in ('/usr/sbin/nvpmodel', '/usr/bin/nvpmodel'):
                if not Path(command).is_file():
                    continue
                try:
                    query = subprocess.run([command, '-q'], capture_output=True, text=True, timeout=3, check=False)
                    match = re.search(r'NV Power Mode:\s*([^\r\n]+)', query.stdout)
                    result['sources']['power_mode'] = {'command': [command, '-q'], 'returncode': query.returncode}
                    if query.returncode == 0 and match:
                        result['power_mode'] = match[1].strip()
                except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                    result['errors'].append({'source': command + ' -q', 'error': type(exc).__name__})
                break
        return result

    def module_aliases(self,kernel):
        from .jp6_names import aliases_from_text
        path=self.path('/lib/modules/'+kernel+'/modules.alias')
        text=path.read_text() if path.exists() else ''
        return {'scope':'SAVED_INDEX_OR_EXPLICIT_TEST_FILESYSTEM','aliases':aliases_from_text(text)}

    def mutation_preflight(self):
        raise NativeWritesDisabled('Native installation is not enabled in this local candidate; approved artifact integration and native review are pending')

    def package_owners(self, logical):
        if self.simulation:
            return []  # Explicit fixture filesystem has no package database.
        raise RuntimeError('Package ownership observation requires native backend')

    def bind_transaction(self, directory, plan):
        pass

    def selection_matches(self, actual, expected):
        return actual == expected

    def command(self, argv):
        raise RuntimeError('Native system commands are not enabled in this local candidate')

    @contextmanager
    def lock(self, workspace):
        self.mutation_preflight()
        with file_lock(self.path('/run/lock/ecm-camera-manager-install.lock')):
            with file_lock(safe_path(workspace, 'state/camera-operation.lock')):
                yield
