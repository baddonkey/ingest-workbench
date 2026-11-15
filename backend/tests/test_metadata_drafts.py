"""Draft lineage, optimistic concurrency, unchanged sources and operational holds."""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi.testclient import TestClient
from lxml import etree as ET
from app import main
from app.auth import Principal, principal
from app.metadata_drafts import save, discard, summary, artifact, catalog
from app.package_handler import source, workspace, node_detail
from app.packages import PackageError, NS, sha, xml
from app.persistence_transfer import blob_bindings
from app.recovery import backup, restore
from app.service import Service
from app.store import Conflict

ROOT = Path(__file__).resolve().parents[2]


class DraftFixture(unittest.TestCase):
    def new_store(self):
        return None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.service = Service(self.folder / 'source', ROOT / 'schemas', store=self.new_store())
        self.case = json.loads((ROOT / 'fixtures/corpus.json').read_text())['cases'][-1]
        self.transfer, self.sip, self.field = self.create(self.case)

    def create(self, case):
        data = (ROOT / case['file']).read_bytes()
        body = self.service.create('Draft contract', 'archive-a', 'operator')
        body = self.service.upload(body['id'], 'archive-a', 'operator', 0, case['profile'], data)
        sip = body['sips'][0]
        _, _, data, files, root = source(self.service, body['id'], 'archive-a', sip['id'])
        fields = catalog(sip, files, root, data)
        field = next(f for f in fields.values() if f['label'] == 'titel' and ET.QName(f['element'].getparent()).localname == 'dossier')
        return body, sip, field

    def current(self):
        return self.service.get(self.transfer['id'], 'archive-a')

    def change(self, value='Reviewed title', before=None, field=None):
        field = field or self.field
        return {'node_id': field['node_id'], 'field_id': field['field_id'],
                'before': field['source_value'] if before is None else before, 'value': value}

    def save(self, changes=None, **options):
        body = self.current()
        values = dict(service=self.service, ident=body['id'], tenant='archive-a', actor='editor',
                      revision=body['revision'], sip_id=self.sip['id'], source_sha256=self.sip['sha256'],
                      base_draft_id=body.get('active_metadata_drafts', {}).get(self.sip['id']),
                      reason='Correct descriptive metadata with retained source', changes=changes or [self.change()])
        values.update(options)
        return save(**values)

    def discard(self, **options):
        body = self.current()
        values = dict(service=self.service, ident=body['id'], tenant='archive-a', actor='editor',
                      revision=body['revision'], sip_id=self.sip['id'], source_sha256=self.sip['sha256'],
                      base_draft_id=body.get('active_metadata_drafts', {}).get(self.sip['id']), reason='Return to the untouched source')
        values.update(options)
        return discard(**values)


