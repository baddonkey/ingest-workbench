"""Trusted-profile field forms and controlled XML edits; preserve unexposed content."""
import re
from lxml import etree as ET
from .editor_xsd import SchemaInfo, XS
from .editor_labels import FIELDS
from .packages import NS, PackageError, xml, sha, ech_schema_dir
from .package_handler import key

A = '{' + NS['a'] + '}'
XSI = '{http://www.w3.org/2001/XMLSchema-instance}type'
OBJECTS = {'paket','ablieferung','provenienz','ordnungssystem','ordnungssystemposition','dossier',
           'dokument','vorgang','aktivitaet','mappe','datei','ordner','archivischerVorgang'}
STRUCTURAL = OBJECTS | {'inhaltsverzeichnis','dateiRef','unstrukturierterAnhang','archivischeNotiz'}
PROTECTED = {'paketTyp','ablieferungstyp','pruefalgorithmus','pruefsumme'}


def identity_path(element, version=2, identities=None):
    if version==1:return element.getroottree().getpath(element)
    parts=[]
    current=element
    while current is not None:
        if current.get('id'):
            suffix='/'.join(reversed(parts))
            path = 'source-id:'+current.get('id')+('/'+suffix if suffix else '')
            break
        parent=current.getparent()
        siblings=[e for e in parent if e.tag==current.tag] if parent is not None else [current]
        parts.append(ET.QName(current).localname+'['+str(siblings.index(current)+1)+']')
        current=parent
    else:path = '/'+ '/'.join(reversed(parts))
    if identities:
        prefix, suffix = path, []
        while prefix:
            if prefix in identities:
                return identities[prefix]+('/'+'/'.join(reversed(suffix)) if suffix else '')
            prefix, separator, part = prefix.rpartition('/')
            if not separator:break
            suffix.append(part)
    return path


def identity_bindings(root, identities=None):
    return {element:identity_path(element,identities=identities) for element in root.iter()
            if isinstance(element.tag,str) and element.tag in {A+kind for kind in OBJECTS}
            and not element.get('id')}


def validate_identities(root, identities):
    if not isinstance(identities,dict) or len(identities)>20000 or any(
            not isinstance(k,str) or not isinstance(v,str) or not k or not v
            or len(k)>8000 or len(v)>8000 for k,v in identities.items()):
        raise PackageError('Invalid editor identity sidecar')
    if identities and (set(identities)!={identity_path(e) for e in identity_bindings(root)}
                       or len(set(identities.values()))!=len(identities)):
        raise PackageError('Editor identities differ from the selected XML objects')


def identity_snapshots(records):
    current={};result={}
    for record in records:
        before=current
        retained=record['changes'].get('identities')
        if retained is not None:
            if (record.get('version')!=10 or record['kind']!='structure-reorder'
                    or not isinstance(retained,dict) or set(retained)!={'version','before','after'}
                    or retained['version']!=1 or retained['before']!=current):
                raise PackageError('Editor identity history binding mismatch')
            current=retained['after']
        elif record.get('version')==10:
            raise PackageError('Missing retained editor identities')
        elif record['kind'] in ('undo','redo','restore'):
            target=result.get(record['changes']['related_operation_id'])
            if target is None:raise PackageError('Editor identities have an unknown compensation target')
            current=target[0 if record['kind']=='undo' else 1]
        result[record['id']]=(before,current)
    return result


def current_identities(records):
    versions=identity_snapshots(records)
    return versions[records[-1]['id']][1] if records else {}


def identity_document(records):
    return ({'version':1,'head':records[-1]['id'],'current':current_identities(records)}
            if any(r.get('version')==10 for r in records) else None)


def object_title(element):
    kind=ET.QName(element).localname
    return (element.findtext(A+'titel') or element.findtext(A+'name')
            or element.findtext(A+'aktenbildnerName') or element.findtext(A+'ablieferndeStelle')
            or ((element.text or '').strip() if kind in ('vorgang','aktivitaet') else '')
            or element.get('id') or label(kind))


def schema(schemas, sip):
    folder = ech_schema_dir(schemas, sip['profile'])
    digest = sha(b''.join(p.name.encode() + p.read_bytes() for p in sorted(folder.glob('*.xsd'))))
    if digest != sip['schema_bundle_sha256']:
        raise PackageError('Trusted schema bundle changed since receipt')
    info = SchemaInfo(folder)
    # Name anonymous local types only in the parsed in-memory schema model.
    for definition in list(info._complex.values()):
        for element in definition.iter(XS+'element'):
            if element.get('type') or not element.get('name'):
                continue
            anonymous = element.find(XS+'complexType')
            if anonymous is not None:
                name = '_inline_'+str(len(info._complex))
                info._complex[name] = anonymous
                element.set('type', name)
    return info


