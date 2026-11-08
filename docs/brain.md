# Project Brain

Project Brain is Rehearse's local project-evidence layer. It connects project structure, tests,
deployment, recovery procedures, review findings, decisions, failed attempts, and verified
rehearsal runs in one portable SQLite file such as `project.rrbrain`.

It is not a general note-taking vault and it does not claim to understand every line of a project.
[Obsidian's graph](https://obsidian.md/help/plugins/graph) visualizes links between notes, while
[Graphify](https://github.com/Graphify-Labs/graphify) provides much deeper deterministic AST and
document extraction. Project Brain focuses on the evidence that joins architecture to tests,
operations, recovery, product review, and project history. A Graphify importer is planned; broad
tree-sitter coverage is deliberately deferred.

The general direction is supported by the
[Repository Intelligence Graph study](https://arxiv.org/abs/2601.10112), which found that an
evidence-backed build/test graph improved agent accuracy and efficiency in its evaluation. Durable
decision and failure history is informed by the local-first, event-oriented approach described by
[PROJECTMEM](https://arxiv.org/abs/2606.12329).

## Privacy model

The `.rrbrain` file contains structural metadata rather than source bodies:

- relative paths, file kinds, bounded hashes, symbols, services, dependencies, and relationships;
- evidence locations and extractor provenance;
- structured review findings;
- memories deliberately entered through standard input; and
- allowlisted status from imported Rehearse reports.

It excludes source bodies, environment values, backup contents, Git author identity, remote URLs,
absolute project paths, and raw command output. Git ignore rules are honored, known credential/key/
backup paths are never opened, symlinks are never followed, and scans have file and byte limits.
The database uses mode `0600`, no telemetry, no uploads, and no WAL sidecar. Treat the file as
sensitive project metadata anyway; do not commit or share it casually.

This is an outbound-data boundary, not a partial-project mode. Every Git-tracked or unignored file
is inventoried locally within explicit size limits, and supported text formats are parsed for
relationships. API keys, credentials, tokens, private keys, and backup contents must never be sent
to an online service. Before sending any other project/source data or personal/customer information
online, an agent must state the destination and scope and obtain explicit user approval.

## Commands

Build or atomically update a brain at the top of a Git repository:

```console
rehearse brain build . --output project.rrbrain
```

Query bounded evidence for people and agents:

```console
rehearse brain summary project.rrbrain --json
rehearse brain query project.rrbrain "database recovery" --json
rehearse brain context project.rrbrain src/service.py --depth 2 --json
rehearse brain findings project.rrbrain --lens reliability-recovery --json
rehearse brain lenses
```

Record a decision or failed approach without putting its body in shell history:

```console
printf '%s\n' 'Rejected because it broke the migration fixture.' |
  rehearse brain remember project.rrbrain --kind failure --title 'Direct v1 to v3 upgrade' \
  --path migrations/upgrade.py
```

List the memory index, supersede an obsolete record with new evidence, or redact one exact memory:

```console
rehearse brain memories project.rrbrain --status active --json
printf '%s\n' 'The compatibility fixture now passes.' |
  rehearse brain remember project.rrbrain --kind fix --title 'Migration fixture fixed' \
  --supersedes MEMORY_ID
rehearse brain forget project.rrbrain MEMORY_ID
```

`forget` replaces the title and body with a deletion marker, enables SQLite secure deletion, and
compacts the one-file database. It is intentionally exact-ID and must not be run by an agent without
the user's explicit request.

Do not record credentials, personal data, source dumps, or confidential customer material. The CLI
rejects common credential/private-key patterns, but pattern checks cannot identify every secret.

Join runtime evidence and generate an offline visual:

```console
rehearse brain record-run project.rrbrain rehearsal-report.json
rehearse brain visualize project.rrbrain --output project-brain.html
rehearse brain doctor project.rrbrain --json
```

The HTML graph is a separate, self-contained view generated from the one source-of-truth database.
It contains no source bodies and loads no remote scripts, fonts, analytics, or other resources. It
is still project metadata and is written privately without overwriting an existing path.

## Ten review lenses

Project Brain provides ten rule-based lenses:

1. Architecture Map (`architecture`)
2. Change Impact and Contracts (`change-impact`)
3. Test Strategy and Verification (`test-strategy`)
4. Reliability and Recovery (`reliability-recovery`)
5. Security and Supply Chain (`security-supply-chain`)
6. Performance and Operability (`performance-operability`)
7. License and Attribution (`license-attribution`)
8. Privacy and Data Governance (`privacy-data`)
9. UX Flows and Product Content (`ux-flow`)
10. Accessibility and Design System (`accessibility-design`)

These are issue-spotting tools, not autonomous judges. Findings say what evidence was found or is
missing, how confident the rule is, and how to verify a change. Legal/privacy findings are not legal
advice or compliance certification. UX and architectural heuristics require human/user validation.

## Agent workflow

Install the bundled Project Brain skill in an agent's skills directory. Agents should update/query
the brain before broad exploration, request a small context neighborhood before editing, inspect
impact/tests/open findings, consult previous failures, and record only meaningful decisions
afterward. They must never load the whole database into a prompt or infer that absent evidence proves
absence in the project.

```console
rehearse skill install .agents/skills --name project-brain
```
