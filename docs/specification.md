# Ingest Workbench Specification

Status: consolidated source of truth, 14 November 2026. This describes the agreed product contract, current implementation boundaries, and local operating conventions. Open work is tracked only in [gap-list.md](gap-list.md). Historical increment narratives and repeated status summaries have been retired.

## Purpose and boundary

Ingest Workbench is a Python/FastAPI and Angular reference application for receiving **m SIPs and arranging them into n AIP candidates**. It supports eCH-0160 1.0, 1.1, 1.2.0, and 1.3.0, Keycloak OIDC, METS/PREMIS evidence, SQLite by default and optional PostgreSQL.

This is not a certified preservation system. Receipt validation, preparation review, candidate readiness, institutional approval and archival acceptance are distinct states. Creating a candidate does not imply archival acceptance. Finalization remains disabled.

## Repository layout

| Path | Meaning |
|---|---|
| `backend/app/` | Python domain services, API, persistence and adapters |
| `frontend/` | Angular workbench and browser tests |
| `config/` | Local Keycloak realm/profile and development policy/configuration JSON; not infrastructure-as-code |
| `fixtures/corpus/` | Pinned SIP samples used by regression and qualification tests |
| `fixtures/corpus.json` | Expected sample inventory, profile, source identity, size and digest |
| `schemas/ech-0160/source-bundles/` | Original pinned eCH-0160 schema ZIP downloads |
| `schemas/ech-0160/<version>/` | Extracted trusted eCH-0160 XSDs, one directory per profile |
| `schemas/preservation/` | METS, PREMIS and supporting XML schemas |
| `profiles/` | Workbench AIP application-profile JSON contracts, not eCH schemas |
| `references/provenance.json` | Consolidated source URLs, licenses, and source/import SHA-256 records |
| `docs/` | This specification, the gap list, and retained license texts |
| `var/local-evidence/` | Ignored workstation execution reports and generated evidence; never a source of truth |

The eCH-0160 schemas are intentionally separate from METS/PREMIS schemas. The source ZIPs and extracted XSDs are both retained: the ZIP pins the retrieved source; the extracted files are what the application validates against. `scripts/install_schemas.py` verifies source archive hashes from `references/provenance.json` before extraction. `fixtures/` is for sample SIP data, not schema distributions.

## Input profiles and arrangement

| eCH-0160 profile | Required `schemaVersion` | Runtime schemas |
|---|---:|---|
| 1.0 | 4.0 | Corrected official German 1.0 bundle |
| 1.1 | 4.1 | Official 1.1 bundle |
| 1.2.0 | 5.0 | Official 1.2.0 bundle |
| 1.3.0 | 5.1 | Official 1.3.0 bundle |

The declared profile and `schemaVersion` must agree. Never silently upgrade a submission to a newer schema. Mixed-profile transfers are allowed; each SIP retains its own profile, source bytes, validation and evidence. ZIP/XML parsing is bounded, rejects unsafe paths, collisions, links and encrypted entries, disables XML entities/network access, and uses pinned local schemas.

Mapping supports 1:1, 1:n, m:1 and m:n. The current smallest selectable unit is a top-level dossier; descendants, payload references and ordering stay together. Every accepted unit is assigned exactly once. Shared files or relationships crossing target boundaries and unsupported layouts are rejected; exclusions and finer-grained splitting require explicit policy before implementation. Source identifiers are qualified by SIP identity. Identical bytes do not merge archival identities.

## Lifecycle and gates

The Transfers overview queries tenant-scoped pages through `GET /api/transfer-pages`, with server-side title/status search, deterministic sorting and total/matching counts. Additional pages load on scroll or through the load-more control. Pages reflect current data rather than a frozen snapshot; refresh after concurrent changes.

