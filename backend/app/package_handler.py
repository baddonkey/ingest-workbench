"""Read-only Package Turbo-style navigation over preserved, accepted SIP bytes."""
import codecs
import io
import zipfile
from pathlib import PurePosixPath
from urllib.parse import quote
from lxml import etree as ET
from .packages import NS, PackageError, members, sha, text, xml
from . import content_previews

MAX_PREVIEW = 64 * 1024
MAX_FIELDS = 500
MAX_NODES = 20000
LOGICAL = {'paket', 'ablieferung', 'ordnungssystem', 'ordnungssystemposition',
           'dossier', 'dokument', 'mappe', 'vorgang', 'aktivitaet', 'dateiRef'}
TEXT_SUFFIXES = {'.txt', '.csv', '.json', '.xml', '.log', '.md', '.yaml', '.yml', '.xsd'}


def key(kind, path):
    return kind + '-' + sha(path.encode('utf-8'))


def source(service, ident, tenant, sip_id):
    transfer = service.get(ident, tenant)
    sip = next((s for s in transfer['sips'] if s['id'] == sip_id), None)
    if sip is None:
        raise KeyError(sip_id)
    data = service.blob(sip['sha256'])
    files = members(data)
    metadata = files[sip['metadata_path']]
    return transfer, sip, data, files, xml(metadata)


def project(sip, files, root, archive_data):
    """Keep XML document order, source identities and ZIP member order separate."""
    nodes = []
    elements = {}
    payload = sip['files']

    def logical(element, parent=None, unit=None):
        if not isinstance(element.tag, str):
            return
        name = ET.QName(element).localname
        if ET.QName(element).namespace == NS['a'] and name in LOGICAL:
            path = element.getroottree().getpath(element)
            ident = key('logical', path)
            kind = ('subdossier' if name == 'dossier' and element.getparent() is not None
                    and ET.QName(element.getparent()).localname == 'dossier' else name)
            source_id = element.get('id')
            if name == 'dossier' and any(u['id'] == source_id for u in sip['units']):
                unit = source_id
            file_id = (element.text or '').strip() if name == 'dateiRef' else None
            entry = payload.get(file_id)
            title = (PurePosixPath(entry['path']).name if entry else
                     text(element, 'titel') or text(element, 'name') or source_id or name)
            node = {'id': ident, 'parent_id': parent, 'view': 'logical', 'kind': kind,
                    'title': title, 'source_id': source_id, 'path': path,
                    'reference': text(element, 'aktenzeichen') or text(element, 'nummer'),
                    'unit_id': unit}
            if entry:
                node['content_node_id'] = key('file', entry['path'])
                node['file_id'] = file_id
            nodes.append(node)
            elements[ident] = element
            parent = ident
        if len(nodes) > MAX_NODES:
            raise PackageError('Ingest Packager navigation exceeds local node limit')
        for child in element:
            logical(child, parent, unit)

    logical(root)
    nodes.append({'id': 'physical-root', 'parent_id': None, 'view': 'physical',
                  'kind': 'package', 'title': 'ZIP contents', 'path': '', 'source_id': None})
    folders = {'': 'physical-root'}
    by_path = {f['path']: f for f in payload.values()}
    inventory_elements = {}
    prefix = sip['metadata_path'][:-len('header/metadata.xml')]
    for element in root.findall('./a:inhaltsverzeichnis//a:datei', NS):
        ancestors = [text(parent, 'name') for parent in element.iterancestors()
                     if parent.tag == '{' + NS['a'] + '}ordner']
        path = prefix + '/'.join([*reversed(ancestors), text(element, 'name')])
        inventory_elements[path] = element
    with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
        entries = [(entry.orig_filename.rstrip('/'), entry.is_dir()) for entry in archive.infolist()]
    for path, is_directory in entries:
        parent = 'physical-root'
        parts = path.split('/')
        for index, part in enumerate(parts if is_directory else parts[:-1], 1):
            folder = '/'.join(parts[:index])
            if folder not in folders:
                ident = key('folder', folder)
                folders[folder] = ident
                nodes.append({'id': ident, 'parent_id': parent, 'view': 'physical',
                              'kind': 'folder', 'title': part, 'path': folder, 'source_id': None})
            parent = folders[folder]
        if is_directory:
            if len(nodes) > MAX_NODES:
                raise PackageError('Ingest Packager navigation exceeds local node limit')
            continue
        content = files[path]
        entry = by_path.get(path)
        ident = key('file', path)
        submitted = inventory_elements.get(path)
        if submitted is not None:
            elements[ident] = submitted
        nodes.append({'id': ident, 'parent_id': parent, 'view': 'physical',
                      'kind': 'file', 'title': parts[-1], 'path': path,
                      'source_id': submitted.get('id') if submitted is not None else None, 'size': len(content),
                      'payload': entry is not None, 'sha256': sha(content)})
        if len(nodes) > MAX_NODES:
            raise PackageError('Ingest Packager navigation exceeds local node limit')
    return nodes, elements


