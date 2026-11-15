"""Prepared-input candidate wrappers."""
from typing import Literal
from .aip_models import Strict, Digest, Uuid, SourceRecord, Approval, Descriptor, ObjectRecord, Target


class SelectedSource(SourceRecord):
    # sha256/size/metadata refer to selected bytes; these fields retain receipt lineage.
    source_sha256: Digest
    source_size: int
    prepared_id: Uuid | None


class CandidateAuthorization(Approval):
    id: Uuid
    review_id: Uuid
    review_evidence_sha256: Digest
    inputs_sha256: Digest
    reason: str
    revision: int
    request_sha256: Digest
    production_acceptance: Literal[False]


class Input(Strict):
    sip_id: Uuid
    prepared_id: Uuid | None
    source_sha256: Digest
    input_sha256: Digest
    profile: Literal['1.0', '1.1', '1.2.0', '1.3.0']
    schema_bundle_sha256: Digest
    size: int
    preparation_authors: list[str]
    receipt_checks: list[str]
    operation_id: Uuid | None = None
    metadata_sha256: Digest | None = None
    preparation_sha256: Digest | None = None
    freeze_evidence_sha256: Digest | None = None


class Plan(Strict):
    format: Literal[2]
    id: Uuid
    transfer_id: Uuid
    tenant: str
    targets: list[Target]
    reason: str
    author: str
    created: str
    inputs: list[Input]
    sha256: Digest


class PreparationDescriptor(Descriptor):
    profile_id: Literal['urn:ch-archive:aip-profile:4.0.0']
    profile_version: Literal['4.0.0']
    sources: list[SelectedSource]
    approval: CandidateAuthorization
    preparation_snapshot_sha256: Digest


class PreparedLineage(Strict):
    aip_id: Uuid
    plan: Plan
    files: list[ObjectRecord]
