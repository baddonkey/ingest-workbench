"""Explicit candidate authorization and portable preparation dossier.

Each target retains the complete transfer preparation dossier because the review
covers the whole m:n arrangement. This is a bounded local profile, not an
institutional acceptance decision.
"""
import copy
import json
from . import prepared_review, prepared_inputs, working_metadata, metadata_drafts
from .aip import canonical, profile_definition
from .aip_models_v2 import CandidateAuthorization
from .packages import PackageError, inspect, members, sha
from .service import event, now
from .store import Conflict

PROFILE_VERSION = '4.0.0'
PROFILE_ID = 'urn:ch-archive:aip-profile:4.0.0'
SNAPSHOT = 'evidence/preparation/snapshot.json'
PREFIX = 'evidence/preparation/blobs/'
FIELDS = ('id', 'tenant', 'sips', 'plan', 'aip_context', 'context_author',
          'representation_resources', 'metadata_drafts', 'active_metadata_drafts',
          'metadata_operations', 'working_metadata', 'prepared_inputs',
          'prepared_plans', 'prepared_reviews', 'prepared_review_id')


def current_review(service, body):
    plan = prepared_review.current_plan(service, body)
    prepared_review.validate_retained(body, service.blob, service.schemas)
    review = next((r for r in body.get('prepared_reviews', [])
                   if r['id'] == body.get('prepared_review_id')), None)
    if review is None or review['plan_sha256'] != plan['sha256']:
        raise Conflict('Current independent preparation review required')
    return review


def authorize(service, ident, tenant, actor, request):
    if not request['reason'].strip() or len(request['reason']) > 2000:
        raise PackageError('Candidate authorization reason required (2000 characters maximum)')
    digest = sha(canonical({'actor': actor, 'request': request}))
    with service.store.transaction() as tx:
        body = tx.transfer(ident, tenant)
        previous = next((e['detail'] for e in body['events'] if e['type'] == 'authorization'
                         and e['detail'].get('id') == request['request_id']), None)
        if previous:
            if previous.get('request_sha256') != digest:
                raise Conflict('Authorization identity already has different retained content')
            return {'transfer': body, 'authorization': previous, 'replayed': True}
        if body['revision'] != request['revision'] or body['state'] != 'PLANNED':
            raise Conflict('Stale revision or mapping is not awaiting candidate authorization')
        review = current_review(service, body)
        if request['review_id'] != review['id'] or request['plan_sha256'] != body['plan']['sha256']:
            raise Conflict('Preparation review or mapping changed')
        if actor != review['actor']:
            raise Conflict('The independent preparation reviewer must authorize this candidate')
        approval = {'id': request['request_id'], 'profile_sha256': sha(profile_definition(service.schemas, PROFILE_VERSION)),
                    'approver': actor, 'author': body['plan']['author'], 'time': now(),
                    'plan_sha256': body['plan']['sha256'], **prepared_review.context_binding(body,service.blob,service.schemas),
                    'review_id': review['id'], 'review_evidence_sha256': review['evidence_sha256'],
                    'inputs_sha256': review['inputs_sha256'],
                    'reason': request['reason'], 'revision': request['revision'], 'request_sha256': digest,
                    'production_acceptance': False}
        CandidateAuthorization.model_validate(approval)
        body['approval'] = approval
        body['state'] = 'APPROVED'
        event(body, 'authorization', actor, approval)
        tx.save_transfer(body, request['revision'])
    return {'transfer': body, 'authorization': approval, 'replayed': False}


def check_authorization(body, approval, schemas, blob=None, profile_version=PROFILE_VERSION):
    CandidateAuthorization.model_validate(approval)
    plan = body['plan']
    review = next((r for r in body['prepared_reviews'] if r['id'] == approval['review_id']), None)
    request = dict(request_id=approval['id'], revision=approval['revision'], review_id=approval['review_id'],
                   plan_sha256=approval['plan_sha256'], reason=approval['reason'])
    if (review is None or body.get('prepared_review_id') != review['id']
            or approval['review_evidence_sha256'] != review['evidence_sha256']
            or approval['inputs_sha256'] != sha(canonical(plan['inputs']))
            or approval['profile_sha256'] != sha(profile_definition(schemas, profile_version))
            or approval['plan_sha256'] != plan['sha256'] or approval['author'] != plan['author']
            or approval['approver'] != review['actor']
            or approval['request_sha256'] != sha(canonical({'actor': approval['approver'], 'request': request}))
            or any(approval[k] != v for k, v in prepared_review.context_binding(body,blob,schemas).items())):
        raise PackageError('Prepared candidate authorization binding mismatch')


