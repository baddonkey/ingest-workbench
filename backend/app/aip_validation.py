"""Offline AIP conformance checks. No network calls and no reliance on a packaged 'valid' flag."""
from __future__ import annotations
import hashlib,json,posixpath,re,uuid
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import unquote,quote,urlsplit
from lxml import etree as ET
from .packages import members,xml,sha,PackageError,PROFILES,NS,ech_schema_dir
from .aip import (PROFILE_ID,canonical,profile_bundle,profile_definition,file_id,representation_id,right_id,readiness,logical_projection)
from .aip_source import source_description,inventory,file_refs,nodes,file_identifier,urn
from .aip_context import PreservationContext
from .aip_models import Descriptor,Lineage
from .metadata_validation import validate_metadata
M='http://www.loc.gov/METS/';P='http://www.loc.gov/premis/v3';X='http://www.w3.org/1999/xlink';I='http://www.w3.org/2001/XMLSchema-instance';N={'m':M,'p':P,'x':X}
def require(condition,message):
    if not condition:raise PackageError(message)

def json_file(files,name):
    value=json.loads(files[name]);require(files[name]==canonical(value),'Non-canonical JSON: '+name);return value

def resolve_href(href):
    parsed=urlsplit(href);require(not parsed.scheme and not parsed.netloc and not parsed.query and not parsed.fragment,'External/nonlocal METS URI')
    decoded=unquote(href);require(quote(decoded,safe='/')==href,'Noncanonical METS URI encoding')
    path=posixpath.normpath('metadata/'+decoded);require(not path.startswith('../') and not path.startswith('/'),'METS URI escapes package');return path

