"""Attributable, immutable metadata drafts; never replace an operational source SIP."""
import json
from lxml import etree as ET
from .packages import NS, MAX_XML, PackageError, sha, ech_schema_dir
from .package_handler import source, project, key
from .service import event, now, put, uid
from .store import Conflict
from .json_codec import canonical

MAX_CHANGES = 200
MAX_PATCH_BYTES = 256 * 1024
MAX_HISTORY = 100
EDITABLE_KINDS = {'ordnungssystem', 'ordnungssystemposition', 'dossier', 'dokument', 'mappe'}
EDITABLE_TEXT = {'titel', 'name', 'aktenzeichen', 'bemerkung'}


def require_resolved(body):
    if body.get('metadata_operations') or (body.get('plan') or {}).get('format')==2:
        raise Conflict('Prepared inputs require explicit candidate authorization; legacy approval remains held')
    if body.get('active_metadata_drafts'):
        raise Conflict('Resolve saved metadata drafts before approval or building; source SIPs are unchanged')


def catalog(sip, files, root, data):
    nodes, elements = project(sip, files, root, data)
    result = {}
    for node in nodes:
        if node['view'] != 'logical' or node['kind'] not in EDITABLE_KINDS:
            continue
        element = elements[node['id']]
        for field in element:
            if (not isinstance(field.tag, str) or ET.QName(field).namespace != NS['a']
                    or ET.QName(field).localname not in EDITABLE_TEXT or len(field)):
                continue
            path = field.getroottree().getpath(field)
            ident = key('field', path)
            result[ident] = {'node_id': node['id'], 'field_id': ident, 'path': path,
                             'label': ET.QName(field).localname, 'source_value': field.text or '',
                             'element': field}
    return result


def active(service, body, sip):
    ident = body.get('active_metadata_drafts', {}).get(sip['id'])
    if ident is None:
        return None, {}
    record = find_record(body, sip, ident)
    descriptor = retained_descriptor(record, service.blob)
    return record, {patch['field_id']: patch for patch in descriptor['patches']}


def retained_descriptor(record, blob):
    descriptor = json.loads(blob(record['changes_sha256']))
    expected = {'draft_id': record['id'], **{name: record[name] for name in
                ('source_sha256', 'source_metadata_sha256', 'metadata_sha256', 'predecessor_id', 'actor', 'reason', 'created')}}
    if (any(descriptor.get(name) != value for name, value in expected.items())
            or len(descriptor.get('patches', [])) != record['patch_count']
            or len(descriptor.get('changes', [])) != record['change_count']
            or len({p['field_id'] for p in descriptor['patches']}) != record['patch_count']):
        raise PackageError('Retained draft evidence binding mismatch')
    blob(record['metadata_sha256'])
    return descriptor


def validate_retained(body, blob):
    """Recovery/import checks for history, lineage, active pointers and both artifact bindings."""
    sips = {sip['id']: sip for sip in body['sips']}
    records = {}
    sequences = {}
    for record in body.get('metadata_drafts', []):
        sip = sips.get(record['sip_id'])
        predecessor = records.get(record['predecessor_id'])
        expected = sequences.get(record['sip_id'], 0) + 1
        if (sip is None or record['id'] in records or record['source_sha256'] != sip['sha256']
                or record['sequence'] != expected
                or (record['predecessor_id'] is not None and (predecessor is None or predecessor['sip_id'] != sip['id']))):
            raise PackageError('Retained metadata draft lineage/source mismatch')
        retained_descriptor(record, blob)
        records[record['id']] = record
        sequences[record['sip_id']] = expected
    for sip_id, ident in body.get('active_metadata_drafts', {}).items():
        record = records.get(ident)
        if record is None or record['sip_id'] != sip_id or record['patch_count'] <= 0:
            raise PackageError('Active metadata draft binding is missing or invalid')


def find_record(body, sip, ident):
    record = next((r for r in body.get('metadata_drafts', []) if r['id'] == ident and r['sip_id'] == sip['id']), None)
    if record is None:
        raise KeyError(ident)
    if record['source_sha256'] != sip['sha256']:
        raise PackageError('Draft belongs to a different source package')
    return record


