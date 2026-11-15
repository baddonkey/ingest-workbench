"""Immutable derived SIPs. Export never promotes a draft into operational ingest."""
import copy
import io
import json
import zipfile
from lxml import etree as ET
from .json_codec import canonical
from .metadata_drafts import catalog, find_record, retained_descriptor, validate_edit_state
from .packages import PackageError, inspect, members, sha, xml
from .service import event, now, put, uid
from .store import Conflict

MAX_EXPORTS = 100


def checked_metadata(sip, draft, source, blob):
    """Reapply only the authorized leaf patches; reject protected-field/blob substitution."""
    files = members(source)
    root = xml(files[sip['metadata_path']])
    descriptor = retained_descriptor(draft, blob)
    if sha(files[sip['metadata_path']]) != draft['source_metadata_sha256']:
        raise PackageError('Draft metadata source binding mismatch')
    fields = catalog(sip, files, root, source)
    for patch in descriptor['patches']:
        field = fields.get(patch['field_id'])
        if (field is None or any(patch[k] != field[k] for k in ('node_id', 'path'))
                or patch['before'] != field['source_value']):
            raise PackageError('Export contains an unauthorized metadata patch')
        field['element'].text = patch['after']
    expected = ET.tostring(root.getroottree(), encoding='UTF-8', xml_declaration=True)
    metadata = blob(draft['metadata_sha256'])
    if expected != metadata:
        raise PackageError('Draft XML differs from its retained authorized patches')
    return metadata


def member_manifest(source, derived, metadata_path):
    """Verify ordered paths, directories and exact uncompressed member bytes."""
    members(source)
    members(derived)
    result = []
    with zipfile.ZipFile(io.BytesIO(source)) as before, zipfile.ZipFile(io.BytesIO(derived)) as after:
        original, exported = before.infolist(), after.infolist()
        if ([i.orig_filename for i in original] != [i.orig_filename for i in exported]
                or before.comment != after.comment):
            raise PackageError('Export ZIP member order/paths or archive comment changed')
        for old, new in zip(original, exported):
            a, b = before.read(old), after.read(new)
            changed = old.orig_filename == metadata_path
            if old.is_dir() != new.is_dir() or (not changed and a != b):
                raise PackageError('Export changed a protected member: ' + old.orig_filename)
            result.append({'path': old.orig_filename, 'directory': old.is_dir(),
                           'source_sha256': sha(a), 'sha256': sha(b), 'size': len(b),
                           'replaced_metadata': changed})
    return result


def find(body, sip_id, export_id):
    record = next((r for r in body.get('sip_exports', []) if r['id'] == export_id and r['sip_id'] == sip_id), None)
    if record is None:
        raise KeyError(export_id)
    return record


def validate_record(body, record, blob):
    sip = next((s for s in body['sips'] if s['id'] == record['sip_id']), None)
    if sip is None or sip['sha256'] != record['source_sha256']:
        raise PackageError('Export source binding mismatch')
    try:
        draft = find_record(body, sip, record['draft_id'])
    except KeyError as exc:
        raise PackageError('Export refers to a missing saved draft') from exc
    descriptor = json.loads(blob(record['evidence_sha256']))
    if (descriptor.get('export') != {k: v for k, v in record.items() if k != 'evidence_sha256'}
            or record['metadata_sha256'] != draft['metadata_sha256']
            or record['changes_sha256'] != draft['changes_sha256']
            or record['schema_bundle_sha256'] != draft['schema_bundle_sha256']
            or record['schema_bundle_sha256'] != sip['schema_bundle_sha256']
            or record['profile'] != sip['profile'] or record['metadata_path'] != sip['metadata_path']
            or record['production_acceptance'] is not False):
        raise PackageError('Export lineage evidence binding mismatch')
    source, derived = blob(sip['sha256']), blob(record['sha256'])
    metadata = checked_metadata(sip, draft, source, blob)
    if members(derived).get(sip['metadata_path']) != metadata:
        raise PackageError('Export metadata differs from its saved draft')
    if descriptor.get('members') != member_manifest(source, derived, sip['metadata_path']):
        raise PackageError('Export member conservation evidence mismatch')
    request = {k: record[k] for k in ('source_sha256', 'draft_id', 'reason', 'actor', 'revision')}
    if sha(canonical(request)) != record['request_sha256']:
        raise PackageError('Export request binding mismatch')
    return descriptor


