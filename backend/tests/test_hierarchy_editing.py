"""Identity-safe sibling order, exact replay, retained evidence and candidate flow."""
import base64
import copy
import json
import uuid
from fastapi.testclient import TestClient
from app import main, hierarchy_editing as hierarchy, editor_fields as fields, working_metadata as working
from app import prepared_inputs, prepared_review
from app.auth import Principal, principal
from app.packages import PackageError, xml, sha, members, inspect
from app.recovery import backup, restore
from app.service import Service
from app.store import Conflict
from test_preparation_notes import NotesFixture
import test_prepared_aip as candidates
import test_prepared_review as reviews
from test_working_metadata import ROOT


class HierarchyFixture(NotesFixture):
    def reorder(self, group=None, **extra):
        group=group or self.document()['reorder_groups'][0]
        before=[c['id'] for c in group['children']]
        request=dict(parent_id=group['parent_id'],child_kind=group['child_kind'],before_order=before,after_order=list(reversed(before)))
        return self.operation(hierarchy.KIND,**{**request,**extra})


class HierarchyTests(HierarchyFixture):
    def test_activity_identity_survives_reorder_edit_preview_import_and_frozen_notes(self):
        case=next(c for c in json.loads((ROOT/'fixtures/corpus.json').read_bytes())['cases'] if '1.1G' in c['file'])
        self.transfer,self.sip,_=self.create(case)
        prior=main.service;main.service=self.service
        self.addCleanup(setattr,main,'service',prior)
        self.addCleanup(main.app.dependency_overrides.clear)
        main.app.dependency_overrides[principal]=lambda:Principal('editor','archive-a',frozenset({'archive-reader','archive-operator'}),'archive-workbench')
        client=TestClient(main.app)
        base=f"/api/transfers/{self.transfer['id']}/sips/{self.sip['id']}/preparation"
        def request(kind,**extra):
            doc=self.document()
            return dict(request_id=str(uuid.uuid4()),revision=doc['revision'],source_sha256=self.sip['sha256'],
                        base_operation_id=doc['state']['head'],reason='Review activity identity',kind=kind,**extra)
        def post(path,body):
            result=client.post(base+'/'+path,json=body)
            self.assertEqual(result.status_code,200,result.text)
            return result.json()
        def group():
            return next(g for g in self.document()['reorder_groups'] if g['child_kind']=='aktivitaet')
        def node(ident):
            return next(n for n in self.document()['nodes'] if n['id']==ident)

        before=group();first,second=before['children'][:2];selected=first['id']
        self.assertNotEqual(first['title'],second['title'])
        original=node(selected)
        note=self.operation('note-add',node_id=selected,note_text='The note follows this activity',note_purpose='internal')['operation']
        reordered=self.reorder(before)['operation']
        self.assertEqual(reordered['version'],10)
        old_xml,new_xml=(self.service.blob(reordered[k]) for k in ('before_metadata_sha256','after_metadata_sha256'))
        info=fields.schema(self.service.schemas,self.sip)
        early=copy.deepcopy(reordered)
        early['changes']['objects']=next(g['children'] for g in hierarchy.groups(xml(old_xml),activity_assignment=False)
                                         if g['parent_id']==before['parent_id'] and g['child_kind']=='aktivitaet')
        hierarchy.verify(old_xml,new_xml,early,info,self.service.schemas,self.sip)
        forged=copy.deepcopy(reordered)
        forged['changes']['identities']['after']=forged['changes']['identities']['before']
        with self.assertRaises(PackageError):
            hierarchy.verify(old_xml,new_xml,forged,info,self.service.schemas,self.sip)
        self.assertEqual(group()['children'][-1],first)
        self.assertEqual(node(selected),original)
        self.assertEqual(self.document()['notes'][0]['node_id'],selected)
        self.operation('undo');self.assertEqual(group(),before)
        self.operation('redo');self.assertEqual(group()['children'][-1],first)

        template=client.get(base+'/import-template/json',params={'object_id':selected})
        self.assertEqual(template.status_code,200,template.text)
        rows=template.json()
        assignment=next(row for row in rows if row['field']=='vorschreibung')
        self.assertEqual(assignment['value'],first['title'])
        field=next(f for f in original['fields'] if f['tag']=='vorschreibung')
        values=copy.deepcopy(field['values']);values[0]['text']='Reviewed assignment'
        edit=request('edit-batch',edits=[dict(node_id=selected,field_id=field['id'],before=field['values'],values=values)])
        preview=post('edit-preview',edit)
        applied=post('commands',edit)['operation']
        self.assertEqual(preview['metadata_sha256'],applied['after_metadata_sha256'])
        self.assertEqual(node(selected)['title'],'Reviewed assignment')
        self.assertEqual(node(second['id'])['title'],second['title'])

        raw=json.dumps([{'object_id':selected,'field':'vorschreibung','value':'Imported assignment'}]).encode()
        retained=post('commands',request('import-record',import_source={
            'filename':'activity.json','format':'json','data_base64':base64.b64encode(raw).decode()}))['operation']
        row=retained['changes']['metadata_import_proposal']['rows'][0]
        review=request('import-apply',import_id=retained['id'],import_decisions=[
            dict(row_id=row['id'],action='apply',node_id=selected,field='vorschreibung',reason='')])
        preview=post('import-preview',review)
        self.assertEqual(preview['metadata_sha256'],post('commands',review)['operation']['after_metadata_sha256'])
        frozen=self.freeze()['prepared_input']
        self.operation('restore',target_id=note['id'])
        self.assertEqual(group(),before)
        notes=prepared_review.note_views(self.service,self.current(),[{'sip_id':self.sip['id'],'prepared_id':frozen['id']}])[0]['notes']
        self.assertEqual([(n['node_id'],n['object_title']) for n in notes],[(selected,'Imported assignment')])
        self.assertEqual(node(selected)['title'],first['title'])
        working.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_all_eight_inputs_preserve_identities_inventory_note_links_and_versioned_freeze(self):
        for case in json.loads((ROOT/'fixtures/corpus.json').read_bytes())['cases']:
            with self.subTest(case=case['file']):
                self.transfer,self.sip,_=self.create(case)
                before=self.document();source=self.service.blob(self.sip['sha256'])
                group=before['reorder_groups'][0];record=self.reorder(group)['operation']
                after=self.document()
                self.assertEqual({n['id']:n for n in before['nodes']},{n['id']:n for n in after['nodes']})
                actual=next(g for g in after['reorder_groups'] if (g['parent_id'],g['child_kind'])==(group['parent_id'],group['child_kind']))
                self.assertEqual([n['id'] for n in actual['children']],record['changes']['after_order'])
                self.assertEqual(self.current()['sips'][0],self.sip)
                self.assertEqual(self.service.blob(self.sip['sha256']),source)
                frozen=self.freeze()['prepared_input'];self.assertEqual(frozen['version'],2)
                prepared_inputs.validate_record(self.current(),frozen,self.service.blob,self.service.schemas)
                report=inspect(self.service.blob(frozen['sha256']),case['profile'],self.service.schemas)
                self.assertEqual(report['files'],self.sip['files'])
                self.assertTrue(prepared_inputs.same_references(self.sip['references'],report['references'],2))
                working.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_reorder_rename_notes_shared_undo_redo_restore_and_historical_freezes(self):
        group=next(g for g in self.document()['reorder_groups'] if g['child_kind']=='dossier')
        selected=group['children'][0]['id'];node=next(n for n in self.document()['nodes'] if n['id']==selected)
        self.operation('note-add',node_id=selected,note_text='Retain this note identity through reordering',note_purpose='internal')
        first=self.document()['state']['head'];old=self.freeze()['prepared_input'];notes=self.document()['notes']
        self.reorder(group);field=next(f for f in node['fields'] if f['tag']=='titel')
        proposed=copy.deepcopy(field['values']);proposed[0]['text']='Renamed with stable identity'
        self.operation(node_id=selected,field_id=field['id'],before=field['values'],values=proposed)
        self.assertEqual(self.document()['notes'],notes)
        self.operation('undo');self.operation('undo');self.operation('redo')
        self.assertEqual(self.document()['notes'],notes)
        self.operation('restore',target_id=first)
        self.assertEqual(self.document()['reorder_groups'],working.workspace(self.service,self.transfer['id'],'archive-a',self.sip['id'],first)['reorder_groups'])
        prepared_inputs.validate_record(self.current(),old,self.service.blob,self.service.schemas)
        self.assertEqual(old['version'],1)
        working.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_invalid_partial_duplicate_cross_group_inventory_and_stale_orders_are_atomic(self):
        group=self.document()['reorder_groups'][0];before=self.current();ids=[n['id'] for n in group['children']]
        for extra in [dict(after_order=ids),dict(after_order=ids[:-1]),dict(after_order=[ids[0]]*len(ids)),
                      dict(after_order=['unknown']+ids[1:]),dict(child_kind='datei'),dict(parent_id='missing')]:
            with self.subTest(extra=extra),self.assertRaises(PackageError):self.reorder(group,**extra)
            self.assertEqual(self.current(),before)
        self.reorder(group)
        with self.assertRaises(Conflict):self.reorder(group)
        # A group with a missing native identity must not be offered or changed.
        root=xml(working.baseline(self.service,self.current(),self.sip)[1]);info=fields.schema(self.service.schemas,self.sip)
        target=next(e for e in root.iter() if isinstance(e.tag,str) and hierarchy.ident(e)==ids[0]);target.attrib.pop('id')
        with self.assertRaises(PackageError):hierarchy.apply(root,info,dict(parent_id=group['parent_id'],child_kind=group['child_kind'],before_order=ids,after_order=list(reversed(ids))))

    def test_retained_operation_scope_reference_multiplicities_and_backup_restore(self):
        record=self.reorder()['operation'];info=fields.schema(self.service.schemas,self.sip)
        before=self.service.blob(record['before_metadata_sha256']);after=self.service.blob(record['after_metadata_sha256'])
        forged=copy.deepcopy(record);forged['changes']['objects'][0]['title']='forged attribution'
        with self.assertRaises(PackageError):hierarchy.verify(before,after,forged,info,self.service.schemas,self.sip)
        root=xml(after);root.find('.//'+fields.A+'ablieferndeStelle').text='Unapproved change'
        with self.assertRaises(PackageError):hierarchy.verify(before,fields.validate(root,self.service.schemas,self.sip),record,info,self.service.schemas,self.sip)
        refs=[{'file_id':'f1','attributes':{}},{'file_id':'f2','attributes':{}},{'file_id':'f1','attributes':{}}]
        self.assertTrue(prepared_inputs.same_references(refs,[refs[1],refs[2],refs[0]],2))
        self.assertFalse(prepared_inputs.same_references(refs,refs[:-1],2))
        self.assertFalse(prepared_inputs.same_references(refs,[{**r,'attributes':{'information':'different'}} for r in refs],2))
        self.assertFalse(prepared_inputs.same_references(refs,[refs[1],refs[0],refs[2]],1))
        snapshot=self.folder/'hierarchy-backup';report=backup(self.service,snapshot,actor='backup',quiesced=True)
        destination=self.folder/'restored';store=self.new_store()
        restore(snapshot,destination,ROOT/'schemas',expected_digest=report['snapshot_sha256'],actor='restore',quiesced=True,store=store)
        restored=Service(destination,ROOT/'schemas',store=store)
        self.assertEqual(working.workspace(restored,self.transfer['id'],'archive-a',self.sip['id'])['reorder_groups'],self.document()['reorder_groups'])

    def test_api_roles_tenants_exact_retry_and_conflicting_reuse(self):
        prior=main.service;main.service=self.service
        self.addCleanup(setattr,main,'service',prior);self.addCleanup(main.app.dependency_overrides.clear)
        client=TestClient(main.app);base='/api/transfers/'+self.transfer['id']+'/sips/'+self.sip['id']+'/preparation'
        def user(tenant='archive-a',roles=('archive-reader','archive-operator')):
            main.app.dependency_overrides[principal]=lambda:Principal('editor',tenant,frozenset(roles),'archive-workbench')
        doc=self.document();group=doc['reorder_groups'][0];ids=[c['id'] for c in group['children']]
        request=dict(request_id=str(uuid.uuid4()),revision=doc['revision'],source_sha256=self.sip['sha256'],base_operation_id=None,kind=hierarchy.KIND,reason='Reviewed sibling order',parent_id=group['parent_id'],child_kind=group['child_kind'],before_order=ids,after_order=list(reversed(ids)))
        user(roles=('archive-reader',));self.assertEqual(client.post(base+'/commands',json=request).status_code,403)
        user(tenant='other');self.assertEqual(client.post(base+'/commands',json=request).status_code,404)
        user();response=client.post(base+'/commands',json=request);self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(client.post(base+'/commands',json=request).json()['replayed'])
        request['reason']='Changed';self.assertEqual(client.post(base+'/commands',json=request).status_code,409)


class HierarchyCandidateTests(HierarchyFixture):
    selected=reviews.ReviewFixture.selected
    plan=reviews.ReviewFixture.plan
    review_request=reviews.ReviewFixture.review_request
    review=reviews.ReviewFixture.review
    authorization_request=candidates.PreparedAipTests.authorization_request
    authorize=candidates.PreparedAipTests.authorize
    build=candidates.PreparedAipTests.build
    candidate=candidates.PreparedAipTests.candidate
    prepare=candidates.PreparedAipTests.prepare

    def test_selected_order_notes_candidate_offline_verification_and_successor_review_hold(self):
        self.add_note();self.reorder();self.frozen=self.freeze()['prepared_input']
        self.prepare();self.build();candidate=self.candidate()
        from app.aip_validation import validate_package
        result=validate_package(candidate,ROOT/'schemas');self.assertTrue(result['structural_valid'],result['errors'])
        self.assertFalse(result['preservation_ready'])
        self.assertEqual(self.frozen['version'],2)
        self.reorder()
        self.assertNotIn('prepared_review_id',self.current());self.assertNotIn('approval',self.current())
        self.assertTrue(validate_package(candidate,ROOT/'schemas')['structural_valid'])
