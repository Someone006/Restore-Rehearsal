# Changelog

All notable changes are documented here. The project follows semantic versioning for the Python
package; recipe and report schemas are versioned independently while alpha.

## [Unreleased]

## [0.1.0] - 2026-11-08

### Added

- Strict version-1 recipe schema for Compose files, backup inputs, restore steps, candidate images,
  and exec/health checks.
- Full Compose-model isolation with internal networks, run-scoped resources, read-only backup
  injection, and fail-closed refusals.
- Baseline restore/check and candidate upgrade/check state machine.
- Ownership-checked cleanup and share-safe private JSON reports.
- Standalone `rehearse` CLI, synthetic Alpine example, and portable Agent Skill installer.