def validate_retained(body, blob):
    seen = set()
    for record in body.get('sip_exports', []):
        key = (record['sip_id'], record['draft_id'])
        if key in seen:
            raise PackageError('Duplicate derived SIP export for a draft')
        seen.add(key)
        validate_record(body, record, blob)


def create(service, ident, tenant, actor, revision, sip_id, source_sha256, draft_id, reason):
    if not reason.strip() or len(reason) > 2000:
        raise PackageError('An export reason is required (2000 characters maximum)')
    request_digest = sha(canonical(dict(source_sha256=source_sha256, draft_id=draft_id,
                                      reason=reason, actor=actor, revision=revision)))
    with service.store.transaction() as tx:
        body = tx.transfer(ident, tenant)
        sip = next((s for s in body['sips'] if s['id'] == sip_id), None)
        if sip is None:
            raise KeyError(sip_id)
        existing = next((r for r in body.get('sip_exports', []) if r['sip_id'] == sip_id and r['draft_id'] == draft_id), None)
        if existing:
            if existing['request_sha256'] != request_digest:
                raise Conflict('Saved draft already exported; reload and download its retained export')
            validate_record(body, existing, service.blob)
            return {'transfer_revision': body['revision'], 'export': existing, 'replayed': True}
        validate_edit_state(body, revision, sip, source_sha256, draft_id)
        draft = find_record(body, sip, draft_id)
        if not draft['patch_count']:
            raise Conflict('Export requires a saved draft with active differences')
        if len(body.get('sip_exports', [])) >= MAX_EXPORTS:
            raise Conflict('Derived SIP export history limit reached')
        source = service.blob(source_sha256)
        metadata = checked_metadata(sip, draft, source, service.blob)
        stream = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(source)) as original, zipfile.ZipFile(stream, 'w') as derived:
            derived.comment = original.comment
            for entry in original.infolist():
                derived.writestr(copy.copy(entry), metadata if entry.orig_filename == sip['metadata_path'] else original.read(entry))
        data = stream.getvalue()
        report = inspect(data, sip['profile'], service.schemas)
        if (report['schema_bundle_sha256'] != sip['schema_bundle_sha256']
                or report['schema_bundle_sha256'] != draft['schema_bundle_sha256']
                or report['files'] != sip['files'] or report['references'] != sip['references']):
            raise PackageError('Export changed trusted schema, payload inventory or references')
        manifest = member_manifest(source, data, sip['metadata_path'])
        record = {'id': uid(), 'sip_id': sip_id, 'draft_id': draft_id, 'source_sha256': source_sha256,
                  'sha256': put(service.data, data), 'metadata_sha256': draft['metadata_sha256'],
                  'changes_sha256': draft['changes_sha256'], 'schema_bundle_sha256': report['schema_bundle_sha256'],
                  'profile': sip['profile'], 'metadata_path': sip['metadata_path'], 'actor': actor, 'reason': reason,
                  'created': now(), 'revision': revision, 'request_sha256': request_digest,
                  'internal_status': 'PASS', 'production_acceptance': False}
        descriptor = {'export': dict(record), 'members': manifest,
                      'transformation': 'Reserialize ZIP container; replace only metadata.xml with the saved UTF-8 draft; retain every other member byte, ordered path and explicit directory',
                      'internal_scope': 'Trusted profile XSD, inventory, submitted fixity, references and member conservation; no specialist format qualification',
                      'promoted': False}
        record['evidence_sha256'] = put(service.data, canonical(descriptor))
        body.setdefault('sip_exports', []).append(record)
        event(body, 'derived-sip-exported', actor, record)
        tx.save_transfer(body, revision)
    return {'transfer_revision': body['revision'], 'export': record, 'replayed': False}


def artifact(service, ident, tenant, sip_id, export_id, kind):
    body = service.get(ident, tenant)
    record = find(body, sip_id, export_id)
    validate_record(body, record, service.blob)
    return service.blob(record['sha256'] if kind == 'zip' else record['evidence_sha256'])
