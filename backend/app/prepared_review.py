"""Frozen-input arrangement and independent preparation review, separate from AIP approval."""
import copy
import json
from . import prepared_inputs
from .aip_context import PreservationContext
from .json_codec import canonical
from .packages import PackageError, inspect, members, sha
from .service import event, now, put, uid
from .store import Conflict

MAX_PLANS = 100
MAX_REVIEWS = 100


def invalidate(body):
    body.pop('prepared_review_id', None)


def selections(plan):
    return [{'sip_id': i['sip_id'], 'prepared_id': i['prepared_id']} for i in plan['inputs']]


def authors(body, sip_id, record):
    result = {record['actor']}
    for operation in (r for r in body['metadata_operations'] if r['sip_id'] == sip_id):
        result.add(operation['actor'])
        if operation.get('legacy_draft_id'):
            result.update(d['actor'] for d in body.get('metadata_drafts', []) if d['id'] == operation['legacy_draft_id'])
        if operation['id'] == record['operation_id']: break
    return sorted(result)


def inputs(service, body, selected, *, current=True):
    """Project selected bytes without replacing operational source SIP records."""
    if (len(selected) != len(body['sips']) or len({i['sip_id'] for i in selected}) != len(selected)
            or {i['sip_id'] for i in selected} != {s['id'] for s in body['sips']}):
        raise PackageError('Select exactly one source or frozen version for every SIP')
    choices = {i['sip_id']: i.get('prepared_id') for i in selected}
    projected, bindings = [], []
    for sip in body['sips']:
        ident = choices[sip['id']]
        working = body.get('working_metadata', {}).get(sip['id'])
        draft = body.get('active_metadata_drafts', {}).get(sip['id'])
        contributors = []
        if ident is None:
            if current and (working or draft):
                raise Conflict('Preparation history requires a current frozen input; source selection cannot bypass it')
            row = copy.deepcopy(sip)
            binding = {'sip_id': sip['id'], 'prepared_id': None, 'source_sha256': sip['sha256'],
                       'input_sha256': sip['sha256'], 'profile': sip['profile'],
                       'schema_bundle_sha256': sip['schema_bundle_sha256']}
        else:
            record = prepared_inputs.find(body, sip['id'], ident)
            prepared_inputs.validate_record(body, record, service.blob, service.schemas)
            if current and (not working or working['head'] != record['operation_id']):
                raise Conflict('Selected freeze is historical; freeze and select the current working head')
            row = inspect(service.blob(record['sha256']), sip['profile'], service.schemas)
            row['id'] = sip['id']
            binding = {'sip_id': sip['id'], 'prepared_id': ident, 'source_sha256': sip['sha256'],
                       'input_sha256': record['sha256'], 'profile': record['profile'],
                       'schema_bundle_sha256': record['schema_bundle_sha256'],
                       'operation_id': record['operation_id'], 'metadata_sha256': record['metadata_sha256'],
                       'preparation_sha256': record['preparation_sha256'], 'freeze_evidence_sha256': record['evidence_sha256']}
            contributors = authors(body, sip['id'], record)
        binding['preparation_authors'] = contributors
        binding['receipt_checks'] = ['safe-zip', 'trusted-xsd', 'submitted-fixity', 'inventory', 'references']
        binding['size'] = len(service.blob(binding['input_sha256']))
        bindings.append(binding)
        projected.append(row)
    return projected, bindings


def extend_plan(service, body, plan, bindings, *, allow_source=False):
    if not any(i['prepared_id'] for i in bindings) and not allow_source:
        raise PackageError('Use the historical source-only plan contract when no prepared input is selected')
    plan.update(format=2, id=uid(), transfer_id=body['id'], tenant=body['tenant'],
                inputs=bindings)
    plan['sha256'] = sha(canonical({k: v for k, v in plan.items() if k != 'sha256'}))
    if len(body.get('prepared_plans', [])) >= MAX_PLANS:
        raise Conflict('Local prepared plan history limit reached')
    descriptor = put(service.data, canonical(plan))
    body.setdefault('prepared_plans', []).append({'sha256': plan['sha256'], 'descriptor_sha256': descriptor})
    body.setdefault('prepared_review_id', None)
    return plan


