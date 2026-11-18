"""Random dossier samples from a metadata workspace."""
from random import SystemRandom

from .json_codec import canonical
from .packages import PackageError,sha

KINDS = {'sample-record','sample-review'}
ALGORITHM = 'sha256-ranked-v1'


def plan(nodes, mode, size, seed, name_filter='', reference_filter=''):
    if mode not in ('absolute','percent') or type(size) is not int or not 1<=size<=(100 if mode=='percent' else 500):
        raise PackageError('Invalid sample mode or size')
    if not isinstance(seed,str) or not 1<=len(seed)<=200:
        raise PackageError('A reproducible sample seed is required (200 characters maximum)')
    if any(not isinstance(value,str) or len(value)>200 for value in (name_filter,reference_filter)):
        raise PackageError('Sample criteria exceed the local limit')
    try:
        for value in (seed,name_filter,reference_filter):value.encode('utf-8')
    except UnicodeError as error:
        raise PackageError('Sample parameters must be valid Unicode') from error
    population=draw_sample(nodes,'percent',100)
    population=[item for item in population if name_filter.casefold() in item['name'].casefold()
                and reference_filter.casefold() in item['reference'].casefold()]
    count=(len(population)*size+99)//100 if mode=='percent' else size
    count=min(count,len(population))
    if count>500:raise PackageError('Sample exceeds 500 selections; reduce the size or narrow the criteria')
    selected={item['node_id'] for item in sorted(population,key=lambda item:(sha((seed+'\0'+item['node_id']).encode()),item['node_id']))[:count]}
    return {'algorithm':ALGORITHM,'mode':mode,'size':size,'seed':seed,
            'name_filter':name_filter,'reference_filter':reference_filter,
            'population_count':len(population),'population_sha256':sha(canonical(population)),
            'items':[item for item in population if item['node_id'] in selected]}


def retained_plan(nodes, request, metadata_sha256, operation_id):
    sample=plan(nodes,request.get('sample_mode'),request.get('sample_size'),request.get('sample_seed'),
                request.get('sample_name',''),request.get('sample_reference',''))
    if not sample['items']:raise PackageError('Cannot retain an empty sample')
    return {**sample,'revision':request['revision'],'metadata_sha256':metadata_sha256,'input_operation_id':operation_id}


def review(records, request):
    sample=next((record for record in records if record['id']==request.get('sample_id') and record['kind']=='sample-record'),None)
    if sample is None:raise PackageError('Choose a retained sample from this SIP')
    selections=sample['changes']['sample']['items']
    reviews=request.get('reviews')
    if not isinstance(reviews,list) or len(reviews)!=len(selections):
        raise PackageError('Review every selected sample object')
    for selection,value in zip(selections,reviews):
        if (not isinstance(value,dict) or set(value)!={'node_id','outcome','comment'}
                or value['node_id']!=selection['node_id'] or value['outcome'] not in ('checked','issue')
                or not isinstance(value['comment'],str) or len(value['comment'])>2000
                or (value['outcome']=='issue' and not value['comment'].strip())):
            raise PackageError('Each sample item needs an outcome; issues require a comment (2000 characters maximum)')
        try:value['comment'].encode('utf-8')
        except UnicodeError as error:raise PackageError('Sample comments must be valid Unicode') from error
    latest=next((r for r in reversed(records) if r['kind']=='sample-review' and r['changes']['sample_review']['sample_id']==sample['id']),None)
    if latest and latest['changes']['sample_review']['reviews']==reviews:
        raise PackageError('No sample review changes to retain')
    return {'sample_id':sample['id'],'metadata_sha256':sample['changes']['sample']['metadata_sha256'],
            'input_operation_id':sample['changes']['sample']['input_operation_id'],
            'population_sha256':sample['changes']['sample']['population_sha256'],'reviews':reviews}


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
