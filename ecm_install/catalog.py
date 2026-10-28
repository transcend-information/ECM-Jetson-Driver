"""Eight product contracts. Names and artifacts arrive through approved manifests."""
from pathlib import Path
import json

AWAITING = 'AWAITING_INTERNAL_FIXED_SOURCE_AND_ARTIFACT_HANDOFF'
PACKAGES = {}
for family in ('6', '7'):
    for product, sensor in (('ECM100', 'GC2093'), ('ECM300', 'IMX675'), ('ECM400', 'IMX415')):
        key = product.lower() + '-mipi-jp' + family
        roles = {'sensor_ko':'ko', 'overlay':'dtbo', 'isp':'isp'}
        if family == '7':
            roles['nito'] = 'nito'
        PACKAGES[key] = dict(id=key, product=product, sensor=sensor, connection='MIPI',
                             jetpack=family, port='CAM1', roles=roles, status=AWAITING)
    key = 'ecm300-gmsl-max96724-jp' + family
    roles = {'sensor_ko':'ko', 'serializer_ko':'ko', 'deserializer_ko':'ko', 'overlay':'dtbo'}
    if family == '6':
        roles['isp'] = 'isp'
    else:
        roles['isp'] = 'isp'
        roles['nito'] = 'nito'
    PACKAGES[key] = dict(id=key, product='ECM300G2F', sensor='IMX675', connection='GMSL',
                         jetpack=family, port='A-D', serdes='MAX96717/MAX96724', roles=roles, status=AWAITING)


class Catalog:
    def __init__(self, package_root):
        from .contracts import load_json
        self.root = Path(package_root)
        index = load_json(self.root / 'manifests/release-index.json')
        if index.get('schema') != 'ecm-release-index-v1':
            raise ValueError('Unsupported release index schema')
        self.records = index['packages']
        if set(self.records) != set(PACKAGES):
            raise ValueError('Release index must describe exactly eight packages')

    def manifest(self, package_id):
        record = self.records[package_id]
        if record['status'] == AWAITING:
            return None
        # Receiving a fixed manifest does not activate native writes or confer
        # runtime qualification. Reception status is read from this fixed index.
        from .contracts import safe_path, require
        import re
        require(record['status'] == 'FIXED_ARTIFACTS_RECEIVED', 'Unknown artifact reception status')
        require(re.fullmatch(r'[0-9a-f]{64}', record.get('manifest_sha256', '')), 'Index requires an immutable manifest digest')
        return safe_path(self.root, record.get('manifest', '')), record['manifest_sha256']
