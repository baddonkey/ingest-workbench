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
from app import main, working_metadata as working
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
