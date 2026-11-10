# Supply-chain controls

Rehearse keeps its runtime dependency set small, records exact development resolution in `uv.lock`,
and lists direct runtime licenses in `THIRD_PARTY.md`. GitHub Actions are pinned by full commit SHA.

The repository provides these independent controls:

- CI lint, unit, installed-wheel, example, portable-brain, and real Docker integration jobs;
- scheduled and pull-request CodeQL analysis;
- pull-request dependency review and a scheduled audit of locked direct runtime dependencies;
- reproducible wheel/source builds using a fixed `SOURCE_DATE_EPOCH`;
- deterministic SPDX and CycloneDX release SBOMs;
- SHA-256 manifests and GitHub artifact provenance; and
- a minimal non-root brain-only container base pinned by digest.

No single control establishes trust. Review the tagged source, workflow run, dependency changes,
checksums, and attestations together. Vulnerability scanners can be incomplete or temporarily wrong;
follow the private reporting process for suspected issues.

Third-party Agent Skills and Python plugins are code and instructions from outside the core project.
Skills should be inspected before installation. External Python plugins are never auto-loaded by
Rehearse and must be explicitly trusted because they execute with the user's privileges.
