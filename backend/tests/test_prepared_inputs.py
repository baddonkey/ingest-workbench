"""Frozen working versions: exact bytes, provenance, retries and recovery."""
import copy
import io
import json
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main, prepared_inputs as prepared
from app.auth import Principal, principal
from app.json_codec import canonical
from app.metadata_drafts import require_resolved
from app.metadata_validation import validate_metadata
from app.packages import PackageError, inspect, members, sha
from app.persistence_transfer import blob_bindings
from app.recovery import backup, restore
from app.service import Service, put
from app.store import Conflict
from test_working_metadata import WorkingFixture, ROOT


class PreparedFixture(WorkingFixture):
    def request(self, **overrides):
        doc = self.document()
        return dict(request_id=str(uuid.uuid4()), revision=doc['revision'], source_sha256=self.sip['sha256'],
                    operation_id=doc['state']['head'], reason='Freeze the described version for subsequent review', **overrides)

    def freeze(self, request=None, actor='editor'):
        return prepared.create(self.service, self.transfer['id'], 'archive-a', actor, self.sip['id'], request or self.request())


class PreparedTests(PreparedFixture):
    def test_all_eight_profiles_freeze_current_bytes_and_preserve_originals_with_actual_mets_premis(self):
        for case in json.loads((ROOT/'fixtures/corpus.json').read_text(encoding='utf-8'))['cases']:
            with self.subTest(case=case['file']):
                self.transfer, self.sip, _ = self.create(case)
                source = self.service.blob(self.sip['sha256'])
                self.edit()
                op = self.edit('provenienz', 'systemBeschreibung', 'Prepared provenance')['operation']
                record = self.freeze()['prepared_input']
                data = self.service.blob(record['sha256'])
                files = members(data)
                original = members(source)
                self.assertEqual(files[self.sip['metadata_path']], self.service.blob(op['after_metadata_sha256']))
                self.assertEqual({k:v for k,v in files.items() if k != self.sip['metadata_path']},
                                 {k:v for k,v in original.items() if k != self.sip['metadata_path']})
                report = inspect(data, case['profile'], self.service.schemas)
                self.assertEqual(report['files'], self.sip['files'])
                self.assertEqual(report['references'], self.sip['references'])
                evidence = members(self.service.blob(record['evidence_sha256']))
                validate_metadata(evidence, self.service.schemas/'preservation')
                self.assertEqual(evidence['prepared.zip'], data)
                self.assertEqual(evidence['preparation.zip'], self.service.blob(op['evidence_sha256']))
                descriptor = json.loads(evidence['freeze.json'])
                self.assertEqual(descriptor['prepared_input']['operation_id'], op['id'])
                self.assertFalse(descriptor['promoted'])
                self.assertIn(b':freeze', evidence['metadata/premis.xml'])
                self.assertEqual(self.current()['sips'][0], self.sip)
                self.assertEqual(self.service.blob(self.sip['sha256']), (ROOT/case['file']).read_bytes())
                prepared.validate_retained(self.current(), self.service.blob, self.service.schemas)
                self.assertFalse(record['production_acceptance'])
                with self.assertRaises(Conflict):
                    require_resolved(self.current())

    def test_concurrent_exact_retries_commit_one_record_and_event_with_actor_fencing(self):
        self.edit()
        request = self.request()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.freeze(request), range(2)))
        self.assertEqual(results[0]['prepared_input'], results[1]['prepared_input'])
        self.assertEqual(sorted(r['replayed'] for r in results), [False, True])
        body = self.current()
        self.assertEqual(len(body['prepared_inputs']), 1)
        self.assertEqual(len([e for e in body['events'] if e['type'] == 'prepared-input-frozen']), 1)
        with self.assertRaises(Conflict):
            self.freeze(request, actor='different')
        with self.assertRaises(Conflict):
            self.freeze({**request, 'reason': 'Changed intent'})
        self.assertEqual(self.current(), body)

    def test_missing_head_stale_revision_source_head_and_duplicate_refused(self):
        with self.assertRaises(Conflict):
            self.freeze()
        self.edit()
        request = self.request()
        body = self.current()
        for override in ({'revision': 0}, {'source_sha256': '0'*64}, {'operation_id': 'missing'}, {'reason': ' '}):
            with self.subTest(override=override), self.assertRaises((Conflict, PackageError)):
                self.freeze({**request, **override})
            self.assertEqual(self.current(), body)
        self.freeze()
        with self.assertRaisesRegex(Conflict, 'already frozen'):
            self.freeze()

    def test_later_edit_and_checkpoint_need_successors_even_when_xml_matches_and_old_retries_stay_exact(self):
        self.edit()
        request = self.request()
        first = self.freeze(request)['prepared_input']
        old_zip = prepared.artifact(self.service, self.transfer['id'], 'archive-a', self.sip['id'], first['id'], 'zip')
        self.operation('checkpoint', name='Explicit reviewed checkpoint')
        second = self.freeze()['prepared_input']
        self.assertEqual(first['sha256'], second['sha256'])
        self.assertNotEqual(first['operation_id'], second['operation_id'])
        self.assertNotEqual(first['preparation_sha256'], second['preparation_sha256'])
        self.assertEqual(second['predecessor_id'], first['id'])
        self.edit(text='Successor changed organisation')
        self.assertTrue(self.freeze(request)['replayed'])
        self.assertEqual(prepared.artifact(self.service, self.transfer['id'], 'archive-a', self.sip['id'], first['id'], 'zip'), old_zip)
        self.assertNotEqual(self.document()['state']['head'], second['operation_id'])
        third = self.freeze()['prepared_input']
        self.assertNotEqual(third['sha256'], first['sha256'])
        self.assertEqual(third['predecessor_id'], second['id'])
        prepared.validate_retained(self.current(), self.service.blob, self.service.schemas)

    def test_undo_to_source_and_inherited_legacy_draft_retain_history_and_closed_review(self):
        self.save()
        self.operation('checkpoint', name='Inherited saved draft')
        first = self.freeze()['prepared_input']
        nested = members(members(self.service.blob(first['evidence_sha256']))['preparation.zip'])
        self.assertTrue(any(p.startswith('legacy/') for p in nested))
        self.edit()
        self.operation('undo')
        second = self.freeze()['prepared_input']
        self.assertEqual(first['sha256'], second['sha256'])
        self.assertNotEqual(first['preparation_sha256'], second['preparation_sha256'])
        with self.assertRaises(Conflict):
            require_resolved(self.current())

    def test_freezing_preserves_source_and_sealed_outputs(self):
        self.edit()
        body = self.current()
        body['outputs'] = [{'id':'historical', 'sha256':'a'*64, 'receipt':'immutable historical receipt'}]
        body['approval'] = {'approver':'historical-reviewer'}
        self.service.store.save(body, body['revision'])
        self.freeze()
        self.assertEqual(self.current()['sips'][0], self.sip)
        self.assertEqual(self.current()['outputs'], body['outputs'])
        self.assertNotIn('approval', self.current())

    def test_missing_or_forged_lineage_and_evidence_refused(self):
        self.edit()
        record = self.freeze()['prepared_input']
        body = self.current()
        for field in ('source_sha256', 'metadata_sha256', 'preparation_sha256', 'schema_bundle_sha256',
                      'operation_id', 'predecessor_id', 'tenant', 'transfer_id', 'actor', 'reason', 'profile'):
            bad = copy.deepcopy(body)
            bad['prepared_inputs'][0][field] = 'forged'
            with self.subTest(field=field), self.assertRaises((PackageError, KeyError)):
                prepared.validate_retained(bad, self.service.blob, self.service.schemas)
        for entries in ([record, record], [{**record, 'sip_id':'missing'}]):
            with self.assertRaises(PackageError):
                prepared.validate_retained({**body, 'prepared_inputs':entries}, self.service.blob)
        bad = copy.deepcopy(body)
        bad['prepared_inputs'][0]['evidence_sha256'] = put(self.service.data, b'not an evidence ZIP')
        with self.assertRaises(PackageError):
            prepared.validate_retained(bad, self.service.blob)

    def test_schema_valid_but_forged_freeze_event_after_manifest_resealing_is_refused(self):
        self.edit()
        record = self.freeze()['prepared_input']
        evidence = members(self.service.blob(record['evidence_sha256']))
        evidence['metadata/premis.xml'] = evidence['metadata/premis.xml'].replace(
            b'<premis:eventOutcome>success</premis:eventOutcome>', b'<premis:eventOutcome>failure</premis:eventOutcome>')
        validate_metadata(evidence, self.service.schemas/'preservation')
        evidence['manifest-sha256.txt'] = ''.join(sha(data)+'  '+path+'\n' for path,data in sorted(evidence.items())
                                                if path != 'manifest-sha256.txt').encode()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path,data in evidence.items():
                archive.writestr(path,data)
        body = self.current()
        body['prepared_inputs'][0]['evidence_sha256'] = put(self.service.data, stream.getvalue())
        with self.assertRaisesRegex(PackageError, 'METS/PREMIS'):
            prepared.validate_retained(body, self.service.blob, self.service.schemas)

    def test_protected_payload_corruption_during_freeze_is_atomic(self):
        self.edit()
        body = self.current()
        path = next(iter(self.sip['files'].values()))['path']
        original = zipfile.ZipFile.writestr
        def altered(archive, info, data, *args, **kwargs):
            if isinstance(info, zipfile.ZipInfo) and info.orig_filename == path:
                data += b'corrupt'
            return original(archive, info, data, *args, **kwargs)
        with patch.object(zipfile.ZipFile, 'writestr', altered), self.assertRaises(PackageError):
            self.freeze()
        self.assertEqual(self.current(), body)

    def test_failed_evidence_does_not_publish_freeze_and_recovery_retains_historical_bytes(self):
        self.edit()
        body = self.current()
        with patch('app.prepared_input_evidence.bundle', side_effect=PackageError('schema failure')):
            with self.assertRaises(PackageError):
                self.freeze()
        self.assertEqual(self.current(), body)
        first = self.freeze()['prepared_input']
        self.edit(text='Later working metadata')
        self.freeze()
        self.assertTrue({first[k] for k in ('sha256','metadata_sha256','preparation_sha256','evidence_sha256')}
                        <= set(blob_bindings(self.current())))
        snapshot = self.folder/'frozen-backup'
        report = backup(self.service, snapshot, actor='backup', quiesced=True)
        destination = self.folder/'restored'
        store = self.new_store()
        restore(snapshot, destination, ROOT/'schemas', expected_digest=report['snapshot_sha256'], actor='restore', quiesced=True, store=store)
        restored = Service(destination, ROOT/'schemas', store=store)
        for kind in ('zip','evidence'):
            self.assertEqual(prepared.artifact(restored,self.transfer['id'],'archive-a',self.sip['id'],first['id'],kind),
                             prepared.artifact(self.service,self.transfer['id'],'archive-a',self.sip['id'],first['id'],kind))
        (self.service.data/'blobs'/first['evidence_sha256']).unlink()
        with self.assertRaises(PackageError):
            backup(self.service, self.folder/'missing-evidence', actor='backup', quiesced=True)


