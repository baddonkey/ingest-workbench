"""Bounded, offline eCH package inspection. Never trust a SIP's embedded XSD."""
from __future__ import annotations
import hashlib, io, stat, unicodedata, zipfile
from pathlib import Path, PurePosixPath
from lxml import etree as ET

NS = {'a': 'http://bar.admin.ch/arelda/v4'}
PROFILES = {'1.0': '4.0', '1.1': '4.1', '1.2.0': '5.0', '1.3.0': '5.1'}
MAX_ZIP = 64 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
MAX_FILES = 10000
MAX_XML = 8 * 1024 * 1024

class PackageError(ValueError):
    pass

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def ech_schema_dir(schemas: Path, profile: str) -> Path:
    if profile not in PROFILES:
        raise PackageError('Unsupported profile')
    return schemas / 'ech-0160' / profile

def members(data: bytes) -> dict[str, bytes]:
    if len(data) > MAX_ZIP:
        raise PackageError('Compressed package exceeds 64 MiB local reference limit')
    result, seen, total = {}, set(), 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            if len(infos) > MAX_FILES:
                raise PackageError('Too many ZIP entries')
            for entry in infos:
                # ZipInfo.filename is OS-normalized (and NUL-truncated).
                # Validate the name actually submitted in the ZIP directory.
                name = entry.orig_filename
                p = PurePosixPath(name)
                canonical = unicodedata.normalize('NFC', name.rstrip('/')).casefold()
                if (not name or p.is_absolute() or '\\' in name or ':' in name
                    or any(x in ('', '.', '..') for x in name.rstrip('/').split('/'))
                    or any(ord(c) < 32 for c in name) or canonical in seen):
                    raise PackageError('Unsafe or colliding ZIP path: ' + name)
                seen.add(canonical)
                if entry.flag_bits & 1 or stat.S_ISLNK(entry.external_attr >> 16):
                    raise PackageError('Encrypted entries and symlinks are forbidden')
                total += entry.file_size
                if total > MAX_EXPANDED or (entry.file_size > 1024 * 1024 and entry.file_size / max(1, entry.compress_size) > 1000):
                    raise PackageError('ZIP expansion limit exceeded')
                if not entry.is_dir():
                    with z.open(entry) as f:
                        content = f.read(min(entry.file_size + 1, MAX_EXPANDED + 1))
                    if len(content) != entry.file_size:
                        raise PackageError('ZIP entry length mismatch')
                    result[name] = content
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as e:
        raise PackageError('Unreadable ZIP') from e
    return result

def xml(data: bytes):
    if len(data) > MAX_XML or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise PackageError('XML exceeds limit or contains DTD/entity declarations')
    try:
        return ET.fromstring(data, ET.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False))
    except ET.XMLSyntaxError as e:
        raise PackageError('Invalid XML: ' + str(e)) from e

def text(node, name):
    return node.findtext('a:' + name, namespaces=NS) or ''

