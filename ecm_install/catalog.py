"""Product contracts bound to the release index. Names and artifacts arrive through approved manifests."""
from pathlib import Path
import json

AWAITING = 'AWAITING_ARTIFACTS'
PACKAGES = {}
for family in ('6', '7'):
    for product, sensor, connection, port in (
        ('ECM100', 'GC2093', 'MIPI', 'CAM1'),
        ('ECM300', 'IMX675', 'MIPI', 'CAM1'),
        ('ECM300', 'IMX675', 'GMSL', 'A-D'),
        ('ECM400', 'IMX415', 'GMSL', 'Single channel / VC0'),
    ):
        roles = {'sensor_ko': 'ko', 'overlay': 'dtbo', 'isp': 'isp'}
        key = product.lower() + '-mipi-jp' + family
        item = dict(product=product, sensor=sensor, connection=connection, port=port,
                    jetpack=family, roles=roles)
        if connection == 'GMSL':
            key = product.lower() + '-gmsl-max96724-jp' + family
            item.update(product=product+'G2F', serdes='MAX96717/MAX96724')
            roles.update(serializer_ko='ko', deserializer_ko='ko')
        if family == '7':
            roles['nito'] = 'nito'
        PACKAGES[key] = dict(item, id=key)

class Catalog:
    def __init__(self, package_root):
        from .contracts import load_json
        self.root = Path(package_root)
        index = load_json(self.root / 'manifests/release-index.json')
        if index.get('schema') != 'ecm-release-index-v1':
            raise ValueError('Unsupported release index schema')
        self.records = index['packages']
        if set(self.records) != set(PACKAGES):
            raise ValueError('Release index package IDs must match the catalog')
    def selectable_packages(self):
        return dict(PACKAGES)

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
