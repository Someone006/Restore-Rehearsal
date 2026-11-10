---
name: release-readiness
description: Verify local build, package, compatibility, SBOM, checksum, provenance, and release-gate evidence without publishing automatically.
license: Apache-2.0
compatibility: Requires the project release tools and Rehearse 0.3+ for Project Brain evidence.
metadata:
  version: "0.3.0"
---

# Release Readiness

Use this skill when a user asks whether a project or Rehearse itself is ready to package or release.

## Workflow

1. Read the project's declared release policy and select an exact commit/tag candidate. Follow
   [package verification](references/packages.md) for clean builds and installed-artifact tests.
2. Run required unit, integration, recovery, compatibility, and documentation gates in approved
   environments. Do not substitute static findings for executed evidence.
3. Generate local checksums and SBOMs and review dependencies, licenses, workflow pinning, and
   provenance using [supply-chain evidence](references/supply-chain.md).
4. Apply [release gates](references/release-gates.md). Separate repository readiness from external
   PyPI, registry, Homebrew, signing, notarization, or repository-setting prerequisites.
5. Publish only when the user explicitly requests the destination and the required account,
   protection, signing, and rollback controls are available.

## Guardrails

- Readiness checks are non-publishing by default. Never upload, tag, release, deploy, or change
  repository settings merely because checks pass.
- Do not expose tokens, identity files, signing material, private logs, or customer test data.
- API keys, credentials, tokens, private keys, and backups must never be sent online. Other
  outbound project or personal data requires destination, scope, and explicit approval.
