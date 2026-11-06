"""Display saved installation identity without claiming it is currently loaded."""
from pathlib import Path
from .diagnostics import error_detail
from .contracts import load_json,digest,require,token
from .setup_display import resolution

def recorded_identity(engine):
    try:
        state=engine.state()
        pending=engine.pending()
        if pending or (state and state.get('status')=='partial'):
            record=pending or state
            detail=record.get('diagnostic',{})
            return dict(status='PARTIAL',label='Installation incomplete; repair required',
                        package_id=record.get('package_id',(state or {}).get('package_id')),
                        transaction_id=record.get('transaction_id'),incomplete_stage=detail.get('stage',(state or {}).get('incomplete_stage')),
                        runtime_verified=False,reboot_required=False)
        if state is None:return {'status':'NONE','label':'No managed installation recorded'}
        if state.get('schema')!='ecm-external-state-v1':
            engine._previous_entries(state)
            # Display only a legacy record already verified by its owning contract.
            from .legacy import MIPI
            from .catalog import PACKAGES
            plan = load_json(engine.wpath(Path(state['plan_path']).relative_to(engine.workspace).as_posix()))
            manifest = plan['manifest']; profile = manifest['profile']
            key = profile.get('upstream_package')
            if key is None and profile['id'] in MIPI:
                key = profile['id'].split('-')[0] + '-mipi-jp6'
            item = PACKAGES.get(key, {})
            camera = {k: profile.get(k) or item.get(k) for k in ('product','sensor','connection','port','serdes')}
            return {'status':'VERIFIED_LEGACY_RECORD','label':state.get('profile_id','Legacy setup'),
                    'profile_id':profile['id'],'package_id':key,'camera':camera,
                    'max_resolution':resolution(manifest),
                    'transaction_id':state['transaction_id'],'runtime_verified':False}
        tid=token(state['transaction_id'],'transaction ID')
        directory=engine.wpath('backups/'+tid);path=directory/'plan.json'
        plan=load_json(path)
        require(digest(path.read_bytes())==state['plan_sha256'] and plan.get('schema')=='ecm-install-transaction-v1' and
            plan.get('transaction_id')==tid and plan.get('package_id')==state['package_id'] and
            plan.get('artifact_set_id')==state['artifact_set_id'] and plan.get('overlay')==state.get('overlay') and
            load_json(directory/'progress.json').get('status')=='applied',
            'Saved identity does not match its transaction','TRANSACTION_INCOMPLETE')
        manifest=plan['manifest']
        require(manifest['artifact_set_id']==plan['artifact_set_id'] and manifest['package_id']==plan['package_id'],
            'Saved manifest identity differs','TRANSACTION_INCOMPLETE')
        from .catalog import PACKAGES
        item = PACKAGES.get(plan['package_id'], {})
        camera = {k: manifest.get(k) or item.get(k) for k in ('product','sensor','connection','port','serdes')}
        saved = engine.verify_legacy_attachment(directory, plan, read_display=True)
        return dict(camera=camera, max_resolution=resolution(manifest, saved), status='VERIFIED_RECORD',label=state['package_id'],package_id=state['package_id'],
            transaction_id=state['transaction_id'],artifact_set_id=plan['artifact_set_id'],
            manifest_sha256=plan['manifest_sha256'],driver_version=manifest.get('driver_version'),
            target_kernel=plan['target']['kernel'],runtime_verified=False)
    except (KeyError,ValueError,RuntimeError,OSError) as exc:
        return {'status':'UNKNOWN','label':'Recorded identity not verified','diagnostic':error_detail(exc,'transaction_precheck'),'runtime_verified':False}
