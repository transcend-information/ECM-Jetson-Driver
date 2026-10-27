"""System I/O boundary. This unqualified local candidate never mutates a real host."""
from contextlib import contextmanager
import os
from pathlib import Path
import platform
import re
from .contracts import safe_path
from . import r20_storage as S


@contextmanager
def file_lock(path):
    """R20 nonblocking OS lock semantics; crash releases the OS lock."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_path(path.parent, path.name)
    stream = path.open('a+b')
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
                result['jetpack'] = {'36':'6', '38':'7'}.get(match[1])
        except (OSError, ValueError):
            pass
        try:
            result['board'] = self.path('/sys/firmware/devicetree/base/model').read_bytes().rstrip(b'\0').decode()
        except (OSError, ValueError):
            pass
        return result

    def mutation_preflight(self):
        raise RuntimeError('Native installation is not enabled in this local candidate; approved artifact integration and native review are pending')

    def command(self, argv):
        raise RuntimeError('Native system commands are not enabled in this local candidate')

    @contextmanager
    def lock(self, workspace):
        self.mutation_preflight()
        with file_lock(self.path('/run/lock/ecm-camera-manager-install.lock')):
            with file_lock(safe_path(workspace, 'state/camera-operation.lock')):
                yield
