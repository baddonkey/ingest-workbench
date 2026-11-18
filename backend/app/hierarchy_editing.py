"""Logical sibling reordering with retained identities; no boundary reassignment."""
from lxml import etree as ET
from . import editor_fields as fields
from .packages import PackageError, xml
from .package_handler import key
from .store import Conflict

KIND = 'structure-reorder'
CHILD_KINDS = {'ordnungssystemposition', 'dossier', 'dokument', 'mappe', 'vorgang', 'aktivitaet'}
MAX_SIBLINGS = 200


def ident(element, identities=None):
    return key('logical', fields.identity_path(element,identities=identities))


def groups(root, identities=None):
    """Only complete, identified same-kind groups under a logical object qualify."""
    result = []
    for parent in root.iter():
        if not isinstance(parent.tag, str) or parent.tag not in {fields.A+k for k in CHILD_KINDS|{'ordnungssystem'}}:
            continue
        for kind in sorted(CHILD_KINDS):
            children = parent.findall(fields.A+kind)
            if not 2 <= len(children) <= MAX_SIBLINGS or kind not in ('vorgang','aktivitaet') and not all(c.get('id') for c in children):
                continue
            result.append({'parent_id': ident(parent,identities), 'child_kind': kind,
                           'children': [{'id':ident(c,identities), 'source_id':c.get('id'),
                                         'title':fields.object_title(c)} for c in children]})
    return result


def apply(root, info, request, identities=None):
    parent_id, child_kind = request.get('parent_id'), request.get('child_kind')
    group = next((g for g in groups(root,identities) if g['parent_id']==parent_id and g['child_kind']==child_kind), None)
    if group is None:
        raise PackageError('Reordering requires 2–200 supported logical siblings of the same kind')
    before = [c['id'] for c in group['children']]
    if request.get('before_order') != before:
        raise Conflict('Sibling order changed; reload before applying this proposal')
    order = request.get('after_order')
    if not isinstance(order, list) or not all(isinstance(value,str) for value in order) or len(order)!=len(before) or len(set(order))!=len(order) or set(order)!=set(before):
        raise PackageError('Sibling order must be a complete permutation of the current group')
    if order == before:
        raise PackageError('No hierarchy order changes to apply')
    prior_nodes, prior_fields = fields.catalog(root, info,identities=identities)
    bindings=fields.identity_bindings(root,identities)
    parent = next(e for e in root.iter() if isinstance(e.tag,str) and e.tag in {fields.A+k for k in fields.OBJECTS} and ident(e,identities)==parent_id)
    children = list(parent)
    slots = [i for i,c in enumerate(children) if c.tag==fields.A+child_kind]
    by_id = {ident(children[i],identities):children[i] for i in slots}
    retained=any(not child.get('id') for child in by_id.values())
    for slot, node_id in zip(slots, order):
        children[slot] = by_id[node_id]
    parent[:] = children
    after_identities={fields.identity_path(element):value for element,value in bindings.items()} if retained else identities
    after_nodes, after_fields = fields.catalog(root, info,identities=after_identities)
    if ({n['id']:n for n in prior_nodes} != {n['id']:n for n in after_nodes}
            or set(prior_fields)!=set(after_fields)):
        raise PackageError('Hierarchy reorder would change an existing object or field identity')
    change={'parent_id':parent_id, 'child_kind':child_kind, 'before_order':before,
            'after_order':order, 'objects':group['children']}
    if retained:change['identities']={'version':1,'before':dict(identities or {}),'after':after_identities}
    return change


def verify(before, after, record, info, schemas, sip, identities=None):
    change = record['changes']
    expected={'parent_id','child_kind','before_order','after_order','objects'}
    if record.get('version')==10:expected.add('identities')
    if record.get('version') not in (4,10) or set(change) != expected:
        raise PackageError('Unknown retained hierarchy operation contract')
    root = xml(before)
    actual = apply(root, info, change,identities)
    if actual != change or fields.validate(root, schemas, sip) != after:
        raise PackageError('Retained hierarchy operation changed unapproved structure or metadata')
