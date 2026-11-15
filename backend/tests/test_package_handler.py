"""Preserved-source navigation, safe text previews and tenant-bound HTTP downloads."""
import json
import io
import zipfile
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from app import main
from app.auth import Principal, principal
from app.package_handler import MAX_PREVIEW, content, node_detail, preview, project, source, workspace
from app.packages import members, sha, xml
from app.service import Service

ROOT = Path(__file__).resolve().parents[2]


class PackageHandlerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = Service(Path(self.tmp.name), ROOT / 'schemas')

    def upload(self, case):
        original = (ROOT / case['file']).read_bytes()
        transfer = self.service.create('Ingest Packager corpus', 'archive-a', 'operator')
        transfer = self.service.upload(transfer['id'], 'archive-a', 'operator', 0, case['profile'], original)
        return transfer, transfer['sips'][0], original

    def test_nested_dossier_kind_is_distinguished_for_source_navigation_badges(self):
        root = xml(('''<paket xmlns="http://bar.admin.ch/arelda/v4"><ablieferung><ordnungssystem>
          <ordnungssystemposition><dossier id="dossier"><dossier id="subdossier"/></dossier>
        </ordnungssystemposition></ordnungssystem></ablieferung></paket>''').encode())
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w'):
            pass
        nodes, _ = project({'files': {}, 'units': [], 'metadata_path': 'header/metadata.xml'},
                           {}, root, archive.getvalue())
        dossiers = {node['source_id']: node['kind'] for node in nodes if node['kind'] in ('dossier', 'subdossier')}
        self.assertEqual(dossiers, {'dossier': 'dossier', 'subdossier': 'subdossier'})

    def test_all_eight_corpus_packages_conserve_logical_and_physical_inventory(self):
        for case in json.loads((ROOT / 'fixtures/corpus.json').read_text())['cases']:
            with self.subTest(case=case['file']):
                transfer, sip, original = self.upload(case)
                before = self.service.get(transfer['id'], 'archive-a')
                doc = workspace(self.service, transfer['id'], 'archive-a', sip['id'])
                nodes = doc['nodes']
                ids = {n['id'] for n in nodes}
                self.assertEqual(len(ids), len(nodes))
                self.assertTrue(all(n['parent_id'] is None or n['parent_id'] in ids for n in nodes))
                physical = [n for n in nodes if n['kind'] == 'file']
                originals = members(original)
                self.assertEqual([n['path'] for n in physical], list(originals))
                self.assertEqual({n['source_id'] for n in physical if n['payload']}, set(sip['files']))
                for node in physical:
                    self.assertEqual(node['sha256'], sha(originals[node['path']]))
                    self.assertEqual(node['size'], len(originals[node['path']]))
                logical = [n for n in nodes if n['view'] == 'logical']
                self.assertTrue(any(n['kind'] == 'ordnungssystem' for n in logical))
                self.assertEqual({n['file_id'] for n in logical if n.get('file_id')}, set(sip['files']))
                unit_ids = {u['id'] for u in sip['units']}
                self.assertEqual({n['unit_id'] for n in logical if n.get('unit_id')}, unit_ids)
                for node in logical:
                    if node.get('content_node_id'):
                        self.assertIn(node['content_node_id'], ids)
                self.assertEqual(self.service.get(transfer['id'], 'archive-a'), before)
                self.assertEqual(self.service.blob(sip['sha256']), original)
                self.assertEqual(doc['input_sha256'], case['sha256'])
                self.assertEqual(doc['validation']['acceptance'], 'CLOSED')

    def test_selected_metadata_retains_field_values_ids_relations_and_unit_context(self):
        case = json.loads((ROOT / 'fixtures/corpus.json').read_text())['cases'][-1]
        transfer, sip, original = self.upload(case)
        doc = workspace(self.service, transfer['id'], 'archive-a', sip['id'])
        dossier = next(n for n in doc['nodes'] if n['kind'] == 'dossier')
        detail = node_detail(self.service, transfer['id'], 'archive-a', sip['id'], dossier['id'])
        self.assertIn({'path': 'dossier/@id', 'value': dossier['source_id']}, detail['fields'])
        self.assertTrue(any(f['value'] == dossier['title'] for f in detail['fields']))
        self.assertIn(dossier['source_id'], detail['xml'])
        reference = next(n for n in doc['nodes'] if n.get('content_node_id'))
        detail = node_detail(self.service, transfer['id'], 'archive-a', sip['id'], reference['id'])
        self.assertEqual(detail['file']['source_id'], reference['file_id'])
        self.assertEqual(detail['node']['unit_id'], reference['unit_id'])
        data, _ = content(self.service, transfer['id'], 'archive-a', sip['id'], detail['file']['id'])
        self.assertEqual(sha(data), detail['file']['sha256'])
        physical_detail = node_detail(self.service, transfer['id'], 'archive-a', sip['id'], detail['file']['id'])
        original_assertions = sip['files'][reference['file_id']]
        values = [field['value'].strip() for field in physical_detail['fields']]
        self.assertIn(original_assertions['original_algorithm'], values)
        self.assertIn(original_assertions['original_digest'].lower(), [v.lower() for v in values])
        self.assertEqual(source(self.service, transfer['id'], 'archive-a', sip['id'])[2], original)

    def test_changed_stored_blob_is_refused_before_navigation(self):
        case = json.loads((ROOT / 'fixtures/corpus.json').read_text())['cases'][-1]
        transfer, sip, _ = self.upload(case)
        (self.service.data / 'blobs' / sip['sha256']).write_bytes(b'changed source')
        from app.packages import PackageError
        with self.assertRaises(PackageError):
            workspace(self.service, transfer['id'], 'archive-a', sip['id'])

    def test_preview_escapes_at_render_boundary_and_rejects_active_binary_nonutf8(self):
        attack = b'<script>alert("source text")</script>'
        # JSON preserves text exactly; Angular interpolation is the escaping boundary.
        self.assertEqual(preview('content/note.txt', attack)['text'], attack.decode())
        for name, value in [('content/page.html', attack), ('image.svg', attack),
                            ('document.pdf', b'%PDF'), ('text.txt', b'\x00bad'), ('text.txt', b'\xff')]:
            with self.subTest(name=name):
                self.assertFalse(preview(name, value)['available'])

    def test_preview_limit_handles_multibyte_boundary_without_replacement(self):
        value = b'x' * (MAX_PREVIEW - 1) + '\u00e4'.encode() + b'more'
        result = preview('text.txt', value)
        self.assertTrue(result['available'])
        self.assertTrue(result['truncated'])
        self.assertEqual(result['text'], 'x' * (MAX_PREVIEW - 1))
        self.assertEqual(preview('note.txt', b'\xef\xbb\xbfhello')['text'], 'hello')

    def test_explicit_empty_zip_directory_remains_visible_without_extracting(self):
        original = (ROOT / 'fixtures/reference-1.3.zip').read_bytes()
        stream = io.BytesIO(original)
        with zipfile.ZipFile(stream, 'a') as archive:
            archive.writestr('SIP_20240430_eCH1.3F_1a-3d-IO/empty-original-folder/', b'')
        data = stream.getvalue()
        transfer = self.service.create('Derived directory fixture', 'archive-a', 'operator')
        transfer = self.service.upload(transfer['id'], 'archive-a', 'operator', 0, '1.3.0', data)
        doc = workspace(self.service, transfer['id'], 'archive-a', transfer['sips'][0]['id'])
        self.assertTrue(any(n['kind'] == 'folder' and n['title'] == 'empty-original-folder' for n in doc['nodes']))
        self.assertEqual(self.service.blob(doc['input_sha256']), data)


