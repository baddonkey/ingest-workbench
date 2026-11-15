import io,json,tempfile,unittest,zipfile
from pathlib import Path
from lxml import etree as ET
from app.packages import inspect, members, PackageError, NS, sha
from app.service import Service, canonical
from app.store import Conflict
ROOT=Path(__file__).resolve().parents[2]
REFERENCE=(ROOT/'fixtures/reference-1.3.zip').read_bytes()

def synthetic_legacy(profile):
    """Derived compatibility specimen, not an official reference for older profiles."""
    files=members(REFERENCE);meta=next(n for n in files if n.endswith('/header/metadata.xml'))
    root=ET.fromstring(files[meta]);root.set('schemaVersion',{'1.1':'4.1','1.2.0':'5.0'}[profile])
    for node in reversed(root.findall('.//a:mappe',NS)):
        parent=node.getparent();index=parent.index(node)
        children=list(node)
        for child in children:
            if child.tag.endswith('}dateiRef') or child.tag.endswith('}mappe'):
                parent.insert(index,child);index+=1
        parent.remove(node)
    if profile=='1.1':
        for node in root.findall('.//a:dateiRef',NS):node.attrib.clear()
    files[meta]=ET.tostring(root,encoding='UTF-8',xml_declaration=True)
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        for n,data in files.items():z.writestr(n,data)
    return buf.getvalue()