def type_name(element, info):
    if element.get(XSI):
        return element.get(XSI).split(':')[-1]
    parent = element.getparent()
    if parent is not None:
        definition = info.element(type_name(parent, info), ET.QName(element).localname)
        if definition:
            return definition.type
    return ET.QName(element).localname


def label(tag):
    for fields in FIELDS.values():
        found = next((f.label for f in fields if f.tag == tag), None)
        if found:
            return found
    return re.sub(r'([a-z])([A-Z])', r'\1 \2', tag).replace('_',' ').capitalize()


def object_kind(element):
    kind = ET.QName(element).localname
    if kind == 'dossier' and element.getparent() is not None and ET.QName(element.getparent()).localname == 'dossier':
        return 'subdossier'
    return kind


def definition(item, info, depth=0, identity_version=2):
    if depth > 8:
        raise PackageError('Metadata form nesting limit exceeded')
    simple = info.simple(item.type)
    complex_type = info._complex.get(item.type)
    if complex_type is not None:
        extension = complex_type.find(XS+'simpleContent/'+XS+'extension')
        if extension is not None:
            simple = info.simple(extension.get('base','').split(':')[-1])
    children = [definition(child,info,depth+1,identity_version) for child in info.elements(item.type)
                if child.name not in STRUCTURAL]
    # A union such as date | gYear | 'keine Angabe' remains free text with hints.
    union = info._simple.get(item.type)
    open_union = union is not None and union.find(XS+'union') is not None
    return {'tag':item.name,'label':label(item.name),'type':item.type,'minimum':item.min_occurs,'identity_version':identity_version,
            'maximum':item.max_occurs,'choices':['true','false'] if simple.boolean else simple.enums,
            'choice_only':bool(simple.boolean or simple.enums) and not open_union,
            'max_length':min(simple.max_length or 8000,8000),'children':children,
            'attributes':[{'name':a.name,'required':a.required,'label':label(a.name)}
                          for a in info.attributes(item.type) if a.name not in ('id','schemaVersion')],
            'date':item.type in ('historischerZeitpunkt','historischerZeitraum','zeitpunkt','zeitraum','datumTypA','datumTypB')}


def values(parent, field, identities=None):
    result = []
    for element in parent.findall(A+field['tag']):
        row = {'id':key('row',identity_path(element,field.get('identity_version',2),identities)), 'text':element.text or '',
               'attributes':{a['name']:element.get(a['name'],'') for a in field['attributes']},
               'children':{child['tag']:values(element,child,identities) for child in field['children']}}
        result.append(row)
    return result


def catalog(root, info, identity_version=2, identities=None):
    if identities is not None:validate_identities(root,identities)
    nodes, available = [], {}
    for element in root.iter():
        if not isinstance(element.tag,str) or not element.tag.startswith(A) or ET.QName(element).localname not in OBJECTS:
            continue
        kind = object_kind(element)
        path = identity_path(element,identity_version,identities)
        node_id = key('logical',path)
        name = object_title(element)
        nodes.append({'id':node_id,'kind':kind,'label':label(kind),'title':name,'path':path,
                      'source_id':element.get('id'),'depth':sum(1 for p in element.iterancestors() if isinstance(p.tag,str) and ET.QName(p).localname in OBJECTS)})
        fields = []
        for item in info.elements(type_name(element,info)):
            if item.name in STRUCTURAL:
                continue
            field = definition(item,info,identity_version=identity_version)
            field['id'] = key('editor-field',path+'/'+item.name)
            field['editable'] = item.name not in PROTECTED and not (kind in ('datei','ordner') and item.name=='name')
            field['values'] = values(element,field,identities)
            fields.append(field)
            available[field['id']] = (element,field,node_id)
        nodes[-1]['fields'] = fields
    if len(nodes)>20000 or len(available)>50000:
        raise PackageError('Metadata form exceeds local workspace limit')
    return nodes, available


