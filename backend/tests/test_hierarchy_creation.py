"""Creation must survive attributable replay, history, freezing and candidate verification."""
import copy,json,uuid
from fastapi.testclient import TestClient
from app import hierarchy_creation as creation, editor_fields as fields, working_metadata as working, main, prepared_inputs
from app.auth import Principal,principal
from app.packages import PackageError,xml,members
from app.store import Conflict
from app.recovery import backup,restore
from app.service import Service
from test_hierarchy_editing import HierarchyFixture,ROOT
import test_hierarchy_editing as hierarchy_tests


class CreationFixture(HierarchyFixture):
    def creation_request(self,kind='dossier',count=1):
        doc=self.document();node=next(n for n in doc['nodes'] if n['kind']==kind)
        metadata=xml(working.baseline(self.service,self.current(),self.sip)[1])
        original=next(e for e in metadata.iter() if isinstance(e.tag,str) and creation.ident(e)==node['id'])
        option=next(o for o in doc['creation_options'] if o['parent_id']==creation.ident(original.getparent()) and o['child_kind']==kind)
        values={f['tag']:copy.deepcopy(f['values']) for f in node['fields'] if f['values']}
        def renew(rows):
            for row in rows:
                row['id']='new-'+str(uuid.uuid4())
                for children in row['children'].values():renew(children)
        for rows in values.values():renew(rows)
        if 'titel' in values:values['titel'][0]['text']='Created for reviewed preparation'
        return dict(request_id=str(uuid.uuid4()),revision=doc['revision'],source_sha256=doc['source_sha256'],
                    base_operation_id=doc['state']['head'],kind=creation.KIND,reason='Create objects without changing submitted originals',
                    parent_id=option['parent_id'],child_kind=kind,count=count,object_values=values)

    def create_objects(self,request=None,kind='dossier',count=1):
        return working.command(self.service,self.transfer['id'],'archive-a','editor',self.sip['id'],request or self.creation_request(kind,count))