def workspace(service, ident, tenant, sip_id):
    from .metadata_drafts import summary
    transfer, sip, data, files, root = source(service, ident, tenant, sip_id)
    nodes, _ = project(sip, files, root, data)
    findings = []
    draft = summary(service, transfer, sip)
    if draft['active']:
        findings.append({'code': 'metadata.draft', 'severity': 'blocked', 'message': 'Saved metadata draft awaits resolution.', 'action': 'Review draft differences; discard or explicitly promote a derived SIP before archival review.'})
    return {'sip_id': sip_id, 'input_sha256': sip['sha256'], 'original_filename': sip.get('original_filename'), 'profile': sip['profile'],
            'kind': sip['kind'], 'transfer_revision': transfer['revision'], 'read_only': True,
            'metadata_path': sip['metadata_path'], 'nodes': nodes,
            'statistics': {'payload_files': len(sip['files']), 'payload_bytes': sum(f['size'] for f in sip['files'].values()),
                           'zip_members': len(files), 'top_level_dossiers': len(sip['units'])},
            'validation': {'internal': 'PASS', 'internal_scope': 'Trusted schema, inventory, submitted checksums and references at receipt',
                           'acceptance': 'CLOSED'}, 'findings': findings, 'draft': draft,
            'exports': [r for r in transfer.get('sip_exports', []) if r['sip_id'] == sip_id]}


def preview(path, content):
    if PurePosixPath(path).suffix.lower() not in TEXT_SUFFIXES:
        return {'available': False, 'reason': 'Binary or active content is available as an attachment only.'}
    sample = content[:MAX_PREVIEW]
    try:
        value = codecs.getincrementaldecoder('utf-8-sig')().decode(sample, final=len(content) <= MAX_PREVIEW)
    except UnicodeError:
        return {'available': False, 'reason': 'Text is not UTF-8; original bytes remain downloadable.'}
    if any(ord(c) < 32 and c not in '\n\r\t' for c in value):
        return {'available': False, 'reason': 'Binary control characters prevent text preview.'}
    return {'available': True, 'text': value, 'truncated': len(content) > MAX_PREVIEW,
            'byte_limit': MAX_PREVIEW, 'encoding': 'UTF-8'}


def node_detail(service, ident, tenant, sip_id, node_id):
    from .metadata_drafts import edit_fields
    body, sip, data, files, root = source(service, ident, tenant, sip_id)
    nodes, elements = project(sip, files, root, data)
    by_id = {n['id']: n for n in nodes}
    node = by_id.get(node_id)
    if node is None:
        raise KeyError(node_id)
    result = {'node': node, 'fields': [], 'fields_truncated': False, 'xml': None,
              'file': None, 'preview': None, 'input_sha256': sip['sha256'],
              'edit_fields': edit_fields(service, body, sip, files, root, data, node_id)}
    element = elements.get(node_id)
    if element is not None:
        fields = []

        def walk(current, path):
            if not isinstance(current.tag, str):
                return
            for name, value in current.attrib.items():
                fields.append({'path': path + '/@' + name, 'value': value})
            if current.text and current.text.strip():
                fields.append({'path': path, 'value': current.text})
            for index, child in enumerate(current):
                if not isinstance(child.tag, str):
                    continue
                if ET.QName(child).namespace == NS['a'] and ET.QName(child).localname in LOGICAL:
                    continue
                walk(child, path + '/' + ET.QName(child).localname + '[' + str(index + 1) + ']')

        walk(element, ET.QName(element).localname)
        result['fields'] = fields[:MAX_FIELDS]
        result['fields_truncated'] = len(fields) > MAX_FIELDS
        fragment = ET.tostring(element, encoding='unicode')
        result['xml'] = fragment[:MAX_PREVIEW]
        result['xml_truncated'] = len(fragment) > MAX_PREVIEW
    file_node = node if node['kind'] == 'file' else by_id.get(node.get('content_node_id'))
    if file_node:
        result['file'] = file_node
        result['preview'] = preview(file_node['path'], files[file_node['path']])
        result['rich_preview'] = content_previews.project(file_node['path'], files[file_node['path']])
    return result


def content(service, ident, tenant, sip_id, node_id):
    _, sip, data, files, root = source(service, ident, tenant, sip_id)
    nodes, _ = project(sip, files, root, data)
    node = next((n for n in nodes if n['id'] == node_id and n['kind'] == 'file'), None)
    if node is None:
        raise KeyError(node_id)
    return files[node['path']], PurePosixPath(node['path']).name


def attachment_headers(filename):
    return {'Content-Disposition': "attachment; filename*=UTF-8''" + quote(filename, safe=''),
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Content-Security-Policy': "default-src 'none'; sandbox"}