def validate_package(data,schemas,expected_sha256=None,source_resolver=None):
    result={'profile_id':PROFILE_ID,'structural_valid':False,'preservation_ready':False,'errors':[],'readiness_reasons':[],
            'package_sha256':sha(data),'external_digest_checked':expected_sha256 is not None,'original_sips_verified':0,'authenticity':'No signature or remote-attestation claim'}
    try:
        if expected_sha256 is not None:require(sha(data)==expected_sha256,'External package SHA-256 mismatch')
        files=members(data)
        identity=json.loads(files['aip.json'])
        profile_version=identity.get('profile_version')
        require(profile_version in ('3.0.0','4.0.0'),'Unknown AIP profile version')
        prepared=profile_version=='4.0.0';profile_id='urn:ch-archive:aip-profile:'+profile_version
        result['profile_id']=profile_id
        definition=json.loads(profile_definition(schemas,profile_version))
        require(set(definition['required_files'])<=set(files),'Required package member missing')
        manifest={}
        for line in files['manifest-sha256.txt'].decode('utf-8').splitlines():
            digest,path=line.split('  ',1);require(re.fullmatch('[0-9a-f]{64}',digest) is not None and path not in manifest,'Invalid/duplicate manifest entry');manifest[path]=digest
        require(set(manifest)==set(files)-{'manifest-sha256.txt'},'Manifest does not cover exactly every other member')
        for path,digest in manifest.items():require(sha(files[path])==digest,'Manifest fixity failure: '+path)
        for path,expected in profile_bundle(schemas,profile_version).items():require(files.get(path)==expected,'Packaged profile/schema differs from trusted reference: '+path)
        descriptor=json_file(files,'aip.json');context=json_file(files,'metadata/preservation-context.json');description=json_file(files,'metadata/description.json')
        lineage=json_file(files,'metadata/lineage.json');representation=json_file(files,'metadata/representation.json');events=json_file(files,'evidence/events.json')
        if prepared:
            from .aip_models_v2 import PreparationDescriptor,PreparedLineage
            PreparationDescriptor.model_validate(descriptor);PreparedLineage.model_validate(lineage)
        else:
            Descriptor.model_validate(descriptor);Lineage.model_validate(lineage)
        require(descriptor['profile_id']==profile_id and descriptor['profile_sha256']==sha(profile_definition(schemas,profile_version)),'Profile identity/fingerprint mismatch')
        require(descriptor['status']=='CANDIDATE','Unverified archival-acceptance assertion in package')
        aip_id=str(uuid.UUID(descriptor['aip_id']));version=descriptor['version'];require(type(version)is int and version>=1,'Invalid package version')
        require(descriptor['version_id']==str(uuid.uuid5(uuid.UUID(aip_id),'package-version:'+str(version))),'Version identity mismatch')
        require(descriptor['representation_id']==representation_id(aip_id,version),'Representation identity mismatch')
        previous=descriptor['previous_version']
        if version==1:require(previous is None,'Initial version cannot claim a predecessor')
        else:require(previous is not None and previous['version']==version-1 and previous['version_id']==str(uuid.uuid5(uuid.UUID(aip_id),'package-version:'+str(version-1))) and re.fullmatch('[0-9a-f]{64}',previous['sha256']),'Invalid predecessor version reference')
        created=datetime.fromisoformat(descriptor['created_at']);require(created.tzinfo is not None and created.utcoffset().total_seconds()==0,'Creation timestamp must be UTC')
        normalized=PreservationContext.model_validate(context).model_dump(mode='json');require(context==normalized,'Preservation context shape/defaults mismatch')
        require(sha(canonical(context))==descriptor['context_sha256'],'Context digest mismatch')
        plan=lineage['plan'];require(sha(canonical({k:v for k,v in plan.items() if k!='sha256'}))==plan['sha256']==descriptor['plan_sha256'],'Reviewed plan digest mismatch')
        require(lineage['aip_id']==aip_id and description['aip_id']==aip_id,'Cross-file AIP identity mismatch')
        targets=[t for t in plan['targets'] if t['aip_id']==aip_id];require(len(targets)==1 and targets[0]['label']==descriptor['label'],'Target AIP boundary mismatch')
        target=targets[0];approval=descriptor['approval'];require(approval['author']==plan['author'] and approval['author']!=approval['approver'] and approval.get('context_author')!=approval['approver'],'Two-person mapping/context approval missing')
        require(approval.get('profile_sha256')==descriptor['profile_sha256'] and approval['plan_sha256']==plan['sha256'] and approval['target_contexts'][aip_id]==descriptor['context_sha256'],'Approval does not bind this plan/context')
        prev='0'*64;event_ids=set()
        for event in events:
            require(event['id'] not in event_ids,'Duplicate event ID');event_ids.add(event['id'])
            require(event['previous']==prev and sha(canonical({k:v for k,v in event.items() if k!='hash'}))==event['hash'],'Audit event chain is invalid');prev=event['hash']
            when=datetime.fromisoformat(event['time']);require(when.tzinfo is not None and when.utcoffset().total_seconds()==0,'Event timestamp is not UTC')
        require(any(e['type']=='authorization' and e['detail']==approval for e in events),'Approval event absent from evidence chain')
        require(events[-1]['type']=='package-creation' and events[-1]['detail']['aip_id']==aip_id and events[-1]['detail']['version']==version,'Package sealing event mismatch')
        objects=lineage['files'];keys=[(o['sip_id'],o['source_id']) for o in objects];require(len(keys)==len(set(keys)),'Duplicate logical file identity')
        require(len({o['object_id'] for o in objects})==len(objects) and len({o['target_path'] for o in objects})==len(objects),'Duplicate target identity/path')
        require({p for p in files if p.startswith('objects/')}=={o['target_path'] for o in objects},'Payload coverage mismatch')
        by_key=dict(zip(keys,objects));source_records={s['id']:s for s in descriptor['sources']};require(len(source_records)==len(descriptor['sources']),'Duplicate source SIP identity')
        expected_sips={u['sip_id'] for u in target['units']};require(set(source_records)==expected_sips,'Source SIP set differs from target plan')
        projected=[];expected_keys=set();allowed=set(definition['required_files'])|set(profile_bundle(schemas,profile_version))|{o['target_path'] for o in objects}
        dossier=None
        if prepared:
            from .prepared_aip import verify_evidence,project
            from types import SimpleNamespace
            dossier,dossier_blob,evidence_paths=verify_evidence(files,descriptor,plan,schemas)
            allowed|=evidence_paths
            selected_projection=project(SimpleNamespace(blob=dossier_blob,schemas=schemas),dossier)
            from .aip import context_for,selected_sources
            require(context==context_for(selected_projection,selected_sources(selected_projection,target)),'Prepared target context differs from reviewed snapshot')
            require(any(e['type']=='prepared-input-review' and e['detail']==r for e in events for r in dossier['prepared_reviews'] if r['id']==approval['review_id']),'Independent preparation review event missing')
        for sid,s in source_records.items():
            if prepared:
                binding=next(i for i in plan['inputs'] if i['sip_id']==sid)
                original_source=next(i for i in dossier['sips'] if i['id']==sid)
                require(s['sha256']==binding['input_sha256'] and s['source_sha256']==binding['source_sha256'] and s['prepared_id']==binding['prepared_id'] and s['size']==binding['size'] and s['source_size']==len(dossier_blob(s['source_sha256'])),'Selected/source SIP lineage mismatch')
                require(s['profile']==binding['profile'] and s['original_metadata_path']==original_source['metadata_path'],'Selected input profile/path mismatch')
                require(members(dossier_blob(s['sha256']))[s['original_metadata_path']]==files[s['metadata_path']],'Candidate metadata differs from selected input bytes')
            uuid.UUID(sid);require(s['profile'] in PROFILES,'Unsupported source profile')
            require(s['metadata_path']=='metadata/source/'+sid+'.xml','Source metadata path mismatch');metadata=files[s['metadata_path']]
            require(sha(metadata)==s['metadata_sha256'],'Source metadata digest mismatch')
            root=xml(metadata);require(root.get('schemaVersion')==PROFILES[s['profile']],'Source profile/schemaVersion mismatch')
            schema=ET.XMLSchema(ET.parse(str(ech_schema_dir(schemas,s['profile'])/'arelda.xsd'),ET.XMLParser(resolve_entities=False,no_network=True)))
            require(schema.validate(root),'Source metadata no longer validates against its trusted schema')
            selected={u['unit_id'] for u in target['units'] if u['sip_id']==sid};require(set(s['unit_ids'])==selected and len(s['unit_ids'])==len(selected),'Selected dossier set differs from plan')
            projection=source_description(metadata,sid,selected);projected.append(projection)
            entries=inventory(root,s['original_metadata_path'][:-len('header/metadata.xml')])
            selected_refs=[r for unit in projection['units'] for r in file_refs(unit['unit'])]
            selected_files={r['file_id'] for r in selected_refs};selected_nodes={n['source_id'] for unit in projection['units'] for n in nodes(unit['unit']) if n['source_id']}
            context_nodes={c['source_id'] for unit in projection['units'] for c in unit['context'] if c['source_id']}
            for ref in selected_refs:
                for attr in ('information','repraesentation'):
                    related=ref['attributes'].get(attr)
                    if related:require(related in selected_files|selected_nodes|context_nodes,'Cross-target source relation would be lost')
            for fid in selected_files:
                key=(sid,fid);expected_keys.add(key);require(key in by_key and fid in entries,'Source file lineage missing');o=by_key[key];entry=entries[fid];content=files[o['target_path']]
                require(o['source_path']==entry['path'],'Source inventory path differs from lineage')
                require(sha(content)==o['sha256'] and len(content)==o['size'],'Payload digest/size differs from lineage')
                algorithm={'MD5':'md5','SHA-1':'sha1','SHA-256':'sha256','SHA-512':'sha512'}.get(entry['algorithm']);require(algorithm is not None,'Unsupported original fixity algorithm')
                digest=hashlib.new(algorithm,content,usedforsecurity=False).hexdigest();require(digest.lower()==entry['digest'].lower()==o['original_digest'].lower() and o['original_algorithm']==entry['algorithm'],'Payload differs from original submitted fixity')
                require(o['object_id']==file_identifier(sid,fid,o['sha256']),'Stable file identity mismatch')
                expected_path='objects/'+sid+'/'+o['object_id'].removeprefix('urn:uuid:')+'/'+Path(entry['path']).name;require(o['target_path']==expected_path,'Noncanonical payload path')
            evidence_path='evidence/sources/'+sid+'.json';evidence=json_file(files,evidence_path);require(evidence['sip_id']==sid and evidence['sha256']==s['sha256'] and evidence['original_evidence_uri']=='urn:sha256:'+s['sha256'],'Source evidence-vault reference mismatch')
            allowed|={s['metadata_path'],evidence_path}
            if source_resolver:
                original_digest=s['source_sha256'] if prepared else s['sha256']
                original=source_resolver(original_digest);require(sha(original)==original_digest and len(original)==(s['source_size'] if prepared else s['size']),'Original evidence SIP digest/size mismatch')
                expected_metadata=members(dossier_blob(original_digest))[s['original_metadata_path']] if prepared else metadata
                require(members(original)[s['original_metadata_path']]==expected_metadata,'Source metadata is not byte-exact to original SIP');result['original_sips_verified']+=1
        require(set(keys)==expected_keys,'Selected source object coverage differs from lineage')
        expected_description={'profile':'CH-ARCHIVAL-DESCRIPTION/1.0','aip_id':aip_id,'sources':projected};require(description==expected_description,'Archival hierarchy/context differs from source metadata')
        require(representation=={'representation_id':descriptor['representation_id'],'role':'original','objects':objects,'assessments':context['formats'],'resources':descriptor['resources']},'Representation information differs across metadata')
        require(approval['target_resources'][aip_id]=={r['id']:r['sha256'] for r in descriptor['resources']},'Approval does not bind documentation resource bytes')
        resources={r['id']:r for r in descriptor['resources']};require(len(resources)==len(descriptor['resources']),'Duplicate documentation resource ID')
        for rid,r in resources.items():
            uuid.UUID(rid);require(r['path']=='representation/resources/'+rid+'.bin','Resource path mismatch');require(sha(files[r['path']])==r['sha256'] and len(files[r['path']])==r['size'],'Representation resource fixity mismatch');allowed.add(r['path'])
        require({r['sip_id'] for r in context['rights']}==expected_sips and len(context['rights'])==len(expected_sips),'Rights coverage mismatch')
        formats={(f['sip_id'],f['file_id']):f for f in context['formats']};require(len(formats)==len(context['formats']) and set(formats)<=set(keys),'Unknown/duplicate format object')
        used={rid for r in context['rights'] for rid in r['resource_ids']}|{rid for f in context['formats'] for rid in f['resource_ids']};require(used==set(resources),'Documentation resource coverage mismatch')
        require(set(files)==allowed,'Unexpected or missing package members')
        validate_metadata(files,schemas/'preservation')
        mets=xml(files['metadata/mets.xml']);premis=xml(files['metadata/premis.xml'])
        require(mets.get('OBJID')==urn(aip_id) and mets.get('PROFILE')==profile_id,'METS profile/object identity mismatch')
        listed={}
        for file in mets.findall('m:fileSec/m:fileGrp/m:file',N):
            locations=file.findall('m:FLocat',N);require(len(locations)==1,'METS location count mismatch');path=resolve_href(locations[0].get('{'+X+'}href'))
            require(path not in listed and path in files,'METS duplicate or dangling file location');listed[path]=file.get('ID')
            require(file.get('ID')==file_id(path) and file.get('CHECKSUMTYPE')=='SHA-256' and file.get('CHECKSUM')==sha(files[path]) and int(file.get('SIZE'))==len(files[path]),'METS file fixity/identity mismatch')
        require(set(listed)==set(files)-{'metadata/mets.xml','manifest-sha256.txt'},'METS fileSec coverage mismatch')
        for mdref in mets.findall('.//m:mdRef',N):require(resolve_href(mdref.get('{'+X+'}href')) in files,'METS metadata link is dangling')
        require([r.get('{'+X+'}href') for r in mets.findall('m:dmdSec/m:mdRef',N)]==['description.json'],'METS descriptive metadata binding mismatch')
        require([r.get('{'+X+'}href') for r in mets.findall('m:amdSec/*/m:mdRef',N)]==['premis.xml']*3,'METS administrative metadata binding mismatch')
        logical=mets.findall('m:structMap[@TYPE="logical"]/m:div',N);physical=mets.findall('m:structMap[@TYPE="physical"]/m:div',N)
        require(len(logical)==len(physical)==1 and logical[0].get('CONTENTIDS')==urn(aip_id),'METS logical/physical roots mismatch')
        object_paths={o['object_id']:o['target_path'] for o in objects}
        def projected_div(div):
            kind=div.get('TYPE');oid=div.get('CONTENTIDS');pointers=div.findall('m:fptr',N)
            if kind=='file-reference':require(oid in object_paths and len(pointers)==1 and pointers[0].get('FILEID')==listed[object_paths[oid]],'Logical file pointer mismatch')
            else:require(not pointers,'Unexpected logical pointer')
            children=div.findall('m:div',N)
            for index,child in enumerate(children,1):require(child.get('ORDER')==str(index),'METS source order changed')
            return {'type':kind,'id':oid,'label':div.get('LABEL'),'order_label':div.get('ORDERLABEL',''),'members':[projected_div(c) for c in children]}
        actual=[projected_div(d) for d in logical[0].findall('m:div',N)];require(actual==logical_projection(description,by_key),'METS logical hierarchy differs from source projection')
        require(physical[0].get('CONTENTIDS')==descriptor['representation_id'],'METS physical representation ID mismatch')
        ptrs=[p.get('FILEID') for p in physical[0].findall('m:fptr',N)];require(len(ptrs)==len(objects) and set(ptrs)=={listed[o['target_path']] for o in objects},'Physical payload coverage mismatch')
        if prepared:
            from .aip import make_premis
            require(files['metadata/premis.xml']==make_premis(descriptor,objects,context,events),'Prepared PREMIS differs from bound source/derivation/event evidence')
        else:validate_premis(premis,descriptor,objects,context,events,files)
        ready=readiness(context,objects,descriptor['resources']);result.update(structural_valid=True,preservation_ready=ready['preservation_ready'],readiness_reasons=ready['reasons'],aip_id=aip_id,version=version,payload_files=len(objects),sources=len(source_records))
    except (PackageError,ValueError,KeyError,TypeError,IndexError,AttributeError,OSError,ET.XMLSchemaError) as exc:result['errors'].append(str(exc))
    return result


