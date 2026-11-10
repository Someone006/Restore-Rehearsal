---
name: plugin-authoring
description: Build deterministic, bounded Rehearse analyzer, evidence-importer, recovery-adapter, or inspection plugins with explicit trust.
license: Apache-2.0
compatibility: Requires Python 3.11+, Rehearse 0.3+, and a separate trusted Python package for external plugins.
metadata:
  version: "0.3.0"
---

# Plugin Authoring

Use this skill when a user wants to extend Rehearse with an analyzer, importer, recovery adapter, or
inspection check.

## Workflow

1. Choose one narrow capability and its versioned entry-point group. Implement the typed contract
   in [plugin contracts](references/contracts.md) with stable IDs and deterministic output.
2. Define byte, record, depth, time, and graph limits before parsing. Normalize allowlisted facts;
   never retain arbitrary raw properties, source bodies, logs, environments, or secret values.
3. Review [the trust boundary](references/trust-boundary.md). External Python plugins are trusted
   code and must never be discovered and executed automatically.
4. Add synthetic success, malformed, oversized, traversal, secret-canary, determinism, and failure
   tests using [the testing guide](references/testing.md).
5. Package independently, document activation by exact plugin ID, and verify with plugin doctor.

## Guardrails

- Never describe external plugins as sandboxed. They execute with the invoking user's privileges.
- Cloud or production mutation must be a separate explicit action with its own consent and threat
  model; analyzers and importers remain local and non-executing.
- API keys, credentials, tokens, private keys, and backup contents must never be sent online.
  Other outbound project or personal data requires destination, scope, and explicit approval.
