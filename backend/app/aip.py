"""Portable preservation package candidates for the current application profiles."""
from __future__ import annotations
import io,json,hashlib,posixpath,re,uuid,zipfile
from pathlib import Path
from urllib.parse import quote,unquote,urlsplit
from lxml import etree as ET
from .packages import sha,xml,members,NS,PROFILES,PackageError,ech_schema_dir
from .aip_source import source_description,file_refs,file_identifier,stable,urn,inventory
from .aip_context import PreservationContext,SourceRights
from .metadata_validation import validate_metadata

M='http://www.loc.gov/METS/';P='http://www.loc.gov/premis/v3';X='http://www.w3.org/1999/xlink';I='http://www.w3.org/2001/XMLSchema-instance'
N={'m':M,'p':P,'x':X};PROFILE_ID='urn:ch-archive:aip-profile:3.0.0';PROFILE_VERSION='3.0.0'

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def el(parent,ns,name,value=None,**attrs):
    node=ET.SubElement(parent,'{'+ns+'}'+name,**attrs)
    if value is not None:node.text=str(value)
    return node

def profile_root(schemas,version=PROFILE_VERSION):
    if version not in ('3.0.0','4.0.0','5.0.0'):raise PackageError('Unknown AIP profile version')
    return schemas.parent/'profiles/ch-aip'/version
def profile_definition(schemas,version=PROFILE_VERSION):return (profile_root(schemas,version)/'profile.json').read_bytes()
def profile_bundle(schemas,version=PROFILE_VERSION):
    result={'representation/profile/'+p.name:p.read_bytes() for p in sorted(profile_root(schemas,version).glob('*.json'))}
    for directory in [schemas/'preservation']+[ech_schema_dir(schemas,v) for v in PROFILES]:
        for path in sorted(directory.glob('*.xsd')):result['representation/profile/schemas/'+path.relative_to(schemas).as_posix()]=path.read_bytes()
    return result

def context_for(transfer,selected):
    context=PreservationContext.model_validate(transfer.get('aip_context',{})).model_dump(mode='json')
    keys={(s['id'],fid) for s in selected.values() for fid in s['selected_files']}
    rights={r['sip_id']:r for r in context['rights']}
    context['rights']=[rights.get(sid,SourceRights(sip_id=sid).model_dump(mode='json')) for sid in sorted(selected)]
    context['formats']=[f for f in context['formats'] if (f['sip_id'],f['file_id']) in keys]
    return context

def selected_sources(transfer,target):
    sources={}
    for item in target['units']:
        s=next(s for s in transfer['sips'] if s['id']==item['sip_id']);unit=next(u for u in s['units'] if u['id']==item['unit_id'])
        entry=sources.setdefault(s['id'],{**s,'selected_units':[],'selected_files':set()})
        entry['selected_units'].append(unit['id']);entry['selected_files'].update(unit['files'])
    return sources

def target_context_hash(transfer,target):return sha(canonical(context_for(transfer,selected_sources(transfer,target))))

def target_resource_bindings(transfer,target):
    context=context_for(transfer,selected_sources(transfer,target))
    used={rid for r in context['rights'] for rid in r['resource_ids']}|{rid for f in context['formats'] for rid in f['resource_ids']}
    return {r['id']:r['sha256'] for r in transfer.get('representation_resources',[]) if r['id'] in used}


def file_id(path):return 'F'+sha(path.encode())
def representation_id(aip_id,version):return urn(uuid.uuid5(uuid.UUID(aip_id),'representation:'+str(version)))
def right_id(sid,context_hash):return stable(sid,'rights',context_hash)