def validate_premis(root,descriptor,objects,context,events,files):
    def first_id(node,kind):return node.findtext('p:'+kind+'/p:'+kind+'Value',namespaces=N)
    entries=root.findall('p:object',N);by_id={first_id(n,'objectIdentifier'):n for n in entries};expected={descriptor['representation_id']}|{o['object_id'] for o in objects}|{urn(s['id']) for s in descriptor['sources']}
    require(len(by_id)==len(entries) and set(by_id)==expected,'PREMIS object coverage mismatch')
    def relations(obj):return {(r.findtext('p:relationshipType',namespaces=N),r.findtext('p:relationshipSubType',namespaces=N),r.findtext('p:relatedObjectIdentifier/p:relatedObjectIdentifierValue',namespaces=N)) for r in obj.findall('p:relationship',N)}
    require(by_id[descriptor['representation_id']].get('{'+I+'}type')=='premis:representation','PREMIS representation type mismatch')
    require(relations(by_id[descriptor['representation_id']])=={('structural','has part',o['object_id']) for o in objects},'PREMIS representation membership mismatch')
    formats={(f['sip_id'],f['file_id']):f for f in context['formats']}
    for o in objects:
        obj=by_id[o['object_id']];require(obj.get('{'+I+'}type')=='premis:file','PREMIS payload object type mismatch');char=obj.find('p:objectCharacteristics',N)
        fixities={(f.findtext('p:messageDigestAlgorithm',namespaces=N),f.findtext('p:messageDigest',namespaces=N)) for f in char.findall('p:fixity',N)}
        require(fixities=={('SHA-256',o['sha256']),(o['original_algorithm'],o['original_digest'])} and int(char.findtext('p:size',namespaces=N))==o['size'],'PREMIS fixity/size mismatch')
        require(obj.findtext('p:originalName',namespaces=N)==o['source_path'] and obj.findtext('p:storage/p:contentLocation/p:contentLocationValue',namespaces=N)==o['target_path'],'PREMIS source/storage path mismatch')
        require(relations(obj)=={('structural','is part of',descriptor['representation_id']),('structural','received in',urn(o['sip_id']))},'PREMIS source/representation relations mismatch')
        require(first_id(obj,'linkingRightsStatementIdentifier')==right_id(o['sip_id'],descriptor['context_sha256']),'PREMIS object rights linkage mismatch')
        fmt=formats.get((o['sip_id'],o['source_id']));require(char.findtext('p:format/p:formatDesignation/p:formatName',namespaces=N)==(fmt['format_name'] if fmt else 'unassessed'),'PREMIS format assertion differs from context')
        if fmt:
            require(char.findtext('p:format/p:formatRegistry/p:formatRegistryName',namespaces=N)==fmt['registry_name'] and char.findtext('p:format/p:formatRegistry/p:formatRegistryKey',namespaces=N)==fmt['registry_key'] and (char.findtext('p:format/p:formatDesignation/p:formatVersion',namespaces=N) or '')==fmt['format_version'],'PREMIS registry assertion mismatch')
            props={p.findtext('p:significantPropertiesType',namespaces=N):p.findtext('p:significantPropertiesValue',namespaces=N) for p in obj.findall('p:significantProperties',N)};require(props==fmt['significant_properties'],'PREMIS significant properties mismatch')
    for source in descriptor['sources']:
        char=by_id[urn(source['id'])].find('p:objectCharacteristics',N);require(char.findtext('p:fixity/p:messageDigestAlgorithm',namespaces=N)=='SHA-256' and char.findtext('p:fixity/p:messageDigest',namespaces=N)==source['sha256'] and int(char.findtext('p:size',namespaces=N))==source['size'],'PREMIS original SIP fixity mismatch')
    event_nodes=root.findall('p:event',N);by_event={first_id(e,'eventIdentifier'):e for e in event_nodes};require(len(by_event)==len(event_nodes)==len(events) and set(by_event)=={e['id'] for e in events},'PREMIS event coverage mismatch')
    agents=root.findall('p:agent',N);agent_ids={first_id(a,'agentIdentifier') for a in agents};expected_agents={e['actor'] for e in events}
    require(len(agent_ids)==len(agents) and agent_ids==expected_agents,'PREMIS agent coverage mismatch')
    for event in events:
        node=by_event[event['id']];require(node.findtext('p:eventDateTime',namespaces=N)==event['time'] and node.findtext('p:eventOutcomeInformation/p:eventOutcome',namespaces=N)==event['outcome'],'PREMIS event time/outcome mismatch')
        require(node.findtext('p:eventDetailInformation/p:eventDetail',namespaces=N)==canonical(event['detail']).decode(),'PREMIS event detail differs from audit evidence')
        linked={i.findtext('p:linkingAgentIdentifierValue',namespaces=N) for i in node.findall('p:linkingAgentIdentifier',N)}
        require(linked=={event['actor']},'PREMIS event agent link differs from audit actor')
        links={i.findtext('p:linkingObjectIdentifierValue',namespaces=N) for i in node.findall('p:linkingObjectIdentifier',N)};applicable=event['detail'].get('sip_id')
        require(links==({descriptor['representation_id']}|{o['object_id'] for o in objects if not applicable or o['sip_id']==applicable}),'PREMIS event object scope differs from evidence')
    statements=root.findall('p:rights/p:rightsStatement',N);by_right={first_id(r,'rightsStatementIdentifier'):r for r in statements};require(len(by_right)==len(statements)==len(context['rights']),'PREMIS rights statement count mismatch')
    for right in context['rights']:
        ident=right_id(right['sip_id'],descriptor['context_sha256']);require(ident in by_right,'PREMIS source rights missing');node=by_right[ident]
        basis=right['basis'] if right['status']=='assessed' and right['basis']!='unassessed' else 'other';require(node.findtext('p:rightsBasis',namespaces=N)==basis,'PREMIS rights basis mismatch')
        path={'statute':'statuteInformation/statuteCitation','license':'licenseInformation/licenseTerms','copyright':'copyrightInformation/copyrightNote','other':'otherRightsInformation/otherRightsNote'}[basis]
        expected_statement=right['statement'] or ('No preservation grant is asserted.' if basis=='other' else 'unassessed')
        require(node.findtext('/'.join('p:'+part for part in path.split('/')),namespaces=N)==expected_statement,'PREMIS rights statement differs from reviewed context')
        if basis in ('statute','copyright'):
            prefix='statute' if basis=='statute' else 'copyright'
            require(node.findtext('p:'+prefix+'Information/p:'+prefix+'Jurisdiction',namespaces=N)==(right['jurisdiction'] or 'unassessed'),'PREMIS rights jurisdiction mismatch')
        granted=node.findall('p:rightsGranted',N);require([r.findtext('p:act',namespaces=N) for r in granted]==(right['acts'] if right['status']=='assessed' else []),'PREMIS grant invented or omitted')
        for grant in granted:
            require(grant.findtext('p:termOfGrant/p:startDate',namespaces=N)==right['start_date'] and grant.findtext('p:termOfGrant/p:endDate',namespaces=N)==right['end_date'],'PREMIS grant dates mismatch')
        linked={n.findtext('p:linkingObjectIdentifierValue',namespaces=N) for n in node.findall('p:linkingObjectIdentifier',N)};require(linked=={o['object_id'] for o in objects if o['sip_id']==right['sip_id']},'PREMIS rights object scope mismatch')


