"""Schema-driven native-ID logical objects; append without changing existing identities."""
import re
from lxml import etree as ET
from . import editor_fields as fields
from .hierarchy_editing import ident
from .packages import PackageError, xml

KIND = 'structure-create'
MAX_CREATED = 20
CHILD_KINDS = {'ordnungssystemposition', 'dossier', 'dokument', 'mappe'}


def options(root, info, identities=None):
    result = []
    for parent in root.iter():
        if not isinstance(parent.tag, str) or parent.tag not in {fields.A+k for k in fields.OBJECTS}:
            continue
        for item in info.elements(fields.type_name(parent, info)):
            if item.name not in CHILD_KINDS or not any(a.name=='id' for a in info.attributes(item.type)):
                continue
            existing = len(parent.findall(fields.A+item.name))
            maximum = min(MAX_CREATED, item.max_occurs-existing) if item.max_occurs is not None else MAX_CREATED
            if maximum <= 0:continue
            definitions = [fields.definition(child, info) for child in info.elements(item.type)
                           if child.name not in fields.STRUCTURAL]
            result.append({'parent_id':ident(parent,identities), 'child_kind':item.name, 'maximum':maximum,
                           'fields':definitions})
    return result


def apply(root, info, request, identities=None):
    option = next((o for o in options(root, info,identities) if o['parent_id']==request.get('parent_id')
                   and o['child_kind']==request.get('child_kind')), None)
    if option is None:raise PackageError('This parent/profile does not support native-ID object creation')
    count, proposed = request.get('count'), request.get('object_values')
    if type(count) is not int or not 1 <= count <= option['maximum']:
        raise PackageError('Creation count exceeds the parent/profile limit (at most 20)')
    available = {f['tag']:f for f in option['fields']}
    if not isinstance(proposed,dict) or not set(proposed)<=set(available):
        raise PackageError('Unknown creation metadata fields')
    operation_id = request.get('request_id','')
    if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',operation_id):
        raise PackageError('Invalid creation operation identity')
    prior, prior_fields = fields.catalog(root, info,identities=identities)
    parent = next(e for e in root.iter() if isinstance(e.tag,str) and e.tag in {fields.A+k for k in fields.OBJECTS}
                  and ident(e,identities)==option['parent_id'])
    parent_type = fields.type_name(parent, info)
    item = info.element(parent_type, option['child_kind'])
    ids = {e.get('id') for e in root.iter() if isinstance(e.tag,str) and e.get('id')}
    order = [e.name for e in info.elements(parent_type)]
    same = parent.findall(fields.A+item.name)
    later = set(order[order.index(item.name)+1:])
    index = parent.index(same[-1])+1 if same else next((i for i,e in enumerate(parent)
            if isinstance(e.tag,str) and ET.QName(e).localname in later),len(parent))
    created = []
    for offset in range(count):
        native_id = 'SI_'+operation_id.replace('-','')+'_'+str(offset+1)
        if native_id in ids:raise PackageError('Allocated object identity already exists')
        node = ET.Element(fields.A+item.name,id=native_id)
        # Attach first so type inference sees the trusted parent/child definition.
        parent.insert(index+offset,node)
        for field in option['fields']:
            fields.attach_order(field,info,item.type)
            fields.apply(node,field,proposed.get(field['tag'],[]),identities=identities)
        created.append({'id':ident(node),'source_id':native_id,
                        'title':node.findtext(fields.A+'titel') or native_id})
    after, after_fields = fields.catalog(root, info,identities=identities)
    indexed = {n['id']:n for n in after}
    if any(indexed.get(n['id'])!=n for n in prior) or not set(prior_fields)<=set(after_fields):
        raise PackageError('Creation would change an existing object or field identity')
    return {'parent_id':option['parent_id'],'child_kind':item.name,'count':count,
            'object_values':proposed,'objects':created}


def verify(before, after, record, info, schemas, sip, identities=None):
    change = record['changes']
    if record.get('version')!=5 or set(change)!={'parent_id','child_kind','count','object_values','objects'}:
        raise PackageError('Unknown retained object-creation contract')
    root = xml(before)
    actual = apply(root,info,{**change,'request_id':record['id']},identities)
    if actual!=change or fields.validate(root,schemas,sip)!=after:
        raise PackageError('Retained creation changed unapproved structure, metadata or references')
