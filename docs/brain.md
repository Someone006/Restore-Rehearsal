# Project Brain

Project Brain is Rehearse's local project-evidence layer. It connects project structure, tests,
deployment, recovery procedures, imported evidence, review findings, decisions, failed attempts,
structural history, and verified rehearsal runs in one portable SQLite file such as
`project.rrbrain`.

Git is optional. The selected root may be a source repository, application, documentation set,
infrastructure folder, data project, or mixed local project. The scanner is static: it does not
import project modules, install dependencies, execute builds, or run package scripts.

Project Brain is not a general note-taking vault and does not claim to understand every line of a
project. [Obsidian's graph](https://obsidian.md/help/plugins/graph) is designed for links between
human-authored notes, while [Graphify](https://github.com/Graphify-Labs/graphify) provides deeper
deterministic AST and document extraction. Project Brain focuses on joining bounded project
topology to tests, operations, recovery, product review, prior evidence, and project history. A
Graphify importer and broad tree-sitter coverage are not currently implemented.

## Contents and privacy

The `.rrbrain` file contains structural and deliberately recorded metadata:

- relative paths, file kinds, bounded hashes, symbols, services, dependencies, and relationships;
- evidence locations and extractor provenance;
- structured rule findings and ten review-lens definitions;
- up to 20 bounded structural snapshots and their content-minimized node/edge/finding state;
- memories deliberately entered through standard input;
- allowlisted status from imported Rehearse reports; and
- bounded normalized evidence-import metadata, summaries, and graph fragments.

It excludes source bodies, environment values, backup contents, Git author identity, remote URLs,
absolute project paths, and raw command output. Each artifact importer has additional minimization
rules; see [evidence importers](importers.md). Source text may be parsed during a bounded local scan,
but it is not persisted in the brain.

Git work trees use Git's ignore decisions and then apply nested `.rehearseignore` privacy
exclusions. Plain folders honor nested `.gitignore` and `.rehearseignore` files. Known credential,
key, environment, and backup paths are never opened. Symlinks are never followed, and scans enforce
file, directory, path, depth, per-file, and total-read limits.

The database is written atomically with mode `0600`, uses no WAL sidecar, and makes no network call.
Treat it as sensitive project metadata anyway: graph structure, names, findings, imports, and
deliberate memories can still reveal confidential facts. Do not commit or share it casually.

`.rehearseignore` controls local analysis only. It does not remove an exposed secret from Git
history, revoke a credential, or replace a secret manager. Every non-ignored file in the selected
root remains in inventory scope.

The outbound-data rule is separate and strict: API keys, credentials, tokens, private keys, and
backup contents must never be sent to an online service. Before sending any other source, project
metadata, brain content, report, screenshot, or personal/customer information online, state the
destination and exact scope and obtain the user's explicit approval. Purely local analysis does not
need outbound approval.

## Initialize and build

Preview safe starter files, then create them without overwriting existing paths:

```console
rehearse init . --dry-run --json
rehearse init .
```

Build or atomically update a brain for any dedicated local project folder:

```console
rehearse brain build . --output project.rrbrain --label initial
rehearse brain build /path/to/a/non-git-project --output project.rrbrain
```

An existing schema-version-1 brain is migrated through the same atomic rebuild path. Memories and
run evidence are preserved. Unknown schemas fail closed. A build with the same semantic graph as
the latest snapshot reuses that snapshot instead of creating a duplicate.

## Query focused context

People and agents should request bounded evidence rather than loading the SQLite file wholesale:

```console
rehearse brain summary project.rrbrain --json
rehearse brain query project.rrbrain "database recovery" --json
rehearse brain context project.rrbrain src/service.py --depth 2 --json
rehearse brain findings project.rrbrain --lens reliability-recovery --json
rehearse brain lenses --json
rehearse brain doctor project.rrbrain --json
```

`query` searches nodes, findings, memories, and import summaries. `context` resolves one node/path
and returns a depth-limited neighborhood. Missing evidence never proves that a runtime dependency,
requirement, or risk is absent.

## Snapshots, diffs, and impact

List the retained history and compare two explicit IDs or sequence numbers:

```console
rehearse brain snapshots project.rrbrain --json
rehearse brain diff project.rrbrain --from 1 --to 2 --json
rehearse brain impact project.rrbrain --from 1 --to 2 --depth 2 --json
```

Without explicit references, `diff` and `impact` compare the latest two distinct snapshots.
`latest` may be used as the `--to` reference. Diffs report bounded added, removed, and modified
nodes, edges, and findings. Impact starts at changed nodes, traverses the union of extracted
relationships up to depth 3, and groups results into components, services, tests, deployment,
recovery, contracts, and other evidence.

Impact confidence is explicitly `relationship-derived`. Dynamic dispatch, reflection,
configuration assembled at runtime, external systems, and unsupported formats can be missing. Use
the result to focus review and tests, not to skip broader verification without evidence.

## Durable memories

Record a decision or failed approach through standard input so the body does not enter shell
history:

```console
printf '%s\n' 'Rejected because it broke the migration fixture.' |
  rehearse brain remember project.rrbrain --kind failure \
  --title 'Direct v1 to v3 upgrade' --path migrations/upgrade.py
```

List the memory index and supersede obsolete evidence:

```console
rehearse brain memories project.rrbrain --status active --json
printf '%s\n' 'The compatibility fixture now passes.' |
  rehearse brain remember project.rrbrain --kind fix --title 'Migration fixture fixed' \
  --supersedes MEMORY_ID
```

`rehearse brain forget project.rrbrain MEMORY_ID` replaces one exact memory title/body with a
deletion marker, enables SQLite secure deletion, and compacts the one-file database. It is a
destructive privacy operation and must not be run by an agent without the user's explicit request.

Do not record credentials, personal data, source dumps, contract terms, or confidential customer
material. Secret-pattern checks are a backstop, not a complete data classifier.

## Rehearsal and imported evidence

Join the allowlisted result of a Rehearse run:

```console
rehearse brain record-run project.rrbrain rehearsal-report.json
```

Normalize a supported local artifact and inspect its persistent record:

```console
rehearse brain importers --json
rehearse brain import project.rrbrain build/results.sarif --type sarif --json
rehearse brain imports project.rrbrain --json
rehearse brain show-import project.rrbrain IMPORT_ID --json
```

The original artifact is not copied into SQLite. The brain stores its basename, SHA-256, importer
identity/version, summary, and bounded allowlisted fragments. Those fragments are visible as import
evidence but are not silently merged into the canonical scanner graph. This preserves provenance
and avoids implying that imported or third-party facts were independently proved by Rehearse.

`brain remove-import` deletes one exact normalized import record. It does not delete the source
artifact. See [import formats and minimization](importers.md).

## Offline visual

```console
rehearse brain visualize project.rrbrain --output project-brain.html
```

The private self-contained HTML provides:

- Graph: searchable/filterable nodes, relationships, and an accessible table;
- Changes: snapshot history and the latest structural diff;
- Findings: severity and lens filters with review evidence;
- Recovery: recovery-related nodes, findings, and run status;
- Evidence: normalized imports and durable memories; and
- Timeline: snapshots, imports, memories, and rehearsal runs.

The export embeds its bounded data, loads no remote script/font/image/analytics resource, has a
restrictive content-security policy, and refuses to overwrite an existing path. It is still
private project metadata and should not be uploaded without the same consent review as the brain.

## Local agent interface

```console
rehearse mcp serve project.rrbrain
```

The MCP transport is newline-delimited JSON-RPC on inherited standard input/output. It opens no
socket. The default tool set is read-only and bounded; `--allow-write` adds only a validated memory
append. It does not expose deletion, raw SQL, arbitrary file access, project execution, artifact
import, or network access. Configure this command in a trusted local MCP host and keep stdout
reserved for the protocol.

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

These are issue-spotting tools, not autonomous judges. Findings state what evidence was found or is
missing, rule confidence, and a verification route. Legal/privacy findings are not legal advice or
compliance certification. UX, accessibility, and architectural heuristics need human and runtime
validation.

## Recommended agent workflow

Install the Project Brain, change-impact, and evidence-import skills as needed:

```console
rehearse skill install .agents/skills --name project-brain
rehearse skill install .agents/skills --name change-impact
rehearse skill install .agents/skills --name evidence-import
```

An agent should update/query the brain before broad exploration, request a small context
neighborhood before editing, inspect diffs/impact/open findings and prior failures, run authorized
verification, and record only durable decisions afterward. It must not load the whole database into
a prompt, infer absence from missing evidence, execute project code merely to enrich the graph, or
transmit private evidence without the outbound-data consent described above.
