import json
import unittest
import tempfile
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import httpx
from app.adapters.kost import KostClient
from app import kost_validation as kost
from app.service import Service, put
from app.packages import PackageError
from app.store import Conflict

JOB_ID = '12345678-1234-4234-8234-123456789abc'


class KostTransportTests(unittest.TestCase):
    def test_submit_contract_and_authentication(self):
        def respond(request):
            self.assertEqual(request.headers['X-API-Key'], 'test-secret')
            self.assertEqual(request.url.path, '/validations')
            self.assertIn(b'name="sip"; filename="received.zip"', request.content)
            self.assertIn(b'application/zip', request.content)
            self.assertIn(b'name="language"', request.content)
            return httpx.Response(202, json={'id': JOB_ID, 'status': 'queued'})
        client = KostClient('http://kost.test', 'test-secret', transport=httpx.MockTransport(respond))
        self.addCleanup(client.close)
        job, raw = client.submit(b'zip-bytes', 'received.zip')
        self.assertEqual(job['status'], 'queued')
        self.assertEqual(json.loads(raw)['id'], JOB_ID)

    def test_report_uses_known_origin_and_job_path(self):
        def respond(request):
            self.assertEqual(str(request.url), 'http://kost.test/validations/'+JOB_ID+'/report')
            return httpx.Response(200, content=b'<report/>')
        client = KostClient('http://kost.test', transport=httpx.MockTransport(respond))
        self.addCleanup(client.close)
        self.assertEqual(client.report(JOB_ID), b'<report/>')

    def test_invalid_is_distinct_from_service_error(self):
        for status in ('invalid', 'error'):
            client = KostClient('http://kost.test', transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={'id': JOB_ID, 'status': status,
                    'errors': [{'module': 'SIP', 'message': 'Not conformant'}]})))
            self.addCleanup(client.close)
            self.assertEqual(client.status(JOB_ID)[0]['status'], status)

    def test_rejects_unknown_status_redirect_and_oversized_response(self):
        for response in (httpx.Response(200, json={'id': JOB_ID, 'status': 'accepted'}),
                         httpx.Response(302, headers={'location': 'http://other.test'}),
                         httpx.Response(200, content=b'x'*(1024*1024+1))):
            client = KostClient('http://kost.test', transport=httpx.MockTransport(lambda request: response))
            self.addCleanup(client.close)
            with self.assertRaises((ValueError, httpx.HTTPError)):
                client.status(JOB_ID)


