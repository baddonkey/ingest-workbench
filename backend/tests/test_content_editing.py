"""Content changes must survive exact replay, compensation, freeze and recovery."""
import base64
import copy
import json
import random
import uuid
from fastapi.testclient import TestClient
from app import main, content_editing as content, working_metadata as working, prepared_inputs
from app.auth import Principal,principal
from app.packages import PackageError, inspect, members
from app.recovery import backup,restore
from app.service import Service
from test_preparation_notes import NotesFixture,ROOT
import test_hierarchy_editing as hierarchy_tests


class ContentFixture(NotesFixture):
    def file_node(self):
        source_id=next(iter(self.sip['files']))
        return next(n for n in self.document()['nodes'] if n['kind']=='datei' and n['source_id']==source_id)

    def rename(self):
        return self.operation(content.KIND,node_id=self.file_node()['id'],content_action='rename',content_name='reviewed-payload.bin')

    def replace(self):
        return self.operation(content.KIND,node_id=self.file_node()['id'],content_action='replace',
            content_source={'filename':'replacement.txt','data_base64':base64.b64encode(b'Explicit replacement bytes\n').decode()})

    def archive(self):
        _,metadata,_=working.baseline(self.service,self.current(),self.sip)
        return content.prepared(self.service.blob,self.sip,working.history(self.current(),self.sip['id']),metadata)


class ContentTests(ContentFixture):
    def test_large_payload_evidence_is_deduplicated_and_freeze_capacity_failure_is_atomic(self):
        data=random.Random(42).randbytes(24*1024*1024)
        self.operation(content.KIND,node_id=self.file_node()['id'],content_action='replace',
            content_source={'filename':'large.bin','data_base64':base64.b64encode(data).decode()})
        record=self.rename()['operation'];evidence=self.service.blob(record['evidence_sha256'])
        self.assertLess(len(evidence),40*1024*1024)
        self.assertEqual(sum(value==data for value in members(evidence).values()),1)
        working.validate_retained(self.current(),self.service.blob,self.service.schemas)
        before=self.current()
        with self.assertRaisesRegex(PackageError,'Frozen evidence exceeds local ZIP limit'):self.freeze()
        self.assertEqual(self.current(),before)
        self.operation('undo');self.operation('undo')
        self.assertFalse(self.document()['state']['changed'])
        self.freeze()

    def test_all_eight_profiles_retain_native_ids_references_originals_and_freeze_new_bytes(self):
        for case in json.loads((ROOT/'fixtures/corpus.json').read_bytes())['cases']:
            with self.subTest(case=case['file']):
                self.transfer,self.sip,_=self.create(case);original=self.service.blob(self.sip['sha256'])
                node=self.file_node();field_ids=[f['id'] for f in node['fields']]
                self.rename();self.replace()
                after=self.file_node();self.assertEqual(after['id'],node['id']);self.assertEqual(after['source_id'],node['source_id'])
                self.assertEqual([f['id'] for f in after['fields']],field_ids)
                report=inspect(self.archive(),self.sip['profile'],self.service.schemas)
                self.assertEqual(report['references'],self.sip['references'])
                self.assertEqual(members(self.archive())[report['files'][node['source_id']]['path']],b'Explicit replacement bytes\n')
                self.assertEqual(self.service.blob(self.sip['sha256']),original)
                self.assertEqual(self.current()['sips'][0],self.sip)
                frozen=self.freeze()['prepared_input'];self.assertEqual(frozen['version'],4)
                prepared_inputs.validate_record(self.current(),frozen,self.service.blob,self.service.schemas)
                working.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_notes_metadata_compensation_restore_and_recovery_keep_exact_content_versions(self):
        self.add_note(kind='datei');before=self.archive()
        renamed=self.rename()['operation'];renamed_archive=self.archive()
        self.edit();self.replace();replaced=self.archive()
        self.operation('undo')
        expected=content.rewrite(renamed_archive,self.sip['metadata_path'],members(self.archive())[self.sip['metadata_path']])
        self.assertEqual(content.inventory(self.archive()),content.inventory(expected))
        self.operation('redo');self.assertEqual(content.inventory(self.archive()),content.inventory(replaced))
        self.operation('restore',target_id=renamed['id']);self.assertEqual(content.inventory(self.archive()),content.inventory(renamed_archive))
        self.assertEqual(len(self.document()['notes']),1)
        self.operation('undo');self.assertEqual(content.inventory(self.archive()),content.inventory(replaced))
        working.validate_retained(self.current(),self.service.blob,self.service.schemas)
        folder=self.folder/'content-backup';report=backup(self.service,folder,actor='backup',quiesced=True)
        destination=self.folder/'restored-content';store=self.new_store()
        restore(folder,destination,ROOT/'schemas',expected_digest=report['snapshot_sha256'],actor='restore',quiesced=True,store=store)
        restored=Service(destination,ROOT/'schemas',store=store)
        data=content.prepared(restored.blob,self.sip,working.history(restored.get(self.transfer['id'],'archive-a'),self.sip['id']),
                              restored.blob(self.document()['state']['metadata_sha256']))
        self.assertEqual(data,self.archive());self.assertNotEqual(data,before)

    def test_invalid_names_protected_members_and_hidden_scope_changes_are_rejected(self):
        before=self.current();node=self.file_node()
        for name in ('../escape','x/y','x\\y','x:y','.',node['title'],'bad\0name','bad\ud800'):
            with self.subTest(name=repr(name)),self.assertRaises(PackageError):
                self.operation(content.KIND,node_id=node['id'],content_action='rename',content_name=name)
            self.assertEqual(self.current(),before)
        record=self.rename()['operation'];forged=copy.deepcopy(record)
        forged['changes']['content']['before_sha256']='0'*64
        with self.assertRaises(PackageError):content.verify(forged,self.sip['sha256'],self.service.blob,self.sip,self.service.schemas)
        working.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_api_read_only_preview_permissions_stale_requests_and_exact_retry(self):
        prior=main.service;main.service=self.service
        self.addCleanup(setattr,main,'service',prior);self.addCleanup(main.app.dependency_overrides.clear)
        client=TestClient(main.app);base='/api/transfers/'+self.transfer['id']+'/sips/'+self.sip['id']+'/preparation'
        doc=self.document();request=dict(request_id=str(uuid.uuid4()),revision=doc['revision'],
            source_sha256=self.sip['sha256'],base_operation_id=doc['state']['head'],kind=content.KIND,
            reason='Reviewed filename change',node_id=self.file_node()['id'],content_action='rename',content_name='reviewed.txt')
        def user(tenant='archive-a',roles=('archive-reader','archive-operator')):
            main.app.dependency_overrides[principal]=lambda:Principal('editor',tenant,frozenset(roles),'archive-workbench')
        user(roles=('archive-reader',));self.assertEqual(client.post(base+'/content-preview',json=request).status_code,403)
        self.assertEqual(client.post(base+'/commands',json=request).status_code,403)
        user(tenant='other');self.assertEqual(client.post(base+'/content-preview',json=request).status_code,404)
        user();before=self.current();preview=client.post(base+'/content-preview',json=request)
        self.assertEqual(preview.status_code,200,preview.text);self.assertEqual(self.current(),before)
        response=client.post(base+'/commands',json=request);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['operation']['changes'],preview.json())
        self.assertTrue(client.post(base+'/commands',json=request).json()['replayed'])
        self.assertEqual(client.post(base+'/content-preview',json=request).status_code,409)


