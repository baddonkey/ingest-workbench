"""Prepared candidate profile; qualification and institutional acceptance stay separate."""
import copy
import json
import uuid
from app import prepared_aip, prepared_review
from app.aip import canonical
from app.aip_validation import validate_package, institutional_gate
from app.packages import members, sha
from app.store import Conflict
from test_aip import repack
from test_prepared_review import ReviewFixture, ROOT
from test_workflow import synthetic_legacy, REFERENCE
from app.recovery import backup, restore
from app.service import Service


class PreparedAipTests(ReviewFixture):
    def authorization_request(self):
        body=self.current()
        return dict(request_id=str(uuid.uuid4()),revision=body['revision'],review_id=body['prepared_review_id'],
                    plan_sha256=body['plan']['sha256'],reason='Authorize an evidence-bound local candidate; no archival acceptance')

    def authorize(self,request=None,actor='reviewer'):
        return prepared_aip.authorize(self.service,self.transfer['id'],'archive-a',actor,request or self.authorization_request())

    def build(self):
        body=self.current()
        return self.service.build(body['id'],'archive-a','operator',body['revision'])

    def candidate(self):
        return self.service.blob(self.current()['outputs'][-1]['sha256'])

    def prepare(self):
        self.plan();self.review();self.authorize()

    def test_candidate_uses_frozen_metadata_and_self_contained_history(self):
        source=copy.deepcopy(self.current()['sips']);self.prepare();self.build()
        data=self.candidate();files=members(data)
        self.assertFalse(any(path.startswith('integration/') for path in files))
        report=validate_package(data,ROOT/'schemas',expected_sha256=sha(data),source_resolver=self.service.blob)
        self.assertTrue(report['structural_valid'],report['errors'])
        self.assertEqual(report['profile_id'],prepared_aip.PROFILE_ID)
        self.assertFalse(report['preservation_ready']);self.assertEqual(report['original_sips_verified'],1)
        self.assertEqual(self.current()['sips'],source)
        self.assertEqual(self.current()['plan']['inputs'][0]['receipt_checks'],
                 ['safe-zip','trusted-xsd','submitted-fixity','inventory','references'])
        descriptor=json.loads(files['aip.json']);selected=descriptor['sources'][0]
        self.assertEqual(selected['source_sha256'],self.sip['sha256'])
        self.assertEqual(selected['sha256'],self.frozen['sha256'])
        self.assertIn(b'Selected frozen dossier title',files[selected['metadata_path']])
        for digest in (self.sip['sha256'],self.frozen['sha256'],self.frozen['preparation_sha256'],self.frozen['evidence_sha256'],self.current()['prepared_reviews'][0]['evidence_sha256']):
            from app.evidence_codec import Decoder
            self.assertEqual(sha(Decoder(files,prepared_aip.PREFIX)(digest)),digest)
        self.assertFalse(institutional_gate(self.service,self.current(),json.loads((ROOT/'config/aip-policy.json').read_bytes()))['ready'])
        self.assertEqual(self.build()['outputs'],self.current()['outputs'])

    def test_all_eight_profiles_build_and_verify_offline(self):
        for case in json.loads((ROOT/'fixtures/corpus.json').read_bytes())['cases']:
            with self.subTest(case=case['file']):
                self.transfer,self.sip,_=self.create(case)
                self.edit('dossier','titel','Selected '+case['profile'])
                self.frozen=self.freeze()['prepared_input'];self.prepare();self.build()
                result=validate_package(self.candidate(),ROOT/'schemas')
                self.assertTrue(result['structural_valid'],result['errors'])

    def test_mixed_source_prepared_all_four_cardinalities(self):
        for count,outputs in ((1,1),(1,3),(2,1),(2,3)):
            with self.subTest(count=count,outputs=outputs):
                body=self.service.create('Prepared cardinalities','archive-a','operator')
                body=self.service.upload(body['id'],'archive-a','operator',0,'1.3.0',REFERENCE)
                if count==2:body=self.service.upload(body['id'],'archive-a','operator',body['revision'],'1.2.0',synthetic_legacy('1.2.0'))
                self.transfer,self.sip=body,body['sips'][0]
                self.edit('dossier','titel','Frozen cardinality title');self.frozen=self.freeze()['prepared_input']
                targets=None if outputs==1 else [{'label':'Target '+str(i),'units':[{'sip_id':s['id'],'unit_id':s['units'][i]['id']} for s in body['sips']]} for i in range(3)]
                self.plan(targets);self.review();self.authorize();result=self.build()
                self.assertEqual(len(result['outputs']),outputs)
                for out in result['outputs']:
                    checked=validate_package(self.service.blob(out['sha256']),ROOT/'schemas')
                    self.assertTrue(checked['structural_valid'],checked['errors'])

    def test_authorization_is_explicit_independent_idempotent_and_never_resurrected(self):
        self.plan();self.review()
        with self.assertRaises(Conflict):self.build()
        request=self.authorization_request()
        for actor in ('editor','planner','another-reviewer'):
            with self.subTest(actor=actor),self.assertRaises(Conflict):self.authorize(request,actor)
        self.authorize(request)
        self.assertTrue(self.authorize(request)['replayed'])
        with self.assertRaises(Conflict):self.authorize({**request,'reason':'Different intent'})
        self.build();old=self.candidate()
        self.operation('checkpoint',name='Same bytes, successor history')
        self.assertNotIn('approval',self.current())
        self.assertTrue(self.authorize(request)['replayed'])
        self.assertNotIn('approval',self.current())
        with self.assertRaises(Conflict):self.build()
        self.assertTrue(validate_package(old,ROOT/'schemas')['structural_valid'])
        self.frozen=self.freeze()['prepared_input'];self.prepare();self.build()
        self.assertEqual(self.current()['outputs'][-1]['version'],2)
        self.assertEqual(json.loads(members(self.candidate())['aip.json'])['previous_version']['sha256'],sha(old))

    def test_resealed_evidence_and_selected_lineage_corruption_are_rejected(self):
        self.prepare();self.build();original=members(self.candidate())
        def mutate(path,callback):
            files=copy.copy(original);body=json.loads(files[path]);callback(body);files[path]=canonical(body)
            return files
        cases=[mutate('aip.json',lambda d:d['sources'][0].update(source_sha256=d['sources'][0]['sha256'])),
               mutate('aip.json',lambda d:d['approval'].update(review_id=str(uuid.uuid4()))),
               mutate(prepared_aip.SNAPSHOT,lambda d:d['body']['metadata_operations'][0].update(actor='forged')),
               mutate('aip.json',lambda d:d.update(profile_version='1.0.0'))]
        missing=copy.copy(original);missing.pop(prepared_aip.PREFIX+self.frozen['preparation_sha256']+'.zip-recipe.json');cases.append(missing)
        extra=copy.copy(original);extra[prepared_aip.PREFIX+'0'*64]=b'undeclared';cases.append(extra)
        for files in cases:
            with self.subTest(keys=len(files)):
                report=validate_package(repack(files),ROOT/'schemas')
                self.assertFalse(report['structural_valid'],report)

    def test_semantic_review_substitution_rejected_after_metadata_and_manifest_resealing(self):
        self.prepare();self.build();files=members(self.candidate())
        # Rebuild METS and PREMIS too: rejection must come from review semantics.
        descriptor=json.loads(files['aip.json']);descriptor['approval']['review_id']=str(uuid.uuid4())
        files['aip.json']=canonical(descriptor)
        from app.aip import make_mets,make_premis
        objects=json.loads(files['metadata/lineage.json'])['files']
        context=json.loads(files['metadata/preservation-context.json'])
        events=json.loads(files['evidence/events.json'])
        files['metadata/premis.xml']=make_premis(descriptor,objects,context,events)
        files['metadata/mets.xml']=make_mets(descriptor,json.loads(files['metadata/description.json']),objects,
                                          {k:v for k,v in files.items() if k not in ('metadata/mets.xml','manifest-sha256.txt')})
        report=validate_package(repack(files),ROOT/'schemas')
        self.assertFalse(report['structural_valid']);self.assertIn('Approval event absent',' '.join(report['errors']))

    def test_paired_recovery_preserves_old_candidates_and_authorization_after_successor(self):
        self.prepare();self.build();old=self.candidate()
        self.operation('checkpoint',name='Successor keeps sealed output')
        report=backup(self.service,self.folder/'prepared-aip-snapshot',actor='backup',quiesced=True)
        destination=self.folder/'restored-candidates'
        store=self.new_store()
        restore(self.folder/'prepared-aip-snapshot',destination,ROOT/'schemas',expected_digest=report['snapshot_sha256'],actor='restore',quiesced=True,store=store)
        restored=Service(destination,ROOT/'schemas',store=store)
        self.assertEqual(restored.blob(sha(old)),old)
        prepared_aip.validate_retained(restored.get(self.transfer['id'],'archive-a'),restored.blob,ROOT/'schemas')
        prepared_aip.validate_retained(self.current(),self.service.blob,ROOT/'schemas')

    def test_historical_source_only_candidate_remains_verifiable_after_preparation(self):
        body=self.current()
        # A separate source-only transfer has the same stable arrangement identity after preparation.
        body=self.service.create('Source then prepared','archive-a','operator')
        body=self.service.upload(body['id'],'archive-a','operator',0,'1.3.0',REFERENCE)
        self.transfer,self.sip=body,body['sips'][0]
        targets=[{'label':'Reviewed target','units':[{'sip_id':self.sip['id'],'unit_id':u['id']} for u in self.sip['units']]}]
        body=self.service.plan(body['id'],'archive-a','planner',body['revision'],targets,'Original metadata')
        body=self.service.approve(body['id'],'archive-a','reviewer',body['revision']);self.build();original=self.candidate()
        self.edit('dossier','titel','Selected successor');self.frozen=self.freeze()['prepared_input'];self.prepare();self.build()
        self.assertEqual(self.current()['outputs'][-1]['version'],2)
        self.assertTrue(validate_package(original,ROOT/'schemas')['structural_valid'])
        self.assertTrue(validate_package(self.candidate(),ROOT/'schemas')['structural_valid'])


