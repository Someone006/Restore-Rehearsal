---
name: project-brain
description: Build and query a local one-file Rehearse project evidence graph before changing code, tests, deployment, recovery, UX, accessibility, licensing, or privacy artifacts.
license: Apache-2.0
compatibility: Requires Python 3.11+ and the rehearse CLI.
metadata:
  version: "0.3.0"
---

# Rehearse Project Brain

Use this skill when an agent needs project-wide context, change impact, previous decisions or failed
attempts, verification gaps, or one of the ten review lenses in `references/`.

## Workflow

1. If the brain is absent or stale, run `rehearse brain build . --output project.rrbrain` from the
   selected local project root. Git is optional. Never weaken ignore rules or scan a parent
   directory merely to gain more context.
2. Start with `rehearse brain summary project.rrbrain --json` or a narrow
   `rehearse brain query project.rrbrain "QUESTION" --json`.
3. Before editing, request only the relevant neighborhood with
   `rehearse brain context project.rrbrain RELATIVE_PATH --depth 2 --json`.
4. Check relevant unresolved findings and prior memories. A missing relationship means insufficient
   extracted evidence, not proof that no relationship exists.
5. Run the proposed tests or checks. When useful, import a share-safe Rehearse report with
   `rehearse brain record-run`.
6. Record durable decisions or failed approaches through standard input. Do not record routine chat,
   credentials, API keys, authentication tokens, or unnecessary personal/customer data.
7. Use `--supersedes MEMORY_ID` when new evidence replaces an earlier active memory. Run
   `rehearse brain forget` only when the user explicitly asks to redact that exact memory.

## Guardrails

- Never dump the entire SQLite database or load the whole brain into model context.
- Never send credentials, API keys, authentication tokens, private keys, or backup contents to an
  online service. This prohibition does not have an approval bypass.
- Before sending any other source, project metadata, `.rrbrain` content, exported HTML, personal
  data, or customer information to an online service, explain the destination and exact scope and
  obtain explicit user approval. Purely local analysis does not require this approval.
- Do not commit `.rrbrain` or exported HTML by default. They are private project artifacts even
  though the scanner does not persist source bodies.
- Never delete or redact a durable memory merely because it looks stale; supersede it with evidence
  or ask the user. Forgetting is an explicit destructive action.
- Never execute repository code merely to enrich the graph.
- Treat rule-derived and heuristic findings as review prompts. Require evidence and tests before
  proposing a source change.
- License/privacy lenses identify review points only. They are not legal advice, compliance
  certification, or a substitute for qualified counsel.
- UX/accessibility lenses do not replace research with users, assistive-technology testing, or a
  full WCAG evaluation.
- Do not claim the brain is more complete than Graphify or another parser. Graphify importing is a
  planned integration; broad AST coverage is not part of this skill.

Use the evidence guide for the relevant lens: [architecture](references/architecture.md),
[change impact](references/change-impact.md), [test strategy](references/test-strategy.md),
[reliability and recovery](references/reliability-recovery.md),
[security and supply chain](references/security-supply-chain.md),
[performance and operability](references/performance-operability.md),
[license attribution](references/license-attribution.md), [privacy and data](references/privacy-data.md),
[UX flow](references/ux-flow.md), or [accessibility and design](references/accessibility-design.md).

Use `rehearse brain lenses` to list the bundled baseline and
`rehearse brain doctor project.rrbrain --json` to validate the one-file format.
