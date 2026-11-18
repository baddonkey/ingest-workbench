"""Full trusted-profile metadata operations and real METS/PREMIS evidence."""
import copy
import io
import json
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from lxml import etree as ET
import test_metadata_drafts as drafts
from app import main, working_metadata as working, preparation_statistics
from app.auth import Principal,principal
from app.metadata_drafts import require_resolved
from app.packages import PackageError,sha,xml,members
from app.preparation_evidence import verify
from app.recovery import backup,restore
from app.service import Service
from app.store import Conflict

ROOT=drafts.ROOT


class WorkingFixture(drafts.DraftFixture):
    def document(self):return working.workspace(self.service,self.transfer['id'],'archive-a',self.sip['id'])
    def find_field(self,kind,tag):
        return next((n,f) for n in self.document()['nodes'] if n['kind']==kind for f in n['fields'] if f['tag']==tag)
    def operation(self,kind='edit',**extra):
        doc=self.document()
        request=dict(request_id=str(uuid.uuid4()),revision=doc['revision'],source_sha256=doc['source_sha256'],base_operation_id=doc['state']['head'],kind=kind,reason='Describe applied changes for archival review',**extra)
        return working.command(self.service,self.transfer['id'],'archive-a','editor',self.sip['id'],request)
    def edit(self,kind='ablieferung',tag='ablieferndeStelle',text='Revised submitting organisation'):
        node,field=self.find_field(kind,tag)
        proposed=copy.deepcopy(field['values'])
        if not proposed:proposed=[self.new_row(field)]
        proposed[0]['text']=text
        return self.operation(node_id=node['id'],field_id=field['id'],before=field['values'],values=proposed)
    def new_row(self,field):
        return {'id':'new-'+str(uuid.uuid4()),'text':'','attributes':{a['name']:'' for a in field['attributes']},
                'children':{c['tag']:[self.new_row(c) for _ in range(c['minimum'])] for c in field['children']}}


