"""Reference corpus and negative mutations: received fixture bytes are never changed."""
import io,json,tempfile,unittest,zipfile
from pathlib import Path
from posixpath import normpath
from lxml import etree as ET
from app.packages import inspect,members,xml,NS,sha,PackageError,PROFILES
from app.service import Service
ROOT=Path(__file__).resolve().parents[2]
CASES=json.loads((ROOT/'fixtures/corpus.json').read_text())['cases']

def repack(files):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_STORED) as z:
        for n,data in files.items():
            # ZipInfo normalizes os.sep on Windows. Preserve the deliberately
            # unsafe name so the negative specimen tests the actual reader.
            entry=zipfile.ZipInfo(n)
            entry.filename=n
            z.writestr(entry,data)
    return out.getvalue()

def edit_metadata(data,change):
    files=members(data);name=next(n for n in files if n.endswith('header/metadata.xml'))
    root=xml(files[name]);change(root)
    files[name]=ET.tostring(root,encoding='UTF-8',xml_declaration=True)
    return repack(files)

class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data={c['file']:(ROOT/c['file']).read_bytes() for c in CASES}
        cls.reports={c['file']:inspect(cls.data[c['file']],c['profile'],ROOT/'schemas') for c in CASES}
    def test_received_bytes_and_expected_structure(self):
        for c in CASES:
            with self.subTest(file=c['file']):
                data=self.data[c['file']];report=self.reports[c['file']]
                self.assertEqual(sha(data),c['sha256']);self.assertEqual(len(data),c['size_bytes'])
                self.assertEqual((ROOT/c['file']).read_bytes(),data)
                self.assertEqual(report['schema_version'],c['schema_version']);self.assertEqual(report['kind'],c['kind'])
                self.assertEqual(len(report['files']),c['payload_files']);self.assertEqual(len(report['units']),c['top_level_dossiers'])
    def test_wrong_profile_always_rejected(self):
        for c in CASES:
            for profile in PROFILES:
                if profile==c['profile']:continue
                with self.subTest(file=c['file'],selected=profile):
                    with self.assertRaisesRegex(PackageError,'requires schemaVersion'):
                        inspect(self.data[c['file']],profile,ROOT/'schemas')
    def test_payload_modification_rejected(self):
        for c in CASES:
            with self.subTest(file=c['file']):
                files=members(self.data[c['file']]);target=next(iter(self.reports[c['file']]['files'].values()))['path']
                files[target]+=b'\nCORRUPTED'
                with self.assertRaisesRegex(PackageError,'Submitted checksum mismatch'):
                    inspect(repack(files),c['profile'],ROOT/'schemas')
    def test_missing_payload_rejected(self):
        for c in CASES:
            with self.subTest(file=c['file']):
                files=members(self.data[c['file']]);target=next(iter(self.reports[c['file']]['files'].values()))['path'];del files[target]
                with self.assertRaisesRegex(PackageError,'Inventory file missing'):
                    inspect(repack(files),c['profile'],ROOT/'schemas')
    def test_unlisted_payload_rejected(self):
        for c in CASES:
            with self.subTest(file=c['file']):
                files=members(self.data[c['file']]);meta=self.reports[c['file']]['metadata_path'];prefix=meta[:-len('header/metadata.xml')]
                files[prefix+'content/unlisted.txt']=b'not in metadata'
                with self.assertRaisesRegex(PackageError,'Unlisted files'):
                    inspect(repack(files),c['profile'],ROOT/'schemas')
    def test_missing_required_metadata_rejected(self):
        for c in CASES:
            with self.subTest(file=c['file']):
                def change(root):root.remove(root.find('a:ablieferung',NS))
                with self.assertRaisesRegex(PackageError,'XSD validation failed'):
                    inspect(edit_metadata(self.data[c['file']],change),c['profile'],ROOT/'schemas')
    def test_dangling_reference_rejected(self):
        for c in CASES:
            with self.subTest(file=c['file']):
                def change(root):root.find('.//a:dateiRef',NS).text='NO_SUCH_FILE'
                with self.assertRaisesRegex(PackageError,'XSD validation failed|Dangling/non-payload dateiRef'):
                    inspect(edit_metadata(self.data[c['file']],change),c['profile'],ROOT/'schemas')
    def test_duplicate_dossier_id_rejected(self):
        for c in CASES:
            with self.subTest(file=c['file']):
                def change(root):
                    ds=root.findall('.//a:dossier',NS);ds[1].set('id',ds[0].get('id'))
                with self.assertRaisesRegex(PackageError,'XSD validation failed'):
                    inspect(edit_metadata(self.data[c['file']],change),c['profile'],ROOT/'schemas')
    def test_schema_location_is_not_trusted(self):
        for c in CASES:
            with self.subTest(file=c['file']):
                def change(root):root.set('{http://www.w3.org/2001/XMLSchema-instance}schemaLocation',NS['a']+' file:///nonexistent/untrusted.xsd')
                r=inspect(edit_metadata(self.data[c['file']],change),c['profile'],ROOT/'schemas')
                self.assertEqual(r['schema_bundle_sha256'],self.reports[c['file']]['schema_bundle_sha256'])
    def test_embedded_xsd_cannot_override_trusted_schema(self):
        for c in CASES:
            with self.subTest(file=c['file']):
                files=members(self.data[c['file']]);meta=self.reports[c['file']]['metadata_path'];prefix=meta[:-len('header/metadata.xml')]
                files[prefix+'header/xsd/arelda.xsd']=b'<untrusted-schema/>'
                root=xml(files[meta]);root.remove(root.find('a:ablieferung',NS));files[meta]=ET.tostring(root)
                with self.assertRaisesRegex(PackageError,'XSD validation failed'):
                    inspect(repack(files),c['profile'],ROOT/'schemas')
    def test_unsafe_zip_and_external_entity_rejected(self):
        with self.assertRaisesRegex(PackageError,'Unreadable ZIP'):members(b'not a ZIP')
        with self.assertRaisesRegex(PackageError,'DTD/entity'):xml(b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>')
        for path in ('../escape','/absolute','a/../../escape','C:/escape','a\\escape'):
            with self.subTest(path=path):
                with self.assertRaisesRegex(PackageError,'Unsafe or colliding ZIP path'):members(repack({path:b'x'}))

class RealCorpusMappingTests(unittest.TestCase):
    def test_all_four_cardinalities_with_real_reference_sips(self):
        attached=[c for c in CASES if c.get('source_library_file_id')]
        for cardinality in ('1:1','1:n','m:1','m:n'):
            # For one-input mappings, exercise every received reference independently.
            groups=[[c] for c in attached] if cardinality.startswith('1:') else [attached]
            for cases in groups:
                with self.subTest(cardinality=cardinality,files=[c['file'] for c in cases]),tempfile.TemporaryDirectory() as temp:
                    service=Service(Path(temp),ROOT/'schemas');b=service.create('Reference qualification','qualification','author')
                    for c in cases:b=service.upload(b['id'],b['tenant'],'author',b['revision'],c['profile'],(ROOT/c['file']).read_bytes())
                    expected={(s['id'],fid):f['sha256'] for s in b['sips'] for fid,f in s['files'].items()}
                    source_metadata={s['id']:members(service.blob(s['sha256']))[s['metadata_path']] for s in b['sips']}
                    buckets={}
                    for s in b['sips']:
                        for i,u in enumerate(s['units']):
                            label='Combined' if cardinality.endswith(':1') else 'AIP '+str(i)
                            buckets.setdefault(label,[]).append({'sip_id':s['id'],'unit_id':u['id']})
                    targets=[{'label':label,'units':units} for label,units in buckets.items()]
                    b=service.plan(b['id'],b['tenant'],'author',b['revision'],targets,'Qualify '+cardinality+' without changing reference bytes')
                    b=service.approve(b['id'],b['tenant'],'reviewer',b['revision'])
                    b=service.build(b['id'],b['tenant'],'author',b['revision'])
                    self.assertEqual(len(b['outputs']),len(targets));self.assertEqual(b['state'],'AIP_CANDIDATE')
                    observed={}
                    for out in b['outputs']:
                        with zipfile.ZipFile(io.BytesIO(service.blob(out['sha256']))) as z:
                            lineage=json.loads(z.read('metadata/lineage.json'))
                            for source_id in {row['sip_id'] for row in lineage['files']}:
                                self.assertEqual(z.read(f'metadata/source/{source_id}.xml'),source_metadata[source_id])
                            for row in lineage['files']:
                                key=(row['sip_id'],row['source_id']);self.assertNotIn(key,observed)
                                observed[key]=sha(z.read(row['target_path']));self.assertEqual(observed[key],row['sha256'])
                            for line in z.read('manifest-sha256.txt').decode().splitlines():
                                digest,path=line.split('  ',1);self.assertEqual(sha(z.read(path)),digest)
                            mets=ET.fromstring(z.read('metadata/mets.xml'))
                            for href in mets.xpath('//@xlink:href',namespaces={'xlink':'http://www.w3.org/1999/xlink'}):
                                self.assertIn(normpath('metadata/'+__import__('urllib.parse',fromlist=['unquote']).unquote(href)),z.namelist())
                    self.assertEqual(observed,expected)
                    for s in b['sips']:
                        self.assertEqual(sha(service.blob(s['sha256'])),s['sha256'])

if __name__=='__main__':unittest.main()