class PackageHandlerApiTests(unittest.TestCase):
    def service_store(self):
        return None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_service = main.service
        main.service = Service(Path(self.tmp.name), ROOT / 'schemas', store=self.service_store())
        self.client = TestClient(main.app)
        self.user()
        self.original = (ROOT / 'fixtures/reference-1.3.zip').read_bytes()
        transfer = main.service.create('Read-only handler API', 'archive-a', 'operator')
        self.transfer = main.service.upload(transfer['id'], 'archive-a', 'operator', 0, '1.3.0', self.original, filename='KOST-CECO_2009-1.zip')
        self.sip = self.transfer['sips'][0]
        self.base = '/api/transfers/' + self.transfer['id'] + '/sips/' + self.sip['id']

    def tearDown(self):
        main.app.dependency_overrides.clear()
        main.service = self.original_service
        self.tmp.cleanup()

    def user(self, tenant='archive-a', roles=('archive-reader',)):
        main.app.dependency_overrides[principal] = lambda: Principal('reader', tenant, frozenset(roles), 'archive-workbench')

    def test_reader_can_browse_and_download_original_bytes_without_writes(self):
        response = self.client.get(self.base + '/workspace')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        doc = response.json()
        self.assertEqual(doc['original_filename'], 'KOST-CECO_2009-1.zip')
        file_node = next(n for n in doc['nodes'] if n['kind'] == 'file' and n['title'] == 'ReadMe.txt')
        for path, digest in [('/original', self.sip['sha256']),
                             ('/metadata', sha(members(self.original)[self.sip['metadata_path']])),
                             ('/nodes/' + file_node['id'] + '/content', file_node['sha256'])]:
            with self.subTest(path=path):
                downloaded = self.client.get(self.base + path)
                self.assertEqual(downloaded.status_code, 200)
                self.assertEqual(sha(downloaded.content), digest)
                self.assertTrue(downloaded.headers['content-disposition'].startswith('attachment;'))
                self.assertEqual(downloaded.headers['x-content-type-options'], 'nosniff')
        detail = self.client.get(self.base + '/nodes/' + file_node['id'])
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.json()['preview']['available'])
        self.assertEqual(main.service.get(self.transfer['id'], 'archive-a'), self.transfer)

    def test_every_read_route_is_tenant_and_role_scoped(self):
        doc = self.client.get(self.base + '/workspace').json()
        node = next(n for n in doc['nodes'] if n['kind'] == 'file')['id']
        paths = ['/workspace', '/original', '/metadata', '/nodes/' + node, '/nodes/' + node + '/content']
        for tenant, roles, expected in [('other-archive', ('archive-reader',), 404), ('archive-a', (), 403)]:
            self.user(tenant, roles)
            for path in paths:
                with self.subTest(tenant=tenant, path=path):
                    self.assertEqual(self.client.get(self.base + path).status_code, expected)

    def test_unknown_sips_nodes_and_directory_content_are_not_resolved_as_paths(self):
        self.assertEqual(self.client.get(self.base.replace(self.sip['id'], 'missing') + '/workspace').status_code, 404)
        self.assertEqual(self.client.get(self.base + '/nodes/not-a-node').status_code, 404)
        self.assertEqual(self.client.get(self.base + '/nodes/physical-root/content').status_code, 404)
        self.assertEqual(self.client.post(self.base + '/workspace', json={}).status_code, 405)
