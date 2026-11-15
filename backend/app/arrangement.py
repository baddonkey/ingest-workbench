"""Shared, lossless top-level dossier arrangement rules."""
import uuid
from lxml import etree as ET
from .json_codec import canonical
from .packages import PackageError
from .aip_source import LOGICAL


def normalize(transfer_id, sips, targets):
    units = {(s['id'], u['id']): (s, u) for s in sips for u in s['units']}
    seen, labels, ownership, normalized = [], set(), {}, []
    for target in targets:
        label = target['label'].strip()
        if not label or label in labels or not target['units']:
            raise PackageError('Unique nonempty targets and unit lists required')
        labels.add(label)
        items = []
        for item in target['units']:
            key = (item['sip_id'], item['unit_id'])
            if key not in units:
                raise PackageError('Unknown dossier')
            seen.append(key)
            s, u = units[key]
            items.append({'sip_id': key[0], 'unit_id': key[1]})
            for fid in u['files']:
                fk = (s['id'], fid)
                if fk in ownership and ownership[fk] != label:
                    raise PackageError('Shared files cannot cross target boundaries; group linked dossiers')
                ownership[fk] = label
        aip_id = str(uuid.uuid5(uuid.UUID(transfer_id), 'target:'+canonical(sorted((i['sip_id'], i['unit_id']) for i in items)).decode()))
        normalized.append({'label': label, 'units': items, 'aip_id': aip_id})
    if set(seen) != set(units) or len(seen) != len(set(seen)):
        raise PackageError('Every dossier must occur exactly once; no silent loss or duplication')
    for s in sips:
        unit_owners = {u['id']: ownership[(s['id'], u['files'][0])] for u in s['units'] if u['files']}
        for unit in s['units']:
            if not unit['files']:
                continue
            owner = ownership[(s['id'], unit['files'][0])]
            for node in ET.fromstring(unit['metadata_xml'].encode()).iter():
                if isinstance(node.tag, str) and ET.QName(node).localname in LOGICAL and node.get('id'):
                    unit_owners[node.get('id')] = owner
        for ref in s['references']:
            for attr in ('information', 'repraesentation'):
                other = ref['attributes'].get(attr)
                owner = ownership[(s['id'], ref['file_id'])]
                if other in s['files'] and ownership[(s['id'], other)] != owner:
                    raise PackageError('Cross-target file relation')
                if other in unit_owners and unit_owners[other] != owner:
                    raise PackageError('Cross-target dossier relation')
    return normalized
