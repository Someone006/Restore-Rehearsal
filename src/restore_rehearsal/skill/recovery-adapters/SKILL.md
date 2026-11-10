---
name: recovery-adapters
description: Detect local database or object exports and prepare reviewable disposable recovery plans without contacting production providers.
license: Apache-2.0
compatibility: Requires Python 3.11+ and Rehearse 0.3+; executing a plan also requires supported Docker Compose.
metadata:
  version: "0.3.0"
---

# Recovery Adapters

Use this skill when a user wants a starting plan for PostgreSQL, MySQL/MariaDB, SQLite, MongoDB,
object-storage exports, or logical exports from a managed database.

## Workflow

1. Inspect only the user-selected local export. Match conservative signatures from
   [provider profiles](references/providers.md); ambiguity requires a user choice.
2. Generate or show a declarative proposal. Review every image, version, mount, restore command,
   check, timeout, and placeholder with [the plan checklist](references/plan-review.md).
3. Convert the approved proposal into an ordinary strict Rehearse recipe and validate it before
   any Docker mutation. Keep the backup read-only and the stack disposable and isolated.
4. For managed services, follow [the export boundary](references/managed-services.md). Provider
   snapshots must be exported locally first; never request or use cloud credentials automatically.
5. Run only after explicit user authorization and report restore, checks, candidate, and cleanup.

## Guardrails

- Detection and planning never contact a database, bucket, cloud API, or production endpoint.
- Generated values are placeholders, never guessed credentials, database names, or destructive
  flags. A project owner must review application-specific restore semantics.
- API keys, credentials, tokens, private keys, and backup contents must never be sent online.
  Any other outbound project or personal data requires destination, scope, and explicit approval.
