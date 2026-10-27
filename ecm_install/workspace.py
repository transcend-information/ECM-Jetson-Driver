"""Bounded R20 workspace selection, with nonessential history treated as warnings."""
import os
from pathlib import Path
from .contracts import safe_path

MARKERS = ('state','logs','backups','build','reports','argus-helper','trials')


def inspect(path, *, explicit=False, tools_layout=False):
    path = Path(path).absolute()
    safe_path(path.parent, path.name)
    if not path.exists():
        return {'path':str(path), 'existing':False, 'warnings':[]}
    if not path.is_dir():
        raise ValueError('Workspace is not a directory: ' + str(path))
    names = {p.name for p in path.iterdir()}
    recognized = bool(names & set(MARKERS))
    if names and not recognized and not tools_layout:
        raise ValueError('Unknown workspace; specify a recognized workspace, not a new empty replacement: ' + str(path))
    warnings = []
    if names - set(MARKERS):
        warnings.append('Unrelated workspace entries preserved without inspection')
    # Do not parse every old log/state marker: only the operation's required chain is validated later.
    return {'path':str(path), 'existing':recognized, 'warnings':warnings}


def resolve_workspace(explicit=None, package_dir=None, home=None, cwd=None):
    cwd, home = Path(cwd or Path.cwd()), Path(home or Path.home())
    package = Path(package_dir).absolute()
    if explicit is not None:
        record = inspect(cwd / explicit, explicit=True)
        return record
    paths = [package / 'workspace', home / 'Desktop/ECM_Jetson_Dev', home / 'ECM_Camera_Workspace']
    tools = package.parent.parent if package.parent.name == 'tools' else None
    if tools is not None:
        paths.insert(0, tools)
    records = [inspect(path, tools_layout=path == tools) for path in dict.fromkeys(paths)]
    found = [record for record in records if record['existing']]
    if len(found) > 1:
        raise ValueError('Multiple existing workspaces; specify --workspace. No state was moved: ' + ', '.join(r['path'] for r in found))
    return found[0] if found else {'path':str(home / 'ECM_Camera_Workspace'), 'existing':False, 'warnings':[]}
