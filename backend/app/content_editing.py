"""Retained payload changes; originals and prior content remain immutable."""
import base64
import binascii
import copy
import hashlib
import io
import unicodedata
import zipfile
from lxml import etree as ET
from . import editor_fields as fields
from .hierarchy_editing import ident
from .packages import MAX_EXPANDED, PackageError, inspect, members, sha, text, xml
from .sip_exports import member_manifest
from .json_codec import canonical
from .evidence_codec import Decoder, encode_zip

KIND = 'content-edit'


def filename(value):
    if (not isinstance(value,str) or not value or len(value)>200 or value in ('.','..')
            or any(c in value for c in '/\\:') or any(ord(c)<32 for c in value)
            or unicodedata.normalize('NFC',value)!=value):
        raise PackageError('A content filename must be a safe NFC basename (200 characters maximum)')
    try:ET.Element('name').text=value
    except ValueError as error:raise PackageError('Filename contains characters forbidden in XML') from error
    return value


def source(value):
    if not isinstance(value,dict) or set(value)!={'filename','data_base64'}:
        raise PackageError('A replacement needs its original filename and exact bytes')
    filename(value['filename'])
    encoded=value['data_base64']
    if not isinstance(encoded,str) or len(encoded)>4*((MAX_EXPANDED+2)//3):
        raise PackageError('Replacement exceeds the package expanded-byte limit')
    try:
        data=base64.b64decode(encoded,validate=True)
    except (ValueError,binascii.Error) as error:
        raise PackageError('Replacement is not canonical base64') from error
    if len(data)>MAX_EXPANDED or base64.b64encode(data).decode()!=encoded:
        raise PackageError('Replacement is not bounded canonical base64')
    return data


def snapshots(records, source_sha256):
    current=source_sha256;result={}
    for record in records:
        before=current
        if record['kind']==KIND:
            current=record['changes']['content']['after_archive_sha256']
        elif record['kind'] in ('undo','redo','restore'):
            target=result.get(record['changes']['related_operation_id'])
            if target is None:raise PackageError('Content history has an unknown compensation target')
            current=target[0 if record['kind']=='undo' else 1]
        result[record['id']]=(before,current)
    return result


def current(records, source_sha256):
    versions=snapshots(records,source_sha256)
    return versions[records[-1]['id']][1] if records else source_sha256


def has_changes(blob, sip, records):
    if not any(r['kind']==KIND for r in records):return False
    before,old_comment=inventory(blob(sip['sha256']))
    after,new_comment=inventory(blob(current(records,sip['sha256'])))
    return ([e for e in before if e['path']!=sip['metadata_path']]
            !=[e for e in after if e['path']!=sip['metadata_path']] or old_comment!=new_comment)


def rewrite(archive_data, metadata_path, metadata, old_path=None, new_path=None, replacement=None):
    members(archive_data)
    stream=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive_data)) as original,zipfile.ZipFile(stream,'w') as result:
        result.comment=original.comment
        for entry in original.infolist():
            path=entry.orig_filename;content=original.read(entry)
            if path==metadata_path:content=metadata
            elif path==old_path and replacement is not None:content=replacement
            updated=copy.copy(entry)
            if path==old_path:updated.filename=updated.orig_filename=new_path
            result.writestr(updated,content)
    data=stream.getvalue();members(data)
    return data


def prepared(blob, sip, records, metadata):
    data=blob(current(records,sip['sha256']))
    return data if members(data)[sip['metadata_path']]==metadata else rewrite(data,sip['metadata_path'],metadata)


def inventory(archive_data):
    contents=members(archive_data)
    with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
        return [{'path':e.orig_filename,'directory':e.is_dir(),
                 'sha256':sha(b'' if e.is_dir() else contents[e.orig_filename]),
                 'size':0 if e.is_dir() else len(contents[e.orig_filename])} for e in archive.infolist()],archive.comment


def lineage(source_data, prepared_data):
    before,old_comment=inventory(source_data);after,new_comment=inventory(prepared_data)
    old={e['path']:e for e in before};new={e['path']:e for e in after}
    return {'source_order':[e['path'] for e in before],'prepared_order':[e['path'] for e in after],
            'source_comment_sha256':sha(old_comment),'comment_sha256':sha(new_comment),
            'members':[{'path':path,'source':old.get(path),'prepared':new.get(path)}
                       for path in dict.fromkeys([*old,*new])]}