def readiness(context,objects,resources):
    reasons=[];available={r['id'] for r in resources}
    if not context['archive_identifier'].strip() or not context['archive_name'].strip():reasons.append('Archival institution identity is missing')
    if not context['designated_community'].strip() or not context['knowledge_base'].strip():reasons.append('Designated community and assumed knowledge are not documented')
    rights={r['sip_id']:r for r in context['rights']};formats={(f['sip_id'],f['file_id']):f for f in context['formats']}
    for sid in sorted(rights):
        r=rights.get(sid,{})
        if r.get('status')!='assessed' or r.get('basis')=='unassessed' or not r.get('statement','').strip() or not r.get('source_restrictions_reviewed'):
            reasons.append(sid+': source preservation rights/restrictions are unassessed')
        if r.get('access')=='unassessed':reasons.append(sid+': access restrictions are unassessed')
        if 'replicate' not in r.get('acts',[]):reasons.append(sid+': no documented replication grant')
        if not r.get('resource_ids') or not set(r.get('resource_ids',[]))<=available:reasons.append(sid+': rights documentation is missing')
        if r.get('term')=='unassessed' or not r.get('start_date') or (r.get('term')=='bounded' and not r.get('end_date')):reasons.append(sid+': grant term is unassessed')
        if r.get('basis') in ('statute','copyright') and not r.get('jurisdiction','').strip():reasons.append(sid+': rights jurisdiction missing')
        if r.get('basis')=='copyright' and not r.get('copyright_status','').strip():reasons.append(sid+': copyright status missing')
    for obj in objects:
        f=formats.get((obj['sip_id'],obj['source_id']))
        if not f:reasons.append(obj['object_id']+': format/representation information is unassessed');continue
        if not f['resource_ids'] or not set(f['resource_ids'])<=available:reasons.append(obj['object_id']+': representation documentation missing')
        if not f['significant_properties'] or any(not k.strip() or not v.strip() for k,v in f['significant_properties'].items()):reasons.append(obj['object_id']+': significant properties missing')
    return {'preservation_ready':not reasons,'reasons':reasons}

def logical_projection(description,object_map):
    def unit(node):
        if node['kind']=='file-reference':
            row=object_map[(node['_sip_id'],node['file_id'])]
            return {'type':'file-reference','id':row['object_id'],'label':node['file_id'],'order_label':node['attributes'].get('reihung',''),'members':[]}
        return {'type':node['kind'],'id':node['id'],'label':node['title'],'order_label':'','members':[unit({**child,'_sip_id':node['_sip_id']}) for child in node['members']]}
    result=[]
    for source in description['sources']:
        branches=[]
        for selected in source['units']:
            leaf=unit({**selected['unit'],'_sip_id':source['sip_id']})
            for ctx in reversed(selected['context']):leaf={'type':ctx['kind'],'id':ctx['id'],'label':ctx['title'],'order_label':'','members':[leaf]}
            branches.append(leaf)
        result.append({'type':'source-SIP','id':urn(source['sip_id']),'label':source['sip_id'],'order_label':'','members':branches})
    return result

