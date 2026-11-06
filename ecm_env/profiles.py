"""Exact runtime targets; selection is based on observed host, never menu choice."""
from pathlib import Path

FIELDS=('id','os_id','ubuntu','architecture','l4t','nvidia_version','nvidia_packages','bundle','sums','apply')

def load_profiles():
    profiles={}
    for line in Path(__file__).with_name('profiles.tsv').read_text().splitlines():
        if not line or line.startswith('#'):continue
        values=line.split('\t')
        if len(values)!=len(FIELDS):raise ValueError('Invalid environment profile')
        row=dict(zip(FIELDS,values))
        if row['id'] in profiles:raise ValueError('Duplicate environment profile')
        profiles[row['id']]=row
    return profiles

PROFILES=load_profiles()

def select_observed(environment):
    for profile in PROFILES.values():
        if all(environment.get(key)==profile[key] for key in ('os_id','ubuntu','architecture','l4t')):
            return profile
    return None

def target_mismatch(observed, target_id):
    if target_id is None:return None
    if target_id not in PROFILES:return 'Selected target profile is unknown: '+str(target_id)
    if observed is None or observed['id']!=target_id:
        return 'Observed host does not match selected target profile: '+target_id
    return None
