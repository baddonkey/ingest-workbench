"""Versioned applied metadata operations, immutable history and named checkpoints."""
import json
from lxml import etree as ET
from . import editor_fields as fields
from .packages import PackageError, members, xml, sha
from .metadata_drafts import active, invalidate_review
from .json_codec import canonical
from .service import event, now, put
from .store import Conflict
from . import preparation_notes as notes
from . import hierarchy_editing as hierarchy
from . import hierarchy_creation as creation

MAX_OPERATIONS = 500


def sip_for(body, sip_id):
    sip = next((s for s in body['sips'] if s['id']==sip_id),None)
    if sip is None:raise KeyError(sip_id)
    return sip


def history(body, sip_id):
    return [r for r in body.get('metadata_operations',[]) if r['sip_id']==sip_id]


def baseline(service, body, sip):
    original = members(service.blob(sip['sha256']))[sip['metadata_path']]
    state = body.get('working_metadata',{}).get(sip['id'])
    if state:
        return original, service.blob(state['metadata_sha256']), state
    draft, _ = active(service,body,sip)
    data = service.blob(draft['metadata_sha256']) if draft else original
    return original, data, {'head':None,'metadata_sha256':sha(data),'baseline_metadata_sha256':sha(data),
                           'legacy_draft_id':draft['id'] if draft else None,'undo':[],'redo':[],'changed':bool(draft)}


def workspace(service, ident, tenant, sip_id, operation_id=None):
    body = service.get(ident,tenant)
    sip = sip_for(body,sip_id)
    original,data,state = baseline(service,body,sip)
    records = history(body,sip_id)
    if operation_id:
        record = next((r for r in history(body,sip_id) if r['id']==operation_id),None)
        if record is None:raise KeyError(operation_id)
        data = service.blob(record['after_metadata_sha256'])
        records = records[:next(i for i,r in enumerate(records) if r['id']==operation_id)+1]
    info = fields.schema(service.schemas,sip)
    root = xml(data)
    nodes,_ = fields.catalog(root,info)
    from .prepared_inputs import history as frozen_history
    return {'sip_id':sip_id,'source_sha256':sip['sha256'],'source_metadata_sha256':sha(original),
            'profile':sip['profile'],'revision':body['revision'],'state':state,'nodes':nodes,
            'navigation':fields.navigation(root,nodes),
            'history':history(body,sip_id),'notes':notes.state(records),'reorder_groups':hierarchy.groups(root),
            'creation_options':creation.options(root,info),'prepared_inputs':frozen_history(body,sip_id),
            'viewed_operation_id':operation_id,'acceptance':'CLOSED'}


