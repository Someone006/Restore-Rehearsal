# Changelog

All notable changes are documented here. The project follows semantic versioning for the Python
package; recipe and report schemas are versioned independently while alpha.

## [Unreleased]

### Fixed

- Gave the checkout-free GitHub release publication job explicit repository context.
- Added an explicit, default-off manual recovery path for GitHub prerelease publication.

## [0.3.0] - 2026-11-10

### Added

- Added safe static project initialization with `rehearse init`, dry-run previews, existing-config
  checks, project-stack detection, and private no-overwrite starter files.
- Upgraded Project Brain to schema version 2 with bounded structural snapshots, deterministic
  diffs, relationship-derived impact analysis, duplicate-snapshot suppression, atomic migration
  from version 1, and preserved memories and run evidence across rebuilds.
- Added persistent normalized evidence imports for SARIF, CycloneDX/SPDX SBOM, JUnit,
  LCOV/Cobertura, OpenAPI/Swagger, Terraform, Kubernetes, and CI/check artifacts.
- Added Graph, Changes, Findings, Recovery, Evidence, and Timeline views to the self-contained
  offline visualization, with accessible navigation and bounded filters.
- Added a local newline-JSON stdio MCP server with bounded Project Brain and recovery-adapter tools;
  it is read-only by default and can optionally expose validated memory writes.
- Added non-executing recovery-adapter detection and proposals for PostgreSQL, MySQL/MariaDB,
  SQLite, MongoDB, object-storage exports, and managed-service export handoff.
- Added universal inspection plan/report schemas for web, native/mobile, CLI/TUI, API, code,
  documentation, data, and mixed projects, including local evidence digest verification.
- Added a typed plugin API and registry for analyzers, artifact importers, and restore providers.
  Installed entry-point metadata is inspectable without loading code; execution requires explicit
  activation of one exact trusted plugin.
- Expanded the installable Agent Skill set from two to nine with `rehearse-init`,
  `universal-inspector`, `change-impact`, `evidence-import`, `recovery-adapters`,
  `release-readiness`, and `plugin-authoring`.
- Added synthetic project/recovery templates, deterministic benchmark tooling, a compatibility and
  support policy, release metadata and reproducible-build tools, Homebrew formula generation, and a
  non-root brain-only container definition.
- Added pinned release/security workflows for checksums, CycloneDX/SPDX SBOMs, build provenance,
  CodeQL, dependency review, and dependency auditing. External PyPI and GHCR publication remains
  separately gated by repository administration.

### Changed

- Broadened the local evidence workflow beyond source repositories: ordinary non-Git project
  folders, applications, APIs, documentation, data, and mixed digital projects now have explicit
  initialization and inspection paths.
- The offline HTML export now presents history, imported evidence, recovery coverage, memories,
  and rehearsal runs without remote assets or network access.
- Skill installation now supports selection, atomic `--all` installation, catalog output, and a
  bundled-file doctor while preserving no-overwrite behavior.

### Security

- Added bounded XML, YAML, JSON, artifact, snapshot, import, MCP, inspection, and plugin metadata
  handling with traversal, symlink, entity, alias, secret-canary, and payload-size refusals.
- Evidence importers discard raw logs, environment values, source bodies, Terraform values,
  Kubernetes Secret/ConfigMap data, OpenAPI examples/security values, and SBOM external URLs.
- Recovery detection reads bounded local path metadata only and never contacts a provider,
  database, bucket, or environment credential source.
- Clarified the outbound-data rule: API keys, credentials, tokens, private keys, and backups are
  never uploaded; any other project, report, screenshot, personal, or customer data requires the
  user's explicit approval for a named destination and scope.

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