def institutional_gate(service,transfer,policy):
    prepared=(transfer.get('plan') or {}).get('format')==2
    version='4.0.0' if prepared else '3.0.0';profile_id='urn:ch-archive:aip-profile:'+version
    reasons=[];fingerprint=sha(profile_definition(service.schemas,version))
    if transfer.get('active_metadata_drafts'):reasons.append('Saved metadata drafts require resolution before archival acceptance')
    if prepared:reasons.append('Prepared-input institutional acceptance remains unqualified')
    elif transfer.get('metadata_operations'):reasons.append('Working metadata requires a reviewed frozen-input plan before archival acceptance')
    if policy.get('approved') is not True or not policy.get('approved_by') or not policy.get('approval_reference'):reasons.append('Institutional AIP profile approval is missing')
    if policy.get('archive_id')!=transfer['tenant']:reasons.append('AIP approval does not cover this archive')
    if policy.get('profile_id')!=profile_id or policy.get('profile_sha256')!=fingerprint:reasons.append('Approved AIP profile fingerprint is missing or stale')
    reports=[]
    if not transfer.get('plan') or transfer['state']!='AIP_CANDIDATE':reasons.append('Current reviewed AIP candidates are not built')
    elif any('aip_id' not in t for t in transfer['plan']['targets']):reasons.append('Legacy candidate mapping must be re-saved and rebuilt under a supported AIP profile')
    else:
        from .aip import target_context_hash
        for target in transfer['plan']['targets']:
            versions=[o for o in transfer['outputs'] if o.get('aip_id')==target['aip_id']]
            if not versions:reasons.append(target['aip_id']+': current package missing');continue
            latest=max(versions,key=lambda o:o['version']);data=service.blob(latest['sha256']);report=validate_package(data,service.schemas,expected_sha256=latest['sha256'],source_resolver=service.blob);reports.append(report)
            if not report['structural_valid']:reasons.extend(report['errors'])
            if not report['preservation_ready']:reasons.extend(report['readiness_reasons'])
            if report['profile_id']!=profile_id:reasons.append(target['aip_id']+': candidate uses a historical profile and requires renewed review')
            if latest['version']>1:
                descriptor=json.loads(members(data)['aip.json']);previous=descriptor['previous_version'];prior=[o for o in versions if o['version']==latest['version']-1]
                if len(prior)!=1 or prior[0]['sha256']!=previous['sha256']:reasons.append(target['aip_id']+': predecessor evidence is missing')
            if report['structural_valid']:
                descriptor=json.loads(members(data)['aip.json'])
                if descriptor['plan_sha256']!=transfer['plan']['sha256'] or descriptor['context_sha256']!=target_context_hash(transfer,target):reasons.append(target['aip_id']+': package is stale against reviewed context/mapping')
    return {'ready':not reasons,'reasons':reasons,'reports':reports,'scope':'AIP profile gate only; candidate creation is not archival acceptance'}