class PreparedAipApiTests(ReviewFixture):
    def test_role_tenant_explicit_authorization_build_download_and_replay(self):
        from fastapi.testclient import TestClient
        from app import main
        from app.auth import principal,Principal
        previous=main.service;main.service=self.service
        self.addCleanup(setattr,main,'service',previous);self.addCleanup(main.app.dependency_overrides.clear)
        def user(sub='reader',tenant='archive-a',roles=('archive-reader',)):
            main.app.dependency_overrides[principal]=lambda:Principal(sub,tenant,frozenset(roles),'archive-workbench')
        self.plan();self.review();body=self.current()
        request=dict(request_id=str(uuid.uuid4()),revision=body['revision'],review_id=body['prepared_review_id'],plan_sha256=body['plan']['sha256'],reason='Explicit local candidate authorization')
        client=TestClient(main.app);base='/api/transfers/'+body['id'];path=base+'/prepared-authorizations'
        user();self.assertEqual(client.post(path,json=request).status_code,403)
        user('reviewer','other',('archive-reader','archive-approver'));self.assertEqual(client.post(path,json=request).status_code,404)
        user('planner',roles=('archive-reader','archive-approver'));self.assertEqual(client.post(path,json=request).status_code,409)
        user('reviewer',roles=('archive-reader','archive-approver'));result=client.post(path,json=request)
        self.assertEqual(result.status_code,200,result.text)
        self.assertTrue(client.post(path,json=request).json()['replayed'])
        self.assertEqual(client.post(base+'/build',json={'revision':self.current()['revision']}).status_code,403)
        user('operator',roles=('archive-reader','archive-operator'))
        response=client.post(base+'/build',json={'revision':self.current()['revision']})
        self.assertEqual(response.status_code,200,response.text)
        out=response.json()['outputs'][-1]
        user();download=client.get(base+'/outputs/'+out['id'])
        self.assertEqual(sha(download.content),out['sha256']);self.assertEqual(download.headers['cache-control'],'no-store')
        user(tenant='other');self.assertEqual(client.get(base+'/outputs/'+out['id']).status_code,404)
