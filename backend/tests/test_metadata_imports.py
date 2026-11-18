"""Retained external sources, explicit conflict decisions and atomic application."""
import base64
import copy
import io
import json
import struct
import uuid
import zipfile
from lxml import etree as ET
from openpyxl import load_workbook
from fastapi.testclient import TestClient
from app import main, metadata_imports as imports, working_metadata as working, editor_fields as fields, prepared_inputs
from app.auth import Principal, principal
from app.packages import PackageError, members, sha, xml
from app.recovery import backup, restore
from app.service import Service
from app.store import Conflict
from test_working_metadata import ROOT, WorkingFixture


class ImportTests(WorkingFixture):
    def supplied(self,data,fmt):
        return {'filename':'producer-metadata.'+fmt,'format':fmt,'data_base64':base64.b64encode(data).decode()}

    def data(self,fmt,value='Reviewed imported system description'):
        node,field=self.find_field('provenienz','systemBeschreibung')
        row={'object_id':node['id'],'field':field['tag'],'before':imports.scalar(field),'value':value}
        if fmt=='json':return json.dumps([row]).encode()
        if fmt=='csv':
            import csv
            stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=list(row))
            writer.writeheader();writer.writerow(row);return stream.getvalue().encode()
        if fmt=='xlsx':
            workbook=load_workbook(io.BytesIO(imports.template([node],fmt,b'')))
            for cells in workbook.active.iter_rows(min_row=2):
                if cells[1].value==field['tag']:cells[3].value=value
            stream=io.BytesIO();workbook.save(stream);workbook.close();return stream.getvalue()
        _,data,_=working.baseline(self.service,self.current(),self.sip)
        root=xml(data);info=fields.schema(self.service.schemas,self.sip)
        _,available=fields.catalog(root,info);element,definition,_=available[field['id']]
        proposed=copy.deepcopy(field['values']) or [self.new_row(field)]
        proposed[0]['text']=value
        fields.attach_order(definition,info,fields.type_name(element,info));fields.apply(element,definition,proposed)
        return fields.validate(root,self.service.schemas,self.sip)

    def decisions(self,record):
        return [dict(row_id=row['id'],action='apply',node_id=row['node_id'],field=row['field'],reason='')
                for row in record['changes']['metadata_import_proposal']['rows']]

    def test_all_four_formats_retain_source_then_apply_one_reviewed_undoable_change(self):
        for fmt in ('csv','xlsx','xml','json'):
            with self.subTest(format=fmt):
                self.transfer,self.sip,_=self.create(self.case)
                before=self.document();data=self.data(fmt)
                retained=self.operation('import-record',import_source=self.supplied(data,fmt))['operation']
                self.assertEqual(self.document()['state']['metadata_sha256'],before['state']['metadata_sha256'])
                proposed=retained['changes']['metadata_import_proposal']
                self.assertEqual(len(proposed['rows']),1)
                self.assertEqual(proposed['source']['sha256'],sha(data))
                self.assertEqual(proposed['rows'][0]['status'],'ready')
                self.assertEqual(len(self.document()['state']['undo']),0)
                applied=self.operation('import-apply',import_id=retained['id'],import_decisions=self.decisions(retained))['operation']
                self.assertEqual(len(applied['changes']['metadata_import']['edits']),1)
                self.assertEqual(self.find_field('provenienz','systemBeschreibung')[1]['values'][0]['text'],'Reviewed imported system description')
                evidence=members(self.service.blob(applied['evidence_sha256']))
                self.assertEqual(evidence['imports/'+sha(data)+'.'+fmt],data)
                self.assertIn(('urn:sha256:'+sha(data)).encode(),evidence['metadata/premis.xml'])
                working.validate_retained(self.current(),self.service.blob,self.service.schemas)
                self.operation('undo')
                self.assertEqual(self.document()['state']['metadata_sha256'],before['state']['metadata_sha256'])
                self.operation('redo')
                self.assertEqual(self.document()['state']['metadata_sha256'],applied['after_metadata_sha256'])

    def test_explicit_identity_mapping_exclusions_and_baseline_conflicts(self):
        node,field=self.find_field('provenienz','systemBeschreibung')
        delivery,protected=self.find_field('ablieferung','ablieferungstyp')
        rows=[{'object_id':'unknown-producer-id','field':'producer_title','value':'Mapped external description'},
              {'object_id':delivery['id'],'field':protected['tag'],'value':'Not a submission kind'}]
        retained=self.operation('import-record',import_source=self.supplied(json.dumps(rows).encode(),'json'))['operation']
        proposed=retained['changes']['metadata_import_proposal']
        self.assertEqual([row['status'] for row in proposed['rows']],['identity-conflict','protected-field'])
        decisions=self.decisions(retained);decisions[0].update(node_id=node['id'],field=field['tag'])
        before=self.current()
        with self.assertRaises(PackageError):self.operation('import-apply',import_id=retained['id'],import_decisions=decisions)
        self.assertEqual(self.current(),before)
        decisions[1].update(action='exclude',reason='Protected submission kind is not an import target.')
        self.operation('import-apply',import_id=retained['id'],import_decisions=decisions)
        self.assertEqual(self.find_field('provenienz',field['tag'])[1]['values'][0]['text'],'Mapped external description')
        self.assertEqual(self.find_field('ablieferung',protected['tag'])[1]['values'],protected['values'])
        incoming=[dict(object_id=node['id'],field=field['tag'],before='Older producer baseline',value='Reviewed successor')]
        retained=self.operation('import-record',import_source=self.supplied(json.dumps(incoming).encode(),'json'))['operation']
        self.assertEqual(retained['changes']['metadata_import_proposal']['rows'][0]['status'],'baseline-conflict')
        self.edit()
        with self.assertRaises(Conflict):self.operation('import-apply',import_id=retained['id'],import_decisions=self.decisions(retained))

    def test_templates_round_trip_and_csv_and_excel_do_not_export_formulas(self):
        doc=self.document();node=next(node for node in doc['nodes'] if node['kind']=='provenienz')
        field=next(field for field in node['fields'] if imports.eligible(field))
        field['values']=[self.new_row(field)];field['values'][0]['text']='=HYPERLINK("https://invalid.example","literal")'
        expected=[dict(object_id=node['id'],field=f['tag'],before=imports.scalar(f),value=imports.scalar(f))
                  for f in node['fields'] if imports.eligible(f)]
        for fmt in ('csv','xlsx','json'):
            with self.subTest(format=fmt):
                data=imports.template([node],fmt,b'')
                if fmt=='csv':
                    import csv
                    rows=imports.table(list(csv.reader(io.StringIO(data.decode('utf-8-sig')))))
                    self.assertIn('"""=HYPERLINK',data.decode('utf-8-sig'))
                elif fmt=='xlsx':
                    rows=imports.spreadsheet(data)
                    workbook=load_workbook(io.BytesIO(data))
                    self.assertEqual(workbook.active['D2'].data_type,'s');workbook.close()
                else:rows=imports.records(json.loads(data))
                self.assertEqual(rows,expected)

    def test_excluding_every_change_retains_decisions_without_serializing_xml_or_adding_undo(self):
        retained=self.operation('import-record',import_source=self.supplied(self.data('json'),'json'))['operation']
        before=self.document()
        decisions=self.decisions(retained)
        for decision in decisions:decision.update(action='exclude',reason='Producer value was reviewed and rejected.')
        reviewed=self.operation('import-apply',import_id=retained['id'],import_decisions=decisions)['operation']
        self.assertEqual(reviewed['before_metadata_sha256'],reviewed['after_metadata_sha256'])
        self.assertEqual(reviewed['changes']['metadata_import']['edits'],[])
        self.assertEqual(reviewed['changes']['metadata_import']['decisions'],decisions)
        self.assertEqual(self.document()['state']['undo'],before['state']['undo'])
        working.validate_retained(self.current(),self.service.blob,self.service.schemas)

    def test_invalid_late_field_rolls_back_the_whole_reviewed_import(self):
        node,field=self.find_field('provenienz','systemBeschreibung')
        delivery,other=self.find_field('ablieferung','ablieferndeStelle')
        rows=[dict(object_id=node['id'],field=field['tag'],value='Must not be partially applied'),
              dict(object_id=delivery['id'],field=other['tag'],value='Invalid XML character: \x00')]
        retained=self.operation('import-record',import_source=self.supplied(json.dumps(rows).encode(),'json'))['operation']
        before=self.current()
        with self.assertRaises(PackageError):
            self.operation('import-apply',import_id=retained['id'],import_decisions=self.decisions(retained))
        self.assertEqual(self.current(),before)

    def test_xml_reference_changes_are_explicitly_excluded_before_scalar_application(self):
        root=xml(self.data('xml'))
        reference=next(root.iter(fields.A+'dateiRef'))
        reference.text=next(element.get('id') for element in root.iter(fields.A+'datei')
                            if element.get('id')!=reference.text)
        data=fields.validate(root,self.service.schemas,self.sip)
        retained=self.operation('import-record',import_source=self.supplied(data,'xml'))['operation']
        rows=retained['changes']['metadata_import_proposal']['rows']
        self.assertTrue(any(row['blocked'] and row['status']=='unsupported-field' for row in rows))
        decisions=self.decisions(retained)
        for row,decision in zip(rows,decisions):
            if row['blocked']:decision.update(action='exclude',reason='Payload references remain unchanged.')
        before=xml(working.baseline(self.service,self.current(),self.sip)[1])
        self.operation('import-apply',import_id=retained['id'],import_decisions=decisions)
        after=xml(working.baseline(self.service,self.current(),self.sip)[1])
        self.assertEqual([element.text for element in before.iter(fields.A+'dateiRef')],
                         [element.text for element in after.iter(fields.A+'dateiRef')])
        self.assertEqual(self.find_field('provenienz','systemBeschreibung')[1]['values'][0]['text'],
                         'Reviewed imported system description')

    def test_api_preview_is_read_only_scoped_and_exact_retries_preserve_source_bytes(self):
        original=main.service;main.service=self.service
        self.addCleanup(setattr,main,'service',original);self.addCleanup(main.app.dependency_overrides.clear)
        def user(tenant='archive-a',roles=('archive-reader','archive-operator')):
            main.app.dependency_overrides[principal]=lambda:Principal('editor',tenant,frozenset(roles),'archive-workbench')
        user();client=TestClient(main.app)
        base='/api/transfers/'+self.transfer['id']+'/sips/'+self.sip['id']+'/preparation'
        data=self.data('json');doc=self.document()
        def request(kind,**extra):
            return dict(request_id=str(uuid.uuid4()),revision=doc['revision'],source_sha256=doc['source_sha256'],
                        base_operation_id=doc['state']['head'],reason='Review retained import against exact current input',kind=kind,**extra)
        recording=request('import-record',import_source=self.supplied(data,'json'))
        user(roles=('archive-reader',));self.assertEqual(client.post(base+'/commands',json=recording).status_code,403)
        user();response=client.post(base+'/commands',json=recording);self.assertEqual(response.status_code,200,response.text)
        retained=response.json()['operation']
        self.assertTrue(client.post(base+'/commands',json=recording).json()['replayed'])
        doc=self.document()
        applying=request('import-apply',import_id=retained['id'],import_decisions=self.decisions(retained))
        source_url=base+'/import-source/'+retained['id']
        user(roles=('archive-reader',))
        source=client.get(source_url);self.assertEqual(source.status_code,200,source.text)
        self.assertEqual(source.content,data);self.assertIn('producer-metadata.json',source.headers['content-disposition'])
        for fmt in ('csv','xlsx','xml','json'):
            response=client.get(base+'/import-template/'+fmt);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(client.post(base+'/import-preview',json=applying).status_code,403)
        user(tenant='another-archive')
        for path in (source_url,base+'/import-template/json'):
            self.assertEqual(client.get(path).status_code,404)
        self.assertEqual(client.post(base+'/import-preview',json=applying).status_code,404)
        user();before=self.current()
        preview=client.post(base+'/import-preview',json=applying)
        self.assertEqual(preview.status_code,200,preview.text);self.assertEqual(self.current(),before)
        response=client.post(base+'/commands',json=applying);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['operation']['after_metadata_sha256'],preview.json()['metadata_sha256'])
        self.assertTrue(client.post(base+'/commands',json=applying).json()['replayed'])
        self.assertEqual(len(self.document()['history']),2)
        self.assertEqual(client.post(base+'/import-preview',json=applying).status_code,409)
        self.assertEqual(client.get(source_url).content,data)

    def test_import_sources_and_frozen_evidence_survive_recovery_under_every_trusted_profile(self):
        for case in json.loads((ROOT/'fixtures/corpus.json').read_text(encoding='utf-8'))['cases']:
            with self.subTest(case=case['file']):
                self.transfer,self.sip,_=self.create(case);data=self.data('json')
                retained=self.operation('import-record',import_source=self.supplied(data,'json'))['operation']
                self.operation('import-apply',import_id=retained['id'],import_decisions=self.decisions(retained))
                doc=self.document()
                frozen=prepared_inputs.create(self.service,self.transfer['id'],'archive-a','editor',self.sip['id'],
                    dict(request_id=str(uuid.uuid4()),revision=doc['revision'],source_sha256=doc['source_sha256'],
                         operation_id=doc['state']['head'],reason='Freeze reviewed external metadata'))['prepared_input']
                prepared_inputs.validate_record(self.current(),frozen,self.service.blob,self.service.schemas)
        snapshot=self.folder/'import-backup';report=backup(self.service,snapshot,actor='backup',quiesced=True)
        destination=self.folder/'import-restored';store=self.new_store()
        restore(snapshot,destination,ROOT/'schemas',expected_digest=report['snapshot_sha256'],actor='restore',quiesced=True,store=store)
        restored=Service(destination,ROOT/'schemas',store=store)
        recovered=working.workspace(restored,self.transfer['id'],'archive-a',self.sip['id'])
        self.assertEqual(recovered['history'],self.document()['history'])
        self.assertEqual(imports.source(recovered['history'][0]['changes']['metadata_import_proposal']['source'])[1],data)

    def test_invalid_sources_formulas_duplicate_json_and_expanded_workbooks_are_explicit_errors(self):
        bad=[(b'[{"object_id":"a","field":"x","value":"a","value":"b"}]','json'),
             (b'[{"object_id":"a","field":"x","value":"\\ud800"}]','json'),
             (b'['*2000+b']'*2000,'json'),
             (b'object_id,field,value\n"a','csv'),(b'\xff','csv')]
        data=self.data('xlsx','=HYPERLINK("https://invalid.example","not executable")')
        bad.append((data,'xlsx'))
        damaged=bytearray(self.data('xlsx'))
        with zipfile.ZipFile(io.BytesIO(damaged)) as archive:
            offset=archive.getinfo('[Content_Types].xml').header_offset
        name_size,extra_size=struct.unpack_from('<HH',damaged,offset+26)
        damaged[offset+30+name_size+extra_size]=6
        bad.append((bytes(damaged),'xlsx'))
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:archive.writestr('large.xml',b'x'*(4*1024*1024+1))
        bad.append((stream.getvalue(),'xlsx'))
        before=self.current()
        for data,fmt in bad:
            with self.subTest(format=fmt),self.assertRaises(PackageError):
                self.operation('import-record',import_source=self.supplied(data,fmt))
            self.assertEqual(self.current(),before)