1. Receive and retain the original SIP byte-for-byte; record digest, profile and receipt findings.
2. Inspect and prepare a working version. Applied edits, notes and structure changes are attributable and retained; source bytes do not change.
3. Freeze exact selected bytes and evidence. Any later edit creates a successor and invalidates current downstream review/authorization pointers without deleting historical outputs.
4. Build a complete m:n plan against the selected versions and preserve target-boundary and relation-closure checks.
5. Obtain independent review from a different authorized subject. Review binds exact inputs, plan, context and documentation digests.
6. Permit candidate creation only through the explicit applicable authorization. Candidate creation is not archival acceptance.

The packager's inline **Validation** view presents working-state prerequisites,
**Freeze & validate**, the latest frozen input's identity and internal checks,
and its advisory external-validation status. It does not open a versions drawer.
An initial unchanged input is frozen by automatically recording a named
checkpoint through the existing attributable-command API, followed by the
idempotent freeze API. These are separate retained operations: a failed freeze
can leave the checkpoint, but never a successful snapshot card without a retained
snapshot. The reason is collected beside the action. Named manual checkpoints
remain in edit history. External findings are inspected in the existing
SIP-Validation panel with the exact frozen artifact selected. Missing or failed
external results are not passes and remain advisory rather than a new gate.
The current snapshot can be passed explicitly into the m:n arrangement preview;
the mapping must still be saved, reviewed and authorized separately. Subsequent
proposals disable handoff; applied edits or checkpoints require a successor
freeze. Earlier snapshots retain results and downloads in collapsed history.

Receipt findings, specialist format validation, preservation readiness, reviewer approval, AIP candidate authorization and acceptance must never be conflated. Unknown facts remain unknown; a failed/unsupported/error check is not a pass.

## Package preparation contract

Navigator behavior is based on the pinned Package Turbo source, not its screenshots: `web/src/app/app.ts` (`classificationRows`, `dossierRows`, `fileRows`, recursive `tree`) and `ph_web/app.py` (`list_children`, including referenced inventory files). The preparation workspace exposes explicit `navigation` rows over canonical metadata objects. Classification contains the complete classification/record hierarchy down to referenced files; Dossiers starts at top-level dossiers and includes nested dossiers, maps, processes, activities, documents and referenced files. Package Content contains the package root and full folder/file hierarchy. Shared files may appear beneath multiple logical owners, with separate stable navigation keys but a single editable metadata object. Repeated references within one owner are deduplicated. Expansion and indentation follow navigation parents rather than XML-kind filters. Selecting a referenced file does not switch the navigator away from Dossiers; search preserves matching objects' ancestor rows. SIP bytes, metadata identities and retained edit evidence are unchanged by these read-only navigation projections.

The embedded Package Turbo-inspired workspace is part of the existing transfer lifecycle. It must use existing archive identity, tenant permissions, persistence and evidence; it must not become a separate application, login, package database or ingest workflow. The following capabilities are the agreed target; implementation status is in the gap list.

