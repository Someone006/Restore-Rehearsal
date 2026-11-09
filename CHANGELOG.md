# Changelog

All notable changes are documented here. The project follows semantic versioning for the Python
package; recipe and report schemas are versioned independently while alpha.

## [Unreleased]

## [0.2.1] - 2026-11-09

### Changed

- Project Brain now scans any dedicated local project folder; Git is optional and project
  subdirectories inside larger repositories are supported.
- Plain-folder discovery uses a deterministic, bounded filesystem walker with nested `.gitignore`
  and `.rehearseignore` support, hidden-project-file coverage, cross-device refusal, and no symlink
  following.
- Git inventories now apply nested `.rehearseignore` privacy exclusions, including to tracked
  paths.
- Sensitive-looking local paths now create explicit findings even when no version-control metadata
  exists, while their contents remain unread.

## [0.2.0] - 2026-11-08

### Added

- Local one-file Project Brain with deterministic project inventory, typed relationship graph,
  bounded search and agent context, durable decisions/failed attempts, and Rehearse run evidence.
- Self-contained private HTML visualization with search, kind filters, deterministic clusters,
  accessible node table, restrictive nonce-based CSP, and no network dependencies.
- Ten evidence-backed review lenses spanning architecture, change impact, tests, reliability,
  security, operability, licensing, privacy, UX, accessibility, and design consistency.
- A complete `project-brain` Agent Skill with ten lens references and named skill installation.

### Security

- Added Git-aware streaming inventory limits, sensitive-path refusal, symlink and TOCTOU defenses,
  graph/query/context bounds, exact SQLite schema validation, integrity checks, private atomic files,
  and visualization escaping.
- Defined outbound-data consent: credentials and API keys are never uploaded; other project or
  personal/customer data requires explicit approval before transmission to an online service.

## [0.1.0] - 2026-11-08

### Added

- Strict version-1 recipe schema for Compose files, backup inputs, restore steps, candidate images,
  and exec/health checks.
- Full Compose-model isolation with internal networks, run-scoped resources, read-only backup
  injection, and fail-closed refusals.
- Baseline restore/check and candidate upgrade/check state machine.
- Ownership-checked cleanup and share-safe private JSON reports.
- Standalone `rehearse` CLI, synthetic Alpine example, and portable Agent Skill installer.
