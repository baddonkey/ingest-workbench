"""Profile, immutable versions and independent verification; all legal/format assertions here are test fixtures."""
import copy,io,json,tempfile,unittest,zipfile
from unittest.mock import patch
from pathlib import Path
from lxml import etree as ET
from app.service import Service
from app.packages import members,sha,PackageError
from app.aip import canonical,PROFILE_ID,profile_definition,M,P,N
from app.aip_validation import validate_package,institutional_gate
from app.store import Conflict
ROOT=Path(__file__).resolve().parents[2]

def repack(files,reseal=True):
    if reseal:files['manifest-sha256.txt']=''.join(sha(d)+'  '+p+'\n' for p,d in sorted(files.items()) if p!='manifest-sha256.txt').encode()
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for p,d in sorted(files.items()):z.writestr(p,d)
    return out.getvalue()

class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.service=Service(Path(self.temp.name),ROOT/'schemas')
        b=self.service.create('AIP profile qualification','test-archive','operator');self.b=self.service.upload(b['id'],b['tenant'],'operator',0,'1.3.0',(ROOT/'fixtures/reference-1.3.zip').read_bytes())
    def tearDown(self):self.temp.cleanup()
    def save(self,b):self.b=b;return b
    def plan(self):
        b=self.b;source=b['sips'][0];targets=[{'label':'Test archival unit','units':[{'sip_id':source['id'],'unit_id':u['id']} for u in source['units']]}]
        return self.save(self.service.plan(b['id'],b['tenant'],'operator',b['revision'],targets,'Preserve original archival hierarchy'))
    def build(self):
        b=self.b
        if b['state']=='INSPECTED':b=self.plan()
        b=self.service.approve(b['id'],b['tenant'],'reviewer',b['revision']);return self.save(self.service.build(b['id'],b['tenant'],'operator',b['revision']))
    def package(self):return self.service.blob(self.b['outputs'][-1]['sha256'])
    def complete_test_context(self,basis='other'):
        b=self.b;b=self.service.add_resource(b['id'],b['tenant'],'operator',b['revision'],'SYNTHETIC rights and format documentation','test.json','application/json',b'{"test_only":true}')
        resource=b['representation_resources'][0]['id'];source=b['sips'][0]
        context={'archive_identifier':'urn:test:archive','archive_name':'TEST ARCHIVE','designated_community':'Test evaluators','knowledge_base':'Test reading assumptions',
                 'rights':[{'sip_id':source['id'],'status':'assessed','basis':basis,'statement':'SYNTHETIC TEST GRANT, NOT REAL ARCHIVAL RIGHTS','jurisdiction':'TEST','copyright_status':'TEST',
                            'access':'restricted','source_restrictions_reviewed':True,'acts':['replicate'],'term':'bounded','start_date':'2020-01-01','end_date':'2099-12-31','resource_ids':[resource]}],
                 'formats':[{'sip_id':source['id'],'file_id':fid,'format_name':'SYNTHETIC FORMAT','format_version':'test','registry_name':'TEST REGISTRY','registry_key':fid,'resource_ids':[resource],
                             'significant_properties':{'test property':'synthetic assertion'}} for fid in source['files']]}
        return self.save(self.service.set_context(b['id'],b['tenant'],'operator',b['revision'],context))
    def test_candidate_is_structural_but_unknowns_block_preservation(self):
        self.build();out=self.b['outputs'][-1];report=validate_package(self.package(),ROOT/'schemas',expected_sha256=out['sha256'],source_resolver=self.service.blob)
        self.assertTrue(report['structural_valid'],report['errors']);self.assertFalse(report['preservation_ready']);self.assertEqual(report['original_sips_verified'],1)
        self.assertEqual(report['profile_id'],PROFILE_ID)
        self.assertFalse(any(path.startswith('integration/') for path in members(self.package())))
        self.assertEqual(report['payload_files'],27);self.assertTrue(any('rights' in r for r in report['readiness_reasons']))
    def test_retired_candidate_profiles_are_rejected(self):
        self.build()
        for version in ('1.0.0','2.0.0'):
            with self.subTest(version=version):
                files=members(self.package());descriptor=json.loads(files['aip.json'])
                descriptor['profile_version']=version;files['aip.json']=canonical(descriptor)
                report=validate_package(repack(files),ROOT/'schemas')
                self.assertFalse(report['structural_valid'])
                self.assertIn('Unknown AIP profile version',report['errors'])
    def test_context_complete_for_all_rights_bases_is_schema_checked(self):
        for basis in ('other','statute','license','copyright'):
            with self.subTest(basis=basis):
                self.complete_test_context(basis);self.build();report=validate_package(self.package(),ROOT/'schemas')
                self.assertTrue(report['structural_valid'],report['errors']);self.assertTrue(report['preservation_ready'],report['readiness_reasons'])
    def test_versions_keep_logical_identity_and_old_sealed_bytes(self):
        self.build();old=self.b['outputs'][-1];old_bytes=self.package();self.complete_test_context();self.build();new=self.b['outputs'][-1]
        self.assertEqual(old['aip_id'],new['aip_id']);self.assertEqual(new['version'],2);self.assertNotEqual(old['id'],new['id']);self.assertEqual(self.service.blob(old['sha256']),old_bytes)
        descriptor=json.loads(members(self.package())['aip.json']);self.assertEqual(descriptor['previous_version']['sha256'],old['sha256'])
        again=self.service.build(self.b['id'],self.b['tenant'],'operator',self.b['revision']);self.assertEqual(again['outputs'],self.b['outputs'])
    def test_context_update_requires_fresh_independent_approval(self):
        self.plan();self.save(self.service.approve(self.b['id'],self.b['tenant'],'reviewer',self.b['revision']));self.complete_test_context()
        self.assertEqual(self.b['state'],'PLANNED')
        with self.assertRaises(Conflict):self.service.build(self.b['id'],self.b['tenant'],'operator',self.b['revision'])
    def test_unknown_resource_or_file_is_rejected(self):
        b=self.b
        with self.assertRaises(PackageError):self.service.set_context(b['id'],b['tenant'],'operator',b['revision'],{'rights':[{'sip_id':b['sips'][0]['id'],'resource_ids':['unknown']}]})
    def test_profile_policy_cannot_be_replaced_by_package_validity(self):
        self.complete_test_context();self.build();policy=json.loads((ROOT/'config/aip-policy.json').read_text())
        self.assertFalse(institutional_gate(self.service,self.b,policy)['ready'])
        policy.update(approved=True,archive_id=self.b['tenant'],profile_id=PROFILE_ID,profile_sha256=sha(profile_definition(ROOT/'schemas')),approved_by='TEST POLICY REVIEWER',approval_reference='urn:test:policy-approval')
        self.assertTrue(institutional_gate(self.service,self.b,policy)['ready'])
        old_report=validate_package(self.package(),ROOT/'schemas')
        old_report['profile_id']='urn:ch-archive:aip-profile:1.0.0'
        with patch('app.aip_validation.validate_package',return_value=old_report):
            self.assertFalse(institutional_gate(self.service,self.b,policy)['ready'])
        policy['archive_id']='other';self.assertFalse(institutional_gate(self.service,self.b,policy)['ready'])
    def test_manifest_and_external_digest_detect_corruption(self):
        self.build();files=members(self.package());path=next(p for p in files if p.startswith('objects/'));files[path]+=b'corrupt'
        self.assertFalse(validate_package(repack(files,False),ROOT/'schemas')['structural_valid'])
        self.assertFalse(validate_package(self.package(),ROOT/'schemas',expected_sha256='0'*64)['structural_valid'])
    def test_resealed_semantic_corruption_is_rejected(self):
        self.build();original=members(self.package())
        def mutate_json(path,callback):
            def mutation(files):
                body=json.loads(files[path]);callback(body);files[path]=canonical(body)
            return mutation
        def change_mets(files):
            root=ET.fromstring(files['metadata/mets.xml']);root.find('.//{'+M+'}fptr').set('FILEID','F_UNKNOWN');files['metadata/mets.xml']=ET.tostring(root)
        def change_premis(files):
            root=ET.fromstring(files['metadata/premis.xml']);root.find('.//{'+P+'}size').text='999';files['metadata/premis.xml']=ET.tostring(root)
        mutations=[mutate_json('metadata/lineage.json',lambda b:b['files'].pop()),mutate_json('metadata/description.json',lambda b:b['sources'][0]['units'][0]['unit'].update(title='FORGED')),
                   mutate_json('aip.json',lambda b:b.update(version=999)),mutate_json('aip.json',lambda b:b['approval'].update(approver=b['approval']['author'])),
                   mutate_json('evidence/events.json',lambda b:b[0].update(actor='FORGED')),change_mets,change_premis,
                   lambda f:f.update({'unexpected.txt':b'not declared'}),lambda f:f.update({'representation/profile/profile.json':b'{}'})]
        for index,mutation in enumerate(mutations):
            with self.subTest(mutation=index):
                files=copy.copy(original);mutation(files);report=validate_package(repack(files),ROOT/'schemas');self.assertFalse(report['structural_valid'],report)
    def test_original_source_anchor_rejects_forged_source_snapshot(self):
        self.build()
        report=validate_package(self.package(),ROOT/'schemas',source_resolver=lambda digest:b'wrong original ZIP')
        self.assertFalse(report['structural_valid']);self.assertEqual(report['original_sips_verified'],0)
    def test_hierarchy_includes_maps_subdossiers_and_ordered_references(self):
        self.build();files=members(self.package());root=ET.fromstring(files['metadata/mets.xml'])
        self.assertTrue(root.xpath('//m:structMap[@TYPE="logical"]//m:div[@TYPE="mappe"]',namespaces=N))
        self.assertTrue(root.xpath('//m:structMap[@TYPE="logical"]//m:div[@TYPE="dokument"]',namespaces=N))
        self.assertTrue(root.xpath('//m:structMap[@TYPE="logical"]//m:div[@ORDERLABEL="Seite1"]',namespaces=N))
        self.assertEqual(ET.fromstring(files['metadata/premis.xml']).findall('.//{'+P+'}rightsGranted'),[])

if __name__=='__main__':unittest.main()