| ID | Required behavior |
|---|---|
| PH-01 | Profile-driven metadata editing for all applicable submission, provenance, structure, activity and object fields; preserve unknown source metadata and original language. Support typed, optional, repeated and ordered fields. |
| PH-02 | Create, move, reorder and remove applicable hierarchy objects without changing unrelated identities; validate references and arrangement boundaries. |
| PH-03 | Upload, download, rename, move, replace and remove files/folders; update inventory, fixity and references atomically while retaining original/replaced/removed bytes. |
| PH-04 | Create GEVER and FILES eCH SIPs under the four trusted profiles, then use the same validation/review flow as imported inputs. No implicit version upgrade. |
| PH-05 | Persistent actor/time/reason history with before/after state, compare, restore, undo and redo. Compensating actions remain in history; do not erase prior events. |
| PH-06 | Validate exact working/frozen bytes with trusted schema and receipt checks; provide findings that navigate to affected objects. External format validation remains separate until qualified. |
| PH-07 | Object-linked notes with history and explicit internal-versus-description-candidate purpose; never publish notes implicitly. |
| PH-08 | Advanced metadata/type/date/name/finding search that opens the matching object and identifies whether it searches current or historical versions. |
| PH-09 | Reproducible criteria-based or random sampling bound to the exact population/version, parameters, selections and review evidence. |
| PH-10 | Bulk operations preview every affected object, validate the proposal and retain per-object attributable effects under a shared operation/reason. |
| PH-11 | Permit safe preparation/repair of incomplete or invalid SIPs while preserving exact originals; block progression until required checks pass. |
| PH-12 | Freeze exact versions for review; bind validation, m:n plan, independent review and candidate generation to those versions. Later edits require successor review. |
| PH-13 | Export version-specific eCH SIP and preservation evidence linked by digest, including METS/PREMIS and authorized preparation history. |
| PH-14 | Provide version-bound counts, sizes, format assessments, completeness, broken-reference and validation statistics with object drill-down. |
| PH-15 | Safe, version-bound previews for supported text, image, PDF and audiovisual formats; generated derivatives never replace originals. |
| PH-16 | Import external metadata as a retained proposal; compare, resolve identity/reference conflicts and apply only reviewed changes. |
| PH-17 | Save all pending metadata-field proposals across a SIP atomically under one operation/reason, revision and undo step, retaining per-field identities and before/after evidence. Persist each applied operation, support named checkpoints, show pending/in-flight/retained state, deduplicate exact retries and reject stale concurrent writes. |
| PH-18 | Complete German, French, Italian and English UI labels, help, findings, history and actions; preserve producer-supplied values unchanged. |
| PH-19 | Preserve reviewed Package Turbo SVG assets/action mappings and notices; provide accessible localized labels. |

Local dossier sampling uses `sha256-ranked-v1`: filter applied dossier names and
references case-insensitively, rank by SHA-256 of UTF-8 `seed + NUL + node_id`
(node ID breaks ties), select the requested count and present results in source
order. Percentage sizes round up; results above 500 selections fail explicitly.
Retained sampling operations bind the algorithm, parameters, metadata head and
digest, population digest and selections. Separate attributed review operations
retain a checked/issue outcome and comment for every selected object; issues
require comments. Review evidence remains bound to that original sampling
version after subsequent edits. These advisory preparation notes do not satisfy
independent frozen-input review or candidate authorization.

Local reviewed bulk metadata changes target up to 200 distinct search-matching
objects and only editable scalar, single-valued fields common to all targets.
Protected, structured, attributed and repeated fields are excluded. A read-only
server preview applies the shared batch validator and trusted XSD to the exact
working baseline, returning every actual before/after change and the proposed
metadata digest; unchanged values are skipped. Apply uses that exact reviewed
command identity, baseline, value and reason. Changed proposals require another
preview; stale/invalid proposals fail atomically. Retained batch evidence,
idempotent retry and undo use the same contract as package-wide metadata Save.
This does not implement bulk hierarchy or physical-content operations.

Local preparation statistics bind the exact displayed metadata digest/operation,
source ZIP digest and transfer revision read. Historical API projections use
the requested operation's metadata, not current-head metadata. Counts cover
canonical objects, retained payload files/bytes, populated/empty scalar form
fields, unmet exposed field/attribute requirements, broken file references and
unreferenced payload files. Field indicators are not a semantic completeness
grade or a substitute for trusted XSD/receipt checks. Drilldowns preserve the
binding and open known objects/fields; lists are paged in groups of 100.
Format/finding counts require a structured report for the exact assessed artifact
including original/frozen identity, not merely an equal ZIP digest. They count
report entries/findings, retain run/input/report identities in drilldowns, and
do not map unknown paths to unrelated objects. Missing assessments remain
unassessed rather than zero or passing.

