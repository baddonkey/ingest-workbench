"""Random dossier samples from a metadata workspace."""
from random import SystemRandom

from .packages import PackageError


def draw_sample(nodes, mode='absolute', size=10, rng=None):
    if mode not in ('absolute', 'percent'):
        raise PackageError('Sample mode must be absolute or percent')
    if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > 500:
        raise PackageError('Sample size must be between 0 and 500')
    if mode == 'percent' and size > 100:
        raise PackageError('Sample percentage must be between 0 and 100')

    dossiers = [node for node in nodes if node['kind'] == 'dossier']
    count = round(len(dossiers) * size / 100) if mode == 'percent' else size
    chosen = (rng or SystemRandom()).sample(dossiers, min(count, len(dossiers)))
    order = {node['id']: index for index, node in enumerate(dossiers)}
    chosen.sort(key=lambda node: order[node['id']])

    def field_text(node, tag):
        field = next((field for field in node['fields'] if field['tag'] == tag), None)
        return field['values'][0]['text'] if field and field['values'] else ''

    return [{
        'node_id': node['id'],
        'reference': field_text(node, 'aktenzeichen'),
        'name': node['title'],
    } for node in chosen]
