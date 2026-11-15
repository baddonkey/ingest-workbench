"""Frozen-input mapping/review contracts; no institutional or repository approval."""
import copy
import io
import json
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main, prepared_review as reviews
from app.auth import Principal, principal
from app.metadata_validation import validate_metadata
from app.packages import PackageError, members, sha
from app.recovery import backup, restore
from app.service import Service,put
from app.store import Conflict
from test_prepared_inputs import PreparedFixture, ROOT
from test_workflow import synthetic_legacy, REFERENCE


class ReviewFixture(PreparedFixture):
    def setUp(self):
        super().setUp()
        self.edit('dossier','titel','Selected frozen dossier title')
        self.frozen = self.freeze()['prepared_input']

    def selected(self):
        return [{'sip_id':s['id'],'prepared_id':self.frozen['id'] if s['id']==self.sip['id'] else None} for s in self.current()['sips']]

    def plan(self, targets=None, selected=None, actor='planner'):
        body = self.current()
        targets = targets or [{'label':'Reviewed target','units':[{'sip_id':s['id'],'unit_id':u['id']} for s in body['sips'] for u in s['units']]}]
        return self.service.plan(body['id'],'archive-a',actor,body['revision'],targets,'Arrange exact selected inputs',self.selected() if selected is None else selected)

    def review_request(self):
        body = self.current()
        return dict(request_id=str(uuid.uuid4()),revision=body['revision'],plan_sha256=body['plan']['sha256'],reason='Review selected bytes, findings and arrangement; AIP authorization pending')

    def review(self, request=None, actor='reviewer'):
        return reviews.review(self.service,self.transfer['id'],'archive-a',actor,request or self.review_request())


