import os,json
from pathlib import Path
from typing import Annotated, Literal
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, Field, ConfigDict
from .auth import Principal, principal, require
from .service import Service
from .packages import PackageError, MAX_ZIP, MAX_EXPANDED, PROFILES, ech_schema_dir
from .store import Conflict
from .aip_context import PreservationContext
from .aip_validation import validate_package,institutional_gate

ROOT=Path(__file__).resolve().parents[2]
service=Service(Path(os.getenv('DATA_DIR',str(ROOT/'var'))),Path(os.getenv('SCHEMA_DIR',str(ROOT/'schemas'))),database_url=os.getenv('DATABASE_URL'))
app=FastAPI(title='Ingest Workbench',version='0.0.1',description='Reference implementation. AIP candidates only; external acceptance gates remain closed.')
app.add_middleware(CORSMiddleware,allow_origins=['http://localhost:4200'],allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['Authorization','Content-Type'])

@app.exception_handler(PackageError)
async def bad_package(request:Request,exc:PackageError): return JSONResponse(status_code=422,content={'detail':str(exc)})
@app.exception_handler(Conflict)
async def conflict(request:Request,exc:Conflict): return JSONResponse(status_code=409,content={'detail':str(exc)})
@app.exception_handler(KeyError)
async def missing(request:Request,exc:KeyError): return JSONResponse(status_code=404,content={'detail':'Resource not found'})

