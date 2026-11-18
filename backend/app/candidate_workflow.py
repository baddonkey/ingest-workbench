"""One transfer, one candidate; optional independent review is never fabricated."""
import copy
import os
import uuid
from . import prepared_review
from .aip import profile_definition
from .aip_context import PreservationContext
from .aip_models_v2 import OperatorAuthorization
from .packages import PackageError, sha
from .service import canonical, event, now
from .store import Conflict


def review_required():
    value = os.getenv('AIP_REQUIRE_INDEPENDENT_REVIEW', 'false').lower()
    if value not in ('true', 'false'):
        raise ValueError('AIP_REQUIRE_INDEPENDENT_REVIEW must be true or false')
    return value == 'true'


def plan(service, ident, tenant, actor, revision, label, reason, selected):
    body = service.get(ident, tenant)
    if body['revision'] != revision:
        raise Conflict('Stale transfer revision')
    projected, _ = prepared_review.inputs(service, body, selected)
    units = [{'sip_id': s['id'], 'unit_id': u['id']} for s in projected for u in s['units']]
    if not units:
        raise PackageError('Inspect at least one SIP before preparing a candidate')
    return service.plan(ident, tenant, actor, revision, [{'label': label, 'units': units}],
                        reason, selected, single_candidate=True)


def authorize(service, body, actor):
    plan = prepared_review.current_plan(service, body)
    if service.require_independent_review or plan.get('mode') != 'single-candidate' or len(plan['targets']) != 1:
        raise Conflict('Independent candidate review and authorization required')
    prepared_review.validate_retained(body, service.blob, service.schemas)
    approval = {
        'id': str(uuid.uuid4()), 'profile_sha256': sha(profile_definition(service.schemas, '5.0.0')),
        'approver': actor, 'author': plan['author'], 'time': now(), 'plan_sha256': plan['sha256'],
        **prepared_review.context_binding(body, service.blob, service.schemas),
        'inputs_sha256': sha(canonical(plan['inputs'])), 'review_required': False,
        'reason': plan['reason'], 'production_acceptance': False,
        'context_snapshot': PreservationContext.model_validate(body.get('aip_context', {})).model_dump(mode='json'),
        'resource_snapshots': copy.deepcopy(body.get('representation_resources', [])),
    }
    OperatorAuthorization.model_validate(approval)
    body['approval'] = approval
    body['state'] = 'APPROVED'
    event(body, 'authorization', actor, approval)


def check_authorization(body, approval, schemas, blob):
    OperatorAuthorization.model_validate(approval)
    plan = body['plan']
    if (plan.get('mode') != 'single-candidate' or len(plan['targets']) != 1
            or approval['inputs_sha256'] != sha(canonical(plan['inputs']))
            or approval['plan_sha256'] != plan['sha256'] or approval['author'] != plan['author']
            or approval['profile_sha256'] != sha(profile_definition(schemas, '5.0.0'))
            or approval['context_snapshot'] != PreservationContext.model_validate(body.get('aip_context', {})).model_dump(mode='json')
            or approval['resource_snapshots'] != body.get('representation_resources', [])
            or any(approval[k] != v for k, v in prepared_review.context_binding(body, blob, schemas).items())):
        raise PackageError('Operator candidate authorization binding mismatch')