class DraftTests(DraftFixture):
    def test_source_pane_exposes_saved_draft_values_separately(self):
        record = self.save()['draft']
        doc = workspace(self.service, self.transfer['id'], 'archive-a', self.sip['id'])
        self.assertEqual(doc['draft']['active']['id'], record['id'])
        self.assertTrue(any(f['code'] == 'metadata.draft' for f in doc['findings']))
        detail = node_detail(self.service, self.transfer['id'], 'archive-a', self.sip['id'], self.field['node_id'])
        field = next(f for f in detail['edit_fields'] if f['field_id'] == self.field['field_id'])
        self.assertEqual(field['source_value'], self.field['source_value'])
        self.assertEqual(field['saved_value'], 'Reviewed title')

    def test_all_eight_sources_save_under_their_own_schema_without_other_metadata_changes(self):
        for case in json.loads((ROOT / 'fixtures/corpus.json').read_text())['cases']:
            with self.subTest(case=case['file']):
                self.transfer, self.sip, self.field = self.create(case)
                before = source(self.service, self.transfer['id'], 'archive-a', self.sip['id'])
                result = self.save()
                record = result['draft']
                derived = xml(self.service.blob(record['metadata_sha256']))
                title = derived.find('.//a:dossier/a:titel', NS)
                self.assertEqual(title.text, 'Reviewed title')
                title.text = self.field['source_value']
                self.assertEqual(ET.tostring(derived, method='c14n'), ET.tostring(before[4], method='c14n'))
                self.assertEqual(self.service.blob(self.sip['sha256']), before[2])
                self.assertEqual(self.current()['sips'][0], self.sip)
                self.assertEqual(record['schema_bundle_sha256'], self.sip['schema_bundle_sha256'])
                self.assertFalse(record['production_acceptance'])
                descriptor = json.loads(self.service.blob(record['changes_sha256']))
                self.assertEqual(descriptor['changes'][0]['before'], self.field['source_value'])
                self.assertEqual(descriptor['changes'][0]['after'], 'Reviewed title')
                self.assertEqual(descriptor['actor'], 'editor')

    def test_revision_lineage_discard_and_history_artifacts_survive_reopen(self):
        first = self.save()['draft']
        first_bytes = self.service.blob(first['metadata_sha256'])
        second = self.save([self.change('Second proposal', before='Reviewed title')])['draft']
        self.assertEqual(second['predecessor_id'], first['id'])
        self.assertEqual(second['sequence'], 2)
        self.discard()
        body = self.current()
        self.assertFalse(body['active_metadata_drafts'])
        self.assertEqual(body['metadata_drafts'], [first, second])
        self.assertEqual(self.service.blob(first['metadata_sha256']), first_bytes)
        self.assertEqual(body['events'][-1]['type'], 'metadata-draft-discarded')
        self.assertEqual(artifact(self.service, body['id'], 'archive-a', self.sip['id'], first['id'], 'metadata'), first_bytes)
        self.assertEqual(set(blob_bindings(body)), {self.sip['sha256'], first['metadata_sha256'], first['changes_sha256'], second['metadata_sha256'], second['changes_sha256']})

    def test_stale_transfer_draft_field_and_input_baselines_fail_without_new_events(self):
        before = self.current()
        for options in [dict(revision=0), dict(base_draft_id='old'), dict(source_sha256='0'*64),
                        dict(changes=[self.change(before='wrong saved value')])]:
            with self.subTest(options=options), self.assertRaises(Conflict):
                self.save(**options)
            self.assertEqual(self.current(), before)
        first = self.save()['draft']
        after = self.current()
        with self.assertRaises(Conflict):
            self.save([self.change('overwrite', before='Reviewed title')], base_draft_id=None)
        with self.assertRaises(Conflict):
            self.discard(base_draft_id='old')
        self.assertEqual(self.current(), after)
        self.assertEqual(after['active_metadata_drafts'][self.sip['id']], first['id'])

    def test_protected_duplicate_invalid_xml_and_schema_invalid_edits_are_atomic(self):
        body = self.current()
        _, _, data, files, root = source(self.service, body['id'], 'archive-a', self.sip['id'])
        limited = next(f for f in catalog(self.sip, files, root, data).values() if f['label'] == 'aktenzeichen')
        variants = [[{**self.change(), 'field_id': 'protected-file-checksum'}],
                    [self.change(), self.change('duplicate')], [self.change('bad\x00value')],
                    [self.change('x'*201, field=limited)], [self.change(self.field['source_value'])]]
        for changes in variants:
            with self.subTest(change=changes[0]['field_id']), self.assertRaises(PackageError):
                self.save(changes)
            self.assertEqual(self.current(), body)

    def test_concurrent_saves_have_one_winner_and_one_revision_conflict(self):
        revision = self.current()['revision']
        def attempt(value):
            try:
                return self.save([self.change(value)], revision=revision, base_draft_id=None)['draft']['id']
            except Conflict:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ['First concurrent title', 'Second concurrent title']))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(len(self.current()['metadata_drafts']), 1)

    def test_active_draft_blocks_review_and_build_without_changing_sealed_candidates(self):
        body = self.current()
        targets = [{'label': 'Candidate', 'units': [{'sip_id': self.sip['id'], 'unit_id': u['id']} for u in self.sip['units']]}]
        body = self.service.plan(body['id'], 'archive-a', 'operator', body['revision'], targets, 'Keep original dossier context')
        body = self.service.approve(body['id'], 'archive-a', 'reviewer', body['revision'])
        body = self.service.build(body['id'], 'archive-a', 'operator', body['revision'])
        sealed = [(o['sha256'], self.service.blob(o['sha256'])) for o in body['outputs']]
        self.save()
        body = self.current()
        self.assertNotIn('approval', body)
        for action in [self.service.approve, self.service.build]:
            with self.assertRaises(Conflict):
                action(body['id'], 'archive-a', 'reviewer', body['revision'])
        for digest, data in sealed:
            self.assertEqual(self.service.blob(digest), data)
        self.discard()
        body = self.current()
        self.assertNotIn('approval', body)
        self.assertEqual(body['state'], 'PLANNED')
        self.assertEqual(self.service.approve(body['id'], 'archive-a', 'reviewer', body['revision'])['state'], 'APPROVED')

    def test_reverting_last_patch_resolves_draft_but_retains_both_revisions(self):
        self.save()
        result = self.save([self.change(self.field['source_value'], before='Reviewed title')])
        self.assertFalse(result['active'])
        body = self.current()
        self.assertFalse(body['active_metadata_drafts'])
        self.assertEqual(len(body['metadata_drafts']), 2)

    def test_paired_recovery_retains_active_and_discarded_revision_blobs(self):
        first = self.save()['draft']
        self.discard()
        self.save([self.change('New saved proposal')])
        snapshot = self.folder / 'snapshot'
        report = backup(self.service, snapshot, actor='backup-admin', quiesced=True)
        destination = self.folder / 'restored'
        store = self.new_store()
        restore(snapshot, destination, ROOT / 'schemas', expected_digest=report['snapshot_sha256'], actor='restore-admin', quiesced=True, store=store)
        restored = Service(destination, ROOT / 'schemas', store=store)
        body = restored.get(self.transfer['id'], 'archive-a')
        draft = summary(restored, body, self.sip)
        self.assertEqual(draft['patches'][0]['after'], 'New saved proposal')
        self.assertEqual(restored.blob(first['metadata_sha256']), self.service.blob(first['metadata_sha256']))
        for action in [restored.approve, restored.build]:
            with self.assertRaises(Conflict):
                action(body['id'], 'archive-a', 'reviewer', body['revision'])

    def test_recovery_refuses_missing_historical_draft_artifact(self):
        draft = self.save()['draft']
        self.discard()
        (self.service.data / 'blobs' / draft['changes_sha256']).unlink()
        with self.assertRaises(PackageError):
            backup(self.service, self.folder / 'incomplete', actor='backup-admin', quiesced=True)
        self.assertFalse((self.folder / 'incomplete').exists())

    def test_recovery_refuses_dangling_active_draft_even_with_valid_source_and_events(self):
        self.save()
        body = self.current()
        body['active_metadata_drafts'][self.sip['id']] = 'missing-revision'
        self.service.store.save(body, body['revision'])
        with self.assertRaises(PackageError):
            backup(self.service, self.folder / 'invalid-pointer', actor='backup-admin', quiesced=True)
        self.assertFalse((self.folder / 'invalid-pointer').exists())