def command(service, ident, tenant, actor, sip_id, request):
    if request.get('kind') in ('note-add','note-edit'):notes.text_value(request.get('note_text'))
    if len(canonical(request))>256*1024:
        raise PackageError('Metadata operation exceeds 256 KiB')
    if not request['reason'].strip() or len(request['reason'])>2000:
        raise PackageError('An editing reason/context is required')
    digest = sha(canonical({'actor':actor,'request':request}))
    with service.store.transaction() as tx:
        body = tx.transfer(ident,tenant)
        sip = sip_for(body,sip_id)
        existing = next((r for r in body.get('metadata_operations',[]) if r['id']==request['request_id']),None)
        if existing:
            if existing['request_sha256']!=digest or existing['sip_id']!=sip_id:
                raise Conflict('Operation identity already has different retained content')
            return {'operation':existing,'revision':body['revision'],'replayed':True}
        original,data,state = baseline(service,body,sip)
        if (body['revision']!=request['revision'] or sip['sha256']!=request['source_sha256']
                or state['head']!=request['base_operation_id']):
            raise Conflict('Working version changed; reload and compare before applying this edit')
        records = history(body,sip_id)
        if len(records)>=MAX_OPERATIONS:
            raise Conflict('Local metadata operation limit reached')
        info = fields.schema(service.schemas,sip)
        root = xml(data)
        changes = {}
        kind = request['kind']
        created = now()
        if kind==creation.KIND:
            changes = creation.apply(root,info,request)
            updated = fields.validate(root,service.schemas,sip)
        elif kind==hierarchy.KIND:
            changes = hierarchy.apply(root,info,request)
            updated = fields.validate(root,service.schemas,sip)
        elif kind in notes.KINDS:
            nodes,_ = fields.catalog(root,info)
            changes = notes.change(request,notes.state(records),nodes,actor,created)
            updated = data
        elif kind=='edit':
            _,available = fields.catalog(root,info)
            selected = available.get(request.get('field_id'))
            if selected is None or selected[2]!=request.get('node_id') or not selected[1]['editable']:
                raise PackageError('Unknown or protected metadata field')
            element,field,_ = selected
            if field['values']!=request.get('before'):
                raise Conflict('Metadata field baseline changed')
            if field['values']==request.get('values'):
                raise PackageError('No metadata changes to apply')
            fields.attach_order(field,info,fields.type_name(element,info))
            fields.apply(element,field,request['values'])
            updated = fields.validate(root,service.schemas,sip)
            changes = {'node_id':selected[2],'field_id':field['id'],'tag':field['tag'],
                       'before':request['before'],'proposed':request['values'],'after':fields.values(element,field)}
        elif kind in ('undo','redo'):
            stack = state['undo' if kind=='undo' else 'redo']
            if not stack:raise Conflict('No operation available to '+kind)
            entry = stack.pop()
            updated = service.blob(entry['before' if kind=='undo' else 'after'])
            state['redo' if kind=='undo' else 'undo'].append(entry)
            changes = {'related_operation_id':entry['operation_id']}
        elif kind=='restore':
            target = next((r for r in records if r['id']==request.get('target_id')),None)
            if target is None:raise KeyError(request.get('target_id'))
            updated = service.blob(target['after_metadata_sha256'])
            target_index = next(i for i,r in enumerate(records) if r['id']==target['id'])
            if updated==data and notes.state(records)==notes.state(records[:target_index+1]):
                raise PackageError('Selected version is already current')
            changes = {'related_operation_id':target['id']}
        elif kind=='checkpoint':
            if not request.get('name','').strip() or len(request['name'])>200:
                raise PackageError('A checkpoint name is required (200 characters maximum)')
            updated = data
            changes = {'name':request['name']}
        else:
            raise PackageError('Unknown metadata operation')
        # Revalidate historical targets under the same retained trusted schema pin.
        fields.validate(xml(updated),service.schemas,sip)
        before_digest,after_digest = put(service.data,data),put(service.data,updated)
        if kind in ('edit','restore',hierarchy.KIND,creation.KIND) or kind in notes.KINDS:
            state['undo'].append({'before':before_digest,'after':after_digest,'operation_id':request['request_id']})
            state['redo'] = []
        descriptor = {'id':request['request_id'],'sip_id':sip_id,'transfer_id':ident,'tenant':tenant,
                      'source_sha256':sip['sha256'],'predecessor_id':state['head'],'kind':kind,
                      'before_metadata_sha256':before_digest,'after_metadata_sha256':after_digest,
                      'schema_bundle_sha256':sip['schema_bundle_sha256'],'legacy_draft_id':state['legacy_draft_id'],
                      'actor':actor,'created':created,'reason':request['reason'],'changes':changes,
                      'request_sha256':digest,'version':5 if kind==creation.KIND else 4 if kind==hierarchy.KIND else 3 if kind in notes.KINDS or any(r['kind'] in notes.KINDS for r in records) else 2}
        record = {**descriptor,'changes_sha256':put(service.data,canonical(descriptor))}
        if sum(len(canonical(r)) for r in records)+len(canonical(record))>8*1024*1024:
            raise Conflict('Local metadata journal byte limit reached')
        state.update(head=record['id'],metadata_sha256=after_digest,
                     changed=ET.tostring(xml(updated),method='c14n')!=ET.tostring(xml(original),method='c14n') or bool(notes.state(records+[record])))
        body.setdefault('working_metadata',{})[sip_id] = state
        body.setdefault('metadata_operations',[]).append(record)
        # Produce schema-validated preservation evidence before publishing the edit.
        from .preparation_evidence import bundle
        record['evidence_sha256'] = put(service.data,bundle(service,body,sip))
        invalidate_review(body)
        event(body,'metadata-'+kind,actor,{'sip_id':sip_id,'operation_id':record['id'],
              'changes_sha256':record['changes_sha256'],'evidence_sha256':record['evidence_sha256'],
              'before_metadata_sha256':before_digest,'after_metadata_sha256':after_digest})
        tx.save_transfer(body,request['revision'])
    return {'operation':record,'revision':body['revision'],'replayed':False}


def artifact(service, ident, tenant, sip_id, operation_id, kind):
    body = service.get(ident,tenant)
    sip_for(body,sip_id)
    record = next((r for r in history(body,sip_id) if r['id']==operation_id),None)
    if record is None:raise KeyError(operation_id)
    field = {'metadata':'after_metadata_sha256','changes':'changes_sha256','evidence':'evidence_sha256'}.get(kind)
    if field is None:raise KeyError(kind)
    return service.blob(record[field])