Local external-metadata import accepts UTF-8 CSV/JSON, text-only single-sheet
XLSX and trusted-profile eCH-0160 XML. Explicit templates define the tabular
contract: `object_id`, `field`, `value`, optional `before`; JSON is an array.
CSV templates use JSON-string value/baseline columns and XLSX uses text cells
to avoid formula execution. Limits are 256 KiB source, 2,000 source rows, 200
changed/unresolved fields, and 100 XLSX ZIP members / 4 MiB expanded data.
XLSX formulas, numeric/date cells, external relationships, unsafe ZIP members,
and XML DTDs are rejected.

`import-record` retains the exact original source bytes/digest and baseline
without changing metadata or undo state. Every changed/unresolved row needs an
explicit apply/mapping or reasoned exclusion. Application is limited to editable
scalar, single-valued fields; structured/repeated changes, native IDs, references,
payload paths and hierarchy are not imported. XML differences outside that scope
remain visible as blocked exclusions. Unknown identities/fields can be mapped to
eligible existing targets; duplicate destinations are rejected. External
baseline differences require explicit review, never automatic overwrite.
Read-only preview and atomic application share the batch validator and trusted
XSD, bind the retained proposal as the exact current operation head, and retain
decisions, actual edits, actor/reason, exact retries and one-step undo. An
all-excluded review retains decisions without XML serialization or an undo step.
Intervening operations require a retained successor proposal. Exact raw sources
are included in METS/PREMIS evidence and verified through freeze/recovery;
templates/source downloads require reader access, record/preview/apply require
operator access, and all are SIP/tenant scoped.

Original-file previews are opt-in and retain the original SIP/member identity and
SHA-256 even when launched from the working editor. The authenticated client
fetches bounded attachment bytes, checks exact size and SHA-256, and performs no
metadata/evidence write. Existing escaped UTF-8 previews remain separate.
HTML, SVG and unknown formats remain attachment-only; no submitted document is
embedded in an iframe/object or exposed as interactive HTML.

The passive browser preview policy supports PNG/JPEG/GIF/WebP, bounded classic
TIFF and gray/RGB boxed JP2 (8 MiB, 8192 pixels per side, 16,000,000 pixels);
canvas-only PDF (12 MiB, 200 pages, viewport at most 1600 by 2400 pixels);
and native audio/video controls for supported WAV/MP3/Vorbis-or-Opus Ogg/M4A/
MP4/WebM codecs (32 MiB, positive finite duration up to two hours, bounded video
dimensions). Image sequences use only the first frame. Header eligibility is
not format validation. PDF scripts, links, forms and annotation surfaces are
not enabled; unsupported/oversized embedded content can be omitted. Previews
are explicitly not completeness/fidelity assessments.

Manifest loading, source loading/decoding and each PDF page render have
20-second deadlines. Decoding uses lazy same-origin PDF.js assets and dedicated
TIFF/JP2 workers; closure cancels work, clears canvases, stops media and revokes
Blob URLs. Runtime failures remain explicit, with original downloads available.
Browser-side bounds are not isolated-process memory quotas, malware quarantine
or production qualification. No generated preview replaces or modifies original
bytes, working metadata, receipts or candidate evidence.

Local `content-edit` operations support reviewed payload-file rename and byte
replacement. Preview is read-only and binds revision, original SIP digest,
operation head, stable file identity, action and exact source/name. Apply retains
actor/reason, before/after paths, sizes, SHA-256 and exact content archives under
operation contract 9, with shared undo/redo/restore and exact retries. Rename
cannot replace bytes; replacement cannot implicitly rename, change native IDs,
references, protected headers or schema members. The existing submitted checksum
algorithm is retained and its digest recomputed. Trusted XSD and complete receipt
checks run against the resulting SIP before applying the operation.