def summary(service, body, sip):
    record, patches = active(service, body, sip)
    return {'active': record, 'patches': list(patches.values()),
            'history': [r for r in body.get('metadata_drafts', []) if r['sip_id'] == sip['id']]}


def edit_fields(service, body, sip, files, root, data, node_id):
    _, patches = active(service, body, sip)
    return [{**{k: v for k, v in field.items() if k != 'element'},
             'saved_value': patches.get(ident, {}).get('after', field['source_value'])}
            for ident, field in catalog(sip, files, root, data).items() if field['node_id'] == node_id]


def validate_edit_state(body, revision, sip, source_sha256, base_draft_id):
    if body.get('working_metadata',{}).get(sip['id']):
        raise Conflict('This package uses the versioned metadata editor; legacy drafts remain retained')
    if body['revision'] != revision:
        raise Conflict('Stale transfer revision; reload before saving the metadata draft')
    if source_sha256 != sip['sha256']:
        raise Conflict('Source digest differs from the metadata editing baseline')
    if body.get('active_metadata_drafts', {}).get(sip['id']) != base_draft_id:
        raise Conflict('Saved draft changed; reload and review differences before retrying')


def invalidate_review(body):
    body.pop('approval', None)
    body.pop('prepared_review_id', None)
    body['state'] = 'PLANNED' if body.get('plan') else 'INSPECTED'


def save(service, ident, tenant, actor, revision, sip_id, source_sha256, base_draft_id, reason, changes):
    if not reason.strip() or len(reason) > 2000:
        raise PackageError('A metadata change reason is required (2000 characters maximum)')
    if not 1 <= len(changes) <= MAX_CHANGES:
        raise PackageError('Save between one and 200 field changes')
    with service.store.transaction() as tx:
        body = tx.transfer(ident, tenant)
        sip = next((s for s in body['sips'] if s['id'] == sip_id), None)
        if sip is None:
            raise KeyError(sip_id)
        validate_edit_state(body, revision, sip, source_sha256, base_draft_id)
        data = service.blob(sip['sha256'])
        # Source authorization was checked inside this transaction; do not read a second aggregate.
        from .packages import members, xml
        files = members(data)
        root = xml(files[sip['metadata_path']])
        available = catalog(sip, files, root, data)
        previous, patches = active(service, body, sip)
        history = [r for r in body.get('metadata_drafts', []) if r['sip_id'] == sip_id]
        if len(history) >= MAX_HISTORY:
            raise Conflict('Metadata draft history limit reached; retain and review before further revisions')
        delta = []
        seen = set()
        for change in changes:
            field_id = change['field_id']
            field = available.get(field_id)
            if field is None or field['node_id'] != change['node_id'] or field_id in seen:
                raise PackageError('Unknown, protected or duplicate metadata field')
            seen.add(field_id)
            value = change['value']
            if not isinstance(value, str) or len(value) > 8000:
                raise PackageError('Draft field value exceeds the 8000 character limit')
            current = patches.get(field_id, {}).get('after', field['source_value'])
            if change['before'] != current:
                raise Conflict('Field baseline changed; review saved values before retrying')
            if value == current:
                continue
            delta.append({'node_id': field['node_id'], 'field_id': field_id, 'path': field['path'],
                          'before': current, 'after': value})
            if value == field['source_value']:
                patches.pop(field_id, None)
            else:
                patches[field_id] = {'node_id': field['node_id'], 'field_id': field_id, 'path': field['path'],
                                     'before': field['source_value'], 'after': value}
        if not delta:
            raise PackageError('No metadata changes to save')
        if len(patches) > MAX_CHANGES or len(canonical({'patches': list(patches.values()), 'changes': delta})) > MAX_PATCH_BYTES:
            raise PackageError('Saved metadata patch limit exceeded')
        for field_id, patch in patches.items():
            if field_id not in available or patch['before'] != available[field_id]['source_value']:
                raise PackageError('Retained draft field no longer matches its source')
            try:
                available[field_id]['element'].text = patch['after']
            except ValueError as exc:
                raise PackageError('Field value contains characters forbidden in XML') from exc
        schema_dir = ech_schema_dir(service.schemas, sip['profile'])
        fingerprint = sha(b''.join(p.name.encode() + p.read_bytes() for p in sorted(schema_dir.glob('*.xsd'))))
        if fingerprint != sip['schema_bundle_sha256']:
            raise PackageError('Trusted schema bundle changed since receipt')
        validator = ET.XMLSchema(ET.parse(str(schema_dir / 'arelda.xsd'), ET.XMLParser(resolve_entities=False, no_network=True)))
        if not validator.validate(root):
            raise PackageError('Draft violates the selected trusted schema: ' + str(validator.error_log.last_error)[:800])
        metadata = ET.tostring(root.getroottree(), encoding='UTF-8', xml_declaration=True)
        if len(metadata) > MAX_XML:
            raise PackageError('Draft metadata exceeds the XML size limit')
        draft_id = uid()
        timestamp = now()
        metadata_digest = put(service.data, metadata)
        descriptor = {'draft_id': draft_id, 'source_sha256': sip['sha256'],
                      'source_metadata_sha256': sha(files[sip['metadata_path']]),
                      'metadata_sha256': metadata_digest, 'predecessor_id': base_draft_id,
                      'actor': actor, 'reason': reason, 'created': timestamp,
                      'patches': list(patches.values()), 'changes': delta,
                      'transformation': 'Explicit leaf-text edits; serialize complete XML document as a separate UTF-8 draft; original ZIP and metadata retained'}
        changes_digest = put(service.data, canonical(descriptor))
        record = {'id': draft_id, 'sip_id': sip_id, 'sequence': len(history) + 1,
                  'source_sha256': sip['sha256'], 'source_metadata_sha256': descriptor['source_metadata_sha256'],
                  'metadata_sha256': metadata_digest, 'changes_sha256': changes_digest,
                  'predecessor_id': base_draft_id, 'actor': actor, 'reason': reason,
                  'created': timestamp, 'change_count': len(delta), 'patch_count': len(patches),
                  'schema_bundle_sha256': fingerprint, 'internal_schema_valid': True,
                  'production_acceptance': False}
        body.setdefault('metadata_drafts', []).append(record)
        pointers = body.setdefault('active_metadata_drafts', {})
        if patches:
            pointers[sip_id] = draft_id
        else:
            pointers.pop(sip_id, None)
        invalidate_review(body)
        event(body, 'metadata-draft-saved', actor, {**record, 'approval_invalidated': True, 'active': bool(patches)})
        tx.save_transfer(body, revision)
    return {'transfer_revision': body['revision'], 'draft': record, 'active': bool(patches)}