class Model(BaseModel): model_config=ConfigDict(extra='forbid')
class NewTransfer(Model): title:str=Field(min_length=1,max_length=200)
class Revision(Model): revision:int=Field(ge=0)
class KostValidation(Model):
    request_id:str=Field(pattern='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    input_sha256:str=Field(pattern='^[0-9a-f]{64}$')
    prepared_id:str|None=Field(default=None,min_length=1,max_length=100)
    acknowledge_uncertain:bool=False
class UnitRef(Model): sip_id:str;unit_id:str
class Target(Model): label:str=Field(min_length=1,max_length=200);units:list[UnitRef]=Field(min_length=1,max_length=1000)
class InputSelection(Model):
    sip_id:str
    prepared_id:str|None=Field(default=None,min_length=1,max_length=100)
class SelectedInputs(Revision): selected_inputs:list[InputSelection]=Field(min_length=1,max_length=100)
class Plan(Revision):
    targets:list[Target]=Field(min_length=1,max_length=100)
    reason:str=Field(min_length=1,max_length=2000)
    selected_inputs:list[InputSelection]|None=Field(default=None,min_length=1,max_length=100)
class PreparedReview(Revision):
    request_id:str=Field(pattern='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    plan_sha256:str=Field(pattern='^[0-9a-f]{64}$')
    reason:str=Field(min_length=1,max_length=2000)
class MetadataChange(Model):
    node_id:str=Field(min_length=1,max_length=100)
    field_id:str=Field(min_length=1,max_length=100)
    before:str=Field(max_length=8000)
    value:str=Field(max_length=8000)
class DraftBaseline(Revision):
    source_sha256:str=Field(pattern='^[0-9a-f]{64}$')
    base_draft_id:str|None=Field(default=None,max_length=100)
    reason:str=Field(min_length=1,max_length=2000)
class SaveMetadataDraft(DraftBaseline):
    changes:list[MetadataChange]=Field(min_length=1,max_length=200)
class ExportSip(Revision):
    source_sha256:str=Field(pattern='^[0-9a-f]{64}$')
    draft_id:str=Field(min_length=1,max_length=100)
    reason:str=Field(min_length=1,max_length=2000)
class MetadataFieldProposal(Model):
    node_id:str=Field(min_length=1,max_length=100)
    field_id:str=Field(min_length=1,max_length=100)
    before:list[dict]=Field(max_length=200)
    values:list[dict]=Field(max_length=200)
class SampleItemReview(Model):
    node_id:str=Field(min_length=1,max_length=100)
    outcome:Literal['checked','issue']
    comment:str=Field(default='',max_length=2000)
class MetadataImportSource(Model):
    filename:str=Field(min_length=1,max_length=200)
    format:Literal['csv','xlsx','xml','json']
    data_base64:str=Field(min_length=1,max_length=349528)
class MetadataImportDecision(Model):
    row_id:str=Field(min_length=1,max_length=100)
    action:Literal['apply','exclude']
    node_id:str=Field(default='',max_length=100)
    field:str=Field(default='',max_length=100)
    reason:str=Field(default='',max_length=500)
class ContentSource(Model):
    filename:str=Field(min_length=1,max_length=200)
    data_base64:str=Field(max_length=4*((MAX_EXPANDED+2)//3))
class MetadataOperation(Revision):
    request_id:str=Field(pattern='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    source_sha256:str=Field(pattern='^[0-9a-f]{64}$')
    base_operation_id:str|None=None
    kind:str=Field(pattern='^(edit|edit-batch|undo|redo|restore|checkpoint|note-add|note-edit|note-delete|structure-reorder|structure-create|sample-record|sample-review|import-record|import-apply|content-edit)$')
    content_action:Literal['rename','replace']|None=None
    content_name:str=Field(default='',max_length=200)
    content_source:ContentSource|None=None
    reason:str=Field(min_length=1,max_length=2000)
    node_id:str|None=None
    field_id:str|None=None
    before:list[dict]=Field(default_factory=list,max_length=200)
    values:list[dict]=Field(default_factory=list,max_length=200)
    target_id:str|None=None
    name:str=Field(default='',max_length=200)
    note_id:str|None=Field(default=None,max_length=100)
    note_before:dict|None=None
    note_text:str=Field(default='',max_length=8000)
    note_purpose:str=Field(default='internal',pattern='^(internal|description-candidate)$')
    parent_id:str|None=Field(default=None,max_length=100)
    child_kind:str|None=Field(default=None,max_length=100)
    before_order:list[str]=Field(default_factory=list,max_length=200)
    after_order:list[str]=Field(default_factory=list,max_length=200)
    count:int=Field(default=1,ge=1,le=20,strict=True)
    object_values:dict=Field(default_factory=dict)
    edits:list[MetadataFieldProposal]=Field(default_factory=list,max_length=200)
    sample_mode:Literal['absolute','percent']='absolute'
    sample_size:int=Field(default=10,ge=1,le=500,strict=True)
    sample_seed:str=Field(default='',max_length=200)
    sample_name:str=Field(default='',max_length=200)
    sample_reference:str=Field(default='',max_length=200)
    sample_id:str|None=Field(default=None,max_length=100)
    reviews:list[SampleItemReview]=Field(default_factory=list,max_length=500)
    import_source:MetadataImportSource|None=None
    import_id:str|None=Field(default=None,max_length=100)
    import_decisions:list[MetadataImportDecision]=Field(default_factory=list,max_length=200)
class FreezeInput(Revision):
    request_id:str=Field(pattern='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    source_sha256:str=Field(pattern='^[0-9a-f]{64}$')
    operation_id:str=Field(min_length=1,max_length=100)
    reason:str=Field(min_length=1,max_length=2000)

@app.get('/health')
def health(): return {'status':'ok','mode':'reference','finalization_enabled':False}
@app.get('/ready')
def ready():
    from .readiness import check
    result=check(service)
    return JSONResponse(status_code=200 if result['status']=='ready' else 503,content=result,headers={'Cache-Control':'no-store'})
@app.get('/api/profiles')
def profiles(u:Principal=Depends(require('archive-reader'))):
    return [{'version':p,'schema_version':v,'trusted_schema_installed':(ech_schema_dir(service.schemas,p)/'arelda.xsd').is_file()} for p,v in PROFILES.items()]
@app.get('/api/transfers')
def transfers(u:Principal=Depends(require('archive-reader'))): return service.store.list(u.tenant)
@app.get('/api/transfer-pages')
def transfer_pages(
    q:Annotated[str,Query(max_length=200)]='',
    sort:Literal['created','title','state','sips','revision']='created',
    direction:Literal['ascending','descending']='descending',
    offset:Annotated[int,Query(ge=0)]=0,
    limit:Annotated[int,Query(ge=1,le=100)]=30,
    u:Principal=Depends(require('archive-reader')),
):
    return service.store.list_page(u.tenant,q.strip(),sort,direction,offset,limit)
@app.post('/api/transfers',status_code=201)
def create(data:NewTransfer,u:Principal=Depends(require('archive-operator'))):return service.create(data.title,u.tenant,u.sub)
@app.get('/api/transfers/{ident}')
def get(ident:str,u:Principal=Depends(require('archive-reader'))): return service.get(ident,u.tenant)

# Read-only package navigation shares transfer/tenant authorization and original blobs.
@app.get('/api/transfers/{ident}/sips/{sip_id}/preparation')
def preparation(ident:str,sip_id:str,operation_id:str|None=None,u:Principal=Depends(require('archive-reader'))):
    from .working_metadata import workspace
    return JSONResponse(workspace(service,ident,u.tenant,sip_id,operation_id),headers={'Cache-Control':'no-store'})

@app.get('/api/transfers/{ident}/sips/{sip_id}/preparation/sample')
def preparation_sample(ident:str,sip_id:str,mode:Literal['absolute','percent']='absolute',
                       size:int=Query(default=10,ge=1,le=500),revision:int|None=Query(default=None,ge=0),
                       seed:str|None=Query(default=None,min_length=1,max_length=200),
                       name:str=Query(default='',max_length=200),reference:str=Query(default='',max_length=200),
                       u:Principal=Depends(require('archive-reader'))):
    from .sampling import plan
    from .service import uid
    from .working_metadata import workspace
    if mode=='percent' and size>100:
        raise PackageError('Sample percentage must be between 1 and 100')
    document=workspace(service,ident,u.tenant,sip_id)
    if revision is not None and revision!=document['revision']:
        raise HTTPException(status_code=409,detail='Working version changed; reload before drawing a sample')
    sample=plan(document['nodes'],mode,size,seed if seed is not None else uid(),name,reference)
    return JSONResponse({**sample,'revision':document['revision'],'metadata_sha256':document['state']['metadata_sha256'],
                         'input_operation_id':document['state']['head']},
                        headers={'Cache-Control':'no-store'})

def metadata_request(data:MetadataOperation):
    request=data.model_dump()
    # Keep historical metadata-only request digests stable across this API extension.
    if not data.kind.startswith('note-'):
        for key in ('note_id','note_before','note_text','note_purpose'):request.pop(key)
    if data.kind not in ('structure-reorder','structure-create'):
        for key in ('parent_id','child_kind','before_order','after_order'):request.pop(key)
    if data.kind!='structure-create':
        for key in ('count','object_values'):request.pop(key)
    if data.kind!='edit-batch':request.pop('edits')
    if data.kind!='sample-record':
        for key in ('sample_mode','sample_size','sample_seed','sample_name','sample_reference'):request.pop(key)
    if data.kind!='sample-review':
        for key in ('sample_id','reviews'):request.pop(key)
    if data.kind!='import-record':request.pop('import_source')
    if data.kind!='content-edit':
        for key in ('content_action','content_name','content_source'):request.pop(key)
    if data.kind!='import-apply':
        for key in ('import_id','import_decisions'):request.pop(key)
    return request

@app.post('/api/transfers/{ident}/sips/{sip_id}/preparation/commands')
def preparation_command(ident:str,sip_id:str,data:MetadataOperation,u:Principal=Depends(require('archive-operator'))):
    from .working_metadata import command
    return command(service,ident,u.tenant,u.sub,sip_id,metadata_request(data))

@app.post('/api/transfers/{ident}/sips/{sip_id}/preparation/edit-preview')
def edit_preview(ident:str,sip_id:str,data:MetadataOperation,u:Principal=Depends(require('archive-operator'))):
    from .working_metadata import baseline,sip_for,apply_edits
    from . import editor_fields as fields
    from .packages import xml,sha
    from .json_codec import canonical
    request=metadata_request(data)
    if data.kind!='edit-batch' or not data.reason.strip() or len(canonical(request))>256*1024:
        raise PackageError('Invalid or oversized package-edit proposal')
    body=service.get(ident,u.tenant);sip=sip_for(body,sip_id)
    _,metadata,state=baseline(service,body,sip)
    if body['revision']!=data.revision or sip['sha256']!=data.source_sha256 or state['head']!=data.base_operation_id:
        raise Conflict('Working version changed; reload before previewing edits')
    root=xml(metadata);info=fields.schema(service.schemas,sip)
    changes=apply_edits(root,info,request['edits'])
    updated=fields.validate(root,service.schemas,sip)
    return JSONResponse({'edits':changes,'metadata_sha256':sha(updated)},headers={'Cache-Control':'no-store'})

@app.post('/api/transfers/{ident}/sips/{sip_id}/preparation/import-preview')
def import_preview(ident:str,sip_id:str,data:MetadataOperation,u:Principal=Depends(require('archive-operator'))):
    from .working_metadata import baseline,sip_for,history,apply_import
    from . import editor_fields as fields
    from .packages import xml,sha
    from .json_codec import canonical
    request=metadata_request(data)
    if data.kind!='import-apply' or not data.reason.strip() or len(canonical(request))>256*1024:
        raise PackageError('Invalid or oversized metadata import review')
    body=service.get(ident,u.tenant);sip=sip_for(body,sip_id)
    _,metadata,state=baseline(service,body,sip)
    if body['revision']!=data.revision or sip['sha256']!=data.source_sha256 or state['head']!=data.base_operation_id:
        raise Conflict('Working version changed; reload before previewing imports')
    changes,updated=apply_import(xml(metadata),fields.schema(service.schemas,sip),history(body,sip_id),
        request,state['head'],metadata,service.schemas,sip)
    return JSONResponse({'edits':changes['metadata_import']['edits'],'metadata_sha256':sha(updated)},headers={'Cache-Control':'no-store'})

@app.get('/api/transfers/{ident}/sips/{sip_id}/preparation/import-template/{format}')
def import_template(ident:str,sip_id:str,format:Literal['csv','xlsx','xml','json'],object_id:str|None=None,u:Principal=Depends(require('archive-reader'))):
    from .working_metadata import baseline,sip_for
    from . import editor_fields as fields, metadata_imports
    from .packages import xml
    from .package_handler import attachment_headers
    body=service.get(ident,u.tenant);sip=sip_for(body,sip_id)
    _,metadata,_=baseline(service,body,sip)
    nodes,_=fields.catalog(xml(metadata),fields.schema(service.schemas,sip))
    if object_id is not None:
        if format=='xml':raise PackageError('XML templates require the complete metadata document')
        nodes=[node for node in nodes if node['id']==object_id]
        if not nodes:raise KeyError(object_id)
    data=metadata_imports.template(nodes,format,metadata)
    if len(data)>metadata_imports.MAX_SOURCE:raise PackageError('Template exceeds 256 KiB; select an individual object')
    return Response(data,media_type='application/octet-stream',
                    headers=attachment_headers('metadata-import-template.'+format))

@app.get('/api/transfers/{ident}/sips/{sip_id}/preparation/import-source/{operation_id}')
def import_source(ident:str,sip_id:str,operation_id:str,u:Principal=Depends(require('archive-reader'))):
    from .working_metadata import sip_for,history
    from .metadata_imports import source
    from .package_handler import attachment_headers
    body=service.get(ident,u.tenant);sip_for(body,sip_id)
    record=next((record for record in history(body,sip_id) if record['id']==operation_id and record['kind']=='import-record'),None)
    if record is None:raise KeyError(operation_id)
    retained,data=source(record['changes']['metadata_import_proposal']['source'])
    return Response(data,media_type='application/octet-stream',headers=attachment_headers(retained['filename']))

@app.post('/api/transfers/{ident}/sips/{sip_id}/preparation/content-preview')
def content_edit_preview(ident:str,sip_id:str,data:MetadataOperation,u:Principal=Depends(require('archive-operator'))):
    from . import working_metadata as working, content_editing as content
    if data.kind!=content.KIND:raise PackageError('Expected a payload-edit proposal')
    body=service.get(ident,u.tenant);sip=working.sip_for(body,sip_id)
    _,metadata,state=working.baseline(service,body,sip)
    if body['revision']!=data.revision or sip['sha256']!=data.source_sha256 or state['head']!=data.base_operation_id:
        raise Conflict('Working version changed; reload before previewing content')
    archive=content.prepared(service.blob,sip,working.history(body,sip_id),metadata)
    changes,_,_=content.apply(archive,sip,service.schemas,metadata_request(data))
    return JSONResponse(changes,headers={'Cache-Control':'no-store'})

@app.post('/api/transfers/{ident}/sips/{sip_id}/preparation/creation-preview')
def creation_preview(ident:str,sip_id:str,data:MetadataOperation,u:Principal=Depends(require('archive-operator'))):
    from .working_metadata import baseline,sip_for
    from . import hierarchy_creation as creation, editor_fields as fields
    from .packages import xml
    from .json_codec import canonical
    if data.kind!=creation.KIND or len(canonical(data.model_dump()))>256*1024:
        raise PackageError('Invalid or oversized object-creation proposal')
    body=service.get(ident,u.tenant);sip=sip_for(body,sip_id)
    _,metadata,state=baseline(service,body,sip)
    if body['revision']!=data.revision or sip['sha256']!=data.source_sha256 or state['head']!=data.base_operation_id:
        raise Conflict('Working version changed; reload before previewing creation')
    root=xml(metadata);info=fields.schema(service.schemas,sip)
    result=creation.apply(root,info,data.model_dump())
    fields.validate(root,service.schemas,sip)
    return JSONResponse(result,headers={'Cache-Control':'no-store'})

@app.get('/api/transfers/{ident}/sips/{sip_id}/preparation/{operation_id}/{kind}')
def preparation_artifact(ident:str,sip_id:str,operation_id:str,kind:str,u:Principal=Depends(require('archive-reader'))):
    from .working_metadata import artifact
    from .package_handler import attachment_headers
    data=artifact(service,ident,u.tenant,sip_id,operation_id,kind)
    extension='.zip' if kind in ('evidence','archive') else '.xml' if kind=='metadata' else '.json'
    return Response(data,media_type='application/zip' if kind in ('evidence','archive') else 'application/octet-stream',headers=attachment_headers('preparation-'+operation_id+extension))

@app.post('/api/transfers/{ident}/sips/{sip_id}/prepared-inputs')
def freeze_prepared_input(ident:str,sip_id:str,data:FreezeInput,u:Principal=Depends(require('archive-operator'))):
    from .prepared_inputs import create
    return create(service,ident,u.tenant,u.sub,sip_id,data.model_dump())

@app.get('/api/transfers/{ident}/sips/{sip_id}/prepared-inputs/{prepared_id}/{kind}')
def prepared_input_artifact(ident:str,sip_id:str,prepared_id:str,kind:str,u:Principal=Depends(require('archive-reader'))):
    from .prepared_inputs import artifact
    from .package_handler import attachment_headers
    data=artifact(service,ident,u.tenant,sip_id,prepared_id,kind)
    return Response(data,media_type='application/zip',headers={**attachment_headers('prepared-'+prepared_id+('-evidence' if kind=='evidence' else '')+'.zip'),'Cache-Control':'no-store'})

@app.get('/api/transfers/{ident}/sips/{sip_id}/workspace')
def package_workspace(ident:str,sip_id:str,u:Principal=Depends(require('archive-reader'))):
    from .package_handler import workspace
    return JSONResponse(workspace(service,ident,u.tenant,sip_id),headers={'Cache-Control':'no-store'})

@app.get('/api/transfers/{ident}/sips/{sip_id}/kost-validations')
def kost_inventory(ident:str,sip_id:str,u:Principal=Depends(require('archive-reader'))):
    from .kost_validation import inventory
    return JSONResponse(inventory(service,ident,u.tenant,sip_id),headers={'Cache-Control':'no-store'})

@app.post('/api/transfers/{ident}/sips/{sip_id}/kost-validations',status_code=202)
def kost_start(ident:str,sip_id:str,data:KostValidation,u:Principal=Depends(require('archive-operator'))):
    from .kost_validation import start
    return JSONResponse(start(service,ident,u.tenant,u.sub,sip_id,data.model_dump()),status_code=202,headers={'Cache-Control':'no-store'})

@app.get('/api/transfers/{ident}/sips/{sip_id}/kost-validations/runs')
def kost_history(ident:str,sip_id:str,prepared_id:str|None=None,before:str|None=None,u:Principal=Depends(require('archive-reader'))):
    from .kost_validation import history
    return JSONResponse(history(service,ident,u.tenant,sip_id,prepared_id,before),headers={'Cache-Control':'no-store'})

@app.get('/api/transfers/{ident}/sips/{sip_id}/kost-validations/runs/{run_id}')
def kost_run(ident:str,sip_id:str,run_id:str,u:Principal=Depends(require('archive-reader'))):
    from .kost_validation import scoped,public
    service.get(ident,u.tenant)
    with service.store.transaction() as tx:
        result=public(scoped(tx,ident,u.tenant,sip_id,run_id))
    return JSONResponse(result,headers={'Cache-Control':'no-store'})

@app.get('/api/transfers/{ident}/sips/{sip_id}/kost-validations/runs/{run_id}/report')
def kost_report(ident:str,sip_id:str,run_id:str,u:Principal=Depends(require('archive-reader'))):
    from .kost_validation import scoped
    from .package_handler import attachment_headers
    service.get(ident,u.tenant)
    with service.store.transaction() as tx:
        result=scoped(tx,ident,u.tenant,sip_id,run_id)
    if not result['report_sha256']:
        raise HTTPException(409,'KOST report is not retained: '+result['report_status'])
    return Response(service.blob(result['report_sha256']),media_type='application/xml',headers=attachment_headers('kost-'+run_id+'.xml'))

@app.get('/api/transfers/{ident}/sips/{sip_id}/kost-validations/runs/{run_id}/findings')
def kost_findings(ident:str,sip_id:str,run_id:str,u:Principal=Depends(require('archive-reader'))):
    from .kost_report import findings
    return JSONResponse(findings(service,ident,u.tenant,sip_id,run_id),headers={'Cache-Control':'no-store'})

@app.get('/api/transfers/{ident}/sips/{sip_id}/nodes/{node_id}')
def package_node(ident:str,sip_id:str,node_id:str,u:Principal=Depends(require('archive-reader'))):
    from .package_handler import node_detail
    return JSONResponse(node_detail(service,ident,u.tenant,sip_id,node_id),headers={'Cache-Control':'no-store'})

@app.get('/api/transfers/{ident}/sips/{sip_id}/nodes/{node_id}/content')
def package_content(ident:str,sip_id:str,node_id:str,u:Principal=Depends(require('archive-reader'))):
    from .package_handler import content,attachment_headers
    data,name=content(service,ident,u.tenant,sip_id,node_id)
    return Response(data,media_type='application/octet-stream',headers=attachment_headers(name))

@app.get('/api/transfers/{ident}/sips/{sip_id}/original')
def original_sip(ident:str,sip_id:str,u:Principal=Depends(require('archive-reader'))):
    from .package_handler import source,attachment_headers
    _,_,data,_,_=source(service,ident,u.tenant,sip_id)
    return Response(data,media_type='application/zip',headers=attachment_headers('original-'+sip_id+'.zip'))

@app.get('/api/transfers/{ident}/sips/{sip_id}/metadata')
def original_metadata(ident:str,sip_id:str,u:Principal=Depends(require('archive-reader'))):
    from .package_handler import source,attachment_headers
    _,sip,_,files,_=source(service,ident,u.tenant,sip_id)
    return Response(files[sip['metadata_path']],media_type='application/octet-stream',headers=attachment_headers('metadata.xml'))

@app.post('/api/transfers/{ident}/sips/{sip_id}/draft')
def save_metadata_draft(ident:str,sip_id:str,data:SaveMetadataDraft,u:Principal=Depends(require('archive-operator'))):
    from .metadata_drafts import save
    return save(service,ident,u.tenant,u.sub,data.revision,sip_id,data.source_sha256,data.base_draft_id,data.reason,[c.model_dump() for c in data.changes])

@app.post('/api/transfers/{ident}/sips/{sip_id}/draft/discard')
def discard_metadata_draft(ident:str,sip_id:str,data:DraftBaseline,u:Principal=Depends(require('archive-operator'))):
    from .metadata_drafts import discard
    return discard(service,ident,u.tenant,u.sub,data.revision,sip_id,data.source_sha256,data.base_draft_id,data.reason)

@app.get('/api/transfers/{ident}/sips/{sip_id}/drafts/{draft_id}/metadata')
def draft_metadata(ident:str,sip_id:str,draft_id:str,u:Principal=Depends(require('archive-reader'))):
    from .metadata_drafts import artifact
    from .package_handler import attachment_headers
    return Response(artifact(service,ident,u.tenant,sip_id,draft_id,'metadata'),media_type='application/octet-stream',headers=attachment_headers('draft-metadata-'+draft_id+'.xml'))

@app.get('/api/transfers/{ident}/sips/{sip_id}/drafts/{draft_id}/changes')
def draft_changes(ident:str,sip_id:str,draft_id:str,u:Principal=Depends(require('archive-reader'))):
    from .metadata_drafts import artifact
    from .package_handler import attachment_headers
    return Response(artifact(service,ident,u.tenant,sip_id,draft_id,'changes'),media_type='application/json',headers=attachment_headers('draft-changes-'+draft_id+'.json'))
@app.post('/api/transfers/{ident}/sips/{sip_id}/exports')
def export_sip(ident:str,sip_id:str,data:ExportSip,u:Principal=Depends(require('archive-operator'))):
    from .sip_exports import create
    return create(service,ident,u.tenant,u.sub,data.revision,sip_id,data.source_sha256,data.draft_id,data.reason)

@app.get('/api/transfers/{ident}/sips/{sip_id}/exports/{export_id}/{kind}')
def export_artifact(ident:str,sip_id:str,export_id:str,kind:str,u:Principal=Depends(require('archive-reader'))):
    from .sip_exports import artifact
    from .package_handler import attachment_headers
    if kind not in ('zip','evidence'):raise HTTPException(404,'Resource not found')
    return Response(artifact(service,ident,u.tenant,sip_id,export_id,kind),media_type='application/zip' if kind=='zip' else 'application/json',headers=attachment_headers('derived-'+export_id+('.zip' if kind=='zip' else '-evidence.json')))

@app.post('/api/transfers/{ident}/sips')
async def upload(ident:str,profile:Annotated[str,Form()],revision:Annotated[int,Form()],file:Annotated[UploadFile,File()],u:Principal=Depends(require('archive-operator'))):
    data=await file.read(MAX_ZIP+1)
    await file.close()
    if len(data)>MAX_ZIP: raise HTTPException(413,'Local reference upload limit: 64 MiB')
    # For production, move inspection to isolated durable jobs; never execute untrusted parsers in API workers.
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(service.upload,ident,u.tenant,u.sub,revision,profile,data,filename=file.filename)
@app.post('/api/transfers/{ident}/plan')
def plan(ident:str,data:Plan,u:Principal=Depends(require('archive-operator'))):
    return service.plan(ident,u.tenant,u.sub,data.revision,[t.model_dump() for t in data.targets],data.reason,
                        [s.model_dump() for s in data.selected_inputs] if data.selected_inputs is not None else None)

@app.get('/api/transfers/{ident}/arrangement')
def arrangement(ident:str,u:Principal=Depends(require('archive-reader'))):
    from .prepared_review import workspace
    return JSONResponse(workspace(service,ident,u.tenant),headers={'Cache-Control':'no-store'})

@app.post('/api/transfers/{ident}/arrangement/preview')
def preview_arrangement(ident:str,data:SelectedInputs,u:Principal=Depends(require('archive-reader'))):
    from .prepared_review import preview
    return JSONResponse(preview(service,ident,u.tenant,data.revision,[s.model_dump() for s in data.selected_inputs]),headers={'Cache-Control':'no-store'})

@app.post('/api/transfers/{ident}/prepared-reviews')
def review_preparation(ident:str,data:PreparedReview,u:Principal=Depends(require('archive-approver'))):
    from .prepared_review import review
    return review(service,ident,u.tenant,u.sub,data.model_dump())

@app.get('/api/transfers/{ident}/prepared-reviews/{review_id}/evidence')
def preparation_review_evidence(ident:str,review_id:str,u:Principal=Depends(require('archive-reader'))):
    from .prepared_review import artifact
    from .package_handler import attachment_headers
    return Response(artifact(service,ident,u.tenant,review_id),media_type='application/zip',
                    headers={**attachment_headers('preparation-review-'+review_id+'.zip'),'Cache-Control':'no-store'})
class PreparedAuthorization(PreparedReview):
    review_id:str=Field(pattern='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

@app.post('/api/transfers/{ident}/prepared-authorizations')
def authorize_prepared_candidate(ident:str,data:PreparedAuthorization,u:Principal=Depends(require('archive-approver'))):
    from .prepared_aip import authorize
    return authorize(service,ident,u.tenant,u.sub,data.model_dump())

@app.post('/api/transfers/{ident}/approve')
def approve(ident:str,data:Revision,u:Principal=Depends(require('archive-approver'))):return service.approve(ident,u.tenant,u.sub,data.revision)
@app.post('/api/transfers/{ident}/build')
def build(ident:str,data:Revision,u:Principal=Depends(require('archive-operator'))):return service.build(ident,u.tenant,u.sub,data.revision)
@app.get('/api/transfers/{ident}/outputs/{output_id}')
def download(ident:str,output_id:str,u:Principal=Depends(require('archive-reader'))):
    b=service.get(ident,u.tenant);out=next((o for o in b['outputs'] if o['id']==output_id),None)
    if not out:raise HTTPException(404,'Output not found')
    return Response(service.blob(out['sha256']),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="AIP-candidate-{output_id}.zip"','Cache-Control':'no-store'})
@app.post('/api/transfers/{ident}/finalize')
def finalize(ident:str,data:Revision,u:Principal=Depends(require('archive-approver'))):
    b=service.get(ident,u.tenant)
    if b['revision']!=data.revision:raise Conflict('Stale transfer revision')
    aip=profile_gate(b)
    if not aip['ready']:raise HTTPException(409,{'gate':'AIP','reasons':aip['reasons']})
    raise HTTPException(501,'Archival acceptance is outside this application')

class ContextRequest(Revision): context:PreservationContext


def profile_gate(transfer):
    policy=json.loads(Path(os.getenv('AIP_POLICY_FILE',str(ROOT/'config/aip-policy.json'))).read_text())
    return institutional_gate(service,transfer,policy)

@app.get('/api/aip/profile')
def aip_profile(u:Principal=Depends(require('archive-reader'))):
    from .aip import profile_definition
    raw=profile_definition(service.schemas)
    from .packages import sha
    return {'definition':json.loads(raw),'sha256':sha(raw)}

@app.post('/api/transfers/{ident}/aip-context')
def aip_context(ident:str,data:ContextRequest,u:Principal=Depends(require('archive-operator'))):
    return service.set_context(ident,u.tenant,u.sub,data.revision,data.context.model_dump(mode='json'))

@app.post('/api/transfers/{ident}/representation-resources')
async def representation_resource(ident:str,revision:Annotated[int,Form()],title:Annotated[str,Form()],file:Annotated[UploadFile,File()],u:Principal=Depends(require('archive-operator'))):
    data=await file.read(8*1024*1024+1);name=file.filename or 'resource';media_type=file.content_type or 'application/octet-stream';await file.close()
    if len(data)>8*1024*1024:raise HTTPException(413,'Representation documentation limit: 8 MiB')
    return service.add_resource(ident,u.tenant,u.sub,revision,title,name,media_type,data)

@app.get('/api/transfers/{ident}/aip-gate')
def aip_gate(ident:str,u:Principal=Depends(require('archive-reader'))):return profile_gate(service.get(ident,u.tenant))

@app.get('/api/transfers/{ident}/outputs/{output_id}/conformance')
def aip_conformance(ident:str,output_id:str,u:Principal=Depends(require('archive-reader'))):
    transfer=service.get(ident,u.tenant);output=next((o for o in transfer['outputs'] if o['id']==output_id),None)
    if output is None:raise HTTPException(404,'Output not found')
    return validate_package(service.blob(output['sha256']),service.schemas,expected_sha256=output['sha256'],source_resolver=service.blob)