def current_plan(service, body):
    plan = body.get('plan')
    if not plan or plan.get('format') != 2:
        raise Conflict('Save a mapping with explicitly selected frozen inputs first')
    _, latest = inputs(service, body, selections(plan))
    if latest != plan['inputs']:
        raise Conflict('Selected inputs changed; save and review a fresh mapping')
    if sha(canonical({k: v for k, v in plan.items() if k != 'sha256'})) != plan['sha256']:
        raise PackageError('Prepared mapping digest mismatch')
    retained = next((p for p in body.get('prepared_plans', []) if p['sha256'] == plan['sha256']), None)
    if not retained or service.blob(retained['descriptor_sha256']) != canonical(plan):
        raise PackageError('Prepared mapping differs from its retained descriptor')
    return plan


def context_view(body, blob=None, schemas=None):
    """Resolve created dossier membership from bound freezes, never the working head."""
    plan=body.get('plan') or {}
    structural=any(i.get('prepared_id') and prepared_inputs.find(body,i['sip_id'],i['prepared_id'])['version']==3
                   for i in plan.get('inputs',[]))
    if not structural:return body
    if blob is None or schemas is None:
        raise PackageError('Trusted frozen input resolver required for created-object arrangement')
    selected={i['sip_id']:i for i in plan['inputs']}
    rows=[]
    for sip in body['sips']:
        item=selected[sip['id']]
        data=blob(item['input_sha256'])
        if sha(data)!=item['input_sha256']:raise PackageError('Frozen context input digest mismatch')
        rows.append({**sip,**inspect(data,sip['profile'],schemas),'id':sip['id']})
    return {**body,'sips':rows}


def context_binding(body, blob=None, schemas=None):
    from .aip import target_context_hash, target_resource_bindings
    projected=context_view(body,blob,schemas)
    return {'context_sha256': sha(canonical(PreservationContext.model_validate(body.get('aip_context', {})).model_dump(mode='json'))),
            'context_author': body.get('context_author'),
            'target_contexts': {t['aip_id']: target_context_hash(projected, t) for t in body['plan']['targets']},
            'target_resources': {t['aip_id']: target_resource_bindings(projected, t) for t in body['plan']['targets']}}


def workspace(service, ident, tenant):
    body = service.get(ident, tenant)
    plan = body.get('plan')
    choices = []
    for sip in body['sips']:
        state = body.get('working_metadata', {}).get(sip['id'])
        choices.append({'sip_id': sip['id'], 'source_sha256': sip['sha256'],
                        'source_allowed': not state and sip['id'] not in body.get('active_metadata_drafts', {}),
                        'frozen': [{**r, 'current': bool(state and r['operation_id'] == state['head'])}
                                   for r in prepared_inputs.history(body, sip['id'])]})
    selected = selections(plan) if plan and plan.get('format') == 2 else [dict(sip_id=s['id'], prepared_id=None) for s in body['sips']]
    projected, bindings = inputs(service, body, selected, current=False)
    problem = None
    if plan and plan.get('format') == 2:
        try: current_plan(service, body)
        except Conflict as exc: problem = str(exc)
    return {'require_independent_review':service.require_independent_review,
            'revision': body['revision'], 'state':body['state'], 'plan':plan, 'choices': choices, 'selected_inputs': selected, 'sips': projected,
            'input_notes':note_views(service,body,selected),
            'bindings': plan['inputs'] if plan and plan.get('format') == 2 else bindings,
            'current_findings': bindings, 'plan_current': problem is None, 'stale_reason': problem,
            'prepared_review_id': body.get('prepared_review_id'), 'reviews': body.get('prepared_reviews', []),
            'approval':body.get('approval'), 'outputs':body.get('outputs',[]),
            'build_held': not (body.get('approval',{}).get('review_id')==body.get('prepared_review_id')
                               and body.get('prepared_review_id') and problem is None)}


def preview(service, ident, tenant, revision, selected):
    body = service.get(ident, tenant)
    if body['revision'] != revision:
        raise Conflict('Stale transfer revision')
    projected, bindings = inputs(service, body, selected)
    return {'revision': body['revision'], 'sips': projected, 'bindings': bindings, 'input_notes':note_views(service,body,selected)}