class ReviewTests(ReviewFixture):
    def test_all_eight_profiles_retain_selected_metadata_and_schema_valid_review(self):
        for case in json.loads((ROOT/'fixtures/corpus.json').read_text(encoding='utf-8'))['cases']:
            with self.subTest(case=case['file']):
                self.transfer,self.sip,_=self.create(case)
                self.edit('dossier','titel','Selected '+case['profile']+' metadata')
                self.frozen=self.freeze()['prepared_input']
                body=self.plan();record=self.review()['review']
                self.assertEqual(body['plan']['inputs'][0]['profile'],case['profile'])
                self.assertEqual(body['sips'][0],self.sip)
                validate_metadata(members(self.service.blob(record['evidence_sha256'])),self.service.schemas/'preservation')
                reviews.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_selected_metadata_drives_projection_and_review_evidence_while_aip_stays_held(self):
        source = copy.deepcopy(self.current()['sips'])
        body = self.plan()
        view = reviews.workspace(self.service,body['id'],'archive-a')
        self.assertIn('Selected frozen dossier title',[u['title'] for u in view['sips'][0]['units']])
        self.assertEqual(self.current()['sips'],source)
        self.assertEqual(body['plan']['inputs'][0]['input_sha256'],self.frozen['sha256'])
        result = self.review()
        record = result['review']
        self.assertEqual(result['transfer']['state'],'PLANNED')
        self.assertFalse(record['aip_authorization'])
        self.assertFalse(record['production_acceptance'])
        self.assertNotIn('approval',result['transfer'])
        files = members(self.service.blob(record['evidence_sha256']))
        validate_metadata(files,self.service.schemas/'preservation')
        self.assertEqual(json.loads(files['plan.json']),body['plan'])
        self.assertIn(self.frozen['sha256'].encode(),files['metadata/mets.xml'])
        self.assertIn(self.frozen['preparation_sha256'].encode(),files['metadata/premis.xml'])
        for action in (self.service.approve,self.service.build):
            with self.assertRaises(Conflict):action(body['id'],'archive-a','reviewer',self.current()['revision'])
        reviews.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_all_four_cardinalities_preserve_source_and_selected_frozen_identity(self):
        for count, outputs in ((1,1),(1,3),(2,1),(2,3)):
            with self.subTest(inputs=count,outputs=outputs):
                body = self.service.create('Frozen cardinality','archive-a','operator')
                body = self.service.upload(body['id'],'archive-a','operator',0,'1.3.0',REFERENCE)
                if count==2:body=self.service.upload(body['id'],'archive-a','operator',body['revision'],'1.2.0',synthetic_legacy('1.2.0'))
                self.transfer,self.sip=body,body['sips'][0]
                self.edit('dossier','titel','Frozen cardinality title')
                self.frozen=self.freeze()['prepared_input']
                if outputs==1:targets=None
                else:targets=[{'label':'Target '+str(i),'units':[{'sip_id':s['id'],'unit_id':s['units'][i]['id']} for s in body['sips']]} for i in range(3)]
                plan=self.plan(targets)['plan']
                self.assertEqual(len(plan['targets']),outputs)
                self.assertEqual(len(plan['inputs']),count)
                self.review()
                reviews.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_missing_duplicate_source_bypass_and_historical_freeze_are_refused(self):
        source=self.current()
        for selection in ([],self.selected()*2,[{'sip_id':self.sip['id'],'prepared_id':None}]):
            with self.subTest(selection=selection),self.assertRaises((Conflict,PackageError)):
                self.plan(selected=selection)
            self.assertEqual(self.current(),source)
        self.operation('checkpoint',name='New current head')
        with self.assertRaises(Conflict):self.plan()
        self.frozen=self.freeze()['prepared_input']
        self.plan()
        targets=copy.deepcopy(self.current()['plan']['targets']);targets[0]['units']*=2
        with self.assertRaises(PackageError):self.plan(targets)

    def test_planner_editor_freezer_and_context_contributors_cannot_review(self):
        body=self.current()
        self.service.set_context(body['id'],'archive-a','context-author',body['revision'],{})
        self.plan()
        for actor in ('planner','editor','context-author'):
            with self.subTest(actor=actor),self.assertRaises(Conflict):self.review(actor=actor)
        self.review(actor='independent-reviewer')

    def test_exact_concurrent_lost_ack_replay_preserves_one_review_without_restoring_old_pointer(self):
        self.plan();request=self.review_request()
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:self.review(request),range(2)))
        self.assertEqual(sorted(r['replayed'] for r in results),[False,True])
        self.assertEqual(len(self.current()['prepared_reviews']),1)
        with self.assertRaises(Conflict):self.review({**request,'reason':'Different intent'})
        self.operation('checkpoint',name='Successor requires review')
        self.assertNotIn('prepared_review_id',self.current())
        self.assertTrue(self.review(request)['replayed'])
        self.assertNotIn('prepared_review_id',self.current())
        reviews.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_edits_require_fresh_plan_and_keep_historical_evidence(self):
        body=self.current();self.plan();first=self.review()['review']
        self.edit('dossier','titel','Later edited title')
        self.assertNotIn('prepared_review_id',self.current())
        with self.assertRaises(Conflict):self.review()
        self.assertEqual(reviews.artifact(self.service,body['id'],'archive-a',first['id']),self.service.blob(first['evidence_sha256']))

    def test_context_and_resource_changes_do_not_rewrite_reviewed_snapshots(self):
        self.plan();first=self.review()['review']
        body=self.current();self.service.set_context(body['id'],'archive-a','context-author',body['revision'],{'archive_name':'New context'})
        self.assertNotIn('prepared_review_id',self.current())
        self.review()
        body=self.current();self.service.add_resource(body['id'],'archive-a','resource-author',body['revision'],'Preservation document','context.txt','text/plain',b'Preserved context')
        self.assertNotIn('prepared_review_id',self.current())
        self.review()
        self.assertEqual(reviews.artifact(self.service,body['id'],'archive-a',first['id']),self.service.blob(first['evidence_sha256']))
        reviews.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_schema_valid_forged_review_authorization_after_manifest_resealing_is_refused(self):
        self.plan();record=self.review()['review']
        files=members(self.service.blob(record['evidence_sha256']))
        descriptor=json.loads(files['review.json']);descriptor['aip_authorization']=True
        files['review.json']=json.dumps(descriptor).encode()
        files['manifest-sha256.txt']=''.join(sha(data)+'  '+path+'\n' for path,data in sorted(files.items()) if path!='manifest-sha256.txt').encode()
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
            for path,data in files.items():archive.writestr(path,data)
        body=self.current();body['prepared_reviews'][0]['evidence_sha256']=put(self.service.data,stream.getvalue())
        with self.assertRaises(PackageError):reviews.validate_retained(body,self.service.blob,self.service.schemas)

    def test_review_creation_failure_and_stale_request_are_atomic(self):
        self.plan();before=self.current()
        request=self.review_request()
        with self.assertRaises(Conflict):self.review({**request,'revision':0})
        with patch('app.review_evidence.bundle',side_effect=PackageError('Schema failure')):
            with self.assertRaises(PackageError):self.review(request)
        self.assertEqual(self.current(),before)

    def test_paired_recovery_retains_historical_review_after_successor_editing(self):
        self.plan();first=self.review()['review']
        self.operation('checkpoint',name='Later preparation')
        snapshot=self.folder/'review-snapshot'
        report=backup(self.service,snapshot,actor='backup',quiesced=True)
        store=self.new_store();destination=self.folder/'review-restored'
        restore(snapshot,destination,ROOT/'schemas',expected_digest=report['snapshot_sha256'],actor='restore',quiesced=True,store=store)
        restored=Service(destination,ROOT/'schemas',store=store)
        self.assertEqual(reviews.artifact(restored,self.transfer['id'],'archive-a',first['id']),self.service.blob(first['evidence_sha256']))
        bad=copy.deepcopy(self.current());bad['prepared_review_id']=first['id']
        with self.assertRaises(PackageError):reviews.validate_retained(bad,self.service.blob,self.service.schemas)