class CreationTests(CreationFixture):
    def test_all_eight_profiles_native_ids_existing_identities_and_frozen_member_conservation(self):
        for case in json.loads((ROOT/'fixtures/corpus.json').read_bytes())['cases']:
            with self.subTest(case=case['file']):
                self.transfer,self.sip,_=self.create(case)
                kinds={n['kind'] for n in self.document()['nodes']} & creation.CHILD_KINDS
                source=self.service.blob(self.sip['sha256'])
                for kind in sorted(kinds):
                    before={n['id']:n for n in self.document()['nodes']}
                    record=self.create_objects(kind=kind,count=2)['operation']
                    after={n['id']:n for n in self.document()['nodes']}
                    self.assertEqual({k:after[k] for k in before},before)
                    self.assertEqual(len(after)-len(before),2)
                    self.assertEqual(record['version'],5)
                    self.assertEqual({n['id'] for n in record['changes']['objects']},set(after)-set(before))
                self.assertEqual(self.service.blob(self.sip['sha256']),source)
                self.assertEqual(self.current()['sips'][0],self.sip)
                frozen=self.freeze()['prepared_input'];self.assertEqual(frozen['version'],3)
                prepared_inputs.validate_record(self.current(),frozen,self.service.blob,self.service.schemas)
                working.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_invalid_scope_required_fields_count_and_stale_requests_are_atomic(self):
        request=self.creation_request();before=self.current()
        for overrides in (dict(parent_id='missing'),dict(child_kind='datei'),dict(child_kind='aktivitaet'),
                          dict(count=0),dict(count=True),dict(count=21),dict(object_values={}),
                          dict(object_values={'dateiRef':[]}),dict(request_id='invalid')):
            with self.subTest(overrides=overrides),self.assertRaises(PackageError):self.create_objects({**request,**overrides})
            self.assertEqual(self.current(),before)
        self.create_objects(request)
        with self.assertRaises(Conflict):self.create_objects({**request,'request_id':str(uuid.uuid4())})
        self.assertTrue(self.create_objects(request)['replayed'])
        with self.assertRaises(Conflict):self.create_objects({**request,'reason':'Different reason'})

    def test_notes_rename_undo_redo_restore_and_historical_freezes(self):
        old=self.edit()['operation'];legacy=self.freeze()['prepared_input']
        record=self.create_objects()['operation'];created=record['changes']['objects'][0]['id']
        self.operation('note-add',node_id=created,note_text='Linked to the new dossier',note_purpose='internal')
        self.operation('undo');self.operation('undo')
        self.assertNotIn(created,{n['id'] for n in self.document()['nodes']})
        self.assertFalse(self.document()['notes'])
        self.operation('redo');self.operation('redo')
        self.assertEqual(self.document()['notes'][0]['node_id'],created)
        self.operation('restore',target_id=old['id'])
        self.assertNotIn(created,{n['id'] for n in self.document()['nodes']})
        prepared_inputs.validate_record(self.current(),legacy,self.service.blob,self.service.schemas)
        self.assertEqual(legacy['version'],1)
        working.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_exact_replay_tamper_rejection_and_application_recovery(self):
        record=self.create_objects()['operation'];before=self.service.blob(record['before_metadata_sha256']);after=self.service.blob(record['after_metadata_sha256'])
        info=fields.schema(self.service.schemas,self.sip)
        forged=copy.deepcopy(record);forged['changes']['objects'][0]['source_id']='forged'
        with self.assertRaises(PackageError):creation.verify(before,after,forged,info,self.service.schemas,self.sip)
        tree=xml(after);tree.find('.//'+fields.A+'ablieferndeStelle').text='Outside creation scope'
        with self.assertRaises(PackageError):creation.verify(before,fields.validate(tree,self.service.schemas,self.sip),record,info,self.service.schemas,self.sip)
        snapshot=self.folder/'creation-backup';report=backup(self.service,snapshot,actor='backup',quiesced=True)
        destination=self.folder/'restored';store=self.new_store()
        restore(snapshot,destination,ROOT/'schemas',expected_digest=report['snapshot_sha256'],actor='restore',quiesced=True,store=store)
        restored=Service(destination,ROOT/'schemas',store=store)
        self.assertEqual(working.workspace(restored,self.transfer['id'],'archive-a',self.sip['id'])['nodes'],self.document()['nodes'])

    def test_api_preview_roles_tenants_required_values_and_exact_retry(self):
        prior=main.service;main.service=self.service
        self.addCleanup(setattr,main,'service',prior);self.addCleanup(main.app.dependency_overrides.clear)
        client=TestClient(main.app);base='/api/transfers/'+self.transfer['id']+'/sips/'+self.sip['id']+'/preparation'
        request=self.creation_request(count=2);before=self.current()
        def user(tenant='archive-a',roles=('archive-reader','archive-operator')):
            main.app.dependency_overrides[principal]=lambda:Principal('editor',tenant,frozenset(roles),'archive-workbench')
        user(roles=('archive-reader',));self.assertEqual(client.post(base+'/creation-preview',json=request).status_code,403)
        self.assertEqual(client.post(base+'/commands',json=request).status_code,403)
        user(tenant='other');self.assertEqual(client.post(base+'/creation-preview',json=request).status_code,404)
        user();invalid=client.post(base+'/creation-preview',json={**request,'object_values':{}});self.assertEqual(invalid.status_code,422,invalid.text)
        self.assertEqual(client.post(base+'/creation-preview',json={**request,'count':True}).status_code,422)
        preview=client.post(base+'/creation-preview',json=request);self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(self.current(),before)
        response=client.post(base+'/commands',json=request);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['operation']['changes'],preview.json())
        self.assertTrue(client.post(base+'/commands',json=request).json()['replayed'])
        self.assertEqual(client.post(base+'/creation-preview',json=request).status_code,409)


class CreationCandidateTests(CreationFixture,hierarchy_tests.HierarchyCandidateTests):
    def test_created_object_notes_selected_review_candidate_and_successor_hold(self):
        record=self.create_objects()['operation'];created=record['changes']['objects'][0]['id']
        self.operation('note-add',node_id=created,note_text='Retain new object in candidate evidence',note_purpose='internal')
        self.frozen=self.freeze()['prepared_input']
        from app.prepared_review import inputs
        projected,_=inputs(self.service,self.current(),self.selected())
        targets=[{'label':'Reviewed target','units':[{'sip_id':s['id'],'unit_id':u['id']} for s in projected for u in s['units']]}]
        self.plan(targets=targets);self.review();self.authorize();self.build();candidate=self.candidate()
        from app.aip_validation import validate_package
        result=validate_package(candidate,ROOT/'schemas');self.assertTrue(result['structural_valid'],result['errors'])
        self.assertFalse(result['preservation_ready']);self.assertEqual(self.frozen['version'],3)
        self.create_objects()
        self.assertNotIn('prepared_review_id',self.current());self.assertNotIn('approval',self.current())
        self.assertTrue(validate_package(candidate,ROOT/'schemas')['structural_valid'])