def make_mets(descriptor,description,objects,payload):
    root=ET.Element('{'+M+'}mets',nsmap={'mets':M,'premis':P,'xlink':X},OBJID=urn(descriptor['aip_id']),LABEL=descriptor['label'],PROFILE=descriptor['profile_id'])
    header=el(root,M,'metsHdr',CREATEDATE=descriptor['created_at'],RECORDSTATUS='CANDIDATE');agent=el(header,M,'agent',ROLE='CREATOR',TYPE='OTHER',OTHERTYPE='software');el(agent,M,'name','Ingest Workbench AIP Builder 1.0.0')
    dmd=el(root,M,'dmdSec',ID='DMD1');el(dmd,M,'mdRef',MDTYPE='OTHER',OTHERMDTYPE='CH-ARCHIVAL-DESCRIPTION-JSON-1.0',LOCTYPE='URL',**{'{'+X+'}href':'description.json'})
    amd=el(root,M,'amdSec',ID='AMD1')
    for kind,ident in [('techMD','TECH1'),('rightsMD','RIGHTS1'),('digiprovMD','PROV1')]:
        section=el(amd,M,kind,ID=ident);el(section,M,'mdRef',MDTYPE='PREMIS',LOCTYPE='URL',**{'{'+X+'}href':'premis.xml'})
    section=el(root,M,'fileSec');groups={}
    for path,data in sorted(payload.items()):
        category='original-payload' if path.startswith('objects/') else 'metadata' if path.startswith('metadata/') or path=='aip.json' else 'preservation-evidence' if path.startswith('evidence/') else 'representation-information' if path.startswith('representation/') else 'integration-artifact'
        group=groups.get(category)
        if group is None:group=el(section,M,'fileGrp',USE=category);groups[category]=group
        file=el(group,M,'file',ID=file_id(path),SIZE=str(len(data)),CHECKSUMTYPE='SHA-256',CHECKSUM=sha(data),ADMID='AMD1')
        el(file,M,'FLocat',LOCTYPE='URL',**{'{'+X+'}href':quote('../'+path,safe='/')})
    logical=el(root,M,'structMap',TYPE='logical');base=el(logical,M,'div',TYPE='AIP',LABEL=descriptor['label'],CONTENTIDS=urn(descriptor['aip_id']),DMDID='DMD1',ADMID='AMD1')
    lookup={(o['sip_id'],o['source_id']):o for o in objects};by_object={o['object_id']:o for o in objects}
    def append(parent,node,order):
        attrs={'TYPE':node['type'],'LABEL':node['label'],'CONTENTIDS':node['id'],'ORDER':str(order),'DMDID':'DMD1'}
        if node['order_label']:attrs['ORDERLABEL']=node['order_label']
        div=el(parent,M,'div',**attrs)
        if node['type']=='file-reference':el(div,M,'fptr',FILEID=file_id(by_object[node['id']]['target_path']))
        for index,child in enumerate(node['members'],1):append(div,child,index)
    for index,node in enumerate(logical_projection(description,lookup),1):append(base,node,index)
    physical=el(root,M,'structMap',TYPE='physical');rep=el(physical,M,'div',TYPE='original-representation',CONTENTIDS=descriptor['representation_id'])
    for o in objects:el(rep,M,'fptr',FILEID=file_id(o['target_path']))
    return ET.tostring(root,xml_declaration=True,encoding='UTF-8',pretty_print=True)

def identifier(parent,kind,value):
    ident=el(parent,P,kind);el(ident,P,kind+'Type','URI');el(ident,P,kind+'Value',value);return ident

def relation(parent,kind,subtype,target):
    rel=el(parent,P,'relationship');el(rel,P,'relationshipType',kind);el(rel,P,'relationshipSubType',subtype);identifier(rel,'relatedObjectIdentifier',target)