Working metadata and payload histories are independent snapshots under the same
operation head; intervening metadata edits, notes and compensation do not reset
payload bytes. Read-only working-SIP downloads and statistics project that head,
including historical heads. METS/PREMIS links each retained content archive;
byte-exact ZIP recipes reuse original members and retained metadata versions,
keeping changed payloads once rather than copying all unchanged payloads into
every preparation archive. Replay verifies the exact authorized delta, not merely
the resulting inventory's internal consistency. Recovery retains referenced
content archives as well as their preparation evidence.

Frozen-input contract 4 permits only these replay-verified payload changes and
retains complete original/prepared member lineage. All file-reference occurrences
and attributes remain conserved. Earlier frozen-input contracts keep their
metadata-only member-conservation rules. Selected-input review and self-contained
candidate verification use the exact frozen bytes; subsequent changes invalidate
current review/authorization rather than rewriting sealed artifacts.

The existing 64 MiB ZIP / 256 MiB expanded package and evidence limits still
apply; replacement sources are capped at 256 MiB before packaging. Nested frozen
evidence can exceed its limit even when the working SIP fits. Failure leaves
the retained head and prior freezes unchanged. File/folder creation, moves,
removal and complete SIP creation are separate unfinished acceptance slices.

## Evidence and immutability

Every applied metadata, hierarchy, content, note, appraisal and bulk action binds an authenticated actor, timestamp, operation identity, stable affected object/version, reason or decision context, outcome, and before/after values or retained-byte digests. Retries, concurrent writes, restart and restore preserve operation identity and ordering. The durable journal and exported METS/PREMIS must describe the same events; changes and evidence commit atomically.

Use schema-valid METS/PREMIS objects, events, agents, relationships and stable identifiers. Keep detailed payloads content-addressed and linked; do not invent schema fields. Original submissions, unknown metadata, removed objects and prior versions remain traceable. Later events do not rewrite sealed candidates or receipts; create linked evidence or explicit successors. Internal notes and history obey access/export policy. Hash chains and embedded digests detect inconsistency but are not digital signatures or independent audit anchors.

## AIP candidates

Two versioned candidate contracts are retained under `profiles/ch-aip/`:

- Profile 3.0.0 is the current preparation-only source candidate contract.
- Profile 4.0.0 is the current preparation-only selected-input candidate contract.

Candidates retain stable logical identity, immutable numbered versions, predecessor digest, source-qualified hierarchy, selected payload, manifests, METS/PREMIS, workflow evidence and explicit preservation context. The database-free verifier checks ZIP safety, fixity, profile schemas, XML, graph closure, lineage and review bindings. Prepared-input profiles embed a shared preparation dossier in each target and are not redacted distribution packages. Structural validity, preservation readiness, institutional approval and archival acceptance are separate. The shipped institutional policy remains unapproved.

Candidate profiles 1.0.0 and 2.0.0 and recovery snapshots in the previous format are no longer supported. Recovery refuses retained records from the retired integrations rather than silently omitting them. Existing databases, blobs and former simulator volumes are not deleted.

## Identity, security and persistence

Angular is a public Keycloak client using Authorization Code + PKCE S256; tokens remain in adapter memory. The API validates JWT signature, issuer, audience, expiry, token type and allowed client. Authorization uses verified subject, archive scope, roles and object/tenant checks; never trust browser-supplied actor or tenant headers. Two-person review is enforced server-side.

Treat uploads as hostile. Keep ZIP limits, XML entity/network protections, schema pinning and strict boundary validation. Production still requires parser isolation, malware quarantine, quotas/resumable upload, restricted egress, TLS, managed secrets, production Keycloak/MFA, least privilege, operational logs/metrics and independent audit controls.

SQLite and PostgreSQL are separate supported local persistence paths. PostgreSQL uses explicit Alembic migrations; startup must not create/upgrade its schema or silently fall back to SQLite.

Application snapshot/restore requires stopped workers, explicit quiescence, a new destination and separately retained manifest digest. Workflow records and blobs must be restored consistently. Identity, secrets, infrastructure HA and disaster recovery are separate. Restore expires old capabilities and never sends external writes.