def note_views(service, body, selected):
    """Display notes from the selected operation, never from a later working head."""
    from . import working_metadata, preparation_notes, editor_fields
    from .packages import xml
    result=[]
    for selection in selected:
        rows=[]
        if selection.get('prepared_id'):
            frozen=prepared_inputs.find(body,selection['sip_id'],selection['prepared_id'])
            records=working_metadata.history(body,selection['sip_id'])
            position=next(i for i,r in enumerate(records) if r['id']==frozen['operation_id'])
            sip=working_metadata.sip_for(body,selection['sip_id'])
            records=records[:position+1]
            nodes,_=editor_fields.catalog(xml(service.blob(frozen['metadata_sha256'])),editor_fields.schema(service.schemas,sip),
                                          identities=editor_fields.current_identities(records))
            titles={n['id']:n['title'] for n in nodes}
            rows=[{**note,'object_title':titles[note['node_id']]} for note in preparation_notes.state(records)]
        result.append({**selection,'notes':rows})
    return result


def review(service, ident, tenant, actor, request):
    if not request['reason'].strip() or len(request['reason']) > 2000:
        raise PackageError('A preparation review reason is required')
    digest = sha(canonical({'actor': actor, 'request': request}))
    with service.store.transaction() as tx:
        body = tx.transfer(ident, tenant)
        previous = next((r for r in body.get('prepared_reviews', []) if r['id'] == request['request_id']), None)
        if previous:
            if previous['request_sha256'] != digest:
                raise Conflict('Review identity already has different retained content')
            return {'transfer': body, 'review': previous, 'replayed': True}
        if body['revision'] != request['revision'] or body['state'] != 'PLANNED':
            raise Conflict('Stale transfer revision or mapping is not awaiting preparation review')
        plan = current_plan(service, body)
        if plan['sha256'] != request['plan_sha256']:
            raise Conflict('Selected mapping changed before review')
        contributors = {plan['author'], body.get('context_author')}
        contributors.update(a for i in plan['inputs'] for a in i['preparation_authors'])
        if actor in contributors:
            raise Conflict('A different archivist must review the mapping, context and preparation changes')
        if len(body.get('prepared_reviews', [])) >= MAX_REVIEWS:
            raise Conflict('Local preparation review limit reached')
        record = {'version': 1, 'id': request['request_id'], 'transfer_id': ident, 'tenant': tenant,
                  'plan_sha256': plan['sha256'], 'inputs_sha256': sha(canonical(plan['inputs'])),
                  **context_binding(body,service.blob,service.schemas), 'actor': actor, 'created': now(), 'reason': request['reason'],
                  'context_snapshot':PreservationContext.model_validate(body.get('aip_context',{})).model_dump(mode='json'),
                  'resource_snapshots':copy.deepcopy(body.get('representation_resources',[])),
                  'revision': request['revision'], 'request_sha256': digest, 'status': 'PREPARATION_REVIEWED',
                  'production_acceptance': False, 'aip_authorization': False}
        from .review_evidence import bundle
        record['evidence_sha256'] = put(service.data, bundle(plan, record, service.schemas))
        body.setdefault('prepared_reviews', []).append(record)
        body['prepared_review_id'] = record['id']
        event(body, 'prepared-input-review', actor, record)
        tx.save_transfer(body, request['revision'])
    return {'transfer': body, 'review': record, 'replayed': False}


def artifact(service, ident, tenant, review_id):
    body = service.get(ident, tenant)
    record = next((r for r in body.get('prepared_reviews', []) if r['id'] == review_id), None)
    if record is None:
        raise KeyError(review_id)
    validate_retained(body, service.blob, service.schemas)
    return service.blob(record['evidence_sha256'])


