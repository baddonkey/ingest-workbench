"""Attributable notes share operation, freeze, recovery and candidate contracts."""
import copy
import io
import json
import uuid
import zipfile
from fastapi.testclient import TestClient
from app import main, preparation_notes as notes, working_metadata as working
from app.auth import Principal, principal
from app.json_codec import canonical
from app.packages import PackageError, members, sha
from app.preparation_evidence import verify
from app.store import Conflict
from app.recovery import backup, restore
from app.service import Service
from test_prepared_inputs import PreparedFixture, ROOT
import test_prepared_aip as candidate_tests
import test_prepared_review as review_tests


class NotesFixture(PreparedFixture):
    def add_note(self, text='Internal appraisal context', purpose='internal', kind='dossier'):
        node = next(n for n in self.document()['nodes'] if n['kind'] == kind)
        result = self.operation('note-add', node_id=node['id'], note_text=text, note_purpose=purpose)
        return result['operation']['changes']['note_after']

    def update_note(self, note, text='Reviewed note', purpose='description-candidate'):
        return self.operation('note-edit', node_id=note['node_id'], note_id=note['id'],
                              note_before=note, note_text=text, note_purpose=purpose)['operation']['changes']['note_after']


class NotesTests(NotesFixture):
    def test_all_profiles_and_package_dossier_document_file_links_preserve_xml_and_source(self):
        for case in json.loads((ROOT/'fixtures/corpus.json').read_bytes())['cases']:
            with self.subTest(case=case['file']):
                self.transfer, self.sip, _ = self.create(case)
                digest = self.document()['state']['metadata_sha256']
                self.add_note()
                self.assertEqual(self.document()['state']['metadata_sha256'], digest)
                self.assertEqual(self.current()['sips'][0], self.sip)
                working.validate_retained(self.current(), self.service.blob, self.service.schemas)
        for kind in ('paket', 'dokument', 'datei'):
            with self.subTest(kind=kind): self.add_note(kind=kind)
        self.assertEqual(len(self.document()['notes']), 4)

    def test_shared_undo_redo_restore_exact_retry_and_historical_note_snapshots(self):
        first = self.add_note()
        original_op = self.document()['state']['head']
        second = self.update_note(first)
        self.assertEqual(second['revision'], 2)
        self.assertEqual(second['created_by'], 'editor')
        self.operation('undo'); self.assertEqual(self.document()['notes'], [first])
        self.operation('redo'); self.assertEqual(self.document()['notes'], [second])
        self.operation('note-delete', node_id=second['node_id'], note_id=second['id'], note_before=second)
        self.assertEqual(self.document()['notes'], [])
        self.operation('undo'); self.assertEqual(self.document()['notes'], [second])
        # XML is identical, but restoring a historical note set is a real change.
        self.operation('restore', target_id=original_op)
        self.assertEqual(self.document()['notes'], [first])
        historical = working.workspace(self.service,self.transfer['id'],'archive-a',self.sip['id'], original_op)
        self.assertEqual(historical['notes'], [first])
        doc = self.document()
        request = dict(request_id=str(uuid.uuid4()), revision=doc['revision'], source_sha256=self.sip['sha256'],
                       base_operation_id=doc['state']['head'], kind='note-edit', reason='Exact note retry',
                       node_id=first['node_id'], note_id=first['id'], note_before=first,
                       note_text='Retained once', note_purpose='internal')
        a = working.command(self.service,self.transfer['id'],'archive-a','editor',self.sip['id'],request)
        b = working.command(self.service,self.transfer['id'],'archive-a','editor',self.sip['id'],request)
        self.assertEqual(a['operation'], b['operation']); self.assertTrue(b['replayed'])
        self.edit(); self.operation('undo')
        self.assertEqual(self.document()['notes'][0]['text'], 'Retained once')
        working.validate_retained(self.current(), self.service.blob, self.service.schemas)

    def test_invalid_forged_stale_and_conflicting_requests_leave_state_unchanged(self):
        first = self.add_note(); before = self.current()
        for extra in [dict(note_text=' '), dict(note_text='x\x00'), dict(note_text='x'*8001),
                      dict(note_purpose='public'), dict(node_id='unknown')]:
            with self.subTest(extra=extra), self.assertRaises(PackageError):
                self.operation('note-add', **{**dict(node_id=first['node_id'], note_text='text', note_purpose='internal'), **extra})
            self.assertEqual(self.current(), before)
        forged = {**first, 'created_by':'forged'}
        with self.assertRaises(Conflict): self.update_note(forged)
        self.update_note(first)
        with self.assertRaises(Conflict): self.update_note(first)

    def test_mets_premis_sidecar_freeze_tamper_detection_and_recovery(self):
        note = self.add_note(); doc = self.document(); record = doc['history'][-1]
        data = self.service.blob(record['evidence_sha256']); payload = members(data)
        self.assertEqual(json.loads(payload['notes.json']), notes.document(doc['history']))
        self.assertIn(b'note-add', payload['metadata/premis.xml'])
        self.assertIn(note['text'].encode(), payload['metadata/premis.xml'])
        verify(data,doc['history'],self.sip,self.service.schemas)
        forged = copy.deepcopy(doc['history']); forged[-1]['changes']['note_after']['created_by']='forged'
        with self.assertRaises(PackageError): notes.state(forged)
        payload['notes.json'] = canonical({'version':1,'publication':'held','notes':[]})
        payload['manifest-sha256.txt'] = ''.join(sha(v)+'  '+k+'\n' for k,v in sorted(payload.items()) if k!='manifest-sha256.txt').encode()
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,value in payload.items(): archive.writestr(name,value)
        with self.assertRaisesRegex(PackageError,'note snapshot'): verify(stream.getvalue(),doc['history'],self.sip,self.service.schemas)
        frozen=self.freeze()['prepared_input']; self.update_note(note)
        from app.prepared_inputs import validate_record
        validate_record(self.current(), frozen, self.service.blob, self.service.schemas)
        snapshot=self.folder/'notes-backup';report=backup(self.service,snapshot,actor='backup',quiesced=True)
        destination=self.folder/'notes-restored';store=self.new_store()
        restore(snapshot,destination,ROOT/'schemas',expected_digest=report['snapshot_sha256'],actor='restore',quiesced=True,store=store)
        target=Service(destination,ROOT/'schemas',store=store)
        restored=working.workspace(target,self.transfer['id'],'archive-a',self.sip['id'])
        self.assertEqual(restored['notes'],self.document()['notes'])

    def test_api_operator_reader_tenant_and_exact_replay(self):
        prior=main.service;main.service=self.service
        self.addCleanup(setattr,main,'service',prior);self.addCleanup(main.app.dependency_overrides.clear)
        client=TestClient(main.app);base='/api/transfers/'+self.transfer['id']+'/sips/'+self.sip['id']+'/preparation'
        def user(tenant='archive-a', roles=('archive-reader','archive-operator')):
            main.app.dependency_overrides[principal]=lambda:Principal('editor',tenant,frozenset(roles),'archive-workbench')
        doc=self.document();node=next(n for n in doc['nodes'] if n['kind']=='datei')
        request=dict(request_id=str(uuid.uuid4()),revision=doc['revision'],source_sha256=self.sip['sha256'],base_operation_id=None,kind='note-add',reason='File appraisal',node_id=node['id'],note_text='File note',note_purpose='internal')
        user(roles=('archive-reader',));self.assertEqual(client.post(base+'/commands',json=request).status_code,403)
        user(tenant='other');self.assertEqual(client.post(base+'/commands',json=request).status_code,404)
        user();response=client.post(base+'/commands',json=request);self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(client.post(base+'/commands',json=request).json()['replayed'])
        user(roles=('archive-reader',));self.assertEqual(len(client.get(base).json()['notes']),1)


