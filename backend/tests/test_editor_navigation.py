"""Complete logical and inventory navigation over canonical metadata objects."""
import json
import tempfile
import unittest
from pathlib import Path
from app.service import Service
from app.working_metadata import workspace
from app.editor_fields import navigation, identity_path, object_kind, OBJECTS
from app.package_handler import key
from app.packages import NS, xml
from lxml import etree

ROOT = Path(__file__).resolve().parents[2]


class EditorNavigationTests(unittest.TestCase):
    def test_nested_dossier_kind_is_distinguished_for_navigator_badges(self):
        root = xml(('''<paket xmlns="'''+NS['a']+'''"><ablieferung><ordnungssystem>
          <ordnungssystemposition><dossier id="dossier"><dossier id="subdossier"/></dossier>
        </ordnungssystemposition></ordnungssystem></ablieferung></paket>''').encode())
        dossier = root.find('.//{'+NS['a']+'}dossier')
        subdossier = dossier.find('{'+NS['a']+'}dossier')
        self.assertEqual(object_kind(dossier), 'dossier')
        self.assertEqual(object_kind(subdossier), 'subdossier')

    def test_nested_logical_objects_and_shared_file_references_keep_distinct_rows(self):
        root = xml(('''<paket xmlns="'''+NS['a']+'''">
          <inhaltsverzeichnis><ordner id="folder"><ordner id="nested"><datei id="file"/></ordner></ordner></inhaltsverzeichnis>
          <ablieferung><ordnungssystem><ordnungssystemposition id="position"><dossier id="dossier">
            <dossier id="subdossier"><mappe id="map"><dokument id="doc-a"><dateiRef>file</dateiRef><dateiRef>file</dateiRef></dokument></mappe></dossier>
            <vorgang id="process"><aktivitaet id="activity"><dokument id="doc-b"><dateiRef>file</dateiRef></dokument></aktivitaet></vorgang>
          </dossier></ordnungssystemposition></ordnungssystem></ablieferung></paket>''').encode())
        nodes = [{'id': key('logical', identity_path(element)), 'kind': etree.QName(element).localname}
            for element in root.iter() if etree.QName(element).localname in OBJECTS]
        projected = navigation(root, nodes)
        ids = {element.get('id'): key('logical', identity_path(element)) for element in root.iter() if element.get('id')}
        for view in ('classification', 'hierarchy'):
            rows = projected[view]
            self.assertTrue({ids[name] for name in ('dossier','subdossier','map','doc-a','process','activity','doc-b')} <= {row['object_id'] for row in rows})
            references = [row for row in rows if row['object_id'] == ids['file']]
            self.assertEqual(len(references), 2)
            self.assertNotEqual(references[0]['key'], references[1]['key'])
            by_key = {row['key']: row for row in rows}
            self.assertEqual({by_key[row['parent_key']]['object_id'] for row in references}, {ids['doc-a'],ids['doc-b']})
        self.assertEqual(sum(row['object_id'] == ids['file'] for row in projected['inventory']), 1)
        self.assertEqual(projected, navigation(root, nodes))

    def test_all_profiles_keep_nested_objects_and_referenced_files_in_each_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = Service(Path(temporary), ROOT/'schemas')
            for case in json.loads((ROOT/'fixtures/corpus.json').read_text())['cases']:
                with self.subTest(case=case['file']):
                    body = service.create('Full navigation', 'test-archive', 'operator')
                    body = service.upload(body['id'], 'test-archive', 'operator', 0, case['profile'], (ROOT/case['file']).read_bytes())
                    doc = workspace(service, body['id'], 'test-archive', body['sips'][0]['id'])
                    nodes = {node['id']: node for node in doc['nodes']}
                    navigation = doc['navigation']
                    for view, rows in navigation.items():
                        seen = {}
                        for row in rows:
                            self.assertIn(row['object_id'], nodes)
                            self.assertNotIn(row['key'], seen)
                            if row['parent_key']:
                                self.assertIn(row['parent_key'], seen)
                                self.assertEqual(row['depth'], seen[row['parent_key']]['depth']+1)
                            else:
                                self.assertEqual(row['depth'], 0)
                            seen[row['key']] = row
                    inventory_ids = {row['object_id'] for row in navigation['inventory']}
                    self.assertTrue({ident for ident, node in nodes.items() if node['kind'] in ('ordner', 'datei')} <= inventory_ids)
                    logical_ids = {ident for ident, node in nodes.items() if node['kind'] in ('dossier', 'dokument', 'mappe', 'vorgang', 'aktivitaet')}
                    for view in ('classification', 'hierarchy'):
                        ids = {row['object_id'] for row in navigation[view]}
                        self.assertTrue(logical_ids <= ids)
                        referenced = {ident for ident, node in nodes.items() if node['kind'] == 'datei'
                            and node['source_id'] in {reference['file_id'] for reference in body['sips'][0]['references']}}
                        self.assertTrue(referenced <= ids)