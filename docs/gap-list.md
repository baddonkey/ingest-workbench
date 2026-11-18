# Ingest Workbench Gap List

Remaining work from the original backlog. See the [specification](specification.md) for requirements.

## Package preparation

- **PH-01/02 — Hierarchy editing:** create processes and activities, move objects between parents, and remove branches without breaking IDs, references or notes. Stable editor IDs are started but unverified; they are part of this gap.
- **PH-03/04 — Content operations:** upload, move and remove files and folders, rename folders, and create complete GEVER and FILES SIPs. Branch removal removes only newly unreferenced files from the working SIP; shared files and historical bytes are kept.
- **PH-06/11 — Repair:** inspect invalid or incomplete SIPs, guide repairs, link findings, and block progression until required checks pass.
- **PH-10/16 — Bulk actions and metadata import:** finish reviewed bulk hierarchy/content actions and reviewed import of nested and repeated metadata.
- **PH-18/19 — Package Turbo parity:** finish open/import/close, metadata-only and unsupported-profile inspection, contextual actions, structured additional data, report/export and recovery workflows. Verify DE/FR/IT/EN, accessibility and action/icon placement.

## Preservation policy and AIP

- Approve institutional AIP profiles and policies for designated community, significant properties, representation, formats, rights, retention and access.
- Investigate the SIP validator's unpacking failure and qualify specialist validation, engine/configuration and uploaded-byte attestation, submission idempotency and preservation transformations.
- Stream large SIPs and histories, support asynchronous candidate builds, and test capacity at realistic scale.
- Add independent signatures, trusted timestamps, audit anchors and approved evidence retention.

## Production operations and security

- Qualify production identity, MFA/TLS, secrets, least-privilege service access and restricted egress; obtain independent security review.
- Add quarantine, parser/validator isolation, request limits, quotas, resumable uploads and aggregate/per-tenant resource limits.
- Qualify database/object-store HA, monitoring, audit retention, migration, worker access controls and outage recovery.
- Verify full infrastructure backup and restore, including identity, configuration, external repositories, encrypted offsite copies and archive-led recovery.
- Qualify all four locales and accessibility with archival operators.
