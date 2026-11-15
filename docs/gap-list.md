# Ingest Workbench Gap List

Updated 14 November 2026. This is the single backlog and qualification-status list. The [specification](specification.md) defines required behavior. No local or simulated result enables production archival acceptance.

## Package preparation

The Package Turbo takeover target is not complete. Keep the following work visible as separate acceptance items:

- **PH-01/02:** finish schema-driven process/activity identity allocation, cross-parent moves and removal; preserve IDs, references, order, notes and review invalidation.
- **PH-03/04:** implement payload/folder upload, replace, move, rename and removal with retained bytes/fixity; create complete GEVER/FILES SIPs through the normal workflow.
- **PH-06/11:** unified repair workflow for invalid/incomplete submissions; retain exact versions and block progression until required checks pass. Advisory KOST service validation covers received and frozen SIPs, with a filtered package/file table, file-linked findings and explicit validator-error classification. Qualified specialist validation and guided repairs remain open.
- **PH-08/09/10/14/15/16:** advanced structured search; reproducible, version-bound sampling with retained selections and review evidence; reviewed bulk actions; version-bound statistics; safe preview coverage; and reviewed external metadata import remain incomplete. The Sample tool supports a read-only random draw and CSV export, but does not retain review evidence.
- **PH-17:** current edits are persisted as individually attributable commands; atomic package-wide Save is still required. Preserve exact retry, named checkpoints, stale-write rejection and history.
- **PH-18/19:** complete whole-workbench DE/FR/IT/EN coverage, accessibility/usability review, and all required action/icon placements. Existing translated controls and imported SVGs do not establish parity.
- **Reference workflow:** finish lifecycle/open/import/close options, metadata-only and unsupported-profile inspection, contextual actions, structured additional data, report/export choices and operator-facing recovery guidance.

Implemented local slices include source browsing, profile-driven metadata forms, attributable edits/checkpoints/undo/redo, object-linked notes, sibling reorder, native-ID creation of classification positions/dossiers/documents/maps, frozen inputs, selected-input independent review, prepared-input candidates, and the embedded reference-style workspace. These do not imply full PH coverage.

## Preservation policy and AIP

- The AIP application profiles are proposals; institutional approval remains false.
- Complete approved policy for designated community, significant properties, rights, representation information, formats, retention and access restrictions.
- Advisory external KOST validation is integrated separately from acceptance. Engine/configuration attestation, uploaded-content attestation, submission idempotency, qualified format identification/validation policy and preservation transformations remain open.
- The live wrapper round-trip retained results and reports, but KOST returned module `1A) Lesbarkeit` (could not unpack) for the reference SIP even with a package-directory-matching ZIP filename. Investigate the external wrapper/CLI extraction environment before treating this as a qualified conformance verdict; the workbench preserves the reported invalid result without claiming acceptance.
- Filename follow-up: uploads now retain their actual multipart filename in the SIP and receipt event; KOST receives it unchanged for initial runs, re-runs and frozen inputs. Twenty-seven focused KOST/package-handler tests passed, including a filename differing from the internal ZIP directory. The deployed service CLI log confirmed the exact uploaded `reference-1.3.zip` name for first run and re-run. Older uploads without retained filenames must be uploaded into a new transfer; no generated-name validation is submitted. Earlier reports produced under inferred names are historical, not qualified verdicts on the originally named SIP.
- Large SIP/history streaming, asynchronous candidate builds, capacity limits and realistic-scale testing remain open. Prepared-input candidates duplicate shared preparation evidence per target and are not redacted packages.
- Independent signatures/trusted timestamps/audit anchors and authorized production evidence retention remain open.

## Production operations and security

- Production Keycloak realm, MFA, TLS, secret management, least-privilege service identities, restricted egress and independent security review.
- Malware quarantine, isolated parsers/validators, request limits, quotas, resumable uploads, and aggregate concurrency/per-tenant memory budgets. Current per-request ZIP limits do not bound total parser memory under concurrent uploads.
- Production database/object-store HA, monitoring/alerts, audit retention/anchors, migration/cutover, worker access controls and outage/uncertain-commit drills.
- Full infrastructure backup/restore including identity, configuration, external repositories, offsite encryption, scale and archive-led recovery acceptance.
- Qualify all four locales/accessibility with archival operators.

## Verification baseline and limits

The deployed splitter was checked on an existing transfer: a 60-pixel drag changed the right panes without moving the left navigator. This check does not qualify production recovery or archival acceptance.

The navigator correction was reviewed against the pinned Package Turbo recursive tree builder and child API, including its referenced-file projection. Two focused backend tests cover all eight corpus profiles plus nested records and shared/repeated references; the Angular build, complete-navigator browser regression, and both deterministic KOST browser scenarios passed. Classification, Dossiers and Package Content preserve all descendant rows, canonical object selection and independent branch controls. Desktop/mobile layouts were checked for overflow. The full working-metadata regression module was not verified in this increment: a bounded host run expired during evidence-ZIP generation, and the isolated container run's final status was not captured. Do not treat these focused checks as a clean full-suite result.

The structured KOST-report increment passed 34 focused backend tests (`test_kost_report`, `test_kost_validation`, `test_package_handler`), the Angular build, and two deterministic browser scenarios covering progress, filtered package/file findings, escaped messages, file-context downloads, unsaved edits and original/frozen isolation even with identical ZIP hashes. Desktop/mobile screenshots were reviewed and the Windows launcher deployed the current API/frontend/worker images with existing volumes retained. The supplied full XML was also parsed against its original ZIP: 23 format-valid files, three recognized files, one invalid SIARD, three error findings, three warnings, one information finding and one package-level validator error. This increment did not rerun the full suite or qualify the external KOST engine/resource configuration.

Latest recorded feature checks are incremental, not a fresh full-suite result. The embedded interface and several affected workflows passed local Keycloak/browser checks against SQLite and PostgreSQL; host and Node 24 production builds passed. The latest recorded complete backend suite was 418 tests at an earlier increment; later work has focused regressions rather than a new full-suite count. Eight pinned SIP samples exercise four eCH profiles, but the corpus lacks 1.1 FILES and 1.3 GEVER examples.

The KOST integration increment passed 26 focused backend tests across SQLite and native PostgreSQL, the Angular production build, Compose configuration, a desktop/mobile browser test with deterministic KOST responses, and an opt-in live service upload/result/report/re-run check. The local images were built and the Windows launcher started the API, frontend and healthy KOST worker with existing volumes retained. The existing package/workflow slice passed 22 of 23 tests; `test_sqlite_rejects_rewritten_event_history` still expects a different error message from the unchanged event-chain validation order. Eleven existing preparation tests reported success; the subsequent broader native recovery invocation stalled and was stopped. KOST recovery round-trips passed on both databases. These are focused results, not a clean full-suite claim. Host checks used Python 3.13 with pinned dependencies; the container runtime remains Python 3.12. Browser checks do not establish full locale/accessibility parity or qualified preservation acceptance. Set `ARCHIVE_KOST_LIVE=true` to opt in to the live test in `frontend/tests/kost-validation.spec.ts`; otherwise it is skipped.