## External systems

### Advisory KOST SIP validation

KOST-Val is a separately operated HTTP service, not a CLI subprocess inside the workbench. When enabled, successful receipt uploads and newly frozen prepared inputs atomically queue background validation. Existing inputs remain unassessed until an operator requests a run. Mutable working metadata and derived exports are not submitted. Each run binds tenant, transfer, SIP, optional frozen-input identity, profile and exact ZIP SHA-256; results never carry over to another artifact or later edits.

The original multipart upload filename is retained on the SIP and in its receipt event, and sent unchanged to KOST on every first run and re-run. Frozen inputs use that same source filename. Preserve its spelling, case and extension; do not generate a name from internal ZIP paths or run identifiers, and do not rewrite a nonconforming name into a conforming one. Path/control characters are rejected rather than silently sanitized. Older uploads that predate filename retention remain readable, but new KOST submissions stop with a service error explaining that the original filename is unavailable; upload those bytes into a new transfer with the original filename. Previously retained validation reports remain historical evidence and are not rewritten.

Ingest Packager exposes KOST status in both preparation and original-inspection modes. Operators can select a received or frozen input, inspect module/message findings, download retained XML, and re-run validation. Readers can inspect results and reports. The panel polls lightweight local endpoints, preserving unsaved metadata, focus and transfer revisions. Frozen-version actions select that version's result. Queued/submitting/running, valid, invalid, service error and submission uncertainty remain distinct. All KOST results are advisory: receipt checks, independent review, candidate readiness, institutional approval and archival acceptance are unchanged.

The authenticated SIP-scoped `kost-validations` API supplies artifact summaries, operator POST submission, paginated `runs`, per-run status, and retained `runs/{id}/report` attachments. It never exposes service credentials or redirects report downloads to the external service. XML and response sizes are bounded; findings are escaped text. Reports and raw job responses are content-addressed locally; journal records and transition events are retained in the workflow database and included in application recovery. Aggregate-only import/export refuses datasets with KOST history; use full recovery capsules instead. Restore expires leases and holds interrupted uploads as uncertain without contacting KOST.

`runs/{id}/findings` provides a versioned, read-only projection of retained KOST XML and the run's exact ZIP inventory, including historical reports without re-running validation. The package-wide table separates package and file assessments, supports outcome/severity/text filters, and preserves valid, invalid, recognized-but-not-validated and not-assessed outcomes. Warning markers qualify their preceding finding; informational ffprobe messages are not failures. Missing-resource and unknown-runtime diagnostics are identified separately as validator errors while the original KOST verdict remains visible. Raw XML remains downloadable if parsing fails; malformed reports never become passes. XML entities, DTDs and network resolution are disabled/refused, with bounded report/entry/finding sizes.

Host Windows/Unix report paths are mapped only to a unique, full case-sensitive ZIP-member suffix. Traversal, ambiguous paths and unmatched names remain unmapped; filenames alone are not substituted for member identity. Table links open the corresponding source file and its contextual findings when the report belongs to that source version and no edits are pending. Pending edits remain intact, with report details kept in the validation panel instead. Both original inspection and metadata file forms show the selected run's per-file findings. ZIP hash and frozen-input identity must match; a result is not applied to another artifact or a later unfrozen working version. The report's own file counts and summary remain distinct from recognition-versus-validation counts computed by the projection. These views do not change institutional approval or acceptance gates.

The worker submits multipart `sip` with configured language and XML logging, persists the returned external UUID, polls known jobs, and retries safe status/report reads with bounded backoff. Connection failures before upload stay queued. An upload timeout, invalid acknowledgement or interrupted submission can leave a remote job without a locally known ID: this becomes `SUBMISSION_UNKNOWN`, never an automatic POST retry. An operator must explicitly confirm superseding it; a duplicate remote job may exist. Report retrieval failure does not erase the validation outcome. The current service contract lacks submission idempotency, uploaded-content digest attestation, and full engine/configuration provenance; unknown provenance is not fabricated and this integration is not production format-validation qualification.

