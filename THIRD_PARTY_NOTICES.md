# Third-party notices

The project's [MIT license](LICENSE) does not replace the terms of third-party
code, data, schemas, images or services. Versions and licenses below describe
the checked-in manifests and the local Linux API image inspected on
2026-11-15; they are not a guarantee of redistribution clearance.

## Ingest Packager design reference

The Ingest Packager workspace is informed by
[baddonkey/package-turbo-web](https://github.com/baddonkey/package-turbo-web),
reviewed at commit `4aec0aec9a42715caab6eeaecac944ef2c116e88`.
Its classification/content navigators, selected-object metadata, separate tools
pane and explicit source/draft distinction guide the integration. The current
code is adapted to this application's transfer model, immutable blobs, Keycloak
identity, tenant permissions, pinned KOST worker and archival acceptance gates.

Package Turbo original code is MIT licensed, copyright (c) 2026 Package Turbo
contributors. The complete [MIT notice](docs/licenses/package-turbo-MIT.txt)
is retained. The workspace layout also adapts the reference toolbar, classification/content
panes, aligned fields and lower tools arrangement; reviewed HTML/CSS digests
are recorded in the provenance manifests. The versioned metadata editor adapts `ph_backend/xsd_info.py` and
`ph_backend/schema.py` as `backend/app/editor_xsd.py` and
`backend/app/editor_labels.py`, and reuses the reference SVG action icons in
`frontend/public/package-turbo/`. Lucide icons are ISC licensed; the upstream
Feather-derived icon exceptions are MIT licensed. The complete
[Lucide/Feather notice](docs/licenses/lucide-LICENSE.txt) is retained, including
both copyrights and the exception list. The Package Turbo SVG workspace mark
is now reused for the embedded Ingest Packager. Its context-menu component is
adapted as `frontend/src/context-menu.ts` with this application's asset paths;
the reference navigator badges, table columns, field and chrome styling also
guide the embedded UI. Their source hashes are recorded in the import manifest.
The product is Ingest Workbench with its existing archive identity and lifecycle.
The notes inline composer, five-column table, context-menu copy/edit/remove
commands and edit dialog also adapt the reviewed reference HTML/TypeScript;
their source hashes are retained in the same import manifest.
Container distributions retain the code notice under `/application/licenses/`;
the frontend ships both notices beside its SVG assets under
`/package-turbo/LICENSE.txt` and `/package-turbo/PACKAGE_TURBO_MIT.txt`.
No upstream schemas, binaries, lockfiles, runtime dependencies or local
authentication/session implementation have been imported.

See the [consolidated specification](docs/specification.md#sources-and-licenses)
and the machine-readable [source provenance registry](references/provenance.json).

## Frontend dependencies

The complete npm dependency inventory, including transitive and optional build
and test dependencies, is in [frontend/package-lock.json](frontend/package-lock.json):
each package entry records its version, source and declared license. The lockfile
includes platform-dependent packages that may not be installed or distributed
on every platform. Installed packages retain their own license and copyright
notices in `frontend/node_modules/`; these notices must be retained as required
when redistributing dependencies or build outputs.

| Direct dependency | Version | Declared license | Use |
| --- | --- | --- | --- |
| @angular/common, @angular/compiler, @angular/core, @angular/forms, @angular/platform-browser | 21.2.26 | MIT | Runtime |
| keycloak-js | 26.2.2 | Apache-2.0 | Runtime |
| rxjs | 7.8.2 | Apache-2.0 | Runtime |
| tslib | 2.8.1 | 0BSD | Runtime |
| @angular/build, @angular/cli | 21.2.26 | MIT | Development/build |
| @playwright/test | 1.65.0 | Apache-2.0 | Browser tests |
| @types/node | 26.6.6 | MIT | Development types |
| typescript | 5.9.3 | Apache-2.0 | Development/build |

Two transitive data packages explicitly require attribution: [caniuse-lite](https://github.com/browserslist/caniuse-lite)
1.0.30001826, by Ben Briggs and contributors, is CC-BY-4.0; and
[spdx-exceptions](https://github.com/kemitchell/spdx-exceptions.json) 2.5.0,
by The Linux Foundation and contributors, is CC-BY-3.0. The installed package
metadata and license texts provide their full terms; changes to their material
must be indicated where applicable.

## Python dependencies

Direct Python requirements are pinned in [backend/requirements.txt](backend/requirements.txt)
and [backend/requirements-postgres.txt](backend/requirements-postgres.txt).
Transitive Python dependencies are not locked; the following is a snapshot of
the installed Linux API image, based on distribution license metadata. Other
resolutions or platforms can differ. The image retains wheel-provided license
files in each installed distribution's `.dist-info/licenses` directory.

| Distribution | Version | Declared license |
| --- | --- | --- |
| alembic | 1.20.1 | MIT |
| annotated-doc | 0.0.6 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.16.0 | MIT |
| certifi | 2026.10.23 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| click | 8.5.1 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| fastapi | 0.162.5 | MIT |
| greenlet | 3.5.6 | MIT AND PSF-2.0 |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| lxml | 6.1.1 | BSD-3-Clause; see exceptions below |
| Mako | 1.4.3 | MIT |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| opentelemetry-api | 1.45.2 | Apache-2.0 |
| pip | 25.0.1 | MIT |
| psycopg, psycopg-binary | 3.3.9 | LGPL-3.0-only |
| pycparser | 3.12 | BSD-3-Clause |
| pydantic | 2.14.3 | MIT |
| pydantic_core | 2.50.4 | MIT |
| PyJWT | 2.15.1 | MIT |
| python-multipart | 0.0.33 | Apache-2.0 |
| SQLAlchemy | 2.0.57 | MIT |
| starlette | 1.10.0 | BSD-3-Clause |
| typing-inspection | 0.4.5 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| uvicorn | 0.54.0 | BSD-3-Clause |

The `lxml` wheel's `LICENSES.txt` identifies additional ElementTree-derived
code and Schematron resources with separate terms; consult the installed
distribution, not just its top-level BSD declaration. The Linux
`psycopg-binary` wheel also bundles native libraries including libpq, OpenSSL,
Kerberos and LDAP libraries. Their individual notices and redistribution
obligations have not been independently inventoried here and need review
before distributing that image.

## Schemas, sample data and external services

Sources and SHA-256 hashes for the eCH-0160 schema bundles, reference SIP,
METS, PREMIS, XLink and XML schemas are recorded in
[references/provenance.json](references/provenance.json); the sample corpus is
inventoried in [fixtures/corpus.json](fixtures/corpus.json).

- The [METS schema](schemas/preservation/mets.xsd) identifies the Digital
  Library Federation's CC0-1.0 dedication in the schema itself.
- The eCH-0160 bundles originate from eCH. The separately downloaded reference
  SIPs originate from eCH and KOST. Their applicable
  redistribution terms are not established in this repository. Do not assume
  the project's MIT license applies to them.
- The PREMIS, XLink and W3C XML schemas are retained with their source
  references. Their applicable terms and any required notices have not yet
  been verified for these specific copies.
- Container images and external services, including Python, Node.js, Keycloak,
  PostgreSQL and an optional KOST-Val service, have their own licenses and
  bundled operating-system or native components. Consult the corresponding
  image and upstream distribution notices; the application license does not
  cover them.
