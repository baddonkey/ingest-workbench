"""Schema-valid METS/PREMIS for exact preparation operations and XML versions."""
import io
import json
import zipfile
from urllib.parse import quote
from lxml import etree as ET
from .aip import el, identifier, relation, M, P, X, I
from .aip_validation import resolve_href
from .packages import sha, PackageError, members
from .json_codec import canonical
from .metadata_validation import validate_metadata

VOCABULARY = 'urn:ingest-workbench:preparation-events:1'


def bundle(service, body, sip):
    records = [r for r in body['metadata_operations'] if r['sip_id']==sip['id']]
    payload = {'original.zip':service.blob(sip['sha256'])}
    from . import metadata_imports
    from . import content_editing
    imported=metadata_imports.evidence_sources(records)
    payload.update(imported|content_editing.evidence_sources(records,service.blob))
    archives={digest:service.blob(digest) for digest in content_editing.archive_digests(records)}
    from . import preparation_notes as notes
    if any(r['kind'] in notes.KINDS for r in records):
        payload['notes.json'] = canonical(notes.document(records))
    versions = {}
    for record in records:
        payload['changes/'+record['id']+'.json'] = service.blob(record['changes_sha256'])
        for digest in (record['before_metadata_sha256'],record['after_metadata_sha256']):
            versions[digest] = service.blob(digest)
    for digest,data in versions.items():payload['versions/'+digest+'.xml'] = data
    state = body['working_metadata'][sip['id']]
    payload['history.json'] = canonical({'vocabulary':VOCABULARY,'sip_id':sip['id'],'source_sha256':sip['sha256'],
        'head':state['head'],'metadata_sha256':state['metadata_sha256'],
        'operations':[{k:v for k,v in r.items() if k!='evidence_sha256'} for r in records],
        'production_acceptance':False})
    legacy = state.get('legacy_draft_id')
    if legacy:
        for draft in body.get('metadata_drafts',[]):
            if draft['sip_id']==sip['id']:
                payload['legacy/'+draft['id']+'.json'] = service.blob(draft['changes_sha256'])
                payload['legacy/'+draft['id']+'.xml'] = service.blob(draft['metadata_sha256'])
    premis = ET.Element('{'+P+'}premis',nsmap={'premis':P,'xsi':I},version='3.0')
    objects = {sha(data):data for data in imported.values()} | archives | {sip['sha256']:payload['original.zip'],**versions}
    import_formats={sha(data):path.rsplit('.',1)[-1].upper() for path,data in imported.items()}
    for digest,data in objects.items():
        obj = el(premis,P,'object',**{'{'+I+'}type':'premis:file'})
        identifier(obj,'objectIdentifier','urn:sha256:'+digest)
        char = el(obj,P,'objectCharacteristics');el(char,P,'compositionLevel',0)
        fix = el(char,P,'fixity');el(fix,P,'messageDigestAlgorithm','SHA-256');el(fix,P,'messageDigest',digest)
        el(char,P,'size',len(data))
        fmt = el(char,P,'format');el(el(fmt,P,'formatDesignation'),P,'formatName','ZIP' if digest==sip['sha256'] or digest in archives else 'XML' if digest in versions else import_formats[digest])
        if digest in versions:relation(obj,'derivation','metadata of source SIP','urn:sha256:'+sip['sha256'])
    for record in records:
        ev = el(premis,P,'event');identifier(ev,'eventIdentifier',record['id'])
        el(ev,P,'eventType',VOCABULARY+':'+record['kind']);el(ev,P,'eventDateTime',record['created'])
        detail = el(ev,P,'eventDetailInformation')
        el(detail,P,'eventDetail',canonical({'changes_sha256':record['changes_sha256'],
           'actor':record['actor'],'reason':record['reason'],'changes':record['changes'],
           'before_metadata_sha256':record['before_metadata_sha256'],
           'after_metadata_sha256':record['after_metadata_sha256'],
           'predecessor_id':record['predecessor_id']}).decode())
        outcome = el(ev,P,'eventOutcomeInformation');el(outcome,P,'eventOutcome','success')
        identifier(ev,'linkingAgentIdentifier',record['actor'])
        source_digest=metadata_imports.operation_source(record,records)
        content_digests=([record['changes']['content'][key] for key in ('before_archive_sha256','after_archive_sha256')]
                         if record['kind']==content_editing.KIND else [])
        for digest in dict.fromkeys([sip['sha256'],record['before_metadata_sha256'],record['after_metadata_sha256']]+([source_digest] if source_digest else [])+content_digests):
            identifier(ev,'linkingObjectIdentifier','urn:sha256:'+digest)
    for actor in sorted({r['actor'] for r in records}):
        agent = el(premis,P,'agent');identifier(agent,'agentIdentifier',actor)
        el(agent,P,'agentName',actor);el(agent,P,'agentType','person')
    payload['metadata/premis.xml'] = ET.tostring(premis,encoding='UTF-8',xml_declaration=True)
    mets = ET.Element('{'+M+'}mets',nsmap={'mets':M,'xlink':X},OBJID='urn:uuid:'+sip['id'],
                      PROFILE=VOCABULARY,LABEL='Package preparation evidence')
    header = el(mets,M,'metsHdr',CREATEDATE=records[-1]['created'],RECORDSTATUS='PREPARATION')
    agent = el(header,M,'agent',ROLE='CREATOR',TYPE='OTHER',OTHERTYPE='software');el(agent,M,'name','Ingest Workbench')
    dmd = el(mets,M,'dmdSec',ID='CURRENT')
    el(dmd,M,'mdRef',MDTYPE='OTHER',OTHERMDTYPE='eCH-0160 '+sip['profile'],LOCTYPE='URL',
       **{'{'+X+'}href':'../versions/'+state['metadata_sha256']+'.xml'})
    amd = el(mets,M,'amdSec',ID='PROVENANCE')
    el(el(amd,M,'digiprovMD',ID='EVENTS'),M,'mdRef',MDTYPE='PREMIS',LOCTYPE='URL',**{'{'+X+'}href':'premis.xml'})
    group = el(el(mets,M,'fileSec'),M,'fileGrp',USE='preparation-history')
    identifiers = {}
    for index,(path,data) in enumerate(sorted(payload.items())):
        ident = 'F'+str(index);identifiers[path]=ident
        file = el(group,M,'file',ID=ident,SIZE=str(len(data)),CHECKSUMTYPE='SHA-256',CHECKSUM=sha(data))
        el(file,M,'FLocat',LOCTYPE='URL',**{'{'+X+'}href':quote('../'+path,safe='/')})
    root = el(el(mets,M,'structMap',TYPE='physical'),M,'div',TYPE='preparation',DMDID='CURRENT',ADMID='PROVENANCE')
    for path,ident in identifiers.items():el(el(root,M,'div',TYPE='evidence',LABEL=path),M,'fptr',FILEID=ident)
    payload['metadata/mets.xml'] = ET.tostring(mets,encoding='UTF-8',xml_declaration=True)
    validate_metadata(payload,service.schemas/'preservation')
    if sum(map(len,payload.values()))>256*1024*1024:
        raise PackageError('Preparation evidence exceeds local expanded limit')
    payload['manifest-sha256.txt'] = ''.join(sha(data)+'  '+path+'\n' for path,data in sorted(payload.items())).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        for path,data in payload.items():archive.writestr(path,data)
    if len(stream.getvalue())>64*1024*1024:raise PackageError('Preparation evidence exceeds local ZIP limit')
    return stream.getvalue()