def inspect(data: bytes, profile: str, schemas: Path) -> dict:
    if profile not in PROFILES:
        raise PackageError('Unsupported profile')
    files = members(data)
    metadata = [n for n in files if n == 'header/metadata.xml' or n.endswith('/header/metadata.xml')]
    if len(metadata) != 1:
        raise PackageError('Exactly one header/metadata.xml is required')
    meta_path = metadata[0]
    prefix = meta_path[:-len('header/metadata.xml')]
    if any(not n.startswith(prefix) for n in files):
        raise PackageError('Entries outside SIP root')
    root = xml(files[meta_path])
    if root.tag != '{' + NS['a'] + '}paket' or text(root, 'paketTyp') != 'SIP':
        raise PackageError('Not an ARELDA SIP')
    declared = root.get('schemaVersion')
    if declared != PROFILES[profile]:
        raise PackageError(f'Profile {profile} requires schemaVersion {PROFILES[profile]}, received {declared}; no silent upgrade')
    schema_dir = ech_schema_dir(schemas, profile)
    schema_file = schema_dir / 'arelda.xsd'
    if not schema_file.is_file():
        raise PackageError('Trusted schema not installed: ' + profile)
    schema = ET.XMLSchema(ET.parse(str(schema_file), ET.XMLParser(resolve_entities=False, no_network=True)))
    if not schema.validate(root):
        raise PackageError('XSD validation failed: ' + str(schema.error_log.last_error))
    inventory = {}
    def walk(folder, parent):
        name = text(folder, 'name')
        if not name or name in ('.', '..') or '/' in name or '\\' in name:
            raise PackageError('Unsafe inventory folder')
        path = parent + name + '/'
        for file in folder.findall('a:datei', NS):
            fid, filename = file.get('id'), text(file, 'name')
            if not fid or fid in inventory or '/' in filename or '\\' in filename or filename in ('', '.', '..'):
                raise PackageError('Invalid file identity/path')
            full = prefix + path + filename
            if full not in files:
                raise PackageError('Inventory file missing: ' + full)
            alg = text(file, 'pruefalgorithmus')
            algorithm = {'MD5': 'md5', 'SHA-1': 'sha1', 'SHA-256': 'sha256', 'SHA-512': 'sha512'}.get(alg)
            if not algorithm:
                raise PackageError('Unsupported submitted checksum algorithm: ' + alg)
            actual = hashlib.new(algorithm, files[full], usedforsecurity=False).hexdigest()
            if actual.lower() != text(file, 'pruefsumme').lower():
                raise PackageError('Submitted checksum mismatch: ' + full)
            inventory[fid] = {'id': fid, 'path': full, 'sha256': sha(files[full]), 'size': len(files[full]), 'original_algorithm': alg, 'original_digest': actual}
        for sub in folder.findall('a:ordner', NS):
            walk(sub, path)
    table = root.find('a:inhaltsverzeichnis', NS)
    for folder in table.findall('a:ordner', NS):
        walk(folder, '')
    listed = {f['path'] for f in inventory.values()}
    extras = set(files) - listed - {meta_path}
    if extras:
        raise PackageError('Unlisted files: ' + ', '.join(sorted(extras)[:3]))
    payload = {fid: f for fid, f in inventory.items() if f['path'].startswith(prefix + 'content/')}
    if not payload:
        raise PackageError('No payload files')
    ids = {n.get('id') for n in root.iter() if n.get('id')}
    refs = []
    for ref in root.findall('.//a:dateiRef', NS):
        fid = (ref.text or '').strip()
        if fid not in payload:
            raise PackageError('Dangling/non-payload dateiRef: ' + fid)
        for attr in ('repraesentation', 'information'):
            if ref.get(attr) and ref.get(attr) not in ids:
                raise PackageError('Dangling relation: ' + ref.get(attr))
        refs.append({'file_id': fid, 'attributes': dict(ref.attrib)})
    if set(payload) != {r['file_id'] for r in refs}:
        raise PackageError('Unreferenced payload files')
    # Only top-level dossiers are selectable. Nested dossiers/maps/documents remain atomic.
    units = []
    for dossier in root.findall('.//a:dossier', NS):
        if any(parent.tag == '{' + NS['a'] + '}dossier' for parent in dossier.iterancestors()):
            continue
        selected = sorted({(r.text or '').strip() for r in dossier.findall('.//a:dateiRef', NS)})
        units.append({'id': dossier.get('id'), 'title': text(dossier, 'titel'), 'files': selected,
                      'metadata_xml': ET.tostring(dossier, encoding='unicode')})
    # Unsupported layouts are inspectable only after a profile-specific adapter is added.
    if not units or set().union(*(set(u['files']) for u in units)) != set(payload):
        raise PackageError('Payload outside supported top-level dossier layout')
    return {'profile': profile, 'schema_version': declared, 'kind': root.findtext('a:ablieferung/a:ablieferungstyp', namespaces=NS),
            'sha256': sha(data), 'metadata_path': meta_path, 'files': payload, 'units': units, 'references': refs,
            'schema_bundle_sha256': sha(b''.join(p.name.encode() + p.read_bytes() for p in sorted(schema_dir.glob('*.xsd'))))}
