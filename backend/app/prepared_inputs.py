"""Immutable prepared SIP versions. Freezing never promotes an input into ingest."""
import copy
import io
import json
import zipfile
from collections import Counter
from . import working_metadata as working
from .json_codec import canonical
from .metadata_drafts import invalidate_review
from .packages import PackageError, inspect, members, sha
from .preparation_evidence import verify as verify_history
from .service import event, now, put
from .sip_exports import member_manifest
from .store import Conflict

MAX_PREPARED_INPUTS = 100


def freeze_version(operations):
    if any(r['kind']=='structure-create' for r in operations):return 3
    return 2 if any(r['kind']=='structure-reorder' for r in operations) else 1


def same_references(source, prepared, version):
    if version==1:return source==prepared
    # Logical order is allowed to change; every occurrence and relationship
    # attribute must remain. The v4 operation verifier checks exact XML scope.
    return Counter(canonical(r) for r in source)==Counter(canonical(r) for r in prepared)


def history(body, sip_id):
    return [r for r in body.get('prepared_inputs', []) if r['sip_id'] == sip_id]


def find(body, sip_id, ident):
    record = next((r for r in history(body, sip_id) if r['id'] == ident), None)
    if record is None:
        raise KeyError(ident)
    return record


def request_digest(actor, request):
    return sha(canonical({'actor': actor, 'request': request}))


def validate_record(body, record, blob, schemas=None):
    """Verify historical bindings without substituting the current working head."""
    sip = working.sip_for(body, record['sip_id'])
    operations = working.history(body, sip['id'])
    index = next((i for i, r in enumerate(operations) if r['id'] == record['operation_id']), None)
    if index is None:
        raise PackageError('Prepared input refers to missing preparation history')
    operation = operations[index]
    frozen = history(body, sip['id'])
    position = next(i for i, r in enumerate(frozen) if r['id'] == record['id'])
    request = dict(request_id=record['id'], revision=record['revision'], source_sha256=record['source_sha256'],
                   operation_id=record['operation_id'], reason=record['reason'])
    if (record['version'] != freeze_version(operations[:index+1]) or record['transfer_id'] != body['id'] or record['tenant'] != body['tenant']
            or record['source_sha256'] != sip['sha256'] or record['profile'] != sip['profile']
            or record['schema_bundle_sha256'] != sip['schema_bundle_sha256']
            or record['metadata_path'] != sip['metadata_path']
            or record['metadata_sha256'] != operation['after_metadata_sha256']
            or record['preparation_sha256'] != operation['evidence_sha256']
            or record['predecessor_id'] != (frozen[position-1]['id'] if position else None)
            or record['request_sha256'] != request_digest(record['actor'], request)
            or record['internal_status'] != 'PASS'
            or record['production_acceptance'] is not False):
        raise PackageError('Prepared input lineage binding mismatch')
    source, prepared = blob(sip['sha256']), blob(record['sha256'])
    if members(prepared).get(sip['metadata_path']) != blob(operation['after_metadata_sha256']):
        raise PackageError('Prepared SIP differs from its frozen metadata version')
    lineage = member_manifest(source, prepared, sip['metadata_path'])
    evidence = blob(record['preparation_sha256'])
    verify_history(evidence, operations[:index+1], sip, schemas)
    from .prepared_input_evidence import payload
    expected = payload(record, lineage, prepared, evidence, schemas)
    if members(blob(record['evidence_sha256'])) != expected:
        raise PackageError('Prepared input METS/PREMIS or member-conservation evidence mismatch')
    if schemas is not None:
        report = inspect(prepared, sip['profile'], schemas)
        if (report['schema_bundle_sha256'] != sip['schema_bundle_sha256']
                or report['files'] != sip['files'] or not same_references(sip['references'],report['references'],record['version'])):
            raise PackageError('Prepared input changed protected inventory, references or schema')


def validate_retained(body, blob, schemas=None):
    records = body.get('prepared_inputs', [])
    if (len({r['id'] for r in records}) != len(records)
            or len({(r['sip_id'], r['operation_id']) for r in records}) != len(records)
            or any(r['sip_id'] not in {s['id'] for s in body['sips']} for r in records)):
        raise PackageError('Orphaned or duplicate prepared input')
    for record in records:
        validate_record(body, record, blob, schemas)