class ContentCandidateTests(ContentFixture):
    selected=hierarchy_tests.HierarchyCandidateTests.selected
    plan=hierarchy_tests.HierarchyCandidateTests.plan
    review_request=hierarchy_tests.HierarchyCandidateTests.review_request
    review=hierarchy_tests.HierarchyCandidateTests.review
    authorization_request=hierarchy_tests.HierarchyCandidateTests.authorization_request
    authorize=hierarchy_tests.HierarchyCandidateTests.authorize
    build=hierarchy_tests.HierarchyCandidateTests.build
    candidate=hierarchy_tests.HierarchyCandidateTests.candidate
    prepare=hierarchy_tests.HierarchyCandidateTests.prepare

    def test_changed_payload_candidate_is_self_contained_and_successor_edit_invalidates_review(self):
        self.rename();self.replace();self.frozen=self.freeze()['prepared_input']
        self.prepare();self.build();candidate=self.candidate()
        from app.aip_validation import validate_package
        result=validate_package(candidate,ROOT/'schemas')
        self.assertTrue(result['structural_valid'],result['errors'])
        self.assertFalse(result['preservation_ready'])
        self.assertIn(b'Explicit replacement bytes\n',members(candidate).values())
        self.operation(content.KIND,node_id=self.file_node()['id'],content_action='rename',content_name='successor.bin')
        self.assertNotIn('prepared_review_id',self.current());self.assertNotIn('approval',self.current())
        self.assertTrue(validate_package(candidate,ROOT/'schemas')['structural_valid'])