def validate_retained(body, blob, schemas=None):
    """Historical snapshots remain verifiable after source and working heads change."""
    plans = {}
    for envelope in body.get('prepared_plans', []):
        plan = json.loads(blob(envelope['descriptor_sha256']))
        if (plan['sha256'] in plans or envelope['sha256'] != plan['sha256'] or plan.get('format') != 2
                or plan['transfer_id'] != body['id'] or plan['tenant'] != body['tenant']
                or plan['sha256'] != sha(canonical({k: v for k, v in plan.items() if k != 'sha256'}))):
            raise PackageError('Prepared plan history binding mismatch')
        plans[plan['sha256']] = plan
        if {i['sip_id'] for i in plan['inputs']} != {s['id'] for s in body['sips']} or len(plan['inputs']) != len(body['sips']):
            raise PackageError('Prepared plan input inventory mismatch')
        for item in plan['inputs']:
            sip = next(s for s in body['sips'] if s['id'] == item['sip_id'])
            if item['source_sha256'] != sip['sha256'] or item['profile'] != sip['profile'] or item['schema_bundle_sha256'] != sip['schema_bundle_sha256']:
                raise PackageError('Prepared plan source binding mismatch')
            if item['prepared_id']:
                frozen = prepared_inputs.find(body, sip['id'], item['prepared_id'])
                expected = {'input_sha256': frozen['sha256'], 'operation_id': frozen['operation_id'],
                            'metadata_sha256': frozen['metadata_sha256'], 'preparation_sha256': frozen['preparation_sha256'],
                            'freeze_evidence_sha256': frozen['evidence_sha256'],
                            'preparation_authors': authors(body, sip['id'], frozen)}
            else: expected = {'input_sha256': sip['sha256'], 'preparation_authors': []}
            if any(item.get(k) != v for k, v in expected.items()) or len(blob(item['input_sha256'])) != item['size']:
                raise PackageError('Prepared plan frozen input binding mismatch')
            if item['receipt_checks'] != ['safe-zip', 'trusted-xsd', 'submitted-fixity', 'inventory', 'references']:
                raise PackageError('Prepared plan receipt-check scope mismatch')
        from .arrangement import normalize
        projected=context_view({**body,'plan':plan},blob,schemas)
        if not plan['targets'] or normalize(body['id'],projected['sips'],plan['targets']) != plan['targets']:
            raise PackageError('Prepared plan arrangement closure mismatch')
    active = body.get('plan') or {}
    if active.get('format') == 2 and plans.get(active['sha256']) != active:
        raise PackageError('Active prepared plan is not retained exactly')
    reviews = body.get('prepared_reviews', [])
    if len({r['id'] for r in reviews}) != len(reviews):
        raise PackageError('Duplicate preparation review')
    from .review_evidence import payload
    for record in reviews:
        plan = plans.get(record['plan_sha256'])
        request = dict(request_id=record['id'], revision=record['revision'], plan_sha256=record['plan_sha256'], reason=record['reason'])
        if (plan is None or record['transfer_id'] != body['id'] or record['tenant'] != body['tenant']
                or record['version'] != 1 or record['status'] != 'PREPARATION_REVIEWED'
                or record['production_acceptance'] is not False or record['aip_authorization'] is not False
                or record['inputs_sha256'] != sha(canonical(plan['inputs']))
                or record['request_sha256'] != sha(canonical({'actor': record['actor'], 'request': request}))
                or record['actor'] in {plan['author'], record['context_author']}
                or any(record['actor'] in i['preparation_authors'] for i in plan['inputs'])):
            raise PackageError('Preparation review snapshot binding mismatch')
        if members(blob(record['evidence_sha256'])) != payload(plan, record, schemas):
            raise PackageError('Preparation review METS/PREMIS evidence mismatch')
        historic={**body,'plan':plan,'aip_context':record['context_snapshot'],
                  'context_author':record['context_author'],'representation_resources':record['resource_snapshots']}
        if any(record[k]!=v for k,v in context_binding(historic,blob,schemas).items()):
            raise PackageError('Preparation review historical context snapshot mismatch')
        for resource in record['resource_snapshots']:
            if len(blob(resource['sha256']))!=resource['size']:raise PackageError('Preparation review documentation size mismatch')
    pointer = body.get('prepared_review_id')
    if pointer:
        review = next((r for r in reviews if r['id'] == pointer), None)
        if review is None or review['plan_sha256'] != active.get('sha256'):
            raise PackageError('Active preparation review pointer mismatch')
        if any(review[k]!=v for k,v in context_binding(body,blob,schemas).items()):
            raise PackageError('Active preparation review context binding mismatch')
        for item in active['inputs']:
            if item['prepared_id']:
                state=body.get('working_metadata',{}).get(item['sip_id'],{})
                if state.get('head')!=item['operation_id']:raise PackageError('Active review refers to an earlier working head')
            else:
                if item['sip_id'] in body.get('working_metadata',{}) or item['sip_id'] in body.get('active_metadata_drafts',{}):
                    raise PackageError('Active review bypasses preparation history')