def make_premis(descriptor,objects,context,events):
    root=ET.Element('{'+P+'}premis',nsmap={'premis':P,'xsi':I},version='3.0');rep_id=descriptor['representation_id'];formats={(f['sip_id'],f['file_id']):f for f in context['formats']}
    rep=el(root,P,'object',**{'{'+I+'}type':'premis:representation'});identifier(rep,'objectIdentifier',rep_id)
    for o in objects:relation(rep,'structural','has part',o['object_id'])
    for o in objects:
        obj=el(root,P,'object',**{'{'+I+'}type':'premis:file'});identifier(obj,'objectIdentifier',o['object_id'])
        extra=el(obj,P,'objectIdentifier');el(extra,P,'objectIdentifierType','eCH-0160 qualified source ID');el(extra,P,'objectIdentifierValue',o['sip_id']+':'+o['source_id'])
        fmt=formats.get((o['sip_id'],o['source_id']))
        if fmt:
            for name,value in sorted(fmt['significant_properties'].items()):prop=el(obj,P,'significantProperties');el(prop,P,'significantPropertiesType',name);el(prop,P,'significantPropertiesValue',value)
        char=el(obj,P,'objectCharacteristics');el(char,P,'compositionLevel',0)
        for algorithm,digest in [('SHA-256',o['sha256']),(o['original_algorithm'],o['original_digest'])]:
            if algorithm=='SHA-256' and digest==o['sha256'] and len(char.findall('{'+P+'}fixity')):continue
            fix=el(char,P,'fixity');el(fix,P,'messageDigestAlgorithm',algorithm);el(fix,P,'messageDigest',digest)
        el(char,P,'size',o['size']);format_node=el(char,P,'format');designation=el(format_node,P,'formatDesignation');el(designation,P,'formatName',fmt['format_name'] if fmt else 'unassessed')
        if fmt:
            if fmt['format_version']:el(designation,P,'formatVersion',fmt['format_version'])
            registry=el(format_node,P,'formatRegistry');el(registry,P,'formatRegistryName',fmt['registry_name']);el(registry,P,'formatRegistryKey',fmt['registry_key'])
        el(obj,P,'originalName',o['source_path']);storage=el(obj,P,'storage');location=el(storage,P,'contentLocation');el(location,P,'contentLocationType','package-relative path');el(location,P,'contentLocationValue',o['target_path'])
        relation(obj,'structural','is part of',rep_id);relation(obj,'structural','received in',urn(o['sip_id']))
        identifier(obj,'linkingRightsStatementIdentifier',right_id(o['sip_id'],descriptor['context_sha256']))
    for source in descriptor['sources']:
        obj=el(root,P,'object',**{'{'+I+'}type':'premis:file'});identifier(obj,'objectIdentifier',urn(source['id']))
        char=el(obj,P,'objectCharacteristics');fix=el(char,P,'fixity');el(fix,P,'messageDigestAlgorithm','SHA-256');el(fix,P,'messageDigest',source['sha256']);el(char,P,'size',source['size'])
        prepared=source.get('prepared_id') is not None
        fmt=el(char,P,'format');des=el(fmt,P,'formatDesignation');el(des,P,'formatName','ZIP archive (frozen prepared eCH-0160 SIP)' if prepared else 'ZIP archive (received eCH-0160 SIP)')
        storage=el(obj,P,'storage');loc=el(storage,P,'contentLocation');el(loc,P,'contentLocationType','evidence-vault reference');el(loc,P,'contentLocationValue','urn:sha256:'+source['sha256'])
        if prepared:
            original_id='urn:sha256:'+source['source_sha256']
            relation(obj,'derivation','prepared from submitted SIP',original_id)
            received=el(root,P,'object',**{'{'+I+'}type':'premis:file'});identifier(received,'objectIdentifier',original_id)
            received_char=el(received,P,'objectCharacteristics');fix=el(received_char,P,'fixity');el(fix,P,'messageDigestAlgorithm','SHA-256');el(fix,P,'messageDigest',source['source_sha256']);el(received_char,P,'size',source['source_size'])
            el(el(el(received_char,P,'format'),P,'formatDesignation'),P,'formatName','ZIP archive (received eCH-0160 SIP)')
    agents={}
    for e in events:
        ev=el(root,P,'event');ident=el(ev,P,'eventIdentifier');el(ident,P,'eventIdentifierType','UUID');el(ident,P,'eventIdentifierValue',e['id'])
        el(ev,P,'eventType','package creation' if e['type']=='package-creation' else 'workflow '+e['type']);el(ev,P,'eventDateTime',e['time'])
        detail=el(ev,P,'eventDetailInformation');el(detail,P,'eventDetail',canonical(e['detail']).decode());out=el(ev,P,'eventOutcomeInformation');el(out,P,'eventOutcome',e['outcome'])
        actor=el(ev,P,'linkingAgentIdentifier');el(actor,P,'linkingAgentIdentifierType','OIDC subject');el(actor,P,'linkingAgentIdentifierValue',e['actor'])
        agents[e['actor']]=agents.get(e['actor'],'person')
        identifier(ev,'linkingObjectIdentifier',rep_id)
        applicable=e['detail'].get('sip_id')
        linked=[o for o in objects if not applicable or o['sip_id']==applicable]
        for o in linked:identifier(ev,'linkingObjectIdentifier',o['object_id'])
    for actor,kind in sorted(agents.items()):
        agent=el(root,P,'agent');ident=el(agent,P,'agentIdentifier');el(ident,P,'agentIdentifierType','OIDC subject');el(ident,P,'agentIdentifierValue',actor);el(agent,P,'agentName',actor);el(agent,P,'agentType',kind)
    rights=el(root,P,'rights')
    resource_map={r['id']:r for r in descriptor['resources']}
    for r in context['rights']:
        statement=el(rights,P,'rightsStatement');identifier(statement,'rightsStatementIdentifier',right_id(r['sip_id'],descriptor['context_sha256']))
        assessed=r['status']=='assessed';basis=r['basis'] if assessed and r['basis']!='unassessed' else 'other';el(statement,P,'rightsBasis',basis)
        if basis=='statute':
            info=el(statement,P,'statuteInformation');el(info,P,'statuteJurisdiction',r['jurisdiction'] or 'unassessed');el(info,P,'statuteCitation',r['statement'] or 'unassessed')
        elif basis=='copyright':
            info=el(statement,P,'copyrightInformation');el(info,P,'copyrightStatus',r['copyright_status'] or 'unassessed');el(info,P,'copyrightJurisdiction',r['jurisdiction'] or 'unassessed');el(info,P,'copyrightNote',r['statement'] or 'unassessed')
        elif basis=='license':
            info=el(statement,P,'licenseInformation');el(info,P,'licenseTerms',r['statement'] or 'unassessed')
        else:
            info=el(statement,P,'otherRightsInformation');el(info,P,'otherRightsBasis','documented archival policy' if assessed else 'unassessed');el(info,P,'otherRightsNote',r['statement'] or 'No preservation grant is asserted.')
        for resource_id in r['resource_ids']:
            if resource_id in resource_map:
                # A note uses a content-addressed documentation reference without implying a legal instrument type.
                note={'statute':'statuteNote','copyright':'copyrightNote','license':'licenseNote','other':'otherRightsNote'}[basis]
                el(info,P,note,'Documentation: urn:sha256:'+resource_map[resource_id]['sha256'])
        if assessed:
            for act in r['acts']:
                granted=el(statement,P,'rightsGranted');el(granted,P,'act',act);el(granted,P,'restriction','Access classification: '+r['access'])
                if r['start_date']:
                    term=el(granted,P,'termOfGrant');el(term,P,'startDate',r['start_date'])
                    if r['end_date']:el(term,P,'endDate',r['end_date'])
                el(granted,P,'rightsGrantedNote','Grant term: '+r['term']+'; source restrictions reviewed: '+str(r['source_restrictions_reviewed']))
        for o in objects:
            if o['sip_id']==r['sip_id']:identifier(statement,'linkingObjectIdentifier',o['object_id'])
    return ET.tostring(root,xml_declaration=True,encoding='UTF-8',pretty_print=True)

