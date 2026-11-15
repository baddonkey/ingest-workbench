"""Derived ZIP conservation, draft binding, concurrency and paired recovery."""
import copy
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from fastapi.testclient import TestClient
import test_metadata_drafts as drafts
from app import main, sip_exports
from app.auth import Principal, principal
from app.json_codec import canonical
from app.metadata_drafts import require_resolved
from app.packages import PackageError, inspect, members, sha
from app.persistence_transfer import blob_bindings
from app.recovery import backup, restore
from app.service import Service, put
from app.store import Conflict

ROOT = drafts.ROOT


class ExportFixture(drafts.DraftFixture):
    def export(self, **options):
        body = self.current()
        values = dict(service=self.service, ident=body['id'], tenant='archive-a', actor='editor',
                      revision=body['revision'], sip_id=self.sip['id'], source_sha256=self.sip['sha256'],
                      draft_id=body.get('active_metadata_drafts', {}).get(self.sip['id']), reason='Export reviewed metadata proposal')
        values.update(options)
        return sip_exports.create(**values)


class ExportTests(ExportFixture):
    def test_eight_corpus_exports_preserve_all_other_members_order_directories_and_source(self):
        for case in json.loads((ROOT / 'fixtures/corpus.json').read_text(encoding='utf-8'))['cases']:
            with self.subTest(case=case['file']):
                self.transfer, self.sip, self.field = self.create(case)
                source = self.service.blob(self.sip['sha256'])
                draft = self.save()['draft']
                record = self.export()['export']
                derived = self.service.blob(record['sha256'])
                report = inspect(derived, case['profile'], self.service.schemas)
                self.assertEqual(report['files'], self.sip['files'])
                self.assertEqual(report['references'], self.sip['references'])
                self.assertEqual(report['schema_bundle_sha256'], self.sip['schema_bundle_sha256'])
                self.assertNotEqual(record['sha256'], self.sip['sha256'])
                self.assertEqual(members(derived)[self.sip['metadata_path']], self.service.blob(draft['metadata_sha256']))
                with zipfile.ZipFile(io.BytesIO(source)) as before, zipfile.ZipFile(io.BytesIO(derived)) as after:
                    self.assertEqual(before.namelist(), after.namelist())
                    for entry in before.infolist():
                        if entry.filename != self.sip['metadata_path']:
                            self.assertEqual(before.read(entry), after.read(entry.filename))
                self.assertEqual(self.service.blob(self.sip['sha256']), (ROOT / case['file']).read_bytes())
                self.assertEqual(self.current()['sips'][0], self.sip)
                self.assertEqual(self.current()['active_metadata_drafts'][self.sip['id']], draft['id'])
                sip_exports.validate_retained(self.current(), self.service.blob)
                self.assertFalse(record['production_acceptance'])
                with self.assertRaises(Conflict):
                    require_resolved(self.current())

    def test_exact_lost_ack_replay_and_concurrent_retries_add_one_event(self):
        self.save()
        revision = self.current()['revision']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.export(revision=revision), range(2)))
        self.assertEqual(results[0]['export'], results[1]['export'])
        self.assertEqual(sorted(r['replayed'] for r in results), [False, True])
        body = self.current()
        self.assertEqual(len(body['sip_exports']), 1)
        self.assertEqual(len([e for e in body['events'] if e['type'] == 'derived-sip-exported']), 1)
        for options in [dict(reason='Different intent', revision=revision), dict(actor='other', revision=revision), {}]:
            with self.assertRaises(Conflict):
                self.export(**options)
        self.assertEqual(self.current(), body)

    def test_stale_source_draft_revision_and_missing_draft_fail_without_mutation(self):
        self.save()
        body = self.current()
        for options in [dict(revision=0), dict(source_sha256='0'*64), dict(draft_id='old'), dict(reason=' ')]:
            with self.subTest(options=options), self.assertRaises((Conflict, PackageError)):
                self.export(**options)
            self.assertEqual(self.current(), body)
        self.discard()
        with self.assertRaises(KeyError):
            self.export()

    def test_protected_xml_substitution_rejected_even_with_rebound_draft_descriptor(self):
        record = self.save()['draft']
        body = self.current()
        xml = self.service.blob(record['metadata_sha256']).replace(b'schemaVersion="5.1"', b'schemaVersion="5.0"')
        record['metadata_sha256'] = put(self.service.data, xml)
        descriptor = json.loads(self.service.blob(record['changes_sha256']))
        descriptor['metadata_sha256'] = record['metadata_sha256']
        record['changes_sha256'] = put(self.service.data, canonical(descriptor))
        body['metadata_drafts'][0] = record
        self.service.store.save(body, body['revision'])
        with self.assertRaisesRegex(PackageError, 'authorized patches'):
            self.export()
        self.assertFalse(self.current().get('sip_exports'))

    def test_output_inspection_rejects_replaced_protected_payload(self):
        self.save()
        original = zipfile.ZipFile.writestr
        payload = next(iter(self.sip['files'].values()))['path']
        def altered(archive, info, data, *args, **kwargs):
            return original(archive, info, data + b'corrupt' if info.filename == payload else data, *args, **kwargs)
        with patch.object(zipfile.ZipFile, 'writestr', altered), self.assertRaises(PackageError):
            self.export()
        self.assertFalse(self.current().get('sip_exports'))

    def test_schema_pin_drift_refuses_export(self):
        self.save()
        body = self.current()
        body['sips'][0]['schema_bundle_sha256'] = '0'*64
        self.service.store.save(body, body['revision'])
        with self.assertRaises(PackageError):
            self.export()

    def test_paired_recovery_retains_historical_exports_and_refuses_missing_or_rebound_evidence(self):
        self.save()
        record = self.export()['export']
        self.discard()
        self.assertTrue({record['sha256'],record['evidence_sha256']} <= set(blob_bindings(self.current())))
        snapshot = self.folder / 'snapshot'
        report = backup(self.service, snapshot, actor='backup', quiesced=True)
        store = self.new_store()
        destination = self.folder / 'restored'
        restore(snapshot, destination, ROOT / 'schemas', expected_digest=report['snapshot_sha256'], actor='restore', quiesced=True, store=store)
        restored = Service(destination, ROOT / 'schemas', store=store)
        self.assertEqual(sip_exports.artifact(restored,self.transfer['id'],'archive-a',self.sip['id'],record['id'],'zip'),self.service.blob(record['sha256']))
        bad = copy.deepcopy(self.current())
        bad['sip_exports'][0]['draft_id'] = 'missing'
        with self.assertRaises((KeyError, PackageError)):
            sip_exports.validate_retained(bad, self.service.blob)
        (self.service.data / 'blobs' / record['evidence_sha256']).unlink()
        with self.assertRaises(PackageError):
            backup(self.service, self.folder / 'incomplete', actor='backup', quiesced=True)


