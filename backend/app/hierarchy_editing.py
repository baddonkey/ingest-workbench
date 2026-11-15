"""Conservative logical sibling reordering; no identity or boundary reassignment."""
from lxml import etree as ET
from . import editor_fields as fields
from .packages import PackageError, xml
from .package_handler import key
from .store import Conflict

KIND = 'structure-reorder'
CHILD_KINDS = {'ordnungssystemposition', 'dossier', 'dokument', 'mappe', 'vorgang', 'aktivitaet'}
MAX_SIBLINGS = 200


def ident(element):
    return key('logical', fields.identity_path(element))


def groups(root):
    """Only complete, identified same-kind groups under a logical object qualify."""
    result = []
    for parent in root.iter():
        if not isinstance(parent.tag, str) or parent.tag not in {fields.A+k for k in CHILD_KINDS|{'ordnungssystem'}}:
            continue
        for kind in sorted(CHILD_KINDS):
            children = parent.findall(fields.A+kind)
            if not 2 <= len(children) <= MAX_SIBLINGS or not all(c.get('id') for c in children):
                continue
            result.append({'parent_id': ident(parent), 'child_kind': kind,
                           'children': [{'id':ident(c), 'source_id':c.get('id'),
                                         'title':c.findtext(fields.A+'titel') or c.get('id')} for c in children]})
    return result


def apply(root, info, request):
    parent_id, child_kind = request.get('parent_id'), request.get('child_kind')
    group = next((g for g in groups(root) if g['parent_id']==parent_id and g['child_kind']==child_kind), None)
    if group is None:
        raise PackageError('Reordering requires 2–200 identified logical siblings of the same kind')
    before = [c['id'] for c in group['children']]
    if request.get('before_order') != before:
        raise Conflict('Sibling order changed; reload before applying this proposal')
    order = request.get('after_order')
    if not isinstance(order, list) or not all(isinstance(value,str) for value in order) or len(order)!=len(before) or len(set(order))!=len(order) or set(order)!=set(before):
        raise PackageError('Sibling order must be a complete permutation of the current group')
    if order == before:
        raise PackageError('No hierarchy order changes to apply')
    # Native IDs on every sibling make descendants' existing source-ID-based
    # identities independent of sibling position. Also verify the entire catalog.
    prior_nodes, prior_fields = fields.catalog(root, info)
    parent = next(e for e in root.iter() if isinstance(e.tag,str) and e.tag in {fields.A+k for k in fields.OBJECTS} and ident(e)==parent_id)
    children = list(parent)
    slots = [i for i,c in enumerate(children) if c.tag==fields.A+child_kind]
    by_id = {ident(children[i]):children[i] for i in slots}
    for slot, node_id in zip(slots, order):
        children[slot] = by_id[node_id]
    parent[:] = children
    after_nodes, after_fields = fields.catalog(root, info)
    if ({n['id']:(n['kind'],n['source_id'],n['path']) for n in prior_nodes}
            != {n['id']:(n['kind'],n['source_id'],n['path']) for n in after_nodes}
            or set(prior_fields)!=set(after_fields)):
        raise PackageError('Hierarchy reorder would change an existing object or field identity')
    return {'parent_id':parent_id, 'child_kind':child_kind, 'before_order':before,
            'after_order':order, 'objects':group['children']}


def verify(before, after, record, info, schemas, sip):
    change = record['changes']
    if record.get('version') != 4 or set(change) != {'parent_id','child_kind','before_order','after_order','objects'}:
        raise PackageError('Unknown retained hierarchy operation contract')
    root = xml(before)
    actual = apply(root, info, change)
    if actual != change or fields.validate(root, schemas, sip) != after:
        raise PackageError('Retained hierarchy operation changed unapproved structure or metadata')