def assemble(service,transfer,target,aip_id,version,previous,created_at,events):
    prepared=transfer['plan'].get('format')==2
    profile_version='5.0.0' if transfer.get('approval',{}).get('review_required') is False else '4.0.0' if prepared else PROFILE_VERSION
    profile_id='urn:ch-archive:aip-profile:'+profile_version
    original_transfer=transfer
    if prepared:
        from .prepared_aip import project, evidence_payload
        transfer=project(service,transfer)
    sources=selected_sources(transfer,target);payload=profile_bundle(service.schemas,profile_version);objects=[];source_records=[];descriptions=[]
    if prepared:payload.update(evidence_payload(service,original_transfer))
    for sid,s in sources.items():
        original=service.blob(s['sha256']);files=members(original);metadata=files[s['metadata_path']]
        source_records.append({'id':sid,'profile':s['profile'],'sha256':s['sha256'],'size':len(original),'original_metadata_path':s['metadata_path'],
                               'metadata_path':'metadata/source/'+sid+'.xml','metadata_sha256':sha(metadata),'unit_ids':s['selected_units']})
        if prepared:
            binding=next(i for i in transfer['plan']['inputs'] if i['sip_id']==sid)
            received=next(i for i in original_transfer['sips'] if i['id']==sid)
            source_records[-1].update(source_sha256=binding['source_sha256'],source_size=len(service.blob(received['sha256'])),prepared_id=binding['prepared_id'])
        descriptions.append(source_description(metadata,sid,s['selected_units']));payload['metadata/source/'+sid+'.xml']=metadata
        payload['evidence/sources/'+sid+'.json']=canonical({'sip_id':sid,'sha256':s['sha256'],'original_evidence_uri':'urn:sha256:'+s['sha256'],'retention':'original SIP retained separately in evidence vault'})
        for fid in sorted(s['selected_files']):
            f=s['files'][fid];oid=file_identifier(sid,fid,f['sha256']);path='objects/'+sid+'/'+oid.removeprefix('urn:uuid:')+'/'+Path(f['path']).name
            data=files[f['path']]
            if sha(data)!=f['sha256']:raise PackageError('Input fixity changed')
            payload[path]=data;objects.append({'sip_id':sid,'source_id':fid,'source_path':f['path'],'target_path':path,'object_id':oid,'sha256':f['sha256'],'size':len(data),'original_algorithm':f['original_algorithm'],'original_digest':f['original_digest']})
    context=context_for(transfer,sources);used={rid for r in context['rights'] for rid in r['resource_ids']}|{rid for f in context['formats'] for rid in f['resource_ids']};resources=[]
    for resource in transfer.get('representation_resources',[]):
        if resource['id'] in used:
            record={**resource,'path':'representation/resources/'+resource['id']+'.bin'};resources.append(record);payload[record['path']]=service.blob(record['sha256'])
    descriptor={'profile_id':profile_id,'profile_version':profile_version,'profile_sha256':sha(profile_definition(service.schemas,profile_version)),'status':'CANDIDATE','archive_scope':transfer['tenant'],
                'aip_id':aip_id,'version':version,'version_id':str(uuid.uuid5(uuid.UUID(aip_id),'package-version:'+str(version))), 'representation_id':representation_id(aip_id,version),
                'previous_version':previous,'label':target['label'],'created_at':created_at,'sources':source_records,'resources':resources,
                'plan_sha256':transfer['plan']['sha256'],'context_sha256':sha(canonical(context)),'approval':transfer['approval']}
    if prepared:descriptor['preparation_snapshot_sha256']=sha(payload['evidence/preparation/snapshot.json'])
    description={'profile':'CH-ARCHIVAL-DESCRIPTION/1.0','aip_id':aip_id,'sources':descriptions}
    payload.update({'aip.json':canonical(descriptor),'metadata/description.json':canonical(description),'metadata/lineage.json':canonical({'aip_id':aip_id,'plan':transfer['plan'],'files':objects}),
                    'metadata/preservation-context.json':canonical(context),'metadata/representation.json':canonical({'representation_id':descriptor['representation_id'],'role':'original','objects':objects,'assessments':context['formats'],'resources':resources}),
                    'evidence/events.json':canonical(events)})
    payload['metadata/premis.xml']=make_premis(descriptor,objects,context,events)
    payload['metadata/mets.xml']=make_mets(descriptor,description,objects,payload)
    validate_metadata(payload,service.schemas/'preservation')
    payload['manifest-sha256.txt']=''.join(sha(data)+'  '+path+'\n' for path,data in sorted(payload.items())).encode()
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for path,data in sorted(payload.items()):z.writestr(path,data)
    return out.getvalue(),descriptor,objects