class PackageTests(unittest.TestCase):
    def test_official_13_reference(self):
        r=inspect(REFERENCE,'1.3.0',ROOT/'schemas')
        self.assertEqual(len(r['files']),27);self.assertEqual(len(r['units']),3)
        self.assertIn('Sub-rosamappe',r['units'][2]['metadata_xml'])
    def test_legacy_11_and_12_trusted_schema_compatibility(self):
        for profile in ('1.1','1.2.0'):
            with self.subTest(profile=profile):
                r=inspect(synthetic_legacy(profile),profile,ROOT/'schemas')
                self.assertEqual(len(r['files']),27)
    def test_no_silent_upgrade(self):
        for profile in ('1.1','1.2.0'):
            with self.assertRaises(PackageError):inspect(REFERENCE,profile,ROOT/'schemas')
    def test_traversal_and_case_collisions(self):
        for paths in [('.. /x','../escape'),('a.txt','A.txt'),('C:/test','ok')]:
            buf=io.BytesIO()
            with zipfile.ZipFile(buf,'w') as z:
                for p in paths:z.writestr(p,b'test')
            with self.assertRaises(PackageError):members(buf.getvalue())
    def test_tampering(self):
        files=members(REFERENCE);target=next(n for n in files if n.endswith('/Customers.csv'));files[target]+=b'bad'
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:
            for n,d in files.items():z.writestr(n,d)
        with self.assertRaisesRegex(PackageError,'checksum mismatch'):inspect(buf.getvalue(),'1.3.0',ROOT/'schemas')

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.s=Service(Path(self.temp.name),ROOT/'schemas')
        b=self.s.create('Reference transfer','archive-a','operator');self.ident=b['id']
        self.b=self.s.upload(self.ident,'archive-a','operator',0,'1.3.0',REFERENCE)
    def tearDown(self):self.temp.cleanup()
    def targets(self):
        s=self.b['sips'][0]
        return [{'label':u['title'],'units':[{'sip_id':s['id'],'unit_id':u['id']}]} for u in s['units']]
    def test_m_to_n_aip_candidates_have_complete_fixity_and_lineage(self):
        # Two distinct SIP profiles into 3 AIPs; merge dossier 1 from each source, etc.
        self.b=self.s.upload(self.ident,'archive-a','operator',1,'1.2.0',synthetic_legacy('1.2.0'))
        targets=[]
        for i in range(3):
            targets.append({'label':'AIP '+str(i),'units':[{'sip_id':s['id'],'unit_id':s['units'][i]['id']} for s in self.b['sips']]})
        b=self.s.plan(self.ident,'archive-a','operator',2,targets,'Group equivalent test dossiers')
        b=self.s.approve(self.ident,'archive-a','reviewer',b['revision'])
        b=self.s.build(self.ident,'archive-a','operator',b['revision'])
        self.assertEqual(b['state'],'AIP_CANDIDATE');self.assertEqual(len(b['outputs']),3)
        self.assertEqual(sum(o['files'] for o in b['outputs']),54)
        for o in b['outputs']:
            data=self.s.blob(o['sha256'])
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for line in z.read('manifest-sha256.txt').decode().splitlines():
                    digest,path=line.split('  ',1);self.assertEqual(sha(z.read(path)),digest)
                lineage=json.loads(z.read('metadata/lineage.json'))
                self.assertEqual(lineage['plan']['sha256'],b['plan']['sha256'])
                ET.fromstring(z.read('metadata/mets.xml'));ET.fromstring(z.read('metadata/premis.xml'))
                # Every METS URL resolves relative to metadata/mets.xml.
                from posixpath import normpath
                mets=ET.fromstring(z.read('metadata/mets.xml'))
                for href in mets.xpath('//@xlink:href',namespaces={'xlink':'http://www.w3.org/1999/xlink'}):
                    self.assertIn(normpath('metadata/'+__import__('urllib.parse',fromlist=['unquote']).unquote(href)),z.namelist())
        again=self.s.build(self.ident,'archive-a','operator',b['revision'])
        self.assertEqual(again['outputs'],b['outputs'])
        previous='0'*64
        for e in b['events']:
            self.assertEqual(e['previous'],previous)
            self.assertEqual(sha(canonical({k:v for k,v in e.items() if k!='hash'})),e['hash']);previous=e['hash']
    def test_one_to_one_one_to_many_and_many_to_one(self):
        for inputs,outputs in ((1,1),(1,3),(2,1)):
            with self.subTest(inputs=inputs,outputs=outputs):
                b=self.s.create('Cardinality test','archive-a','operator');ident=b['id']
                b=self.s.upload(ident,'archive-a','operator',0,'1.3.0',REFERENCE)
                if inputs==2:b=self.s.upload(ident,'archive-a','operator',b['revision'],'1.1',synthetic_legacy('1.1'))
                if outputs==1:
                    targets=[{'label':'Combined AIP','units':[{'sip_id':s['id'],'unit_id':u['id']} for s in b['sips'] for u in s['units']]}]
                else:
                    targets=[{'label':u['title'],'units':[{'sip_id':s['id'],'unit_id':u['id']}]} for s in b['sips'] for u in s['units']]
                b=self.s.plan(ident,'archive-a','operator',b['revision'],targets,'Test explicit SIP to AIP cardinality')
                b=self.s.approve(ident,'archive-a','reviewer',b['revision'])
                b=self.s.build(ident,'archive-a','operator',b['revision'])
                self.assertEqual(len(b['outputs']),outputs)
                self.assertEqual(sum(o['files'] for o in b['outputs']),27*inputs)

    def test_self_approval_is_forbidden(self):
        b=self.s.plan(self.ident,'archive-a','operator',1,self.targets(),'Archive dossier boundaries')
        with self.assertRaises(Conflict):self.s.approve(self.ident,'archive-a','operator',b['revision'])
    def test_incomplete_and_duplicate_plans_rejected(self):
        targets=self.targets()
        with self.assertRaises(PackageError):self.s.plan(self.ident,'archive-a','operator',1,targets[:1],'Incomplete')
        targets[0]['units']*=2
        with self.assertRaises(PackageError):self.s.plan(self.ident,'archive-a','operator',1,targets,'Duplicate')
    def test_tenant_isolation(self):
        with self.assertRaises(KeyError):self.s.get(self.ident,'archive-b')
    def test_sqlite_rejects_rewritten_event_history(self):
        body=self.s.get(self.ident,'archive-a')
        body['events'][0]['detail']['title']='Rewritten history'
        body['events'][0]['hash']=sha(canonical({k:v for k,v in body['events'][0].items() if k!='hash'}))
        with self.assertRaisesRegex(PackageError,'Retained workflow events cannot be replaced'):
            self.s.store.save(body,body['revision'])
        self.assertEqual(self.s.get(self.ident,'archive-a')['events'][0]['detail']['title'],'Reference transfer')
    def test_stale_writes_rejected(self):
        self.s.plan(self.ident,'archive-a','operator',1,self.targets(),'Plan A')
        with self.assertRaises(Conflict):self.s.plan(self.ident,'archive-a','operator',1,self.targets(),'Plan B')
    def test_failure_audited(self):
        with self.assertRaises(PackageError):self.s.upload(self.ident,'archive-a','operator',1,'1.1',REFERENCE)
        b=self.s.get(self.ident,'archive-a');self.assertEqual(b['events'][-1]['outcome'],'failure')
    def test_build_before_review_rejected(self):
        with self.assertRaises(Conflict):self.s.build(self.ident,'archive-a','operator',1)

if __name__=='__main__':unittest.main()
