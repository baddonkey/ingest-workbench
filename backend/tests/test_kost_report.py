import unittest
import io
import tempfile
import uuid
import zipfile
from pathlib import Path
from app.kost_report import project

REPORT = b'''<KOSTValLog><Format><Infos><Summary>Example summary</Summary></Infos>
<Validation><ValType>Validation: TIFF</ValType><ValFile> -&gt; C:\\temp\\ZIP\\SIP_example\\content\\photo.tiff</ValFile>
<Error><Modul>B) Jhove</Modul><Message>Unknown IFD tag</Message></Error><Warning>warning</Warning><Valid>valid</Valid></Validation>
<Validation><ValType>Validation: SIARD</ValType><ValFile>/data/ZIP/SIP_example/content/database.siard</ValFile>
<Error><Modul>E) column Validation</Modul><Message>Datatype mismatch</Message></Error>
<Error><Modul>M) LOB validation</Modul><Message>Unrecognized LOB:</Message><Message>record0.bin</Message></Error>
<Error><Modul>W) Warning</Modul><Message>dataOwner is a placeholder</Message></Error><Warning>warning</Warning><Invalid>invalid</Invalid></Validation>
<Validation><ValType>Recognition: TXT</ValType><ValFile>/data/ZIP/SIP_example/content/readme.txt</ValFile><Accepted>accepted</Accepted></Validation>
<Validation><ValType>Validation: WAVE</ValType><ValFile>/data/ZIP/SIP_example/content/audio.wav</ValFile>
<Error><Modul>A) ffprobe</Modul><Message>Information: container=WAV</Message></Error><Valid>valid</Valid></Validation></Format>
<Sip><Validation><ValType>Validation: SIP</ValType><ValFile>C:\\Downloads\\SIP_example.zip</ValFile>
<Error><Modul>3C) format validation</Modul><Message>An unknown error has occurred. Can`t find resource for bundle java.util.PropertyResourceBundle, key val.message.xml.servicemessage</Message></Error>
<Invalid>invalid</Invalid></Validation></Sip></KOSTValLog>'''
MEMBERS = ['SIP_example/content/'+name for name in ('photo.tiff', 'database.siard', 'readme.txt', 'audio.wav')]


class KostReportTests(unittest.TestCase):
    def test_outcomes_warnings_information_and_engine_errors_are_distinct(self):
        result = project(REPORT, MEMBERS)
        self.assertEqual(result['file_counts'], dict(valid=2, invalid=1, recognized=1, not_assessed=0))
        self.assertEqual(result['finding_counts'], dict(error=2, warning=2, info=1, validator_error=1))
        self.assertTrue(result['validator_error'])
        self.assertEqual(result['entries'][0]['outcome'], 'valid')
        self.assertEqual(result['entries'][0]['findings'][0]['severity'], 'warning')
        self.assertEqual(result['entries'][1]['findings'][1]['message'], 'Unrecognized LOB:\nrecord0.bin')
        self.assertEqual(result['entries'][-1]['scope'], 'package')
        self.assertIsNone(result['entries'][-1]['path'])

    def test_windows_and_unix_paths_match_full_case_sensitive_zip_suffixes(self):
        result = project(REPORT, MEMBERS)
        self.assertEqual([entry['path'] for entry in result['entries'][:4]], MEMBERS)
        result = project(REPORT, ['other/photo.tiff', 'SIP_example/content/PHOTO.tiff'])
        self.assertTrue(all(entry['mapping'] == 'unmapped' for entry in result['entries'][:4]))

    def test_ambiguous_suffixes_and_traversal_are_never_linked(self):
        result = project(REPORT, MEMBERS+['content/photo.tiff'])
        self.assertIsNone(result['entries'][0]['path'])

    def test_document_validation_exception_is_not_a_validator_runtime_failure(self):
        report = b'<KOSTValLog><Format><Validation><Error><Modul>XML</Modul><Message>org.xml.sax.SAXParseException: schema constraint failed</Message></Error><Invalid>invalid</Invalid></Validation></Format></KOSTValLog>'
        result = project(report, [])
        self.assertFalse(result['validator_error'])
        self.assertEqual(result['entries'][0]['findings'][0]['severity'], 'error')
        result = project(REPORT.replace(b'ZIP\\SIP_example', b'ZIP\\..\\SIP_example'), MEMBERS)
        self.assertIsNone(result['entries'][0]['path'])

    def test_external_entities_and_unsupported_or_malformed_xml_are_rejected(self):
        from lxml.etree import XMLSyntaxError
        for data in (b'<!DOCTYPE KOSTValLog [<!ENTITY x SYSTEM "file:///private">]><KOSTValLog>&x;</KOSTValLog>',
                     b'<unrelated/>', b'<KOSTValLog>'):
            with self.subTest(data=data), self.assertRaises((ValueError, XMLSyntaxError)):
                project(data, MEMBERS)


