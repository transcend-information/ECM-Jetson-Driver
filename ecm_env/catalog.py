"""Pinned dependency metadata integrity; no archive or network requirement."""
import hashlib,json,re
from pathlib import Path
from urllib.parse import urlsplit

METADATA=('Packages','manifest.json','packages.tsv','prerequisites-driver.tsv','seeds-driver.txt')
PACKAGE=Path(__file__).resolve().parents[1]
def digest(body):return hashlib.sha256(body).hexdigest()
def inspect(profile,package=PACKAGE):
    root=Path(package).resolve();directory=root/profile['bundle']
    if directory.resolve()!=directory or not directory.is_relative_to(root):raise ValueError('Unsafe dependency metadata path')
    sums=(directory/'SHA256SUMS').read_bytes()
    if (directory/'SHA256SUMS').is_symlink() or digest(sums)!=profile['sums']:raise ValueError('Dependency metadata inventory differs')
    rows={}
    for line in sums.decode('ascii').splitlines():
        match=re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9.-]+)',line)
        if not match or match[2] in rows:raise ValueError('Invalid metadata checksum inventory')
        rows[match[2]]=match[1]
    if set(rows)!=set(METADATA):raise ValueError('Incomplete metadata inventory')
    bodies={}
    for name,expected in rows.items():
        p=directory/name
        if p.is_symlink() or not p.is_file():raise ValueError('Missing or unsafe metadata: '+name)
        bodies[name]=p.read_bytes()
        if digest(bodies[name])!=expected:raise ValueError('Corrupt dependency metadata: '+name)
    data=json.loads(bodies['manifest.json'])
    if data['schema']!='ecm-online-runtime-v1':raise ValueError('Unsupported online catalog')
    target=data['target']
    if any(target[k]!=profile[v] for k,v in [('os','os_id'),('version','ubuntu'),('architecture','architecture'),('l4t','l4t'),('nvidia_version','nvidia_version')]):raise ValueError('Catalog does not match observed profile')
    packages=data['packages'];names=set();tsv=[]
    for row in packages:
        name=row['package'];url=urlsplit(row['url'])
        if name in names or not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*',name):raise ValueError('Invalid or duplicate package')
        names.add(name)
        if url.scheme!='https' or url.hostname not in ('ports.ubuntu.com','repo.download.nvidia.com') or url.username or url.password or url.port or url.query or url.fragment:raise ValueError('Untrusted package URL')
        prefix='/ubuntu-ports/pool/' if url.hostname=='ports.ubuntu.com' else '/jetson/'
        if not url.path.startswith(prefix) or '..' in url.path.split('/') or not url.path.endswith('.deb'):raise ValueError('Invalid package URL path')
        if row['architecture'] not in ('arm64','all') or not re.fullmatch(r'[0-9a-f]{64}',row['sha256']) or not isinstance(row['bytes'],int) or row['bytes']<=0:raise ValueError('Invalid package identity')
        if not re.fullmatch(r'debs/[A-Za-z0-9+._%:~-]+\.deb',row['filename']):raise ValueError('Invalid package filename')
        tsv.append('\t'.join(str(row[k]) for k in ('package','version','architecture','sha256','filename','bytes','url')))
    if bodies['packages.tsv'].decode().splitlines()!=tsv:raise ValueError('Manifest/TSV mismatch')
    for group in ('driver',):
        if bodies['seeds-'+group+'.txt'].decode().splitlines()!=data['seed_groups'][group] or not set(data['seed_groups'][group])<=names:raise ValueError('Feature seed mismatch')
    return dict(status='Verified',complete=True,path=str(directory),profile=profile['id'],manifest_sha256=digest(bodies['manifest.json']),metadata_sha256=profile['sums'],package_count=len(packages),target=target,delivery='Online when missing; no archive required for readiness')
