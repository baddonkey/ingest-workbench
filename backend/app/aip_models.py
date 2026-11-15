"""Normative JSON wrapper models for source candidates."""
from typing import Annotated,Literal
from pydantic import BaseModel,ConfigDict,Field
Digest=Annotated[str,Field(pattern=r'^[0-9a-f]{64}$')]
Uuid=Annotated[str,Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')]
class Strict(BaseModel):model_config=ConfigDict(extra='forbid',strict=True)
class SourceRecord(Strict):
    id:Uuid;profile:Literal['1.0','1.1','1.2.0','1.3.0'];sha256:Digest;size:int=Field(ge=1)
    original_metadata_path:str;metadata_path:str;metadata_sha256:Digest;unit_ids:list[str]=Field(min_length=1)
class Resource(Strict):
    id:Uuid;title:str;original_name:str;media_type:str;sha256:Digest;size:int=Field(ge=0);path:str
class Approval(Strict):
    profile_sha256:Digest;approver:str;author:str;context_author:str|None;plan_sha256:Digest;context_sha256:Digest
    target_contexts:dict[str,Digest];target_resources:dict[str,dict[str,Digest]];time:str
class Previous(Strict):version:int=Field(ge=1);version_id:Uuid;sha256:Digest
class Descriptor(Strict):
    profile_id:Literal['urn:ch-archive:aip-profile:3.0.0'];profile_version:Literal['3.0.0'];profile_sha256:Digest;status:Literal['CANDIDATE']
    archive_scope:str;aip_id:Uuid;version:int=Field(ge=1);version_id:Uuid;representation_id:str;previous_version:Previous|None
    label:str;created_at:str;sources:list[SourceRecord]=Field(min_length=1);resources:list[Resource];plan_sha256:Digest;context_sha256:Digest;approval:Approval
class ObjectRecord(Strict):
    sip_id:Uuid;source_id:str;source_path:str;target_path:str;object_id:str;sha256:Digest;size:int=Field(ge=0)
    original_algorithm:Literal['MD5','SHA-1','SHA-256','SHA-512'];original_digest:str
class UnitRef(Strict):sip_id:Uuid;unit_id:str
class Target(Strict):label:str;units:list[UnitRef]=Field(min_length=1);aip_id:Uuid
class Input(Strict):id:Uuid;sha256:Digest
class Plan(Strict):targets:list[Target]=Field(min_length=1);reason:str;author:str;created:str;inputs:list[Input]=Field(min_length=1);sha256:Digest
class Lineage(Strict):aip_id:Uuid;plan:Plan;files:list[ObjectRecord]