Enable with `KOSTVAL_ENABLED=true`, `KOSTVAL_URL`, optional backend-only `KOSTVAL_API_KEY`, and `KOSTVAL_LANGUAGE=de|fr|it|en`. Defaults are disabled, German XML reports, five-second polling and a 3600-second overall deadline; tune `KOSTVAL_POLL_SECONDS` and `KOSTVAL_DEADLINE_SECONDS`. Preserve existing `.env`; add only the required settings. In Podman use `http://host.containers.internal:8001` for the host service and verify its bind/firewall reachability; native Python defaults to `http://localhost:8001`. The normal Windows launcher starts and checks the supervised `kost-worker`; it stays idle when disabled. Use `./scripts/start_local.ps1 -Recreate` after rebuilding when the provider retains containers referencing old image tags. A KOST outage degrades validation, not receipt/readiness. The optional persistence profile has a separate `postgres-kost-worker` using its API's database and volume; explicitly migrate PostgreSQL to `0005_kost_service` before starting it. Set `POSTGRES_API_PORT=8002` if KOST already occupies 8001. For native development, run `python -m app.kost_worker` with the same `PYTHONPATH`, data/database and KOST settings as the API.

## Local development and checks

Use Podman, not Docker. Requirements are rootless Podman plus `podman-compose`; Windows also needs a running Podman Machine. If `.env` does not exist, copy `.env.example` once and preserve any existing local settings. Run `python scripts/install_schemas.py` to verify/install the pinned schema bundles. On Windows, from the project root run `./scripts/start_local.ps1`. The launcher selects `podman-compose`, explicitly builds images, starts services and waits for API, Keycloak and workbench readiness. Do not replace it with direct `podman compose up --build` on Windows. On Linux, set `PODMAN_COMPOSE_PROVIDER=podman-compose`, check `podman compose config`, then use `podman compose up --build -d`. Preserve `.env` and named volumes; never remove volumes as a routine restart.

The workbench container mounts frontend `src` and `public` read-only and polls for changes. Angular applies updates or reloads the browser as needed. Dependency, Angular configuration and backend changes require rebuilding and recreating the relevant containers.

Before upgrading an existing deployment, stop all older application and worker processes and let any in-flight work finish; removing services from the configuration does not stop existing containers. Historical records and evidence are not rewritten. Approvals for an earlier profile must be renewed before building a new candidate.

Install pinned XSDs with `python scripts/install_schemas.py`. Useful checks:

