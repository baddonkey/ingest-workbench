"""Production validation adapter contracts."""
from dataclasses import dataclass
from typing import Protocol, Literal

@dataclass(frozen=True)
class ValidationEvidence:
    input_sha256: str
    profile: str
    status: Literal['PASS','FAIL','ERROR','UNSUPPORTED']
    tool: str
    tool_version: str
    configuration_sha256: str
    raw_report_sha256: str
    checked_at: str

class Validator(Protocol):
    def validate(self, blob_sha256:str, profile:str) -> ValidationEvidence: ...

@dataclass(frozen=True)
class StorageReceipt:
    aip_id: str
    version: int
    sha256: str
    persistent_uri: str
    repository_version_id: str
    verified_at: str
    receipt_sha256: str

class ArchivalStorage(Protocol):
    def deposit(self, *, aip_id:str, version:int, blob_sha256:str, idempotency_key:str) -> StorageReceipt: ...
    def verify(self, receipt:StorageReceipt) -> bool: ...

@dataclass(frozen=True)
class AISReceipt:
    aip_id:str
    version:int
    external_record_id:str
    mapping_profile:str
    request_sha256:str
    acknowledgement_sha256:str

class ArchivalInformationSystem(Protocol):
    def upsert_description(self, *, storage:StorageReceipt, metadata_sha256:str, idempotency_key:str) -> AISReceipt: ...