class ExportApiTests(ExportFixture):
    def test_roles_tenants_download_hashes_replay_and_closed_acceptance(self):
        original = main.service
        main.service = self.service
        self.addCleanup(setattr, main, 'service', original)
        self.addCleanup(main.app.dependency_overrides.clear)
        def user(tenant='archive-a', roles=('archive-reader','archive-operator')):
            main.app.dependency_overrides[principal] = lambda: Principal('editor',tenant,frozenset(roles),'archive-workbench')
        self.save()
        client = TestClient(main.app)
        base = '/api/transfers/'+self.transfer['id']+'/sips/'+self.sip['id']
        payload = dict(revision=self.current()['revision'],source_sha256=self.sip['sha256'],draft_id=self.current()['active_metadata_drafts'][self.sip['id']],reason='HTTP export')
        user(roles=('archive-reader',))
        self.assertEqual(client.post(base+'/exports',json=payload).status_code,403)
        user(tenant='other')
        self.assertEqual(client.post(base+'/exports',json=payload).status_code,404)
        user()
        result = client.post(base+'/exports',json=payload)
        self.assertEqual(result.status_code,200,result.text)
        record = result.json()['export']
        self.assertTrue(client.post(base+'/exports',json=payload).json()['replayed'])
        user(roles=('archive-reader',))
        for kind, field in [('zip','sha256'),('evidence','evidence_sha256')]:
            path = base+'/exports/'+record['id']+'/'+kind
            response = client.get(path)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(sha(response.content),record[field])
            self.assertIn('attachment;',response.headers['content-disposition'])
            user(tenant='other',roles=('archive-reader',))
            self.assertEqual(client.get(path).status_code,404)
            user(roles=('archive-reader',))
        self.assertEqual(client.get(base+'/exports/'+record['id']+'/unknown').status_code,404)
        self.assertEqual(client.get(base+'/workspace').json()['exports'],[record])