def validate_retained(body, blob, schemas=None):
    sips={s['id'] for s in body['sips']}
    operations=body.get('metadata_operations',[])
    if (not set(body.get('working_metadata',{}))<=sips or any(r['sip_id'] not in sips for r in operations)
            or len({r['id'] for r in operations})!=len(operations)):
        raise PackageError('Orphaned or duplicate working metadata history')
    for sip in body['sips']:
        records = history(body,sip['id'])
        state = body.get('working_metadata',{}).get(sip['id'])
        if not records and not state:continue
        if not records or not state:raise PackageError('Missing working metadata state/history')
        predecessor = None
        seen = set()
        undo,redo = [],[]
        info = fields.schema(schemas,sip) if schemas is not None else None
        note_versions = notes.snapshots(records)
        for index,record in enumerate(records):
            descriptor = json.loads(blob(record['changes_sha256']))
            if (record['id'] in seen or record['predecessor_id']!=predecessor
                    or record['source_sha256']!=sip['sha256'] or record['tenant']!=body['tenant']
                    or record['transfer_id']!=body['id'] or record['schema_bundle_sha256']!=sip['schema_bundle_sha256']
                    or record['legacy_draft_id']!=state.get('legacy_draft_id')
                    or descriptor!={k:v for k,v in record.items() if k not in ('changes_sha256','evidence_sha256')}
                    or (index and record['before_metadata_sha256']!=records[index-1]['after_metadata_sha256'])):
                raise PackageError('Working metadata history binding mismatch')
            before,after = blob(record['before_metadata_sha256']),blob(record['after_metadata_sha256'])
            kind = record['kind']
            if kind in ('edit','restore',hierarchy.KIND,creation.KIND) or kind in notes.KINDS:
                undo.append({'before':sha(before),'after':sha(after),'operation_id':record['id']});redo=[]
                if kind==creation.KIND:
                    if info is not None:creation.verify(before,after,record,info,schemas,sip)
                    elif record.get('version')!=5:raise PackageError('Unknown creation operation version')
                elif kind==hierarchy.KIND:
                    if info is not None:hierarchy.verify(before,after,record,info,schemas,sip)
                    elif record.get('version')!=4:raise PackageError('Unknown hierarchy operation version')
                elif kind in notes.KINDS:
                    if before!=after or record.get('version')!=3:
                        raise PackageError('Note operation changed submitted XML or has unknown version')
                    if info is not None:
                        nodes,_=fields.catalog(xml(before),info)
                        if record['changes']['node_id'] not in {n['id'] for n in nodes}:
                            raise PackageError('Retained note refers to an unknown object')
                elif kind=='restore':
                    target = next((r for r in records[:index] if r['id']==record['changes']['related_operation_id']),None)
                    if target is None or target['after_metadata_sha256']!=sha(after):raise PackageError('Invalid restored metadata target')
                elif info is not None:
                    root = xml(before);_,available=fields.catalog(root,info,record.get('version',1))
                    change = record['changes'];selected=available.get(change['field_id'])
                    if selected is None or not selected[1]['editable'] or selected[2]!=change['node_id'] or selected[1]['values']!=change['before']:
                        raise PackageError('Retained edit does not match an allowed field baseline')
                    element,field,_=selected;fields.attach_order(field,info,fields.type_name(element,info))
                    fields.apply(element,field,change['proposed'])
                    if fields.validate(root,schemas,sip)!=after:raise PackageError('Retained edit changed unapproved metadata')
            elif kind in ('undo','redo'):
                stack=undo if kind=='undo' else redo
                if not stack:raise PackageError('Invalid retained undo/redo sequence')
                entry=stack.pop()
                if record['changes']['related_operation_id']!=entry['operation_id'] or sha(after)!=entry['before' if kind=='undo' else 'after']:
                    raise PackageError('Undo/redo snapshot differs from its target')
                (redo if kind=='undo' else undo).append(entry)
            elif kind=='checkpoint':
                if before!=after or not record['changes'].get('name'):raise PackageError('Invalid checkpoint snapshot/name')
            else:raise PackageError('Unknown retained metadata event type')
            from .preparation_evidence import verify
            verify(blob(record['evidence_sha256']),records[:index+1],sip,schemas)
            seen.add(record['id']);predecessor=record['id']
        if state['head']!=records[-1]['id'] or state['metadata_sha256']!=records[-1]['after_metadata_sha256']:
            raise PackageError('Working metadata head differs from retained history')
        baseline=blob(state['baseline_metadata_sha256'])
        original=members(blob(sip['sha256']))[sip['metadata_path']]
        legacy=state.get('legacy_draft_id')
        if legacy:
            draft=next((d for d in body.get('metadata_drafts',[]) if d['id']==legacy and d['sip_id']==sip['id']),None)
            expected=blob(draft['metadata_sha256']) if draft else None
        else:expected=original
        changed=ET.tostring(xml(blob(state['metadata_sha256'])),method='c14n')!=ET.tostring(xml(original),method='c14n') or bool(note_versions[records[-1]['id']][1])
        if (baseline!=expected or records[0]['before_metadata_sha256']!=sha(baseline)
                or state['undo']!=undo or state['redo']!=redo or state['changed']!=changed):
            raise PackageError('Working metadata baseline, undo/redo or ingest hold differs from history')
        for entry in state['undo']+state['redo']:
            if entry['operation_id'] not in seen:raise PackageError('Dangling undo/redo operation')
            blob(entry['before']);blob(entry['after'])