class ReviewApiTests(ReviewFixture):
    def test_roles_tenant_selected_projection_and_review_evidence_download(self):
        original=main.service;main.service=self.service
        self.addCleanup(setattr,main,'service',original);self.addCleanup(main.app.dependency_overrides.clear)
        def user(sub='reader',tenant='archive-a',roles=('archive-reader',)):
            main.app.dependency_overrides[principal]=lambda:Principal(sub,tenant,frozenset(roles),'archive-workbench')
        client=TestClient(main.app);path='/api/transfers/'+self.transfer['id']
        user();view=client.get(path+'/arrangement')
        self.assertEqual(view.status_code,200,view.text)
        self.assertIsNone(view.json()['plan'])
        preview=client.post(path+'/arrangement/preview',json={'revision':self.current()['revision'],'selected_inputs':self.selected()})
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(preview.json()['bindings'][0]['input_sha256'],self.frozen['sha256'])
        self.plan();request=self.review_request()
        self.assertEqual(client.get(path+'/arrangement').json()['plan'],self.current()['plan'])
        self.assertEqual(client.post(path+'/prepared-reviews',json=request).status_code,403)
        user('reviewer','other',('archive-reader','archive-approver'))
        self.assertEqual(client.post(path+'/prepared-reviews',json=request).status_code,404)
        user('reviewer',roles=('archive-reader','archive-approver'))
        response=client.post(path+'/prepared-reviews',json=request)
        self.assertEqual(response.status_code,200,response.text)
        record=response.json()['review']
        user();download=client.get(path+'/prepared-reviews/'+record['id']+'/evidence')
        self.assertEqual(sha(download.content),record['evidence_sha256'])
        self.assertEqual(download.headers['cache-control'],'no-store')
        user(tenant='other')
        self.assertEqual(client.get(path+'/prepared-reviews/'+record['id']+'/evidence').status_code,404)
