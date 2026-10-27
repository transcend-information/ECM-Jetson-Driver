"""Read-only ordinary R20 transaction identity; no legacy restore or builder."""
import json
from pathlib import Path
import re
from .contracts import require, digest, document_digest, token

TYPED_KINDS = ('imx415-direct-to-formal-v1', 'verified-same-pair-reinstall-v1')
MIPI = {'ecm100-gc2093-mipi-cam1':'gc2093', 'ecm300-imx675-mipi-cam1':'nv_imx675', 'ecm400-imx415-mipi-cam1':'nv_imx415'}
NATIVE = {
    'ecm100-mipi-jp7':('gc2093','MIPI','7','gc2093_rear_custom.nito'),
    'ecm300-mipi-jp7':('nv_imx675','MIPI','7','imx675_rear_custom.nito'),
    'ecm400-mipi-jp7':('nv_imx415','MIPI','7','Transcend_front_IMX415.nito'),
    'ecm400-gmsl-max96724-jp6':('imx415_gmsl_96724','GMSL','6',None),
    'ecm400-gmsl-max96724-jp7':('imx415_gmsl_96724','GMSL','7','Transcend_IMX415_MAX96724.nito'),
    'ecm300-gmsl-max96724-jp6':('imx675_gmsl_96724_4ports','GMSL','6',None),
    'ecm300-gmsl-max96724-jp7':('imx675_gmsl_96724_4ports','GMSL','7',None),
}


def expected_package_manifest(manifest, native):
    # Pure serialization from R20 upstream_build.package_manifest and
    # build_driver.package_manifest; no source access or executable build import.
    if native:
        keys=('adapter','upstream_package','source_commit','source_sha256','source_state','profile_id','profile_sha256',
              'target_sha256','source_target','build_target','board_validation','gnu_build_id','crc_validation')
        result=dict(schema_version=1,**{key:manifest[key] for key in keys},runtime_verified=False)
        result.update({key:manifest[key] for key in ('source_repository','source_branch','known_issues','overlay_mode_preservation') if key in manifest})
        result['artifacts']={role:{k:v for k,v in item.items() if k!='path'} for role,item in manifest['artifacts'].items()}
        return result
    result=dict(schema_version=1, package_version=manifest['package_version'], driver_version=manifest['driver_version'],
                overlay_version=manifest['overlay_version'], profile_id=manifest['profile_id'], sensor=manifest['profile']['sensor'],
                target={k:manifest['target'][k] for k in ('id','l4t','kernel')},
                **{k:manifest[k] for k in ('profile_sha256','target_sha256','source_sha256','source_commit','source_state','gnu_build_id')},
                artifacts={role:{k:item[k] for k in ('relative_path','sha256','bytes')} for role,item in manifest['artifacts'].items()}, runtime_verified=False)
    if 'sensor_info' in manifest:result['sensor_info']=manifest['sensor_info']
    return result