class NoteCandidateTests(NotesFixture):
    selected=review_tests.ReviewFixture.selected
    plan=review_tests.ReviewFixture.plan
    review_request=review_tests.ReviewFixture.review_request
    review=review_tests.ReviewFixture.review
    authorization_request=candidate_tests.PreparedAipTests.authorization_request
    authorize=candidate_tests.PreparedAipTests.authorize
    build=candidate_tests.PreparedAipTests.build
    candidate=candidate_tests.PreparedAipTests.candidate
    prepare=candidate_tests.PreparedAipTests.prepare

    def test_note_only_successor_invalidates_review_and_retains_exact_candidate_dossier(self):
        note=self.add_note('Internal text must not become a package description')
        self.add_note('Proposed description requires explicit crosswalk review', 'description-candidate', 'datei')
        self.frozen=self.freeze()['prepared_input'];self.prepare();self.build()
        view=review_tests.reviews.workspace(self.service,self.transfer['id'],'archive-a')
        self.assertEqual(len(view['input_notes'][0]['notes']),2)
        candidate=self.candidate();files=members(candidate)
        from app.aip_validation import validate_package
        from app.evidence_codec import Decoder
        result=validate_package(candidate,ROOT/'schemas')
        self.assertTrue(result['structural_valid'],result['errors'])
        from app.prepared_aip import PREFIX
        evidence=members(Decoder(files,PREFIX)(self.frozen['preparation_sha256']))
        self.assertEqual(len(json.loads(evidence['notes.json'])['notes']),2)
        description=files['metadata/description.json']
        self.assertNotIn(note['text'].encode(),description)
        self.assertNotIn(b'Proposed description requires explicit crosswalk review',description)
        self.update_note(note)
        self.assertNotIn('prepared_review_id',self.current());self.assertNotIn('approval',self.current())
        self.assertTrue(validate_package(candidate,ROOT/'schemas')['structural_valid'])
        stale=review_tests.reviews.workspace(self.service,self.transfer['id'],'archive-a')
        self.assertFalse(stale['plan_current'])
        self.assertEqual(stale['input_notes'][0]['notes'][0]['text'],note['text'])