def create(service, ident, tenant, actor, sip_id, request):
    """Freeze the current applied head, atomically retaining its exact ZIP and evidence."""
    if not request['reason'].strip() or len(request['reason']) > 2000:
        raise PackageError('A freeze reason is required (2000 characters maximum)')
    digest = request_digest(actor, request)
    with service.store.transaction() as tx:
        body = tx.transfer(ident, tenant)
        sip = working.sip_for(body, sip_id)
        existing = next((r for r in body.get('prepared_inputs', []) if r['id'] == request['request_id']), None)
        if existing:
            if existing['request_sha256'] != digest or existing['sip_id'] != sip_id:
                raise Conflict('Freeze identity already has different retained content')
            validate_record(body, existing, service.blob, service.schemas)
            return {'prepared_input': existing, 'revision': body['revision'], 'replayed': True}
        state = body.get('working_metadata', {}).get(sip_id)
        if (body['revision'] != request['revision'] or sip['sha256'] != request['source_sha256']
                or not state or state['head'] != request['operation_id']):
            raise Conflict('Working version changed; reload before freezing its applied head')
        prior = history(body, sip_id)
        if any(r['operation_id'] == state['head'] for r in prior):
            raise Conflict('Working head is already frozen; download its retained version')
        if len(body.get('prepared_inputs', [])) >= MAX_PREPARED_INPUTS:
            raise Conflict('Local prepared input limit reached')
        # Replay allowed edits and verify every retained operation before publishing a freeze.
        working.validate_retained(body, service.blob, service.schemas)
        source, metadata = service.blob(sip['sha256']), service.blob(state['metadata_sha256'])
        stream = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(source)) as original, zipfile.ZipFile(stream, 'w') as prepared:
            prepared.comment = original.comment
            for entry in original.infolist():
                prepared.writestr(copy.copy(entry), metadata if entry.orig_filename == sip['metadata_path'] else original.read(entry))
        data = stream.getvalue()
        report = inspect(data, sip['profile'], service.schemas)
        version = freeze_version(working.history(body,sip_id))
        if (report['schema_bundle_sha256'] != sip['schema_bundle_sha256']
                or report['files'] != sip['files'] or not same_references(sip['references'],report['references'],version)):
            raise PackageError('Prepared SIP changed protected inventory, references or schema')
        operation = working.history(body, sip_id)[-1]
        record = {'version': version, 'id': request['request_id'], 'sip_id': sip_id, 'transfer_id': ident, 'tenant': tenant,
                  'source_sha256': sip['sha256'], 'sha256': put(service.data, data),
                  'metadata_sha256': state['metadata_sha256'], 'metadata_path': sip['metadata_path'],
                  'profile': sip['profile'], 'schema_bundle_sha256': sip['schema_bundle_sha256'],
                  'operation_id': state['head'], 'preparation_sha256': operation['evidence_sha256'],
                  'predecessor_id': prior[-1]['id'] if prior else None,
                  'actor': actor, 'reason': request['reason'], 'created': now(), 'revision': request['revision'],
                  'request_sha256': digest, 'internal_status': 'PASS',
                  'production_acceptance': False}
        from .prepared_input_evidence import bundle
        record['evidence_sha256'] = put(service.data, bundle(record, member_manifest(source, data, sip['metadata_path']),
              data, service.blob(record['preparation_sha256']), service.schemas))
        body.setdefault('prepared_inputs', []).append(record)
        validate_record(body, record, service.blob, service.schemas)
        invalidate_review(body)
        event(body, 'prepared-input-frozen', actor, record)
        tx.save_transfer(body, request['revision'])
        from .kost_validation import auto_enqueue
        auto_enqueue(tx, body, sip_id, service.kost_config, record['id'])
    return {'prepared_input': record, 'revision': body['revision'], 'replayed': False}


def artifact(service, ident, tenant, sip_id, prepared_id, kind):
    if kind not in ('zip', 'evidence'):
        raise KeyError(kind)
    body = service.get(ident, tenant)
    record = find(body, sip_id, prepared_id)
    validate_record(body, record, service.blob, service.schemas)
    return service.blob(record['sha256'] if kind == 'zip' else record['evidence_sha256'])
