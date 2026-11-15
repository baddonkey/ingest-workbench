---
applyTo: "**/*.md"
---

# Documentation quality

- Keep the user manual aligned with implemented UI labels and behavior. When a
  workflow or screen changes, update the affected instructions, links and
  screenshots in the same change; do not describe planned behavior as shipped.
- Use task-oriented steps, meaningful headings, accessible image alt text and
  repository-relative links. Check links and screenshot paths after edits.
- Show informative, expanded nested panels when they help explain a task.
  Capture only sample or explicitly authorized data; never expose credentials,
  tokens, private records or unrelated workspace contents.
- Explain disabled controls, prerequisites and consequential error states.
  Preserve the distinction between receipt, advisory validation, independent
  review, candidate authorization and external archival acceptance.
- Treat `docs/specification.md` as the product/operations contract and
  `docs/gap-list.md` as the backlog. Keep the manual practical and consistent
  with both; do not introduce a competing source of truth.