def apply(archive_data, sip, schemas, request):
    contents=members(archive_data);root=xml(contents[sip['metadata_path']])
    node=next((e for e in root.iter(fields.A+'datei') if ident(e)==request.get('node_id')),None)
    if node is None:raise PackageError('Unknown content file')
    prefix=sip['metadata_path'][:-len('header/metadata.xml')]
    names=[text(p,'name') for p in node.iterancestors(fields.A+'ordner')]
    path=prefix+'/'.join([*reversed(names),text(node,'name')])
    if not path.startswith(prefix+'content/') or path not in contents:
        raise PackageError('Only retained payload files can be changed')
    action=request.get('content_action');new_path=path;replacement=None
    if action=='rename':
        if request.get('content_source') is not None:raise PackageError('A rename cannot include replacement bytes')
        name=filename(request.get('content_name'))
        if name==text(node,'name'):raise PackageError('No filename change to apply')
        if any(sibling is not node and sibling.tag in (fields.A+'ordner',fields.A+'datei')
               and unicodedata.normalize('NFC',text(sibling,'name')).casefold()==name.casefold()
               for sibling in node.getparent()):
            raise PackageError('Filename collides with an existing file or folder')
        new_path=path.rsplit('/',1)[0]+'/'+name
        node.find(fields.A+'name').text=name
    elif action=='replace':
        if request.get('content_name'):raise PackageError('Replacement cannot implicitly rename a file')
        replacement=source(request.get('content_source'))
        if replacement==contents[path]:raise PackageError('Replacement bytes are unchanged')
        algorithm={'MD5':'md5','SHA-1':'sha1','SHA-256':'sha256','SHA-512':'sha512'}.get(text(node,'pruefalgorithmus'))
        if algorithm is None:raise PackageError('Unsupported checksum algorithm')
        node.find(fields.A+'pruefsumme').text=hashlib.new(algorithm,replacement,usedforsecurity=False).hexdigest()
    else:raise PackageError('Unknown content-edit action')
    metadata=fields.validate(root,schemas,sip)
    data=rewrite(archive_data,sip['metadata_path'],metadata,path,new_path,replacement)
    report=inspect(data,sip['profile'],schemas)
    if report['schema_bundle_sha256']!=sip['schema_bundle_sha256']:
        raise PackageError('Content edit changed the trusted schema')
    after=contents[path] if replacement is None else replacement
    change={'action':action,'node_id':request['node_id'],'before_path':path,'after_path':new_path,
            'before_sha256':sha(contents[path]),'after_sha256':sha(after),
            'before_size':len(contents[path]),'after_size':len(after),
            'upload_filename':request['content_source']['filename'] if action=='replace' else None,
            'before_archive_sha256':sha(archive_data),'after_archive_sha256':sha(data)}
    return {'content':change},metadata,data


def verify(record, previous, blob, sip, schemas):
    if record.get('version')!=9 or set(record['changes'])!={'content'}:
        raise PackageError('Unknown retained content-edit contract')
    change=record['changes']['content']
    before=blob(change['before_archive_sha256']);after=blob(change['after_archive_sha256'])
    if sha(before)!=change['before_archive_sha256'] or sha(after)!=change['after_archive_sha256']:
        raise PackageError('Content archive fixity mismatch')
    member_manifest(blob(previous),before,sip['metadata_path'])
    if members(before)[sip['metadata_path']]!=blob(record['before_metadata_sha256']):
        raise PackageError('Content edit metadata baseline mismatch')
    request={'node_id':change['node_id'],'content_action':change['action']}
    if change['action']=='rename':request['content_name']=change['after_path'].rsplit('/',1)[-1]
    if change['action']=='replace':
        request['content_source']={'filename':change['upload_filename'],
                                  'data_base64':base64.b64encode(members(after)[change['after_path']]).decode()}
    expected,metadata,rebuilt=apply(before,sip,schemas,request)
    if ({k:v for k,v in expected['content'].items() if k!='after_archive_sha256'}
            !={k:v for k,v in change.items() if k!='after_archive_sha256'}
            or metadata!=blob(record['after_metadata_sha256']) or inventory(rebuilt)!=inventory(after)):
        raise PackageError('Retained content edit changed unapproved bytes, paths or metadata')


def evidence_sources(records, blob):
    digests=archive_digests(records)
    if not digests:return {}
    baseline=evidence_baseline(records,blob)
    result={}
    for digest in digests:
        path='content/'+digest
        if digest in baseline or path in result or path+'.zip-recipe.json' in result:continue
        data=blob(digest);encoded=encode_zip(data)
        if encoded is None:result[path]=data;continue
        recipe,contents=encoded
        result[path+'.zip-recipe.json']=canonical(recipe)
        for member,data in contents.items():
            path='content/'+member
            if member not in baseline and path+'.zip-recipe.json' not in result:result[path]=data
    return result


def archive_digests(records):
    return list(dict.fromkeys(r['changes']['content'][key] for r in records if r['kind']==KIND
                             for key in ('before_archive_sha256','after_archive_sha256')))


def evidence_baseline(records, blob):
    original=blob(records[0]['source_sha256'])
    result={sha(original):original}|{sha(data):data for data in members(original).values()}
    for r in records:
        for key in ('before_metadata_sha256','after_metadata_sha256'):result[r[key]]=blob(r[key])
    return result


def evidence_archives(records, payload):
    if not archive_digests(records):
        if any(path.startswith('content/') for path in payload):raise PackageError('Unexpected content evidence')
        return {}
    def metadata(digest):
        if digest==records[0]['source_sha256']:return payload['original.zip']
        return payload['versions/'+digest+'.xml']
    baseline=evidence_baseline(records,metadata)
    decoder=Decoder({'content/'+digest:data for digest,data in baseline.items()}|payload,'content/')
    archives={digest:decoder(digest) for digest in archive_digests(records)}
    if {p for p in payload if p.startswith('content/')}!=decoder.used & set(payload):
        raise PackageError('Unreferenced content evidence')
    return archives