def discard(service, ident, tenant, actor, revision, sip_id, source_sha256, base_draft_id, reason):
    if not reason.strip() or len(reason) > 2000:
        raise PackageError('A discard reason is required (2000 characters maximum)')
    with service.store.transaction() as tx:
        body = tx.transfer(ident, tenant)
        sip = next((s for s in body['sips'] if s['id'] == sip_id), None)
        if sip is None:
            raise KeyError(sip_id)
        validate_edit_state(body, revision, sip, source_sha256, base_draft_id)
        if base_draft_id is None:
            raise Conflict('No saved draft to discard')
        record, _ = active(service, body, sip)
        body['active_metadata_drafts'].pop(sip_id)
        invalidate_review(body)
        event(body, 'metadata-draft-discarded', actor, {'sip_id': sip_id, 'draft_id': record['id'],
              'source_sha256': source_sha256, 'metadata_sha256': record['metadata_sha256'],
              'changes_sha256': record['changes_sha256'], 'reason': reason, 'approval_invalidated': True})
        tx.save_transfer(body, revision)
    return {'transfer_revision': body['revision'], 'discarded_draft_id': record['id']}


def artifact(service, ident, tenant, sip_id, draft_id, kind):
    body, sip, _, _, _ = source(service, ident, tenant, sip_id)
    record = find_record(body, sip, draft_id)
    return service.blob(record['metadata_sha256'] if kind == 'metadata' else record['changes_sha256'])
