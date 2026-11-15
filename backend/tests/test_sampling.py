import random
import unittest

from app.packages import PackageError
from app.sampling import draw_sample


def dossier(ident, reference=''):
    return {
        'id': ident,
        'kind': 'dossier',
        'title': 'Title ' + ident,
        'fields': [{'tag': 'aktenzeichen', 'values': [{'text': reference}]}],
    }


class SamplingTests(unittest.TestCase):
    def setUp(self):
        self.nodes = [
            dossier('a', 'REF-A'),
            {'id': 'sub', 'kind': 'subdossier', 'title': 'Nested title',
             'fields': [{'tag': 'aktenzeichen', 'values': [{'text': 'REF-SUB'}]}]},
            {'id': 'doc', 'kind': 'dokument', 'title': 'Document', 'fields': []},
            dossier('b', 'REF-B'),
            dossier('c', 'REF-C'),
            dossier('d', 'REF-D'),
            dossier('e', 'REF-E'),
        ]

    def test_draw_is_unique_and_keeps_source_order_and_dossier_metadata(self):
        items = draw_sample(self.nodes, 'absolute', 3, random.Random(1))
        dossier_ids = [node['id'] for node in self.nodes if node['kind'] == 'dossier']
        self.assertEqual(len(items), 3)
        self.assertEqual(len({item['node_id'] for item in items}), 3)
        self.assertEqual([item['node_id'] for item in items],
                         [ident for ident in dossier_ids if ident in {item['node_id'] for item in items}])
        for item in items:
            node = next(node for node in self.nodes if node['id'] == item['node_id'])
            self.assertEqual(item['name'], node['title'])
            self.assertEqual(item['reference'], node['fields'][0]['values'][0]['text'])

    def test_percent_uses_rounded_population_and_zero_samples_are_supported(self):
        self.assertEqual(len(draw_sample(self.nodes, 'percent', 50, random.Random(1))), 2)
        self.assertEqual(len(draw_sample(self.nodes, 'percent', 100)), 5)
        self.assertEqual(draw_sample(self.nodes, 'absolute', 0), [])

    def test_invalid_mode_size_and_percentage_are_rejected(self):
        for mode, size in [('unknown', 1), ('absolute', -1), ('absolute', 501), ('percent', 101)]:
            with self.subTest(mode=mode, size=size), self.assertRaises(PackageError):
                draw_sample(self.nodes, mode, size)


if __name__ == '__main__':
    unittest.main()