def ordinary_entries(workspace, planpath, state, plan):
    tid=token(plan.get('transaction_id'), 'legacy transaction ID')
    expected_directory=Path(workspace)/'backups'/tid
    require(planpath == expected_directory/'plan.json' and Path(plan.get('directory','')) == expected_directory,
            'Required legacy plan/directory identity mismatch')
    require(plan.get('schema_version')==2 and plan.get('status')=='applied' and state.get('transaction_id')==tid and
            state.get('profile_id')==plan.get('profile_id') and plan.get('label')=='ECM-'+tid, 'Required legacy transaction/state identity mismatch')
    require(not plan.get('transition'), 'Typed plan requires its original recovery adapter')
    manifest=plan.get('manifest',{});profile=manifest.get('profile',{});target=plan.get('target',{})
    require(manifest.get('success') is True and manifest.get('profile_id')==plan['profile_id']==profile.get('id') and
            manifest.get('target')==target and plan.get('target_id')==target.get('id'), 'Required legacy profile/target pairing mismatch')
    kernel=token(target.get('kernel'),'legacy kernel')
    require(kernel!='UNCONFIGURED', 'Legacy target kernel unconfigured')
    native=bool(manifest.get('adapter') or profile.get('upstream_package'))
    primary='isp';modules={};tuning={}
    if native:
        package=profile.get('upstream_package')
        require(manifest.get('adapter')=='upstream-native-v1' and package in NATIVE and manifest.get('upstream_package')==package,
                'Required native package identity mismatch')
        module,connection,family,nito=NATIVE[package]
        four=package.startswith('ecm300-gmsl-')
        require(profile.get('connection')==connection and profile.get('port')==('ABCD' if four else 'CAM1' if connection=='MIPI' else 'A') and
                target.get('target_kind')=='native-provisioned' and str(target.get('jetpack_family'))==family, 'Required native profile/target family mismatch')
        if four:
            require(profile['id']=='ecm300-imx675-gmsl-max96724-abcd-jp'+family and
                    [r.get('port') for r in profile.get('capture_routes',[])]==list('ABCD'), 'Required native four-port identity mismatch')
        elif connection=='GMSL':require(type(profile.get('vc_id')) is int and profile['vc_id']==0,'Required native GMSL VC mismatch')
        modules={'ko':module}
        if connection=='GMSL':modules.update(ko_max96717='max96717',ko_max96724='max96724')
        if four and family=='7':
            tuning={kind+'_'+str(address):str(address)+'_IMX675_MAX96724.'+kind for address in (30,31,32,22) for kind in ('isp','nito')}
            primary='isp_30'
        else:
            tuning={'isp':'Transcend_IMX675_MAX96724.isp' if four else 'camera_overrides.isp' if connection=='MIPI' else 'Transcend_IMX415_MAX96724.isp'}
            if nito:tuning['nito']=nito
        require('package_version' in manifest and manifest.get('prebuilt_artifacts_used') is False and
                manifest.get('board_validation')=='NOT_TESTED_BY_THIS_RELEASE', 'Required native source/evidence identity mismatch')
        require(re.fullmatch(r'[0-9a-f]{40,64}',manifest.get('source_commit','')), 'Required native full source commit missing')
    else:
        module=MIPI.get(plan['profile_id'])
        require(module and profile.get('connection')=='MIPI' and profile.get('port')=='CAM1' and kernel=='5.15.148-tegra' and
                target.get('target_kind')!='native-provisioned', 'Required ordinary MIPI profile/target mismatch')
        modules={'ko':module};tuning={'isp':'camera_overrides.isp'}
    require(plan.get('module_name')==profile.get('module_name')==module, 'Required legacy module identity mismatch')
    expected={role:'/lib/modules/'+kernel+'/updates/drivers/media/i2c/'+name+'.ko' for role,name in modules.items()}
    expected['dtbo']='/boot/ecm-'+tid+'.dtbo'
    expected.update({role:'/var/nvidia/nvcam/settings/'+name for role,name in tuning.items()})
    expected['extlinux']='/boot/extlinux/extlinux.conf'
    entries=plan.get('entries',[])
    require(isinstance(entries,list) and len(entries)==len(expected) and len({e.get('role') for e in entries})==len(expected), 'Required legacy role set missing or duplicated')
    byrole={e['role']:e for e in entries}
    require(set(byrole)==set(expected) and len({e.get('destination') for e in entries})==len(expected), 'Required legacy role/destination uniqueness mismatch')
    require({role:e.get('destination') for role,e in byrole.items()}==expected, 'Required legacy role/destination allowlist mismatch')
    artifacts=manifest.get('artifacts',{})
    require(set(artifacts)==set(expected)-{'extlinux'}, 'Required legacy manifest artifact role set mismatch')
    for role,e in byrole.items():
        require(re.fullmatch(r'[0-9a-f]{64}',e.get('sha256','')), 'Required legacy artifact hash missing')
        if role=='extlinux':continue
        item=artifacts[role]
        require(item.get('sha256')==e['sha256'] and type(item.get('bytes')) is int and item['bytes']>0,'Required legacy manifest/entry artifact mismatch')
        if native:
            if role in modules:
                require(item.get('kind')=='module' and item.get('module_name')==modules[role] and item.get('destination')==expected[role] and
                        re.fullmatch(r'[0-9a-f]{16,128}',item.get('gnu_build_id','')) and item.get('crc_validation',{}).get('passed') is True and
                        item.get('vermagic','').split()[:1]==[kernel], 'Required native module identity/evidence mismatch: '+role)
            elif role=='dtbo':
                require(item.get('kind')=='overlay' and re.fullmatch(r'/boot/[A-Za-z0-9_.-]+\.dtbo',item.get('destination','')), 'Required native overlay contract mismatch')
            else:
                require(item.get('kind')==role.split('_')[0] and item.get('destination')==expected[role], 'Required native tuning contract mismatch: '+role)
    require(manifest.get('crc_validation',{}).get('passed') is True and re.fullmatch(r'[0-9a-f]{16,128}',manifest.get('gnu_build_id','')), 'Required legacy build identity missing')
    if native:
        require(manifest['gnu_build_id']==artifacts['ko']['gnu_build_id'] and manifest['crc_validation']==artifacts['ko']['crc_validation'], 'Required primary module evidence mismatch')
    require(state.get('dtbo_path')==expected['dtbo'], 'Required dtbo_path must identify the dtbo role')
    for role in ('ko','dtbo','isp'):
        require(state.get(role+'_sha256')==byrole[primary if role=='isp' else role]['sha256'], 'Required legacy state hash mismatch: '+role)
    if primary!='isp' or 'primary_isp_role' in state:
        require(state.get('primary_isp_role')==primary,'Required primary_isp_role mismatch')
    if native:
        require(state.get('artifact_hashes')=={role:byrole[role]['sha256'] for role in artifacts}, 'Required native artifact_hashes mismatch')
        require(state.get('assets')=={role:expected[role] for role in artifacts}, 'Required native assets mismatch')
        require(state.get('module_build_ids')=={name:artifacts[role]['gnu_build_id'] for role,name in modules.items()}, 'Required native module_build_ids mismatch')
    if 'package_version' in manifest:
        require(state.get('package_version')==manifest['package_version'],'Required legacy package version mismatch')
        if not native:require(isinstance(manifest['package_version'],str) and re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?',manifest['package_version']),'Invalid legacy package version')
        text=manifest.get('package_manifest_json')
        require(isinstance(text,str) and digest(text.encode())==manifest.get('package_manifest_sha256'), 'Required package manifest digest mismatch')
        require(document_digest(profile)==manifest.get('profile_sha256') and document_digest(target)==manifest.get('target_sha256'), 'Required legacy profile/target digest mismatch')
        require(json.loads(text.lstrip('\ufeff'))==expected_package_manifest(manifest,native), 'Required package manifest pairing mismatch')
    else:
        require('package_manifest_json' not in manifest and 'package_manifest_sha256' not in manifest,'Required legacy package version missing')
    return [dict(destination=e['destination'],sha256=e['sha256'],bytes=artifacts[e['role']]['bytes'] if e['role']!='extlinux' else None) for e in entries], expected['dtbo']
