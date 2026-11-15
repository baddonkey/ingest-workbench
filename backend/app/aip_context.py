"""Explicit preservation assertions. Empty defaults never imply rights or format knowledge."""
from datetime import date
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator
from .packages import PackageError

class Strict(BaseModel):model_config=ConfigDict(extra='forbid')
class SourceRights(Strict):
    sip_id:str
    status:Literal['unassessed','assessed']='unassessed'
    basis:Literal['unassessed','statute','license','copyright','other']='unassessed'
    statement:str=Field(default='',max_length=8000)
    jurisdiction:str=Field(default='',max_length=200)
    copyright_status:str=Field(default='',max_length=200)
    access:Literal['restricted','open','unassessed']='unassessed'
    source_restrictions_reviewed:bool=False
    acts:list[Literal['replicate','migrate','disseminate']]=Field(default_factory=list,max_length=3)
    term:Literal['unassessed','bounded','open-ended']='unassessed'
    start_date:date|None=None
    end_date:date|None=None
    resource_ids:list[str]=Field(default_factory=list,max_length=30)
    @model_validator(mode='after')
    def consistent(self):
        if self.status=='unassessed' and self.acts:raise ValueError('Unassessed rights cannot assert preservation grants')
        if self.end_date and self.start_date and self.end_date<self.start_date:raise ValueError('Rights end precedes start')
        if self.acts and (self.basis=='unassessed' or not self.statement.strip() or not self.resource_ids or self.term=='unassessed' or not self.start_date or (self.term=='bounded' and not self.end_date)):
            raise ValueError('Preservation grants require a documented basis and explicit term')
        if len(set(self.acts))!=len(self.acts):raise ValueError('Duplicate rights act')
        return self
class FormatAssessment(Strict):
    sip_id:str
    file_id:str
    format_name:str=Field(min_length=1,max_length=300)
    format_version:str=Field(default='',max_length=100)
    media_type:str=Field(default='application/octet-stream',pattern=r'^[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+$')
    registry_name:str=Field(min_length=1,max_length=100)
    registry_key:str=Field(min_length=1,max_length=300)
    resource_ids:list[str]=Field(min_length=1,max_length=30)
    significant_properties:dict[str,str]=Field(default_factory=dict,max_length=50)
class PreservationContext(Strict):
    archive_identifier:str=Field(default='',max_length=300)
    archive_name:str=Field(default='',max_length=300)
    designated_community:str=Field(default='',max_length=8000)
    knowledge_base:str=Field(default='',max_length=8000)
    rights:list[SourceRights]=Field(default_factory=list,max_length=1000)
    formats:list[FormatAssessment]=Field(default_factory=list,max_length=10000)

def validate_context(value,transfer):
    context=PreservationContext.model_validate(value).model_dump(mode='json')
    sources={s['id']:s for s in transfer['sips']};resources={r['id'] for r in transfer.get('representation_resources',[])}
    seen=set()
    for right in context['rights']:
        if right['sip_id'] not in sources or right['sip_id'] in seen:raise PackageError('Unknown or duplicate source rights')
        seen.add(right['sip_id'])
        if not set(right['resource_ids'])<=resources:raise PackageError('Unknown rights documentation resource')
    seen=set()
    for assessment in context['formats']:
        key=(assessment['sip_id'],assessment['file_id'])
        if key in seen or key[0] not in sources or key[1] not in sources[key[0]]['files']:raise PackageError('Unknown or duplicate format assessment')
        seen.add(key)
        if not set(assessment['resource_ids'])<=resources:raise PackageError('Unknown representation information resource')
    return context
