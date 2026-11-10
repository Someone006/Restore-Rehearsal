---
name: change-impact
description: Compare Project Brain snapshots, trace affected components and contracts, and select focused verification before changing or releasing a project.
license: Apache-2.0
compatibility: Requires Python 3.11+, Rehearse 0.3+, and a local .rrbrain file.
metadata:
  version: "0.3.0"
---

# Change Impact

Use this skill when a user asks what changed, what may break, which tests to run, or whether a
project is ready after a modification.

## Workflow

1. Build the current brain and list its bounded history. Compare explicit snapshot identifiers;
   never assume timestamps describe the intended baseline. See [snapshot diffs](references/snapshot-diffs.md).
2. Run the deterministic diff, then request relationship-bounded impact for the changed paths or
   nodes. Separate observed graph changes from inferred reachability.
3. Review affected contracts, migrations, recovery paths, services, and tests using
   [contracts and tests](references/contracts-tests.md).
4. Execute only user-authorized, project-declared verification. Record exact results, failures,
   omissions, and cleanup as described in [impact evidence](references/evidence.md).
5. Rebuild after fixes and confirm that the expected graph and findings changed.

## Guardrails

- A graph neighborhood is evidence for review, not proof that every runtime effect was found.
- Do not execute untrusted code, migrations, deployments, or production checks merely to improve
  impact confidence.
- API keys, credentials, tokens, private keys, and backup contents must never be sent online.
  Before sending any other project, report, personal, or customer data online, state destination
  and scope and obtain explicit approval. Purely local analysis needs no approval.
