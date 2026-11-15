# Repository instructions

- Use Podman, not Docker, for container workflows. Use `podman` and `podman compose`; do not recommend Docker Desktop or `docker compose`.
- On Windows, start the stack with `./scripts/start_local.ps1`. The launcher selects `podman-compose`, explicitly builds the images, starts services, and checks readiness; do not replace it with a direct `podman compose up --build` command.
- On Linux, select the `podman-compose` provider, check `podman compose config`, then use `podman compose up --build -d`. See `docs/specification.md` for current local operations.
- Preserve existing `.env` configuration and named volumes. Do not remove volumes as a routine restart or troubleshooting step.
- The application is a local archival-ingest reference, not a production archival-acceptance system. Keep receipt checks, candidate generation and institutional approval distinct; repository deposit and descriptive-system acceptance are separate external processes. Candidate generation is not archival acceptance.
- Keep frontend and backend API contracts aligned, and follow the existing focused test/build commands in `README.md`.
- Keep eCH XSDs under `schemas/ech-0160/`, preservation XML schemas under `schemas/preservation/`, and sample SIPs under `fixtures/`. Use `references/provenance.json` for pinned external-source hashes.