class WorkingTests(WorkingFixture):
    def test_statistics_bind_current_and_historical_metadata_without_mutating_receipt(self):
        before=self.document();retained=self.current()
        stats=before['statistics']
        self.assertEqual(stats['binding'],dict(source_sha256=self.sip['sha256'],
            metadata_sha256=before['state']['metadata_sha256'],operation_id=None,revision=before['revision']))
        self.assertEqual(sum(len(items) for items in stats['objects'].values()),len(before['nodes']))
        self.assertEqual(len(stats['payload_files']),len(self.sip['files']))
        original_data=self.service.blob(self.sip['sha256'])
        original=members(original_data)
        self.assertEqual(stats['payload_bytes'],sum(len(original[file['path']]) for file in self.sip['files'].values()))
        self.assertEqual(self.current(),retained)
        node,field=self.find_field('provenienz','systemBeschreibung')
        was_empty=any(item['field_id']==field['id'] for item in stats['empty_scalar_fields'])
        record=self.edit('provenienz','systemBeschreibung','Populated for exact-version statistics')['operation']
        changed=self.document()['statistics']
        self.assertEqual(changed['binding']['operation_id'],record['id'])
        self.assertEqual(changed['populated_scalar_fields'],stats['populated_scalar_fields']+int(was_empty))
        self.assertFalse(any(item['field_id']==field['id'] for item in changed['empty_scalar_fields']))
        self.edit()
        historical=working.workspace(self.service,self.transfer['id'],'archive-a',self.sip['id'],record['id'])['statistics']
        self.assertEqual(historical['binding']['metadata_sha256'],record['after_metadata_sha256'])
        self.assertEqual(historical['binding']['operation_id'],record['id'])
        self.assertEqual(historical['empty_scalar_fields'],changed['empty_scalar_fields'])
        self.assertEqual(self.current()['sips'][0],retained['sips'][0])
        self.assertEqual(self.service.blob(self.sip['sha256']),original_data)

    def test_statistics_detect_broken_references_and_missing_nested_required_values(self):
        from app import editor_fields as fields
        root=xml(members(self.service.blob(self.sip['sha256']))[self.sip['metadata_path']])
        target=root.find('.//'+fields.A+'dateiRef').text
        for reference in root.iter(fields.A+'dateiRef'):
            if reference.text==target:reference.text='missing-file-identity'
        organisation=root.find('.//'+fields.A+'ablieferndeStelle')
        organisation.getparent().remove(organisation)
        required_date=root.find('.//'+fields.A+'entstehungszeitraum/'+fields.A+'von')
        required_date.getparent().remove(required_date)
        nodes,_=fields.catalog(root,fields.schema(self.service.schemas,self.sip))
        stats=preparation_statistics.project(root,nodes,self.sip['files'],{})
        reversed_payload=dict(reversed(list(self.sip['files'].items())))
        self.assertEqual(preparation_statistics.project(root,nodes,reversed_payload,{}),stats)
        self.assertTrue(stats['broken_references'])
        self.assertTrue(all(item['detail']=='missing-file-identity' and item['node_id']
                            for item in stats['broken_references']))
        self.assertTrue(any(self.sip['files'][target]['path']==item['detail'] for item in stats['unreferenced_payload']))
        self.assertTrue(any(item['detail']=='ablieferndeStelle' for item in stats['missing_required']))
        self.assertTrue(any(item['detail'].endswith('/von') for item in stats['missing_required']))

    def batch_edits(self):
        edits=[]
        for kind,tag,text in [('ablieferung','ablieferndeStelle','Atomic submitting organisation'),
                              ('provenienz','systemBeschreibung','Atomic system description')]:
            node,field=self.find_field(kind,tag)
            values=copy.deepcopy(field['values']) or [self.new_row(field)]
            values[0]['text']=text
            edits.append(dict(node_id=node['id'],field_id=field['id'],before=field['values'],values=values))
        return edits

    def test_batch_save_is_one_revision_with_per_field_evidence_and_one_undo_redo_step(self):
        before=self.document();edits=self.batch_edits()
        record=self.operation('edit-batch',edits=edits)['operation']
        after=self.document()
        self.assertEqual(after['revision'],before['revision']+1)
        self.assertEqual(len(after['history']),1)
        self.assertEqual(len(after['state']['undo']),1)
        self.assertEqual(record['version'],6)
        self.assertEqual(len(record['changes']['edits']),2)
        for change,proposal in zip(record['changes']['edits'],edits):
            self.assertEqual(change['node_id'],proposal['node_id'])
            self.assertEqual(change['before'],proposal['before'])
            self.assertEqual(change['proposed'],proposal['values'])
        self.operation('undo')
        self.assertEqual(self.document()['state']['metadata_sha256'],before['state']['metadata_sha256'])
        self.operation('redo')
        self.assertEqual(self.document()['state']['metadata_sha256'],after['state']['metadata_sha256'])
        working.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_batch_save_rejects_late_invalid_stale_duplicate_and_protected_fields_atomically(self):
        edits=self.batch_edits();before=self.current()
        invalid=copy.deepcopy(edits);invalid[-1]['values'][0]['text']='invalid\x00'
        stale=copy.deepcopy(edits);stale[-1]['before']=[{'id':'stale-row'}]
        node,field=self.find_field('datei','name')
        protected=dict(node_id=node['id'],field_id=field['id'],before=field['values'],values=[])
        for proposed,error in [(invalid,PackageError),(stale,Conflict),([edits[0],edits[0]],PackageError),
                               (edits+[protected],PackageError),([],PackageError)]:
            with self.subTest(proposed=proposed),self.assertRaises(error):
                self.operation('edit-batch',edits=proposed)
            self.assertEqual(self.current(),before)

    def test_batch_save_retry_and_retained_scope_verification(self):
        doc=self.document()
        request=dict(request_id=str(uuid.uuid4()),revision=doc['revision'],source_sha256=doc['source_sha256'],
                     base_operation_id=doc['state']['head'],kind='edit-batch',reason='Atomic retry',edits=self.batch_edits())
        first=working.command(self.service,self.transfer['id'],'archive-a','editor',self.sip['id'],request)
        replay=working.command(self.service,self.transfer['id'],'archive-a','editor',self.sip['id'],request)
        self.assertEqual(first['operation'],replay['operation'])
        self.assertTrue(replay['replayed'])
        self.assertEqual(len(self.document()['history']),1)
        with self.assertRaises(Conflict):
            working.command(self.service,self.transfer['id'],'archive-a','editor',self.sip['id'],
                            {**request,'reason':'Changed retry'})
        record=first['operation'];info=working.fields.schema(self.service.schemas,self.sip)
        forged=copy.deepcopy(record);forged['changes']['edits'][-1]['after'][0]['text']='Unapproved retained value'
        with self.assertRaises(PackageError):
            working.verify_batch(self.service.blob(record['before_metadata_sha256']),
                                 self.service.blob(record['after_metadata_sha256']),forged,info,self.service.schemas,self.sip)
        working.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_batch_save_freezes_and_recovers_under_all_trusted_profiles(self):
        from app import prepared_inputs
        for case in json.loads((ROOT/'fixtures/corpus.json').read_text(encoding='utf-8'))['cases']:
            with self.subTest(case=case['file']):
                self.transfer,self.sip,_=self.create(case)
                self.operation('edit-batch',edits=self.batch_edits())
                doc=self.document()
                frozen=prepared_inputs.create(self.service,self.transfer['id'],'archive-a','editor',self.sip['id'],
                    dict(request_id=str(uuid.uuid4()),revision=doc['revision'],source_sha256=doc['source_sha256'],
                         operation_id=doc['state']['head'],reason='Review exact atomic package save'))['prepared_input']
                prepared_inputs.validate_record(self.current(),frozen,self.service.blob,self.service.schemas)
        snapshot=self.folder/'batch-backup';report=backup(self.service,snapshot,actor='backup',quiesced=True)
        destination=self.folder/'batch-restored';store=self.new_store()
        restore(snapshot,destination,ROOT/'schemas',expected_digest=report['snapshot_sha256'],actor='restore',quiesced=True,store=store)
        restored=Service(destination,ROOT/'schemas',store=store)
        recovered=working.workspace(restored,self.transfer['id'],'archive-a',self.sip['id'])
        expected=self.document()
        self.assertEqual(recovered['revision'],expected['revision']+1)
        self.assertEqual(recovered['statistics']['binding']['revision'],recovered['revision'])
        expected['revision']=recovered['revision']
        expected['statistics']['binding']['revision']=recovered['revision']
        self.assertEqual(recovered,expected)

    def test_retained_sample_and_item_reviews_bind_exact_version_without_editing_metadata(self):
        before=self.document()
        retained=self.operation('sample-record',sample_mode='absolute',sample_size=2,sample_seed='retained-seed',
                                sample_name='',sample_reference='')['operation']
        sample=retained['changes']['sample']
        self.assertEqual(sample['metadata_sha256'],before['state']['metadata_sha256'])
        self.assertEqual(sample['input_operation_id'],before['state']['head'])
        self.assertEqual(self.document()['state']['metadata_sha256'],before['state']['metadata_sha256'])
        reviews=[dict(node_id=item['node_id'],outcome='checked',comment='Reviewed sampled description') for item in sample['items']]
        reviewed=self.operation('sample-review',sample_id=retained['id'],reviews=reviews)['operation']
        self.assertEqual(reviewed['changes']['sample_review']['metadata_sha256'],sample['metadata_sha256'])
        from app import prepared_inputs
        doc=self.document()
        frozen=prepared_inputs.create(self.service,self.transfer['id'],'archive-a','editor',self.sip['id'],
            dict(request_id=str(uuid.uuid4()),revision=doc['revision'],source_sha256=doc['source_sha256'],
                 operation_id=doc['state']['head'],reason='Freeze exact sampled and reviewed metadata'))['prepared_input']
        self.edit()
        self.assertNotEqual(self.document()['state']['metadata_sha256'],sample['metadata_sha256'])
        historical=working.workspace(self.service,self.transfer['id'],'archive-a',self.sip['id'],retained['id'])
        self.assertEqual(historical['state']['head'],self.document()['state']['head'])
        self.assertEqual(self.document()['history'][0]['changes']['sample'],sample)
        prepared_inputs.validate_record(self.current(),frozen,self.service.blob,self.service.schemas)
        working.validate_retained(self.current(),self.service.blob,self.service.schemas)
        evidence=members(self.service.blob(reviewed['evidence_sha256']))
        self.assertIn(b'sample-review',evidence['metadata/premis.xml'])
        self.assertIn(b'retained-seed',evidence['history.json'])

    def test_sampling_rejects_empty_criteria_results_incomplete_and_duplicate_reviews_atomically(self):
        baseline=self.current()
        with self.assertRaises(PackageError):
            self.operation('sample-record',sample_mode='absolute',sample_size=2,sample_seed='seed',sample_name='No matching dossier')
        self.assertEqual(self.current(),baseline)
        retained=self.operation('sample-record',sample_mode='absolute',sample_size=2,sample_seed='seed')['operation']
        items=retained['changes']['sample']['items']
        reviews=[dict(node_id=item['node_id'],outcome='checked',comment='') for item in items]
        baseline=self.current()
        invalid=copy.deepcopy(reviews);invalid[0]['outcome']='issue'
        duplicate=copy.deepcopy(reviews);duplicate[-1]['node_id']='unknown'
        for values in ([],invalid,duplicate):
            with self.subTest(values=values),self.assertRaises(PackageError):
                self.operation('sample-review',sample_id=retained['id'],reviews=values)
            self.assertEqual(self.current(),baseline)
        self.operation('sample-review',sample_id=retained['id'],reviews=reviews)
        with self.assertRaises(PackageError):
            self.operation('sample-review',sample_id=retained['id'],reviews=reviews)
        working.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_all_eight_profiles_edit_delivery_and_optional_provenance_preserving_other_xml(self):
        for case in json.loads((ROOT/'fixtures/corpus.json').read_text(encoding='utf-8'))['cases']:
            with self.subTest(case=case['file']):
                self.transfer,self.sip,self.field_unused=self.create(case)
                original=self.service.blob(self.sip['sha256'])
                self.edit()
                record=self.edit('provenienz','systemBeschreibung','Profile-specific provenance description')['operation']
                metadata=xml(self.service.blob(record['after_metadata_sha256']))
                before=xml(members(original)[self.sip['metadata_path']])
                ns={'a':'http://bar.admin.ch/arelda/v4'}
                changed=metadata.find('.//a:ablieferndeStelle',ns);changed.text=before.find('.//a:ablieferndeStelle',ns).text
                changed=metadata.find('.//a:systemBeschreibung',ns);prior=before.find('.//a:systemBeschreibung',ns)
                if prior is None:changed.getparent().remove(changed)
                else:changed.text=prior.text
                self.assertEqual(ET.tostring(metadata,method='c14n'),ET.tostring(before,method='c14n'))
                self.assertEqual(self.service.blob(self.sip['sha256']),original)
                self.assertEqual(self.current()['sips'][0],self.sip)
                statistics=self.document()['statistics']
                self.assertEqual(statistics['missing_required'],[])
                self.assertEqual(statistics['broken_references'],[])
                self.assertEqual(len(statistics['payload_files']),len(self.sip['files']))
                working.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_grouped_dates_uncertainty_and_repeated_values_reorder_remove(self):
        for case in json.loads((ROOT/'fixtures/corpus.json').read_text(encoding='utf-8'))['cases']:
            self.transfer,self.sip,self.field_unused=self.create(case)
            node,field=self.find_field('ablieferung','entstehungszeitraum')
            proposed=copy.deepcopy(field['values']) or [self.new_row(field)]
            for part,date in [('von','2000'),('bis','2000-12-31')]:
                point=proposed[0]['children'][part][0]
                point['children']['datum'][0]['text']=date
                point_schema=next(f for f in field['children'] if f['tag']==part)
                ca_schema=next(f for f in point_schema['children'] if f['tag']=='ca')
                if not point['children']['ca']:point['children']['ca']=[self.new_row(ca_schema)]
                point['children']['ca'][0]['text']='true' if part=='von' else 'false'
            self.operation(node_id=node['id'],field_id=field['id'],before=field['values'],values=proposed)
        node,field=self.find_field('datei','eigenschaft')
        proposed=[self.new_row(field),self.new_row(field)]
        proposed[0]['text']='First characteristic';proposed[1]['text']='Second characteristic'
        proposed[0]['attributes']['name']='first';proposed[1]['attributes']['name']='second'
        self.operation(node_id=node['id'],field_id=field['id'],before=field['values'],values=proposed)
        node,field=self.find_field('datei','eigenschaft')
        self.operation(node_id=node['id'],field_id=field['id'],before=field['values'],values=list(reversed(field['values'])))
        node,field=self.find_field('datei','eigenschaft')
        self.assertEqual([r['text'] for r in field['values']],['Second characteristic','First characteristic'])
        self.operation(node_id=node['id'],field_id=field['id'],before=field['values'],values=[])
        self.assertEqual(self.find_field('datei','eigenschaft')[1]['values'],[])
        working.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_additional_metadata_attributes_are_editable_but_technical_names_are_protected(self):
        node,field=self.find_field('dossier','zusatzDaten')
        proposed=copy.deepcopy(field['values']) or [self.new_row(field)]
        row=proposed[0]['children']['merkmal'][0]
        row['attributes']['name']='Archive context';row['text']='Unknown extensions remain traceable'
        self.operation(node_id=node['id'],field_id=field['id'],before=field['values'],values=proposed)
        node,field=self.find_field('datei','name')
        with self.assertRaises(PackageError):
            self.operation(node_id=node['id'],field_id=field['id'],before=field['values'],values=[])
        working.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_invalid_values_required_removal_row_forgery_and_schema_dates_are_atomic(self):
        node,field=self.find_field('ablieferung','ablieferndeStelle')
        before=self.current()
        for proposed in [[],[{**field['values'][0],'id':'unknown-row'}],[{**field['values'][0],'text':'invalid\x00'}],[{**field['values'][0],'attributes':{'id':'injected'}}]]:
            with self.subTest(proposed=proposed),self.assertRaises(PackageError):
                self.operation(node_id=node['id'],field_id=field['id'],before=field['values'],values=proposed)
            self.assertEqual(self.current(),before)
        node,field=self.find_field('ablieferung','entstehungszeitraum')
        proposed=copy.deepcopy(field['values'])
        proposed[0]['children']['von'][0]['children']['datum'][0]['text']='2000-99-99'
        with self.assertRaises(PackageError):self.operation(node_id=node['id'],field_id=field['id'],before=field['values'],values=proposed)
        self.assertEqual(self.current(),before)

    def test_undo_redo_restore_checkpoint_preserve_all_events_and_source_holds(self):
        original=self.document()['state']['metadata_sha256']
        first=self.edit()['operation']
        self.operation('checkpoint',name='Ready for metadata review')
        self.edit(text='Second organisation')
        self.operation('undo')
        self.assertEqual(self.document()['state']['metadata_sha256'],first['after_metadata_sha256'])
        self.operation('redo')
        self.operation('restore',target_id=first['id'])
        with self.assertRaises(Conflict):require_resolved(self.current())
        working.validate_retained(self.current(),self.service.blob,self.service.schemas)
        self.assertEqual(len(self.document()['history']),6)
        self.assertNotEqual(original,self.document()['state']['metadata_sha256'])
        self.assertIsNone(self.current().get('approval'))
        while self.document()['state']['undo']:
            self.operation('undo')
        self.assertEqual(self.document()['state']['metadata_sha256'],original)
        self.assertFalse(self.document()['state']['changed'])
        # History must not be dropped merely because values return to the source.
        with self.assertRaises(Conflict):require_resolved(self.current())

    def test_exact_retry_is_idempotent_and_concurrent_conflicting_edit_has_one_winner(self):
        node,field=self.find_field('ablieferung','ablieferndeStelle');doc=self.document()
        proposed=copy.deepcopy(field['values']);proposed[0]['text']='Retained once'
        request=dict(request_id=str(uuid.uuid4()),revision=doc['revision'],source_sha256=doc['source_sha256'],base_operation_id=None,kind='edit',reason='Retry evidence',node_id=node['id'],field_id=field['id'],before=field['values'],values=proposed)
        def run(req):
            try:return working.command(self.service,self.transfer['id'],'archive-a','editor',self.sip['id'],req)
            except Conflict:return None
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,[request,request]))
        self.assertEqual(results[0]['operation'],results[1]['operation'])
        self.assertEqual(len(self.document()['history']),1)
        with self.assertRaises(Conflict):working.command(self.service,self.transfer['id'],'archive-a','other-editor',self.sip['id'],request)
        request['request_id']=str(uuid.uuid4())
        self.assertIsNone(run(request))

    def test_evidence_has_exact_original_all_versions_and_attributable_mets_premis(self):
        self.edit();record=self.operation('checkpoint',name='Evidence checkpoint')['operation']
        evidence=self.service.blob(record['evidence_sha256'])
        verify(evidence,self.document()['history'],self.sip,self.service.schemas)
        files=members(evidence)
        self.assertEqual(sha(files['original.zip']),self.sip['sha256'])
        self.assertIn(b'urn:ingest-workbench:preparation-events:1:edit',files['metadata/premis.xml'])
        self.assertIn(b'Revised submitting organisation',files['metadata/premis.xml'])
        files['metadata/premis.xml']=files['metadata/premis.xml'].replace(b'Revised submitting organisation',b'Altered unauthorised description')
        stream=io.BytesIO()
        files['manifest-sha256.txt']=''.join(sha(data)+'  '+path+'\n' for path,data in sorted(files.items()) if path!='manifest-sha256.txt').encode()
        with zipfile.ZipFile(stream,'w') as archive:
            for path,data in files.items():archive.writestr(path,data)
        with self.assertRaises(PackageError):verify(stream.getvalue(),self.document()['history'],self.sip,self.service.schemas)

    def test_legacy_drafts_are_inherited_with_evidence_and_cannot_overwrite_working_edits(self):
        legacy=self.save()['draft'];record=self.edit()['operation']
        self.assertEqual(record['legacy_draft_id'],legacy['id'])
        self.assertIn('legacy/'+legacy['id']+'.json',members(self.service.blob(record['evidence_sha256'])))
        with self.assertRaises(Conflict):self.discard()
        with self.assertRaises(Conflict):self.save()
        working.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_paired_recovery_retains_history_undo_and_refuses_false_ingest_hold(self):
        self.edit();self.operation('checkpoint',name='Recovery test')
        snapshot=self.folder/'snapshot';report=backup(self.service,snapshot,actor='backup',quiesced=True)
        store=self.new_store();destination=self.folder/'restored'
        restore(snapshot,destination,ROOT/'schemas',expected_digest=report['snapshot_sha256'],actor='restore',quiesced=True,store=store)
        restored=Service(destination,ROOT/'schemas',store=store)
        self.assertEqual(working.workspace(restored,self.transfer['id'],'archive-a',self.sip['id'])['history'],self.document()['history'])
        orphaned=copy.deepcopy(self.current());orphaned['metadata_operations'][0]['sip_id']='missing-sip'
        with self.assertRaises(PackageError):working.validate_retained(orphaned,self.service.blob,self.service.schemas)
        body=self.current();body['working_metadata'][self.sip['id']]['changed']=False
        self.service.store.save(body,body['revision'])
        with self.assertRaises(PackageError):backup(self.service,self.folder/'bad-hold',actor='backup',quiesced=True)