class KostJournalTests(unittest.TestCase):
    def service_store(self):
        return None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = Service(Path(self.temp.name), Path('schemas'), store=self.service_store())
        self.service.kost_config = kost.Config(enabled=True, url='http://kost.test')
        self.body = self.service.create('KOST test', 'archive-a', 'operator')
        self.digest = put(self.service.data, b'immutable zip')
        self.body['sips'] = [{'id': 'sip-a', 'sha256': self.digest, 'profile': '4.0', 'metadata_path': 'Sample-SIP/header/metadata.xml',
            'original_filename': 'SIP_20261114_ABC_Original-name.ZIP'}]
        self.body['prepared_inputs'] = [{'id': 'prepared-a', 'sip_id': 'sip-a', 'sha256': put(self.service.data, b'prepared zip')}]
        self.service.store.save(self.body, 0)

    def start(self, **overrides):
        request = {'request_id': str(uuid.uuid4()), 'input_sha256': self.digest, **overrides}
        return kost.start(self.service, self.body['id'], 'archive-a', 'operator', 'sip-a', request)

    def client(self, respond):
        client = KostClient('http://kost.test', transport=httpx.MockTransport(respond))
        self.addCleanup(client.close)
        return client

    def due(self):
        with self.service.store.transaction() as tx:
            kost.execute(tx, 'UPDATE workflow_kost_runs SET next_at=0,lease_until=0')

    def test_request_replay_active_deduplication_and_no_revision_churn(self):
        request_id = str(uuid.uuid4())
        first = self.start(request_id=request_id)
        self.assertEqual(self.start(request_id=request_id)['id'], first['id'])
        self.assertEqual(self.start()['id'], first['id'])
        client = self.client(lambda request: httpx.Response(200, json={'id': JOB_ID, 'status': 'valid'}))
        kost.step(self.service, client)
        self.assertEqual(kost.inventory(self.service, self.body['id'], 'archive-a', 'sip-a')['targets'][0]['latest']['result'], 'valid')
        self.assertEqual(self.service.get(self.body['id'], 'archive-a')['revision'], 1)
        self.assertNotEqual(self.start()['id'], first['id'])

    def test_resume_poll_and_retain_report(self):
        calls = []
        def respond(request):
            calls.append(request.method)
            if request.method == 'POST':
                self.assertIn(b'filename="SIP_20261114_ABC_Original-name.ZIP"', request.content)
            if request.url.path.endswith('/report'):
                return httpx.Response(200, content=b'<report/>')
            return httpx.Response(200, json={'id': JOB_ID, 'status': 'queued' if request.method == 'POST' else 'invalid',
                'errors': [{'module': 'SIP', 'message': 'Bad SIP'}], 'report_url': 'http://untrusted.test/report'})
        self.start()
        client = self.client(respond)
        kost.step(self.service, client)
        self.due()
        kost.step(self.service, client)
        self.due()
        kost.step(self.service, client)
        latest = kost.inventory(self.service, self.body['id'], 'archive-a', 'sip-a')['targets'][0]['latest']
        self.assertEqual(calls, ['POST', 'GET', 'GET'])
        self.assertEqual(latest['result'], 'invalid')
        self.assertEqual(self.service.blob(latest['report_sha256']), b'<report/>')

    def test_uncertain_submission_requires_explicit_acknowledgement(self):
        def respond(request):
            raise httpx.ReadTimeout('Lost acknowledgement', request=request)
        first = self.start()
        kost.step(self.service, self.client(respond))
        self.due()
        self.assertFalse(kost.step(self.service, self.client(respond)))
        self.assertEqual(self.start()['id'], first['id'])
        second = self.start(acknowledge_uncertain=True)
        self.assertNotEqual(second['id'], first['id'])
        self.assertEqual(len(kost.history(self.service, self.body['id'], 'archive-a', 'sip-a')['runs']), 2)

    def test_crashed_submit_claim_becomes_uncertain_and_leases_are_fenced(self):
        self.start()
        body, token, timestamp = kost.claim(self.service)
        self.assertIsNone(kost.claim(self.service))
        self.due()
        self.assertIsNone(kost.claim(self.service))
        with self.assertRaises(Conflict):
            kost.complete_step(self.service, body, token)
        self.assertEqual(kost.inventory(self.service, self.body['id'], 'archive-a', 'sip-a')['targets'][0]['latest']['state'], 'SUBMISSION_UNKNOWN')

    def test_concurrent_requests_and_worker_claims_have_one_winner(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            requested = list(pool.map(lambda _: self.start(), range(2)))
        self.assertEqual(requested[0]['id'], requested[1]['id'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: kost.claim(self.service), range(2)))
        self.assertEqual(sum(item is not None for item in claims), 1)

    def test_target_separation_and_tenant_authorization(self):
        self.start()
        prepared_digest = self.body['prepared_inputs'][0]['sha256']
        self.start(prepared_id='prepared-a', input_sha256=prepared_digest)
        targets = kost.inventory(self.service, self.body['id'], 'archive-a', 'sip-a')['targets']
        self.assertEqual([item['input_sha256'] for item in targets], [self.digest, prepared_digest])
        self.assertEqual([item['filename'] for item in targets], ['SIP_20261114_ABC_Original-name.ZIP']*2)
        with self.assertRaises(KeyError):
            kost.inventory(self.service, self.body['id'], 'other-archive', 'sip-a')
        with self.assertRaises(Conflict):
            self.start(input_sha256='0'*64)

    def test_legacy_upload_without_filename_never_submits_an_inferred_name(self):
        self.body['sips'][0].pop('original_filename')
        self.service.store.save(self.body, self.body['revision'])
        queued = self.start()
        with self.service.store.transaction() as tx:
            run = kost.scoped(tx, self.body['id'], 'archive-a', 'sip-a', queued['id'])
            run['filename'] = 'Previously-inferred.zip'
            kost.retain(tx, run, 'legacy-fixture', kost.clock(tx))
        def unexpected(request):
            self.fail('KOST must not be called without the retained upload filename')
        kost.step(self.service, self.client(unexpected))
        latest = kost.inventory(self.service, self.body['id'], 'archive-a', 'sip-a')['targets'][0]['latest']
        self.assertEqual(latest['result'], 'error')
        self.assertIn('Original upload filename was not retained', latest['message'])

    def test_outage_and_missing_report_do_not_turn_invalid_into_error(self):
        self.start()
        def disconnected(request):
            raise httpx.ConnectError('Service unavailable', request=request)
        client = self.client(disconnected)
        kost.step(self.service, client)
        latest = kost.inventory(self.service, self.body['id'], 'archive-a', 'sip-a')['targets'][0]['latest']
        self.assertEqual(latest['state'], 'QUEUED')
        self.due()
        kost.step(self.service, self.client(lambda request: httpx.Response(200, json={'id': JOB_ID, 'status': 'invalid', 'report_url': '/report'})))
        for _ in range(5):
            self.due()
            kost.step(self.service, self.client(lambda request: httpx.Response(404)))
        latest = kost.inventory(self.service, self.body['id'], 'archive-a', 'sip-a')['targets'][0]['latest']
        self.assertEqual(latest['result'], 'invalid')
        self.assertEqual(latest['state'], 'COMPLETE')
        self.assertEqual(latest['report_status'], 'unavailable')

    def test_disabled_configuration_and_transaction_rollback(self):
        self.service.kost_config = kost.Config()
        with self.assertRaises(Conflict):
            self.start()
        self.service.kost_config = kost.Config(enabled=True)
        with self.assertRaises(RuntimeError):
            with self.service.store.transaction() as tx:
                kost.auto_enqueue(tx, self.body, 'sip-a', self.service.kost_config)
                raise RuntimeError('Rollback')
        self.assertIsNone(kost.inventory(self.service, self.body['id'], 'archive-a', 'sip-a')['targets'][0]['latest'])

    def test_api_authorization_scoping_and_report_headers(self):
        from fastapi.testclient import TestClient
        from app import main
        from app.auth import Principal, principal
        original = main.service
        main.service = self.service
        self.addCleanup(setattr, main, 'service', original)
        self.addCleanup(main.app.dependency_overrides.clear)
        main.app.dependency_overrides[principal] = lambda: Principal('reader', 'archive-a', {'archive-reader'})
        client = TestClient(main.app)
        self.addCleanup(client.close)
        base = '/api/transfers/'+self.body['id']+'/sips/sip-a/kost-validations'
        self.assertEqual(client.get(base).status_code, 200)
        request = dict(request_id=str(uuid.uuid4()), input_sha256=self.digest)
        self.assertEqual(client.post(base, json=request).status_code, 403)
        main.app.dependency_overrides[principal] = lambda: Principal('operator', 'archive-a', {'archive-reader', 'archive-operator'})
        run = client.post(base, json=request)
        self.assertEqual(run.status_code, 202)
        run_id = run.json()['id']
        self.assertEqual(client.get(base+'/runs/'+run_id+'/report').status_code, 409)
        kost.step(self.service, self.client(lambda request: httpx.Response(200, json={'id': JOB_ID, 'status': 'valid', 'report_url': '/report'})))
        self.due()
        kost.step(self.service, self.client(lambda request: httpx.Response(200, content=b'<report/>')))
        report = client.get(base+'/runs/'+run_id+'/report')
        self.assertEqual(report.content, b'<report/>')
        self.assertIn('attachment', report.headers['content-disposition'])
        self.assertEqual(report.headers['x-content-type-options'], 'nosniff')
        main.app.dependency_overrides[principal] = lambda: Principal('reader', 'archive-b', {'archive-reader'})
        self.assertEqual(client.get(base+'/runs/'+run_id).status_code, 404)


from test_prepared_inputs import PreparedFixture


class KostWorkflowTests(PreparedFixture):
    def create(self, case):
        original = (Path(__file__).resolve().parents[2]/case['file']).read_bytes()
        transfer = self.service.create('Named SIP validation', 'archive-a', 'operator')
        transfer = self.service.upload(transfer['id'], 'archive-a', 'operator', 0, case['profile'], original,
            filename=Path(case['file']).name)
        return transfer, transfer['sips'][0], original

    def setUp(self):
        with patch.dict('os.environ', {'KOSTVAL_ENABLED': 'false'}):
            super().setUp()

    def test_multipart_upload_retains_exact_name_for_first_run_and_rerun(self):
        from fastapi.testclient import TestClient
        from app import main
        from app.auth import Principal, principal
        original_service = main.service
        main.service = self.service
        self.addCleanup(setattr, main, 'service', original_service)
        self.addCleanup(main.app.dependency_overrides.clear)
        main.app.dependency_overrides[principal] = lambda: Principal('operator', 'archive-a', {'archive-reader', 'archive-operator'})
        self.service.kost_config = kost.Config(enabled=True, url='http://kost.test')
        transfer = self.service.create('Original filename', 'archive-a', 'operator')
        filename = 'SIP_20261114_ABC_User-upload-name.ZiP'
        data = self.service.blob(self.sip['sha256'])
        with TestClient(main.app) as client:
            response = client.post('/api/transfers/'+transfer['id']+'/sips',
                data={'profile': self.sip['profile'], 'revision': '0'},
                files={'file': (filename, data, 'application/zip')})
        self.assertEqual(response.status_code, 200)
        transfer = response.json()
        sip = transfer['sips'][0]
        self.assertEqual(sip['original_filename'], filename)
        self.assertEqual(transfer['events'][-1]['detail']['original_filename'], filename)
        self.assertNotEqual(sip['metadata_path'].split('/')[0]+'.zip', filename)
        submitted = []
        def respond(request):
            self.assertIn(('filename="'+filename+'"').encode(), request.content)
            submitted.append(request)
            return httpx.Response(200, json={'id': JOB_ID, 'status': 'valid'})
        client = KostClient('http://kost.test', transport=httpx.MockTransport(respond))
        self.addCleanup(client.close)
        kost.step(self.service, client)
        kost.start(self.service, transfer['id'], 'archive-a', 'operator', sip['id'],
            dict(request_id=str(uuid.uuid4()), input_sha256=sip['sha256']))
        kost.step(self.service, client)
        self.assertEqual(len(submitted), 2)

    def test_upload_rejects_path_or_control_characters_instead_of_renaming(self):
        data = self.service.blob(self.sip['sha256'])
        for filename in ('../SIP.zip', r'C:\incoming\SIP.zip', 'SIP\r\n.zip', ''):
            with self.subTest(filename=filename), self.assertRaisesRegex(PackageError, 'plain filename'):
                self.service.upload(self.transfer['id'], 'archive-a', 'operator', self.transfer['revision'], self.sip['profile'], data, filename=filename)

    def test_recovery_retains_history_reports_and_fences_interrupted_submission(self):
        from app.recovery import backup, restore
        self.service.kost_config = kost.Config(enabled=True, url='http://kost.test')
        transfer_id = self.transfer['id']
        sip_id = self.sip['id']
        request = dict(request_id=str(uuid.uuid4()), input_sha256=self.sip['sha256'])
        first = kost.start(self.service, transfer_id, 'archive-a', 'operator', sip_id, request)
        client = KostClient('http://kost.test', transport=httpx.MockTransport(lambda request:
            httpx.Response(200, content=b'<report/>') if request.url.path.endswith('/report') else
            httpx.Response(200, json={'id': JOB_ID, 'status': 'valid', 'report_url': '/report'})))
        self.addCleanup(client.close)
        kost.step(self.service, client)
        with self.service.store.transaction() as tx:
            kost.execute(tx, 'UPDATE workflow_kost_runs SET next_at=0')
        kost.step(self.service, client)
        kost.start(self.service, transfer_id, 'archive-a', 'operator', sip_id, {**request, 'request_id': str(uuid.uuid4())})
        kost.claim(self.service)
        destination = self.service.data.parent/'kost-snapshot'
        restored_data = self.service.data.parent/'kost-restored'
        report = backup(self.service, destination, actor='administrator', quiesced=True)
        target_store = self.service_store() if self.service.store.backend == 'postgresql' else None
        restore(destination, restored_data, self.service.schemas, expected_digest=report['snapshot_sha256'], actor='administrator', quiesced=True, store=target_store)
        restored = Service(restored_data, self.service.schemas, store=target_store)
        history = kost.history(restored, transfer_id, 'archive-a', sip_id)['runs']
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]['state'], 'SUBMISSION_UNKNOWN')
        completed = next(item for item in history if item['id'] == first['id'])
        self.assertEqual(restored.blob(completed['report_sha256']), b'<report/>')

    def test_upload_and_freeze_automatically_queue_independent_jobs(self):
        self.service.kost_config = kost.Config(enabled=True)
        case = json.loads((Path(__file__).resolve().parents[2]/'fixtures/corpus.json').read_text())['cases'][0]
        self.transfer, self.sip, _ = self.create(case)
        before = self.current()
        inventory = kost.inventory(self.service, self.transfer['id'], 'archive-a', self.sip['id'])
        self.assertEqual(inventory['targets'][0]['latest']['trigger'], 'upload')
        self.assertEqual(self.current(), before)
        self.edit()
        request = self.request()
        frozen = self.freeze(request)['prepared_input']
        self.freeze(request)
        inventory = kost.inventory(self.service, self.transfer['id'], 'archive-a', self.sip['id'])
        self.assertEqual(len(inventory['targets']), 2)
        self.assertEqual(inventory['targets'][1]['latest']['trigger'], 'freeze')
        self.assertEqual(inventory['targets'][1]['input_sha256'], frozen['sha256'])
        self.assertEqual(inventory['targets'][1]['filename'], self.sip['original_filename'])
        self.assertEqual(len(kost.history(self.service, self.transfer['id'], 'archive-a', self.sip['id'], frozen['id'])['runs']), 1)