Before running corpus qualification or the backend tests, download and verify
the [reference SIP fixtures](#reference-sip-fixtures).

```powershell
PYTHONPATH=backend python -m unittest discover -s backend/tests -v
python scripts/qualify_corpus.py
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix frontend run test:browser
```

Open the workbench at `http://localhost:4200`, API docs at `http://localhost:8000/docs`, and Keycloak at `http://localhost:8080`. Optional PostgreSQL uses `POSTGRES_API_PORT` (legacy default 8001, example configuration 8002) and requires explicit migration. These are development endpoints and credentials only.

### Reference SIP fixtures

The eight reference SIPs are not included in the repository because their contents
and redistribution terms need review. Download them from their official sources
and save them at the indicated paths without extracting or modifying the ZIPs:

| Download | Save as |
| --- | --- |
| [KOST eCH 1.0 GEVER](https://kost-ceco.ch/cms/dl/38d35e2c77c9899e31921fce35b6fcc1/SIP_20070923_eCH1.0G_RIS.zip?target=1) | `fixtures/corpus/SIP_20070923_eCH1.0G_RIS.zip` |
| [KOST eCH 1.0 FILES-DOC](https://kost-ceco.ch/cms/dl/590bed8975de1020a95546244bb3c283/SIP_20100222_eCH1.0Fdoc_BFFeuerwehr.zip?target=1) | `fixtures/corpus/SIP_20100222_eCH1.0Fdoc_BFFeuerwehr.zip` |
| [KOST eCH 1.0 FILES](https://kost-ceco.ch/cms/dl/c8c7d85bf0527f4a56a33ae3688fbd39/SIP_20120628_eCH1.0F_BfB.zip?target=1) | `fixtures/corpus/SIP_20120628_eCH1.0F_BfB.zip` |
| [KOST eCH 1.1 GEVER](https://kost-ceco.ch/cms/dl/33a3c30d47738b6e96282f59592024a7/SIP_20070923_eCH1.1G_RIS_vorgang.zip?target=1) | `fixtures/corpus/SIP_20070923_eCH1.1G_RIS_vorgang.zip` |
| [KOST eCH 1.2 FILES](https://kost-ceco.ch/cms/dl/ce2a486d306cda70b271bae6ab10129d/SIP_20220328_KOST_eCH1.2F_1a-3d-IO.zip?target=1) | `fixtures/corpus/SIP_20220328_KOST_eCH1.2F_1a-3d-IO.zip` |
| [KOST eCH 1.2 GEVER](https://kost-ceco.ch/cms/dl/7bb269c27e52cb6cfd988a38d481129c/SIP_20220328_KOST_eCH1.2G_1a-3d-IO.zip?target=1) | `fixtures/corpus/SIP_20220328_KOST_eCH1.2G_1a-3d-IO.zip` |
| [KOST eCH 1.3 FILES (2026)](https://kost-ceco.ch/cms/dl/4e987f3ea41c4da065b191e39c34cf8c/SIP_20260421_KOST_eCH1.3F_1a-3d-IO.zip?target=1) | `fixtures/corpus/SIP_20260421_KOST_eCH1.3F_1a-3d-IO.zip` |
| [eCH 1.3 FILES (2024)](https://ech.ch/sites/default/files/imce/eCH-Dossier/0151-0180/eCH-0160/1.3.0/Beilagen/SIP_20240430_KOST_eCH1.3F_1a-3d-IO.zip) | `fixtures/reference-1.3.zip` |

The last download must be renamed as shown; it is a different archive from the
2026 sample. The [KOST test-package page](https://kost-ceco.ch/cms/ech-0160-archivische-ablieferungsschnittstelle.html)
also lists the KOST downloads. From the repository root, verify all corpus
files against the SHA-256 hashes in `fixtures/corpus.json`:

```shell
python -c "import hashlib,json,pathlib; cases=json.loads(pathlib.Path('fixtures/corpus.json').read_text(encoding='utf-8'))['cases']; bad=[c['file'] for c in cases if hashlib.sha256(pathlib.Path(c['file']).read_bytes()).hexdigest()!=c['sha256']]; print('SHA-256 mismatches: ' + ', '.join(bad) if bad else 'All corpus hashes match'); raise SystemExit(bool(bad))"
```

The check fails if a file is missing. If a download no longer matches, do not
update the pinned hash without reviewing the changed source and test expectations.

## Sources and licenses

`references/provenance.json` is the canonical machine-readable record for pinned external sources and the reviewed Package Turbo source/import hashes. `fixtures/corpus.json` remains the canonical inventory of test SIP bytes. Runtime configuration in `config/` and AIP contracts in `profiles/` remain separate because they are active application inputs, not redundant source citations. Preserve the license texts in `docs/licenses/` and the repository-level `THIRD_PARTY_NOTICES.md`.

Primary standards and integration references include OAIS CCSDS 650.0-M-3, eCH-0160 versions 1.0/1.1/1.2.0/1.3.0, METS, PREMIS 3, Keycloak JS adapter and Angular compatibility guidance. Retrieved artifacts and digests are indexed in the provenance registry.