from __future__ import annotations
import io, json, uuid, zipfile, os, tempfile
from datetime import datetime, timezone
from pathlib import Path
from lxml import etree as ET
from .packages import inspect, members, sha, PackageError
from .store import Store, Conflict
from .metadata_validation import validate_metadata

METS='http://www.loc.gov/METS/'
PREMIS='http://www.loc.gov/premis/v3'
XLINK='http://www.w3.org/1999/xlink'
XSI='http://www.w3.org/2001/XMLSchema-instance'

def uid(): return str(uuid.uuid4())
def canonical(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
def now(): return datetime.now(timezone.utc).isoformat()
def event(body, kind, actor, detail, outcome='success'):
    previous = body['events'][-1]['hash'] if body['events'] else '0'*64
    entry={'id':uid(),'time':now(),'type':kind,'actor':actor,'outcome':outcome,'detail':detail,'previous':previous}
    entry['hash']=sha(canonical(entry));body['events'].append(entry)

def put(root: Path, data: bytes):
    digest=sha(data); p=root/'blobs'/digest;p.parent.mkdir(parents=True,exist_ok=True)
    # Link a fully written, fsynced temporary file; readers never observe partial content.
    descriptor, temporary = tempfile.mkstemp(dir=p.parent)
    try:
        with os.fdopen(descriptor, 'wb') as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        try: os.link(temporary, p)
        except FileExistsError:
            if sha(p.read_bytes()) != digest: raise PackageError('Local blob integrity failure')
    finally: os.unlink(temporary)
    return digest

class Service:
    def __init__(self, data: Path, schemas: Path, *, store=None, database_url=None):
        if store is not None and database_url is not None:raise ValueError('Choose an injected store or a database URL')
        self.data,self.schemas=data,schemas
        from .kost_validation import Config
        self.kost_config=Config.from_env()
        if database_url is not None:
            from .postgres import PostgresStore
            self.store=PostgresStore(database_url)
        else:self.store=store if store is not None else Store(data/'workflow.sqlite')
    def create(self,title,tenant,actor):
        body={'id':uid(),'tenant':tenant,'title':title,'revision':0,'state':'RECEIVED','sips':[], 'plan':None,'outputs':[],'events':[]}
        event(body,'creation',actor,{'title':title});return self.store.create(body)
    def get(self,ident,tenant): return self.store.get(ident,tenant)
    def blob(self,digest):
        data=(self.data/'blobs'/digest).read_bytes()
        if sha(data)!=digest: raise PackageError('Stored blob checksum mismatch')
        return data
    def upload(self,ident,tenant,actor,revision,profile,data,filename=None):
        if filename is not None and (not filename or len(filename)>255 or filename in ('.','..')
                or any(character in '/\\' or ord(character)<32 or ord(character)==127 for character in filename)):
            raise PackageError('Upload filename must be a plain filename without paths or control characters')
        b=self.get(ident,tenant)
        if b['revision']!=revision: raise Conflict('Stale revision')
        if b['state'] not in ('RECEIVED','INSPECTED'): raise Conflict('Create a new transfer revision after planning')
        try: report=inspect(data,profile,self.schemas)
        except (PackageError,ET.XMLSchemaError) as exc:
            event(b,'validation',actor,{'profile':profile,'input_sha256':sha(data),'error':str(exc)},'failure')
            self.store.save(b,revision);raise PackageError(str(exc)) from exc
        digest=put(self.data,data)
        if any(s['sha256']==digest for s in b['sips']): raise Conflict('Identical SIP already submitted')
        report['id']=uid()
        if filename is not None:report['original_filename']=filename
        b['sips'].append(report);b['state']='INSPECTED'
        event(b,'validation',actor,{'sip_id':report['id'],'input_sha256':digest,'profile':profile,'schema_bundle_sha256':report['schema_bundle_sha256'],'checks':['safe-zip','trusted-xsd','submitted-fixity','inventory','references'],**({'original_filename':filename} if filename is not None else {})})
        from .kost_validation import auto_enqueue
        with self.store.transaction() as tx:
            tx.save_transfer(b,revision)
            auto_enqueue(tx,b,report['id'],self.kost_config)
        return b
    def plan(self,ident,tenant,actor,revision,targets,reason,selected_inputs=None):
        b=self.get(ident,tenant)
        if b['revision']!=revision: raise Conflict('Stale revision')
        if b['state'] not in ('INSPECTED','PLANNED'): raise Conflict('Plan requires inspected SIPs')
        if not reason.strip() or not targets: raise PackageError('Reason and at least one target required')
        from .arrangement import normalize
        planning_sips=b['sips'];bindings=None
        if selected_inputs is not None:
            from .prepared_review import inputs
            planning_sips,bindings=inputs(self,b,selected_inputs)
        normalized=normalize(ident,planning_sips,targets)
        plan={'targets':normalized,'reason':reason,'author':actor,'created':now(),'inputs':[{'id':s['id'],'sha256':s['sha256']} for s in b['sips']]}
        plan['sha256']=sha(canonical(plan))
        if bindings is not None:
            from .prepared_review import extend_plan
            plan=extend_plan(self,b,plan,bindings)
        b['plan']=plan;b['state']='PLANNED';b.pop('approval',None);b.pop('prepared_review_id',None)
        event(b,'restructuring-plan',actor,{'plan_sha256':plan['sha256'],'reason':reason,'targets':normalized})
        return self.store.save(b,revision)
    def approve(self,ident,tenant,actor,revision):
        b=self.get(ident,tenant)
        from .metadata_drafts import require_resolved
        require_resolved(b)
        if b['revision']!=revision: raise Conflict('Stale revision')
        if b['state']!='PLANNED': raise Conflict('Plan is not awaiting approval')
        if b['plan']['author']==actor or b.get('context_author')==actor: raise Conflict('A different archivist must approve the mapping')
        if any('aip_id' not in t for t in b['plan']['targets']):raise Conflict('Re-save the mapping under a supported AIP profile before approval')
        from .aip import target_context_hash,target_resource_bindings,profile_definition
        from .aip_context import PreservationContext
        b['approval']={'profile_sha256':sha(profile_definition(self.schemas)),'approver':actor,'author':b['plan']['author'],'context_author':b.get('context_author'),'plan_sha256':b['plan']['sha256'],'context_sha256':sha(canonical(PreservationContext.model_validate(b.get('aip_context',{})).model_dump(mode='json'))),'target_contexts':{t['aip_id']:target_context_hash(b,t) for t in b['plan']['targets']},'target_resources':{t['aip_id']:target_resource_bindings(b,t) for t in b['plan']['targets']},'time':now()}
        b['state']='APPROVED';event(b,'authorization',actor,b['approval'])
        return self.store.save(b,revision)
    def set_context(self,ident,tenant,actor,revision,context):
        from .aip_context import validate_context
        b=self.get(ident,tenant)
        if b['revision']!=revision:raise Conflict('Stale revision')
        if not b['sips']:raise Conflict('Inspect source SIPs before assigning preservation context')
        b['aip_context']=validate_context(context,b);b['context_author']=actor;b.pop('approval',None)
        b.pop('prepared_review_id',None)
        b['state']='PLANNED' if b['plan'] else 'INSPECTED'
        event(b,'preservation-context-update',actor,{'context_sha256':sha(canonical(b['aip_context'])),'approval_invalidated':True})
        return self.store.save(b,revision)
    def add_resource(self,ident,tenant,actor,revision,title,name,media_type,data):
        b=self.get(ident,tenant)
        if b['revision']!=revision:raise Conflict('Stale revision')
        if not b['sips']:raise Conflict('Inspect source SIPs first')
        if not title.strip() or not name or any(ord(c)<32 for c in name) or len(data)>8*1024*1024:raise PackageError('Invalid representation resource (8 MiB limit)')
        digest=put(self.data,data);resource={'id':uid(),'title':title,'original_name':name,'media_type':media_type,'sha256':digest,'size':len(data)}
        b.setdefault('representation_resources',[]).append(resource);b['context_author']=actor;b.pop('approval',None);b['state']='PLANNED' if b['plan'] else 'INSPECTED'
        b.pop('prepared_review_id',None)
        event(b,'representation-resource-addition',actor,{'resource':resource,'approval_invalidated':True})
        return self.store.save(b,revision)
    def build(self,ident,tenant,actor,revision):
        from .aip import assemble,target_context_hash,target_resource_bindings,profile_definition,PROFILE_VERSION
        from .aip_validation import validate_package
        from .aip_context import PreservationContext
        b=self.get(ident,tenant)
        from .metadata_drafts import require_resolved
        prepared=(b.get('plan') or {}).get('format')==2
        profile_version='4.0.0' if prepared else PROFILE_VERSION
        profile_id='urn:ch-archive:aip-profile:'+profile_version
        if prepared:
            from .prepared_aip import current_review,check_authorization
            current_review(self,b)
            if not b.get('approval'):raise Conflict('Explicit prepared candidate authorization required')
            if b['state']!='AIP_CANDIDATE':check_authorization(b,b.get('approval',{}),self.schemas,self.blob)
        else:require_resolved(b)
        if b['revision']!=revision:raise Conflict('Stale revision')
        if b['state']=='AIP_CANDIDATE':return b
        if b['state']!='APPROVED':raise Conflict('Reviewed mapping and preservation context required')
        approval=b.get('approval',{})
        if approval.get('profile_sha256')!=sha(profile_definition(self.schemas,profile_version)) or approval.get('plan_sha256')!=b['plan']['sha256'] or approval.get('context_sha256')!=sha(canonical(PreservationContext.model_validate(b.get('aip_context',{})).model_dump(mode='json'))):raise Conflict('Approval does not match current mapping/context')
        outputs=[]
        context_body=b
        if prepared:
            from .prepared_review import context_view
            context_body=context_view(b,self.blob,self.schemas)
        for target in b['plan']['targets']:
            aip_id=target['aip_id']
            if approval['target_contexts'].get(aip_id)!=target_context_hash(context_body,target) or approval['target_resources'].get(aip_id)!=target_resource_bindings(context_body,target):raise Conflict('Target context approval mismatch')
            history=[o for o in b['outputs'] if o.get('aip_id')==aip_id];prior=max(history,key=lambda o:o['version']) if history else None
            version=prior['version']+1 if prior else 1
            previous={'version':prior['version'],'version_id':prior['id'],'sha256':prior['sha256']} if prior else None
            created=now();events=json.loads(json.dumps(b['events']));snapshot={'events':events}
            event(snapshot,'package-creation',actor,{'aip_id':aip_id,'version':version,'plan_sha256':b['plan']['sha256'],'context_sha256':target_context_hash(context_body,target)})
            data,descriptor,objects=assemble(self,b,target,aip_id,version,previous,created,events)
            report=validate_package(data,self.schemas)
            if not report['structural_valid']:raise PackageError('AIP profile conformance failed: '+'; '.join(report['errors']))
            digest=put(self.data,data);report['package_sha256']=digest;report_digest=put(self.data,canonical(report))
            outputs.append({'id':descriptor['version_id'],'aip_id':aip_id,'version':version,'label':target['label'],'sha256':digest,'size':len(data),'files':len(objects),
                            'profile':profile_id,'profile_sha256':sha(profile_definition(self.schemas,profile_version)),'status':'CANDIDATE','conformance':report,'validation_report_sha256':report_digest})
        b['outputs'].extend(outputs);b['state']='AIP_CANDIDATE'
        event(b,'package-creation',actor,{'outputs':outputs,'finalization_blocked':['institutional AIP approval/readiness','archival acceptance']})
        return self.store.save(b,revision)
