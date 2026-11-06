"""Minimal public payload contract; it makes no internal build-proof claim."""
from .contracts import require

SCHEMA='ecm-public-prebuilt-v1'
AUDIENCE='PUBLIC_DISTRIBUTION_CANDIDATE'
REQUIRED={'schema','audience','package_id','product','sensor','connection','jetpack',
    'artifact_set_id','driver_version','overlay_version','release_approved','runtime_verified',
    'target','artifacts','system_module_dependencies','mode_metadata','installation_policy'}
OPTIONAL={'serdes','port'}
ARTIFACT_FIELDS={'path','bytes','sha256','kind','install_name','module_name','module_version',
    'depends','vermagic','embedded_identity'}
ORIGINAL_ISP={'ecm300-gmsl-max96724-jp6','ecm400-gmsl-max96724-jp6'}

def validate(manifest):
    require(REQUIRED<=set(manifest)<=REQUIRED|OPTIONAL,'Unexpected/missing public manifest fields')
    require(manifest['schema']==SCHEMA and manifest['audience']==AUDIENCE,'Invalid public payload contract')
    require({'kernel','arch','l4t','board'}<=set(manifest['target'])<={'kernel','arch','l4t','board','abi_reference'},'Unexpected public target fields')
    for item in manifest['artifacts'].values():
        require(set(item)<=ARTIFACT_FIELDS,'Internal receipt fields are not public payload metadata')
    policy='PACKAGE_FILENAME' if manifest['package_id'] in ORIGINAL_ISP else 'STANDARD'
    expected={'isp_destination':policy}
    if 'module_naming' in manifest['installation_policy']:
        from . import jp6_names
        require(jp6_names.enabled(manifest),'Unsupported module naming policy')
        jp6_names.validate(manifest)
        expected.update(module_naming=jp6_names.POLICY,overlay_destination='PACKAGE_FILENAME')
    require(manifest['installation_policy']==expected,'Public installation destination policy differs')
def evidence(manifest):
    return dict(artifact_audience=manifest['audience'],manifest_schema=manifest['schema'],
        artifact_set_id=manifest['artifact_set_id'],driver_version=manifest.get('driver_version'),
        overlay_version=manifest.get('overlay_version'),target=manifest['target'],
        artifacts={role:{k:item[k] for k in ('path','kind','install_name','bytes','sha256')} for role,item in manifest['artifacts'].items()},
        mode_metadata=manifest['mode_metadata'],verification_scope='PUBLIC_PAYLOAD_IDENTITY_AND_INTEGRITY',
        build_provenance_in_distribution=False,release_approved=False,native_install_verified=False,hardware_verified=False)