class WorkingApiTests(WorkingFixture):
    def test_batch_api_has_one_operation_and_preserves_old_request_identity(self):
        original=main.service;main.service=self.service
        self.addCleanup(setattr,main,'service',original);self.addCleanup(main.app.dependency_overrides.clear)
        main.app.dependency_overrides[principal]=lambda:Principal('editor','archive-a',frozenset({'archive-reader','archive-operator'}),'archive-workbench')
        client=TestClient(main.app);base='/api/transfers/'+self.transfer['id']+'/sips/'+self.sip['id']+'/preparation'
        doc=self.document();edits=[]
        for kind,tag in [('ablieferung','ablieferndeStelle'),('provenienz','systemBeschreibung')]:
            node,field=self.find_field(kind,tag)
            values=copy.deepcopy(field['values']) or [self.new_row(field)]
            values[0]['text']='One retained package save'
            edits.append(dict(node_id=node['id'],field_id=field['id'],before=field['values'],values=values))
        request=dict(request_id=str(uuid.uuid4()),revision=doc['revision'],source_sha256=doc['source_sha256'],
                     base_operation_id=doc['state']['head'],kind='edit-batch',reason='Save all proposals',edits=edits)
        before=self.current()
        preview=client.post(base+'/edit-preview',json=request)
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(self.current(),before)
        main.app.dependency_overrides[principal]=lambda:Principal('reader','archive-a',frozenset({'archive-reader'}),'archive-workbench')
        self.assertEqual(client.post(base+'/edit-preview',json=request).status_code,403)
        main.app.dependency_overrides[principal]=lambda:Principal('editor','other-archive',frozenset({'archive-operator'}),'archive-workbench')
        self.assertEqual(client.post(base+'/edit-preview',json=request).status_code,404)
        main.app.dependency_overrides[principal]=lambda:Principal('editor','archive-a',frozenset({'archive-operator'}),'archive-workbench')
        invalid=copy.deepcopy(request);invalid['edits'][-1]['values'][0]['text']='invalid\x00'
        self.assertEqual(client.post(base+'/edit-preview',json=invalid).status_code,422)
        self.assertEqual(self.current(),before)
        result=client.post(base+'/commands',json=request)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['operation']['after_metadata_sha256'],preview.json()['metadata_sha256'])
        self.assertEqual(len(result.json()['operation']['changes']['edits']),2)
        self.assertEqual(client.post(base+'/edit-preview',json=request).status_code,409)
        self.assertTrue(client.post(base+'/commands',json=request).json()['replayed'])
        self.assertEqual(client.post(base+'/commands',json={**request,'edits':[dict(edits[0],unexpected=True)]}).status_code,422)

    def test_dossier_samples_are_current_version_read_only_and_tenant_scoped(self):
        original=main.service;main.service=self.service
        self.addCleanup(setattr,main,'service',original);self.addCleanup(main.app.dependency_overrides.clear)
        def user(tenant='archive-a',roles=('archive-reader',)):
            main.app.dependency_overrides[principal]=lambda:Principal('reader',tenant,frozenset(roles),'archive-workbench')
        client=TestClient(main.app);base='/api/transfers/'+self.transfer['id']+'/sips/'+self.sip['id']+'/preparation'
        user();document=client.get(base).json()
        dossiers=[node for node in document['nodes'] if node['kind']=='dossier']
        query={'mode':'absolute','size':'2','revision':document['revision']}
        result=client.get(base+'/sample',params=query)
        self.assertEqual(result.status_code,200,result.text)
        sample=result.json()
        self.assertEqual(sample['revision'],document['revision'])
        self.assertEqual(len(sample['items']),min(2,len(dossiers)))
        self.assertEqual(len({item['node_id'] for item in sample['items']}),len(sample['items']))
        self.assertEqual([item['node_id'] for item in sample['items']],
                         [node['id'] for node in dossiers if node['id'] in {item['node_id'] for item in sample['items']}])
        all_dossiers=client.get(base+'/sample',params={'mode':'percent','size':'100','revision':document['revision']}).json()
        self.assertEqual([item['node_id'] for item in all_dossiers['items']],[node['id'] for node in dossiers])
        self.assertEqual(client.get(base+'/sample',params={**query,'revision':document['revision']+1}).status_code,409)
        self.assertEqual(client.get(base+'/sample',params={'mode':'percent','size':'101'}).status_code,422)
        self.assertEqual(client.get(base+'/sample',params={'mode':'absolute','size':'501'}).status_code,422)
        seeded=client.get(base+'/sample',params={**query,'seed':sample['seed']}).json()
        self.assertEqual(seeded,sample)
        request=dict(request_id=str(uuid.uuid4()),revision=document['revision'],source_sha256=document['source_sha256'],
            base_operation_id=document['state']['head'],kind='sample-record',reason='Retain reproducible review sample',
            sample_mode='absolute',sample_size=2,sample_seed=sample['seed'])
        self.assertEqual(client.post(base+'/commands',json=request).status_code,403)
        user(roles=('archive-reader','archive-operator'))
        retained=client.post(base+'/commands',json=request)
        self.assertEqual(retained.status_code,200,retained.text)
        self.assertEqual(retained.json()['operation']['changes']['sample'],sample)
        self.assertTrue(client.post(base+'/commands',json=request).json()['replayed'])
        self.assertEqual(client.get(base+'/sample',params=query).status_code,409)
        user(tenant='other-archive')
        self.assertEqual(client.get(base+'/sample').status_code,404)

    def test_roles_tenants_downloads_stale_requests_and_historical_comparison(self):
        original=main.service;main.service=self.service
        self.addCleanup(setattr,main,'service',original);self.addCleanup(main.app.dependency_overrides.clear)
        def user(tenant='archive-a',roles=('archive-reader','archive-operator')):
            main.app.dependency_overrides[principal]=lambda:Principal('editor',tenant,frozenset(roles),'archive-workbench')
        client=TestClient(main.app);base='/api/transfers/'+self.transfer['id']+'/sips/'+self.sip['id']+'/preparation'
        user();doc=client.get(base).json();node,field=self.find_field('ablieferung','ablieferndeStelle')
        proposed=copy.deepcopy(field['values']);proposed[0]['text']='HTTP edit'
        request=dict(request_id=str(uuid.uuid4()),revision=doc['revision'],source_sha256=doc['source_sha256'],base_operation_id=None,kind='edit',reason='HTTP metadata preparation',node_id=node['id'],field_id=field['id'],before=field['values'],values=proposed)
        user(roles=('archive-reader',));self.assertEqual(client.post(base+'/commands',json=request).status_code,403)
        user(tenant='other');self.assertEqual(client.get(base).status_code,404)
        user();response=client.post(base+'/commands',json=request);self.assertEqual(response.status_code,200,response.text)
        record=response.json()['operation'];self.assertTrue(client.post(base+'/commands',json=request).json()['replayed'])
        user(roles=('archive-reader',))
        for kind,field in [('metadata','after_metadata_sha256'),('evidence','evidence_sha256'),('changes','changes_sha256')]:
            response=client.get(base+'/'+record['id']+'/'+kind);self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(sha(response.content),record[field])
        self.assertEqual(client.get(base+'?operation_id='+record['id']).status_code,200)
        user(tenant='other');self.assertEqual(client.get(base+'/'+record['id']+'/metadata').status_code,404)
