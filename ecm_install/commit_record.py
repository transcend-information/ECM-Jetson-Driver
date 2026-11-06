"""Read the final atomic commit proof; old transaction formats remain readable."""
from .contracts import digest,load_json,safe_path

PROTOCOL='ATOMIC_ACTIVE_TO_COMMITTED_V1'


def committed(directory,plan):
    if not plan.get('commit_protocol'):
        return True
    if plan['commit_protocol']!=PROTOCOL:
        return False
    try:
        record=load_json(safe_path(directory,'committed.json'))
        return (record.get('transaction_id')==plan['transaction_id'] and
                record.get('plan_sha256')==digest(safe_path(directory,'plan.json').read_bytes()))
    except (ValueError,OSError,KeyError):
        return False