class PreparedApiTests(PreparedFixture):
    def test_operator_reader_tenant_retry_and_historical_download_bindings(self):
        original = main.service
        main.service = self.service
        self.addCleanup(setattr, main, 'service', original)
        self.addCleanup(main.app.dependency_overrides.clear)
        def user(tenant='archive-a', roles=('archive-reader','archive-operator')):
            main.app.dependency_overrides[principal] = lambda: Principal('editor',tenant,frozenset(roles),'archive-workbench')
        self.edit()
        client = TestClient(main.app)
        base = '/api/transfers/'+self.transfer['id']+'/sips/'+self.sip['id']
        request = self.request()
        user(roles=('archive-reader',))
        self.assertEqual(client.post(base+'/prepared-inputs',json=request).status_code,403)
        user(tenant='other')
        self.assertEqual(client.post(base+'/prepared-inputs',json=request).status_code,404)
        user()
        response = client.post(base+'/prepared-inputs',json=request)
        self.assertEqual(response.status_code,200,response.text)
        record = response.json()['prepared_input']
        self.assertTrue(client.post(base+'/prepared-inputs',json=request).json()['replayed'])
        self.assertEqual(client.get(base+'/preparation').json()['prepared_inputs'],[record])
        user(roles=('archive-reader',))
        for kind, field in (('zip','sha256'),('evidence','evidence_sha256')):
            path = base+'/prepared-inputs/'+record['id']+'/'+kind
            response = client.get(path)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(sha(response.content),record[field])
            self.assertIn('attachment;',response.headers['content-disposition'])
            self.assertEqual(response.headers['cache-control'],'no-store')
            user(tenant='other',roles=('archive-reader',))
            self.assertEqual(client.get(path).status_code,404)
            user(roles=('archive-reader',))
        self.assertEqual(client.get(base+'/prepared-inputs/'+record['id']+'/unknown').status_code,404)
        self.assertEqual(client.post('/api/transfers/'+self.transfer['id']+'/approve',json={'revision':self.current()['revision']}).status_code,403)
        user(roles=('archive-reader','archive-approver'))
        self.assertEqual(client.post('/api/transfers/'+self.transfer['id']+'/approve',json={'revision':self.current()['revision']}).status_code,409)