def navigation(root, nodes, identities=None):
    by_id = {node['id']: node for node in nodes}
    elements = {key('logical', identity_path(element,identities=identities)): element for element in root.iter()
                if isinstance(element.tag, str) and element.tag.startswith(A)
                and ET.QName(element).localname in OBJECTS}
    element_ids = {element: ident for ident, element in elements.items()}
    children = {ident: [] for ident in elements}
    parents = {}
    for ident, element in elements.items():
        parent = next((element_ids[ancestor] for ancestor in element.iterancestors() if ancestor in element_ids), None)
        parents[ident] = parent
        if parent:
            children[parent].append(ident)
    files = {element.get('id'): ident for ident, element in elements.items()
             if element.tag == A+'datei' and element.get('id')}
    references = {}
    for reference in root.iter(A+'dateiRef'):
        owner = next((element_ids[ancestor] for ancestor in reference.iterancestors() if ancestor in element_ids), None)
        file_id = files.get((reference.text or '').strip())
        if owner and file_id and file_id not in references.setdefault(owner, []):
            references[owner].append(file_id)

    def tree(view, roots, inventory=False):
        rows = []

        def visit(ident, parent=None, depth=0):
            row_id = key('navigator', view+'/'+(parent or '')+'/'+ident)
            rows.append({'key': row_id, 'object_id': ident, 'parent_key': parent, 'depth': depth})
            for child in children[ident]:
                if (by_id[child]['kind'] in ('ordner', 'datei')) == inventory:
                    visit(child, row_id, depth+1)
            if not inventory:
                for file_id in references.get(ident, []):
                    file_key = key('navigator', view+'/'+row_id+'/'+file_id)
                    rows.append({'key': file_key, 'object_id': file_id, 'parent_key': row_id, 'depth': depth+1})

        for ident in roots:
            visit(ident)
        if len(rows) > 40000:
            raise PackageError('Metadata navigation exceeds local workspace limit')
        return rows

    classification = [ident for ident, node in by_id.items() if node['kind'] == 'ordnungssystem']
    dossiers = [ident for ident, element in elements.items() if element.tag == A+'dossier'
                and not any(ancestor.tag == A+'dossier' for ancestor in element.iterancestors())]
    package = [ident for ident, node in by_id.items() if node['kind'] == 'paket']
    return {'classification': tree('classification', classification),
        'hierarchy': tree('hierarchy', dossiers), 'inventory': tree('inventory', package, inventory=True)}


def apply(parent, field, proposed, budget=None, identities=None):
    budget = budget if budget is not None else [0]
    if not isinstance(proposed,list) or len(proposed)>200 or len(proposed)<field['minimum'] or (field['maximum'] is not None and len(proposed)>field['maximum']):
        raise PackageError('Invalid occurrence count: '+field['label'])
    current = {row['id']:element for row,element in zip(values(parent,field,identities),parent.findall(A+field['tag']))}
    seen = set(); replacements = []
    for row in proposed:
        budget[0] += 1
        if budget[0]>1000 or not isinstance(row,dict) or set(row)!={'id','text','attributes','children'}:
            raise PackageError('Invalid or oversized metadata row')
        ident = row['id']
        if not isinstance(ident,str) or ident in seen or (ident not in current and re.fullmatch(r'new-[0-9a-f-]{36}',ident) is None):
            raise PackageError('Unknown or duplicate metadata row identity')
        seen.add(ident)
        element = current[ident] if ident in current else ET.Element(A+field['tag'])
        value = row['text']
        if not isinstance(value,str) or len(value)>field['max_length'] or (field['choice_only'] and value not in field['choices']):
            raise PackageError('Invalid field value: '+field['label'])
        if field['children'] and value.strip():
            raise PackageError('A grouped metadata field cannot contain free text')
        if not isinstance(row['attributes'],dict) or set(row['attributes'])!={a['name'] for a in field['attributes']}:
            raise PackageError('Unsupported metadata attributes')
        if not isinstance(row['children'],dict) or set(row['children'])!={c['tag'] for c in field['children']}:
            raise PackageError('Unsupported metadata child fields')
        try:
            element.text = value or None
            for name,text in row['attributes'].items():
                if not isinstance(text,str) or len(text)>8000:
                    raise PackageError('Invalid metadata attribute value')
                if text:element.set(name,text)
                else:element.attrib.pop(name,None)
        except ValueError as exc:
            raise PackageError('Characters forbidden in XML') from exc
        for child in field['children']:
            apply(element,child,row['children'][child['tag']],budget,identities)
        replacements.append(element)
    old = parent.findall(A+field['tag'])
    if old:
        index = parent.index(old[0])
    else:
        # Schema order is attached by the caller for each parent type.
        order = field.get('order',[])
        later = set(order[order.index(field['tag'])+1:]) if field['tag'] in order else set()
        index = next((i for i,e in enumerate(parent) if isinstance(e.tag,str) and ET.QName(e).localname in later),len(parent))
    for element in old:parent.remove(element)
    for offset,element in enumerate(replacements):parent.insert(index+offset,element)


def attach_order(field, info, parent_type):
    field['order'] = [item.name for item in info.elements(parent_type)]
    for child in field['children']:
        attach_order(child,info,field['type'])


def validate(root, schemas, sip):
    validator = ET.XMLSchema(ET.parse(str(ech_schema_dir(schemas,sip['profile'])/'arelda.xsd'),ET.XMLParser(resolve_entities=False,no_network=True)))
    if not validator.validate(root):
        raise PackageError('Metadata violates the selected trusted profile: '+str(validator.error_log.last_error)[:900])
    result = ET.tostring(root.getroottree(),encoding='UTF-8',xml_declaration=True)
    if len(result)>8*1024*1024:
        raise PackageError('Metadata exceeds the local XML limit')
    return result
