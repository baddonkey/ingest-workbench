"""Read-only statistics over an exact applied metadata version and retained payload."""
from . import editor_fields as fields


def project(root, nodes, payload, binding, identities=None):
    by_id = {node['id']: node for node in nodes}
    elements = {element: fields.key('logical', fields.identity_path(element,identities=identities))
                for element in root.iter() if isinstance(element.tag, str)
                and element.tag.startswith(fields.A)
                and element.tag[len(fields.A):] in fields.OBJECTS}
    counts = {}
    empty, required = [], []
    slots, populated = 0, 0

    def item(node, detail, field_id=None):
        return {'node_id': node['id'] if node else None,
                'title': node['title'] if node else detail,
                'detail': detail, 'field_id': field_id}

    def inspect(node, field, rows, path, field_id):
        nonlocal slots, populated
        if len(rows) < field['minimum']:
            required.append(item(node, path, field_id))
        if not field['children']:
            slots += 1
            if any(row['text'].strip() for row in rows):
                populated += 1
            else:
                empty.append(item(node, path, field_id))
        for index, row in enumerate(rows):
            position = path+'['+str(index+1)+']'
            for attribute in field['attributes']:
                if attribute['required'] and not row['attributes'].get(attribute['name'], '').strip():
                    required.append(item(node, position+'/@'+attribute['name'], field_id))
            for child in field['children']:
                inspect(node, child, row['children'].get(child['tag'], []),
                        position+'/'+child['tag'], field_id)

    for node in nodes:
        counts.setdefault(node['kind'], []).append(item(node, node['path']))
        for field in node['fields']:
            inspect(node, field, field['values'], field['tag'], field['id'])

    file_nodes = {node['source_id']: node for node in nodes if node['kind'] == 'datei'}
    inventory = {element.get('id') for element in root.iter(fields.A+'datei')}
    referenced, broken = set(), []
    for reference in root.iter(fields.A+'dateiRef'):
        target = (reference.text or '').strip()
        referenced.add(target)
        if target not in inventory:
            owner = next((by_id[elements[ancestor]] for ancestor in reference.iterancestors()
                          if ancestor in elements and elements[ancestor] in by_id), None)
            broken.append(item(owner, target))
    ordered_payload = sorted(payload.items(), key=lambda pair: (pair[1]['path'], pair[0]))
    payload_items = [item(file_nodes.get(ident), entry['path']+' | '+str(entry['size'])+' B')
                     for ident, entry in ordered_payload]
    unreferenced = [item(file_nodes.get(ident), entry['path'])
                    for ident, entry in ordered_payload if ident not in referenced]
    return {'binding': binding, 'objects': counts, 'payload_files': payload_items,
            'payload_bytes': sum(entry['size'] for entry in payload.values()),
            'scalar_fields': slots, 'populated_scalar_fields': populated,
            'empty_scalar_fields': empty, 'missing_required': required,
            'broken_references': broken, 'unreferenced_payload': unreferenced}