class KostReportApiTests(unittest.TestCase):
    def setUp(self):
        from app import main, kost_validation as kost
        from app.auth import Principal, principal
        from app.service import Service, put
        from fastapi.testclient import TestClient
        self.main, self.kost, self.principal, self.Principal = main, kost, principal, Principal
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.service = Service(Path(temporary.name), Path('schemas'))
        self.service.kost_config = kost.Config(enabled=True)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            for name in MEMBERS:
                archive.writestr(name, b'file')
        self.digest = put(self.service.data, stream.getvalue())
        body = self.service.create('Structured reports', 'archive-a', 'operator')
        body['sips'] = [{'id': 'sip-a', 'sha256': self.digest, 'profile': '1.2.0', 'original_filename': 'SIP_example.zip'}]
        self.service.store.save(body, 0)
        self.ident = body['id']
        self.run = kost.start(self.service, self.ident, 'archive-a', 'operator', 'sip-a',
            dict(request_id=str(uuid.uuid4()), input_sha256=self.digest))
        self.retain(REPORT)
        previous = main.service
        main.service = self.service
        self.addCleanup(setattr, main, 'service', previous)
        self.addCleanup(main.app.dependency_overrides.clear)
        main.app.dependency_overrides[principal] = lambda: Principal('reader', 'archive-a', {'archive-reader'})
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.base = '/api/transfers/'+self.ident+'/sips/sip-a/kost-validations/runs/'+self.run['id']

    def retain(self, report):
        from app.service import put
        with self.service.store.transaction() as tx:
            run = self.kost.scoped(tx, self.ident, 'archive-a', 'sip-a', self.run['id'])
            run.update(state='COMPLETE', result='invalid', report_status='retained', report_sha256=put(self.service.data, report))
            self.kost.retain(tx, run, 'test-result', self.kost.clock(tx))

    def test_scoped_report_projection_preserves_raw_verdict_and_transfer_revision(self):
        before = self.service.get(self.ident, 'archive-a')
        response = self.client.get(self.base+'/findings')
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result['available'])
        self.assertTrue(result['validator_error'])
        self.assertEqual(result['reported_result'], 'invalid')
        self.assertEqual(result['input_sha256'], self.digest)
        self.assertTrue(result['checked_at'])
        self.assertEqual(self.client.get(result['report_path']).content, REPORT)
        self.assertEqual(result['entries'][0]['path'], MEMBERS[0])
        self.assertEqual(self.service.get(self.ident, 'archive-a'), before)
        self.assertEqual(self.client.get(self.base+'/report').content, REPORT)
        self.main.app.dependency_overrides[self.principal] = lambda: self.Principal('reader', 'other-archive', {'archive-reader'})
        self.assertEqual(self.client.get(self.base+'/findings').status_code, 404)

    def test_malformed_xml_does_not_destroy_raw_report_or_claim_a_pass(self):
        self.retain(b'<broken')
        result = self.client.get(self.base+'/findings').json()
        self.assertFalse(result['available'])
        self.assertEqual(result['reported_result'], 'invalid')
        self.assertIn('malformed report', result['reason'])
        self.assertEqual(self.client.get(self.base+'/report').content, b'<broken')