def verify(data, records, sip, schemas=None):
    """Check schema, complete fixity and agreement between journal and METS/PREMIS."""
    payload=members(data)
    manifest=''.join(sha(content)+'  '+path+'\n' for path,content in sorted(payload.items()) if path!='manifest-sha256.txt').encode()
    if payload.get('manifest-sha256.txt')!=manifest or sha(payload.get('original.zip',b''))!=sip['sha256']:
        raise PackageError('Preparation evidence manifest/source mismatch')
    if schemas is not None:validate_metadata(payload,schemas/'preservation')
    from .packages import xml
    index=json.loads(payload['history.json'])
    expected=[{k:v for k,v in r.items() if k!='evidence_sha256'} for r in records]
    from . import preparation_notes as notes
    from . import metadata_imports
    from . import content_editing
    imported=metadata_imports.evidence_sources(records)
    if {path:content for path,content in payload.items() if path.startswith('imports/')}!=imported:
        raise PackageError('Metadata import source evidence differs from retained originals')
    content_archives=content_editing.evidence_archives(records,payload)
    expected_notes = canonical(notes.document(records)) if any(r['kind'] in notes.KINDS for r in records) else None
    if payload.get('notes.json') != expected_notes:
        raise PackageError('Preparation note snapshot differs from retained history')
    if (index['operations']!=expected or index['head']!=records[-1]['id'] or index['metadata_sha256']!=records[-1]['after_metadata_sha256']
            or index['sip_id']!=sip['id'] or index['source_sha256']!=sip['sha256'] or index['vocabulary']!=VOCABULARY
            or index['production_acceptance'] is not False):
        raise PackageError('Preparation evidence history mismatch')
    namespaces={'m':M,'p':P}
    mets=xml(payload['metadata/mets.xml']);premis=xml(payload['metadata/premis.xml'])
    listed={}
    for file in mets.findall('.//m:file',namespaces):
        location=file.find('m:FLocat',namespaces)
        path=resolve_href(location.get('{'+X+'}href'))
        if path in listed or path not in payload or file.get('CHECKSUMTYPE')!='SHA-256' or file.get('CHECKSUM')!=sha(payload[path]) or file.get('SIZE')!=str(len(payload[path])):
            raise PackageError('Preparation METS file binding mismatch')
        listed[path]=file.get('ID')
    if set(listed)!=set(payload)-{'metadata/mets.xml','manifest-sha256.txt'}:
        raise PackageError('Preparation METS inventory is incomplete')
    if [f.get('FILEID') for f in mets.findall('.//m:fptr',namespaces)]!=list(listed.values()):
        raise PackageError('Preparation METS structure differs from file inventory')
    references=[r.get('{'+X+'}href') for r in mets.findall('m:dmdSec/m:mdRef',namespaces)]
    if references!=['../versions/'+records[-1]['after_metadata_sha256']+'.xml']:
        raise PackageError('Preparation METS current-version binding mismatch')
    if [r.get('{'+X+'}href') for r in mets.findall('m:amdSec/m:digiprovMD/m:mdRef',namespaces)]!=['premis.xml']:
        raise PackageError('Preparation METS provenance binding mismatch')
    expected_contents={sha(content):content for content in imported.values()}
    expected_contents.update(content_archives)
    expected_contents.update({sip['sha256']:payload.get('original.zip')})
    expected_contents.update({record[key]:payload.get('versions/'+record[key]+'.xml')
                             for record in records for key in ('before_metadata_sha256','after_metadata_sha256')})
    if schemas is not None and any(r.get('version')==10 for r in records):
        from . import editor_fields as fields, hierarchy_editing as hierarchy
        identities=fields.identity_snapshots(records)
        info=fields.schema(schemas,sip)
        for record in records:
            if record.get('version')==10:
                before,after=(expected_contents[record[k]] for k in ('before_metadata_sha256','after_metadata_sha256'))
                if before is None or after is None:
                    raise PackageError('Missing editor identity XML snapshot')
                hierarchy.verify(before,after,record,info,schemas,sip,identities[record['id']][0])
    if schemas is not None and content_archives:
        versions=content_editing.snapshots(records,sip['sha256'])
        for record in records:
            if record['kind']==content_editing.KIND:
                content_editing.verify(record,versions[record['id']][0],expected_contents.__getitem__,sip,schemas)
    expected_objects=set(expected_contents)
    objects=premis.findall('p:object',namespaces)
    actual_objects=set()
    for obj in objects:
        ident=obj.findtext('p:objectIdentifier/p:objectIdentifierValue',namespaces=namespaces)
        digest=ident.removeprefix('urn:sha256:') if ident else ''
        content=expected_contents.get(digest)
        if (digest in actual_objects or digest not in expected_objects or content is None
                or obj.get('{'+I+'}type')!='premis:file'
                or obj.findtext('p:objectCharacteristics/p:fixity/p:messageDigestAlgorithm',namespaces=namespaces)!='SHA-256'
                or obj.findtext('p:objectCharacteristics/p:fixity/p:messageDigest',namespaces=namespaces)!=sha(content)
                or obj.findtext('p:objectCharacteristics/p:size',namespaces=namespaces)!=str(len(content))):
            raise PackageError('Preparation PREMIS object fixity/coverage mismatch')
        actual_objects.add(digest)
    if actual_objects!=expected_objects:raise PackageError('Preparation PREMIS object coverage is incomplete')
    events=premis.findall('p:event',namespaces)
    if len(events)!=len(records):raise PackageError('Preparation PREMIS event coverage mismatch')
    agents={e.findtext('p:agentIdentifier/p:agentIdentifierValue',namespaces=namespaces) for e in premis.findall('p:agent',namespaces)}
    if agents!={r['actor'] for r in records}:raise PackageError('Preparation PREMIS agent mismatch')
    for record,ev in zip(records,events):
        descriptor=payload['changes/'+record['id']+'.json']
        if sha(descriptor)!=record['changes_sha256']:raise PackageError('Preparation change evidence mismatch')
        if (ev.findtext('p:eventIdentifier/p:eventIdentifierValue',namespaces=namespaces)!=record['id']
                or ev.findtext('p:eventType',namespaces=namespaces)!=VOCABULARY+':'+record['kind']
                or ev.findtext('p:eventDateTime',namespaces=namespaces)!=record['created']
                or ev.findtext('p:eventOutcomeInformation/p:eventOutcome',namespaces=namespaces)!='success'
                or ev.findtext('p:linkingAgentIdentifier/p:linkingAgentIdentifierValue',namespaces=namespaces)!=record['actor']):
            raise PackageError('Preparation PREMIS event binding mismatch')
        detail=json.loads(ev.findtext('p:eventDetailInformation/p:eventDetail',namespaces=namespaces))
        if detail!={k:record[k] for k in ('changes_sha256','actor','reason','changes','before_metadata_sha256','after_metadata_sha256','predecessor_id')}:
            raise PackageError('Preparation PREMIS before/after evidence differs from journal')
        linked={e.findtext('p:linkingObjectIdentifierValue',namespaces=namespaces) for e in ev.findall('p:linkingObjectIdentifier',namespaces)}
        source_digest=metadata_imports.operation_source(record,records)
        expected_linked={'urn:sha256:'+d for d in (sip['sha256'],record['before_metadata_sha256'],record['after_metadata_sha256'])}
        if source_digest:expected_linked.add('urn:sha256:'+source_digest)
        if record['kind']==content_editing.KIND:
            expected_linked.update('urn:sha256:'+record['changes']['content'][key]
                                   for key in ('before_archive_sha256','after_archive_sha256'))
        if linked!=expected_linked:
            raise PackageError('Preparation PREMIS object scope mismatch')
        for digest in (record['before_metadata_sha256'],record['after_metadata_sha256']):
            if sha(payload.get('versions/'+digest+'.xml',b''))!=digest:raise PackageError('Preparation snapshot fixity mismatch')
