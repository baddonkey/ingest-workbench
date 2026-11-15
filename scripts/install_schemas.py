"""Verify and install the pinned official eCH-0160 schema bundles."""
import hashlib,json
from pathlib import Path
from zipfile import ZipFile
ROOT=Path(__file__).resolve().parents[1]
manifest=json.loads((ROOT/'references'/'provenance.json').read_text(encoding='utf-8'))
records=manifest['ech0160_schema_bundles']
for record in records:
    archive=ROOT/record['file']
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=record['sha256']:
        raise SystemExit('Schema archive checksum mismatch: '+record['file'])
    destination=ROOT/'schemas'/'ech-0160'/record['profile'];destination.mkdir(parents=True,exist_ok=True)
    with ZipFile(archive) as z:
        for n in z.namelist():
            if n.endswith('.xsd'):
                name=Path(n).name
                if (destination/name).exists() and (destination/name).read_bytes()!=z.read(n):
                    raise SystemExit('Existing trusted schema differs: '+name)
                (destination/name).write_bytes(z.read(n))
print('Installed trusted schemas:',', '.join(record['profile'] for record in records))
