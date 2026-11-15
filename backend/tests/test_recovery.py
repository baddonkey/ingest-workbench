"""Offline recovery of preparation data."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.packages import PackageError, sha
from app.recovery import backup, restore
from app.service import Service, canonical, event
from app.store import Conflict

ROOT = Path(__file__).resolve().parents[2]
TENANT = 'archive-demo'


class RecoveryFixture:
    def __init__(self, store):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.service = Service(self.folder / 'archive', ROOT / 'schemas', store=store)
        body = self.service.create('Recovery qualification', TENANT, 'operator')
        body = self.service.upload(body['id'], TENANT, 'operator', 0, '1.3.0',
                                   (ROOT / 'fixtures/reference-1.3.zip').read_bytes())
        sip = body['sips'][0]
        targets = [{'label': 'Candidate', 'units': [
            {'sip_id': sip['id'], 'unit_id': unit['id']} for unit in sip['units']]}]
        body = self.service.plan(body['id'], TENANT, 'operator', body['revision'], targets, 'Recovery fixture')
        body = self.service.approve(body['id'], TENANT, 'reviewer', body['revision'])
        self.body = self.service.build(body['id'], TENANT, 'operator', body['revision'])
        self.output = self.body['outputs'][0]

    def close(self):
        self.temp.cleanup()

    def current(self):
        return self.service.get(self.body['id'], TENANT)

    def save_body(self, body):
        revision = body['revision']
        event(body, 'fixture-update', 'test', {})
        self.service.store.save(body, revision)


class RecoveryTests(unittest.TestCase):
    def new_store(self):
        return None

    def setUp(self):
        self.source_store = self.new_store()
        self.target_store = self.new_store()
        self.fixture = RecoveryFixture(self.source_store)
        self.folder = self.fixture.folder
        self.source = self.fixture.service
        self.snapshot = self.folder / 'snapshot'
        self.data = self.folder / 'restored'

    def tearDown(self):
        self.fixture.close()

    def save(self):
        self.original = self.fixture.current()
        self.report = backup(self.source, self.snapshot, actor='recovery-admin', quiesced=True)
        self.assertEqual(self.fixture.current(), self.original)

    def recover(self):
        with patch('httpx.Client.send', side_effect=AssertionError('Recovery must not send external requests')):
            report = restore(self.snapshot, self.data, ROOT / 'schemas',
                             expected_digest=self.report['snapshot_sha256'], actor='restore-admin',
                             quiesced=True, store=self.target_store)
        self.assertFalse(report['production_acceptance'])
        self.assertFalse(report['external_effects_performed'])
        self.target = Service(self.data, ROOT / 'schemas', store=self.target_store)
        return self.target

    def test_roundtrip_preserves_candidate_and_events(self):
        self.save()
        self.recover()
        restored = self.target.get(self.fixture.body['id'], TENANT)
        self.assertEqual(restored['events'][:-1], self.original['events'])
        self.assertEqual(restored['revision'], self.original['revision'] + 1)
        self.assertEqual(restored['approval'], self.original['approval'])
        self.assertEqual(sha(self.target.blob(self.fixture.output['sha256'])), self.fixture.output['sha256'])
        self.assertEqual(json.loads((self.data / 'recovery-source-state.json').read_bytes()),
                         json.loads((self.snapshot / 'state.json').read_bytes()))

    def test_retired_records_and_candidates_are_refused_without_modifying_source(self):
        for field, value in [('delivery_operations', [{}]), ('crosswalk_exports', [{}]),
                             ('outputs', [{'profile': 'urn:ch-archive:aip-profile:1.0.0'}])]:
            with self.subTest(field=field):
                body = self.fixture.current()
                original = body.get(field)
                body[field] = value
                self.fixture.save_body(body)
                before = self.fixture.current()
                with self.assertRaisesRegex(Conflict, 'Retired'):
                    backup(self.source, self.snapshot, actor='admin', quiesced=True)
                self.assertEqual(self.fixture.current(), before)
                self.assertFalse(self.snapshot.exists())
                body = self.fixture.current()
                if original is None:
                    body.pop(field)
                else:
                    body[field] = original
                self.fixture.save_body(body)

    def test_manifest_blob_corruption_and_existing_destination_refused(self):
        self.save()
        with self.assertRaises(PackageError):
            restore(self.snapshot, self.data, ROOT / 'schemas', expected_digest='0' * 64, actor='admin', quiesced=True, store=self.target_store)
        self.assertFalse(self.data.exists())
        blob = next((self.snapshot / 'blobs').iterdir())
        original = blob.read_bytes()
        blob.write_bytes(original + b'corruption')
        with self.assertRaises(PackageError):
            self.recover()
        self.assertFalse(self.data.exists())
        blob.write_bytes(original)
        self.data.mkdir()
        (self.data / 'user-file').write_text('preserve')
        with self.assertRaises(Conflict):
            self.recover()
        self.assertEqual((self.data / 'user-file').read_text(), 'preserve')

    def test_quiescence_and_trusted_schema_match_are_required(self):
        with self.assertRaises(ValueError):
            backup(self.source, self.snapshot, actor='admin')
        self.assertFalse(self.snapshot.exists())
        self.save()
        manifest_before = (self.snapshot / 'manifest.json').read_bytes()
        with self.assertRaises(FileExistsError):
            backup(self.source, self.snapshot, actor='admin', quiesced=True)
        self.assertEqual((self.snapshot / 'manifest.json').read_bytes(), manifest_before)
        with self.assertRaises(ValueError):
            restore(self.snapshot, self.data, ROOT / 'schemas', actor='admin', expected_digest=self.report['snapshot_sha256'], store=self.target_store)
        altered = self.folder / 'altered-schemas'
        shutil.copytree(ROOT / 'schemas', altered)
        schema = altered / 'preservation/mets.xsd'
        schema.write_bytes(schema.read_bytes() + b'\n<!-- changed -->')
        with self.assertRaises(Conflict):
            restore(self.snapshot, self.data, altered, actor='admin', quiesced=True,
                    expected_digest=self.report['snapshot_sha256'], store=self.target_store)
        self.assertFalse(self.data.exists())

    def test_cli_roundtrip_requires_explicit_quiescence_and_retained_digest(self):
        def command(action, store, *extra):
            environment = os.environ.copy()
            environment.pop('RECOVERY_DATABASE_URL', None)
            if store is not None:
                environment['RECOVERY_DATABASE_URL'] = store.engine.url.render_as_string(hide_password=False)
            return subprocess.run([sys.executable, str(ROOT / 'scripts/recover_application.py'), action, str(self.snapshot),
                                   '--data-dir', str(self.source.data if action == 'backup' else self.data),
                                   '--actor', 'cli-recovery-admin', *extra],
                                  env=environment, capture_output=True, text=True, timeout=30)
        refused = command('backup', self.source_store)
        self.assertNotEqual(refused.returncode, 0)
        self.assertFalse(self.snapshot.exists())
        backed = command('backup', self.source_store, '--quiesced')
        self.assertEqual(backed.returncode, 0, backed.stderr)
        digest = json.loads(backed.stdout)['snapshot_sha256']
        refused = command('restore', self.target_store, '--quiesced')
        self.assertNotEqual(refused.returncode, 0)
        self.assertFalse(self.data.exists())
        restored = command('restore', self.target_store, '--quiesced', '--expected-sha256', digest)
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertFalse(json.loads(restored.stdout)['runtime_cutover'])

    def test_existing_retired_snapshot_format_is_refused(self):
        self.save()
        manifest_path = self.snapshot / 'manifest.json'
        manifest = json.loads(manifest_path.read_bytes())
        manifest['format'] = 'urn:ch-archive:application-recovery:1'
        raw = canonical(manifest)
        manifest_path.write_bytes(raw)
        with self.assertRaisesRegex(Conflict, 'Unsupported'):
            restore(self.snapshot, self.data, ROOT / 'schemas', expected_digest=sha(raw),
                    actor='admin', quiesced=True, store=self.target_store)
        self.assertFalse(self.data.exists())

    def test_unrecognized_snapshot_state_is_not_silently_discarded(self):
        self.save()
        state_path = self.snapshot / 'state.json'
        state = json.loads(state_path.read_bytes())
        state['journals'] = [{'records': ['retained']}]
        raw = canonical(state)
        state_path.write_bytes(raw)
        manifest_path = self.snapshot / 'manifest.json'
        manifest = json.loads(manifest_path.read_bytes())
        manifest.update(state_sha256=sha(raw), state_size=len(raw))
        raw = canonical(manifest)
        manifest_path.write_bytes(raw)
        with self.assertRaisesRegex(PackageError, 'state fields'):
            restore(self.snapshot, self.data, ROOT / 'schemas', expected_digest=sha(raw),
                    actor='admin', quiesced=True, store=self.target_store)
        self.assertFalse(self.data.exists())


class SQLiteRetiredJournalTests(unittest.TestCase):
    def test_existing_journal_is_refused_and_left_untouched(self):
        helper = RecoveryTests('runTest')
        helper.setUp()
        self.addCleanup(helper.tearDown)
        journal = helper.source.data / 'delivery-journals' / 'retained'
        journal.mkdir(parents=True)
        (journal / 'evidence').write_bytes(b'retained data')
        with self.assertRaisesRegex(Conflict, 'Retired'):
            helper.save()
        self.assertEqual((journal / 'evidence').read_bytes(), b'retained data')
        self.assertFalse(helper.snapshot.exists())


if __name__ == '__main__':
    unittest.main()
