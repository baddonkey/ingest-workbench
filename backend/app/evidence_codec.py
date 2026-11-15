"""Lossless ZIP recipes for content-addressed evidence; hashes verify reconstruction.

Use a recipe only when Python's ZIP writer reproduces the exact original bytes.
Other ZIP encodings remain opaque blobs. Recipes never change retained artifacts.
"""
import io
import json
import struct
import zipfile
from .aip import canonical
from .packages import MAX_EXPANDED, MAX_FILES, PackageError, members, sha

ATTRS = ('filename', 'date_time', 'compress_type', 'comment', 'extra', 'create_system',
         'create_version', 'extract_version', 'reserved', 'flag_bits', 'volume',
         'internal_attr', 'external_attr')


def encode_zip(data):
    if not data.startswith(b'PK\x03\x04'): return None
    contents = members(data)  # Apply the same bounded ZIP/path checks before expanding.
    entries = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if info.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):return None
            attrs = {key: getattr(info, key) for key in ATTRS}
            for key in ('comment', 'extra'): attrs[key] = attrs[key].hex()
            content = contents[info.filename] if not info.is_dir() else b''
            entries.append({'attributes': attrs, 'sha256': sha(content)})
        recipe = {'version': 1, 'comment': archive.comment.hex(), 'entries': entries}
    lookup = {sha(content): content for content in contents.values()}
    if any(e['sha256']==sha(b'') for e in entries):lookup[sha(b'')] = b''
    if decode_zip(recipe, lookup.__getitem__) != data: return None
    return recipe, lookup


def decode_zip(recipe, blob):
    if set(recipe) != {'version', 'comment', 'entries'} or recipe['version'] != 1 or len(recipe['entries']) > MAX_FILES:
        raise PackageError('Invalid evidence ZIP recipe')
    out = io.BytesIO();total = 0
    with zipfile.ZipFile(out, 'w') as archive:
        archive.comment = bytes.fromhex(recipe['comment'])
        for entry in recipe['entries']:
            if set(entry) != {'attributes', 'sha256'} or set(entry['attributes']) != set(ATTRS):
                raise PackageError('Invalid evidence ZIP entry recipe')
            attrs = entry['attributes']
            if (attrs['compress_type'] not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED)
                    or not isinstance(attrs['date_time'],(list,tuple)) or len(attrs['date_time'])!=6
                    or any(type(v) is not int for v in attrs['date_time'])):
                raise PackageError('Unsupported evidence ZIP entry encoding')
            info = zipfile.ZipInfo(attrs['filename'], tuple(attrs['date_time']))
            for key in ATTRS:
                if key in ('filename', 'date_time'): continue
                setattr(info, key, bytes.fromhex(attrs[key]) if key in ('comment', 'extra') else attrs[key])
            data = blob(entry['sha256']);total += len(data)
            if total > MAX_EXPANDED: raise PackageError('Reconstructed evidence ZIP exceeds local expanded limit')
            archive.writestr(info, data)
    data = out.getvalue()
    members(data)
    return data


class Encoder:
    def __init__(self, prefix): self.prefix, self.files, self.seen = prefix, {}, set()

    def add(self, data, depth=0):
        digest = sha(data)
        if digest in self.seen: return digest
        self.seen.add(digest)
        encoded = encode_zip(data) if depth < 8 else None
        if encoded:
            recipe, contents = encoded
            for content in contents.values(): self.add(content, depth+1)
            self.files[self.prefix+digest+'.zip-recipe.json'] = canonical(recipe)
        else: self.files[self.prefix+digest] = data
        return digest


class Decoder:
    def __init__(self, files, prefix):
        self.files, self.prefix, self.used, self.cache, self.active = files, prefix, set(), {}, set()
        self.total=0

    def __call__(self, digest):
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise PackageError('Invalid evidence blob identity')
        if digest in self.cache: return self.cache[digest]
        if digest in self.active or len(self.active) >= 16: raise PackageError('Cyclic or over-deep evidence recipe')
        self.active.add(digest)
        path = self.prefix+digest
        if path in self.files:
            data = self.files[path]
        else:
            path += '.zip-recipe.json'
            raw = self.files[path];recipe = json.loads(raw)
            if raw != canonical(recipe): raise PackageError('Non-canonical evidence recipe')
            try:data = decode_zip(recipe, self)
            except (zipfile.BadZipFile,RuntimeError,NotImplementedError,OverflowError,struct.error) as exc:
                raise PackageError('Invalid evidence ZIP reconstruction') from exc
        if sha(data) != digest: raise PackageError('Reconstructed evidence blob digest mismatch')
        self.total+=len(data)
        if self.total>MAX_EXPANDED:raise PackageError('Evidence reconstruction cache exceeds local expanded limit')
        self.active.remove(digest);self.used.add(path);self.cache[digest] = data
        return data