class DraftApiTests(DraftFixture):
    def setUp(self):
        super().setUp()
        self.original_service = main.service
        main.service = self.service
        self.addCleanup(setattr, main, 'service', self.original_service)
        self.addCleanup(main.app.dependency_overrides.clear)
        self.user()
        self.client = TestClient(main.app)
        self.base = '/api/transfers/' + self.transfer['id'] + '/sips/' + self.sip['id']

    def user(self, tenant='archive-a', roles=('archive-reader', 'archive-operator')):
        main.app.dependency_overrides[principal] = lambda: Principal('editor', tenant, frozenset(roles), 'archive-workbench')

    def payload(self):
        return {'revision': self.current()['revision'], 'source_sha256': self.sip['sha256'],
                'base_draft_id': self.current().get('active_metadata_drafts', {}).get(self.sip['id']),
                'reason': 'HTTP metadata correction', 'changes': [self.change()]}

    def test_http_permissions_stale_requests_and_digest_bound_downloads(self):
        payload = self.payload()
        self.user(roles=('archive-reader',))
        self.assertEqual(self.client.post(self.base + '/draft', json=payload).status_code, 403)
        self.user(tenant='other-archive')
        self.assertEqual(self.client.post(self.base + '/draft', json=payload).status_code, 404)
        self.user()
        response = self.client.post(self.base + '/draft', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        draft = response.json()['draft']
        self.assertEqual(self.client.post(self.base + '/draft', json=payload).status_code, 409)
        self.user(roles=('archive-reader',))
        for kind, field in [('metadata', 'metadata_sha256'), ('changes', 'changes_sha256')]:
            downloaded = self.client.get(self.base + '/drafts/' + draft['id'] + '/' + kind)
            self.assertEqual(downloaded.status_code, 200)
            self.assertEqual(sha(downloaded.content), draft[field])
            self.assertTrue(downloaded.headers['content-disposition'].startswith('attachment;'))
        self.user(tenant='other-archive', roles=('archive-reader',))
        self.assertEqual(self.client.get(self.base + '/drafts/' + draft['id'] + '/metadata').status_code, 404)
        self.user()
        baseline = {k: v for k, v in self.payload().items() if k != 'changes'}
        self.assertEqual(self.client.post(self.base + '/draft/discard', json=baseline).status_code, 200)
        self.assertEqual(self.client.get(self.base + '/drafts/' + draft['id'] + '/metadata').status_code, 200)