def project(service, body):
    rows, _ = prepared_review.inputs(service, body, prepared_review.selections(body['plan']), current=False)
    return {**body, 'sips': rows}


def validate_snapshot(snapshot, blob, schemas):
    if set(snapshot) != {'version', 'body'} or snapshot['version'] != 1:
        raise PackageError('Unknown preparation dossier contract')
    body = snapshot['body']
    if set(body) - set(FIELDS) or not {'id', 'tenant', 'sips', 'plan', 'prepared_review_id'} <= set(body):
        raise PackageError('Unexpected preparation dossier fields')
    for sip in body['sips']:
        actual = inspect(blob(sip['sha256']), sip['profile'], schemas)
        if any(sip.get(k) != v for k, v in actual.items()):
            raise PackageError('Preparation dossier source inspection differs from original bytes')
    metadata_drafts.validate_retained(body, blob)
    working_metadata.validate_retained(body, blob, schemas)
    prepared_inputs.validate_retained(body, blob, schemas)
    prepared_review.validate_retained(body, blob, schemas)
    # Read every explicit binding as well as dependencies discovered by validators.
    from .persistence_transfer import blob_bindings
    for digest in blob_bindings(body): blob(digest)
    return body


def evidence_payload(service, body):
    snapshot = {'version': 1, 'body': {k: copy.deepcopy(body[k]) for k in FIELDS if k in body}}
    files = {SNAPSHOT: canonical(snapshot)}
    from .evidence_codec import Encoder
    encoder=Encoder(PREFIX)
    def retain(digest):
        data = service.blob(digest)
        encoder.add(data)
        return data
    validate_snapshot(snapshot, retain, service.schemas)
    files.update(encoder.files)
    return files


def verify_evidence(files, descriptor, plan, schemas):
    raw = files[SNAPSHOT]
    snapshot = json.loads(raw)
    if raw != canonical(snapshot) or sha(raw) != descriptor['preparation_snapshot_sha256']:
        raise PackageError('Preparation dossier digest mismatch')
    from .evidence_codec import Decoder
    blob = Decoder(files,PREFIX)
    body = validate_snapshot(snapshot, blob, schemas)
    if body['plan'] != plan or body['tenant'] != descriptor['archive_scope']:
        raise PackageError('Candidate plan/tenant differs from preparation dossier')
    check_authorization(body, descriptor['approval'], schemas, blob, descriptor['profile_version'])
    return body, blob, blob.used|{SNAPSHOT}


def validate_retained(body, blob, schemas):
    """Verify historical authorizations/candidates without requiring current heads."""
    if not body.get('prepared_plans'):return
    plans={p['sha256']:json.loads(blob(p['descriptor_sha256'])) for p in body.get('prepared_plans',[])}
    authorizations={}
    for entry in body['events']:
        approval=entry['detail']
        if entry['type']!='authorization' or 'review_id' not in approval:continue
        if approval['id'] in authorizations:raise PackageError('Duplicate prepared candidate authorization')
        review=next((r for r in body.get('prepared_reviews',[]) if r['id']==approval['review_id']),None)
        if review is None or approval['plan_sha256'] not in plans:raise PackageError('Missing authorized preparation history')
        historic={**body,'plan':plans[approval['plan_sha256']],'prepared_review_id':review['id'],
                  'aip_context':review['context_snapshot'],'context_author':review['context_author'],
                  'representation_resources':review['resource_snapshots']}
        check_authorization(historic,approval,schemas,blob)
        if entry['actor']!=approval['approver']:raise PackageError('Authorization audit actor mismatch')
        authorizations[approval['id']]=approval
    current=body.get('approval',{})
    if 'review_id' in current:
        if authorizations.get(current['id'])!=current:raise PackageError('Active authorization is not retained exactly')
        check_authorization(body,current,schemas,blob)
    for output in body.get('outputs',[]):
        if output.get('profile') != PROFILE_ID:continue
        from .aip_validation import validate_package
        data=blob(output['sha256'])
        report=validate_package(data,schemas,expected_sha256=output['sha256'],source_resolver=blob)
        if not report['structural_valid']:raise PackageError('Retained prepared candidate invalid: '+'; '.join(report['errors']))
        descriptor=json.loads(members(data)['aip.json'])
        approval=descriptor['approval']
        if (authorizations.get(approval['id'])!=approval or descriptor['plan_sha256'] not in plans
                or descriptor['version_id']!=output['id'] or descriptor['version']!=output['version']
                or descriptor['aip_id']!=output['aip_id'] or descriptor['profile_sha256']!=output['profile_sha256']):
            raise PackageError('Retained prepared output binding mismatch')
