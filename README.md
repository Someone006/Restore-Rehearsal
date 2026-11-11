# Restore Rehearsal — Local Project Brain and Backup Restore Testing

**Test whether backups restore before production, and give people and AI agents a private evidence
graph of the whole project.**

[![CI](https://github.com/Someone006/Restore-Rehearsal/actions/workflows/ci.yml/badge.svg)](https://github.com/Someone006/Restore-Rehearsal/actions/workflows/ci.yml)
[![Pages](https://github.com/Someone006/Restore-Rehearsal/actions/workflows/pages.yml/badge.svg)](https://github.com/Someone006/Restore-Rehearsal/actions/workflows/pages.yml)
[![Latest release](https://img.shields.io/github/v/release/Someone006/Restore-Rehearsal?display_name=tag&sort=semver)](https://github.com/Someone006/Restore-Rehearsal/releases)
[![License](https://img.shields.io/github/license/Someone006/Restore-Rehearsal)](LICENSE)

[Website](https://someone006.github.io/Restore-Rehearsal/) ·
[Launch video](https://someone006.github.io/Restore-Rehearsal/#launch-film) ·
[Documentation](https://someone006.github.io/Restore-Rehearsal/project-brain/) ·
[v0.3.0 release](https://github.com/Someone006/Restore-Rehearsal/releases/tag/v0.3.0) ·
[Security](SECURITY.md) · [Apache-2.0 license](LICENSE)

**Start here:** [try the synthetic restore rehearsal](examples/minimal/) ·
[build a Project Brain](docs/brain.md) · [install the Agent Skills](#install-as-ai-agent-skills)

Rehearse is an Apache-2.0, local-first command-line application for backup restore testing,
disaster-recovery drills, and codebase knowledge graphs across any software or digital project. It
combines four related capabilities:

- **Project Brain** statically scans a Git or ordinary local folder and keeps architecture,
  dependencies, tests, deployment, recovery evidence, findings, decisions, failed attempts, and
  structural history in one private `.rrbrain` SQLite file.
- **Restore Rehearsal** restores a declared backup into a disposable Docker Compose stack, checks
  it before and after a candidate upgrade, records share-safe evidence, and removes the stack.
- **Universal Inspection** gives agents a consistent evidence workflow for websites, native apps,
  CLIs, APIs, code, documentation, and data—including screenshots when an inspection tool is
  available.
- **Agent and extension interfaces** provide nine portable Agent Skills, a local stdio MCP server,
  bounded evidence importers, offline recovery-adapter proposals, and an explicit-trust Python
  plugin SDK.

```text
local project + review evidence + recovery runs + decisions
                            ↓
              one private Project Brain
                            ↓
         focused context, change impact, tests, and review
```

> [!IMPORTANT]
> This README documents the `0.3.0` release. Rehearse is alpha software. It may
> be used in commercial projects under Apache-2.0, but it should not be the sole production release
> gate, disaster-recovery control, accessibility certification, security assessment, or
> legal/compliance decision. See [commercial readiness](docs/commercial-readiness.md).

## Who needs it?

Project Brain is useful when a project has more context than one person or agent can safely keep in
working memory: source and configuration, tests, deployment files, API contracts, infrastructure,
prior decisions, known failures, or recovery requirements. Git is optional. A website, native app,
CLI, API, data project, documentation set, infrastructure folder, or mixed product can all use the
brain and inspection workflow.

Restore Rehearsal is more specialized. It is useful when someone:

- operates a stateful Docker Compose application;
- creates backups but lacks a realistic staging restore;
- worries about database migrations or skipped intermediate releases; and
- needs repeatable evidence before a human or automated update decision.

The underlying recovery problem is real, although adoption of this particular tool remains a
hypothesis. Self-hosters repeatedly report broken upgrades, untested backups, and manual update
anxiety in [a large homelab recovery discussion](https://www.reddit.com/r/homelab/comments/1s829jo/selfhosting_reality_happened_sometimes/),
[a request for safer update management](https://www.reddit.com/r/selfhosted/comments/1vy6ol8/updating_self_hosted_apps/),
and Hacker News discussions about [self-hosting operational burden](https://news.ycombinator.com/item?id=45528342)
and [restore complexity in deployment platforms](https://news.ycombinator.com/item?id=43555996).
The [Repository Intelligence Graph study](https://arxiv.org/abs/2601.10112) also reported better
agent accuracy and completion time from deterministic build/test evidence in its evaluation.
Rehearse still needs real-project field trials before stronger adoption claims are justified.

## Install the standalone application

Project Brain, inspection plans, importers, skills, adapters, and MCP require Python 3.11 or newer.
They do not require Git or Docker. Executing restore drills additionally requires Linux, Docker
Engine, and Docker Compose v2.

Install the immutable `v0.3.0` Git tag (or choose a newer published tag from the
[GitHub releases page](https://github.com/Someone006/Restore-Rehearsal/releases)):

```console
uv tool install "git+https://github.com/Someone006/Restore-Rehearsal.git@v0.3.0"
rehearse --version
```

For development from a clone:

```console
git clone https://github.com/Someone006/Restore-Rehearsal.git
cd Restore-Rehearsal
uv tool install .
rehearse --version
```

`pipx install .` also works. For development, use `uv sync --extra dev` followed by
`uv run rehearse --version`. The project also prepares a non-root, brain-only OCI image and a
fully hashed Homebrew formula; see [distribution options](docs/distribution.md).

No telemetry is collected. Rehearse does not upload project data, reports, screenshots, or backup
contents. Docker may contact registries according to a reviewed restore recipe's `pull` policy.

## Start safely in an existing project

Initialization performs a bounded static scan; it does not execute project code. Preview its
detections and generated text first:

```console
rehearse init . --dry-run --json
rehearse init .
```

The second command creates `rehearse-project.yaml` and `.rehearseignore` only when those targets do
not already exist. It never invents credentials, backup locations, or executable restore commands.
Treat the generated file as a reviewable starting point.

Build the first brain and create its offline visual:

```console
rehearse brain build . --output project.rrbrain --label initial
rehearse brain summary project.rrbrain --json
rehearse brain visualize project.rrbrain --output project-brain.html
```

The HTML file has Graph, Changes, Findings, Recovery, Evidence, and Timeline views. It is
self-contained, uses no remote scripts, fonts, analytics, or network requests, and remains private
project metadata.

## Use the one-file Project Brain

Run inside any dedicated local project folder, or pass its path:

```console
rehearse brain query project.rrbrain "database recovery" --json
rehearse brain context project.rrbrain src/service.py --depth 2 --json
rehearse brain findings project.rrbrain --json
rehearse brain memories project.rrbrain --status active --json
rehearse brain doctor project.rrbrain --json
```

When Git is present, the scanner uses the selected work tree's tracked/unignored inventory and then
applies nested `.rehearseignore` exclusions. For an ordinary folder it performs a deterministic,
bounded filesystem walk and honors nested `.gitignore` and `.rehearseignore` files. It never follows
symlinks or executes project code. Known credential, key, environment, and backup paths are
represented only as sensitive metadata and are never opened.

The brain stores relative paths, hashes, symbols, relationships, findings, deliberately recorded
memories, normalized import metadata, and allowlisted rehearsal results—not source bodies,
environment values, backup contents, Git author identity, absolute project paths, or raw command
output. Read the [full Project Brain model](docs/brain.md).

### Structural history and change impact

Each meaningful build records a bounded structural snapshot; a semantically unchanged build reuses
the latest snapshot rather than inventing history. Compare explicit snapshot IDs or sequences:

```console
rehearse brain snapshots project.rrbrain --json
rehearse brain diff project.rrbrain --from 1 --to 2 --json
rehearse brain impact project.rrbrain --from 1 --to 2 --depth 2 --json
```

Diffs report added, removed, and modified nodes, relationships, and findings. Impact traverses only
extracted relationships and groups affected components, services, tests, deployment, recovery, and
contracts. It is focused review evidence, not proof that dynamic runtime effects do not exist.

### Durable decisions and failed attempts

Provide memory bodies through standard input so they do not appear in shell history:

```console
printf '%s\n' 'Rejected because it broke the migration fixture.' |
  rehearse brain remember project.rrbrain --kind failure \
  --title 'Direct v1 to v3 upgrade' --path migrations/upgrade.py
```

Supersede an outdated memory with new evidence rather than deleting history. `brain forget` securely
redacts one exact memory ID and is intentionally an explicit destructive action. Never record
credentials, personal data, source dumps, contract terms, or customer content in a memory.

### Bounded external evidence

Rehearse can normalize eight local artifact families without executing them or resolving remote
references:

- SARIF static-analysis results;
- CycloneDX and SPDX SBOMs;
- JUnit test results;
- LCOV and Cobertura coverage;
- OpenAPI and Swagger contracts;
- Terraform plans, state, and static configuration;
- Kubernetes manifests; and
- CI/check summaries.

```console
rehearse brain import project.rrbrain build/results.sarif --type sarif --json
rehearse brain imports project.rrbrain --json
rehearse brain show-import project.rrbrain IMPORT_ID --json
```

Importers retain bounded, allowlisted facts, provenance, and a source digest. They omit raw logs,
source bodies, environment values, Terraform values, Kubernetes Secret/ConfigMap data, OpenAPI
examples/security values, and SBOM external URLs. Imported fragments are stored alongside the
canonical scanned graph; they are not silently presented as scanner-proven relationships. Removal
requires `brain remove-import` with one exact import ID. See [evidence importers](docs/importers.md).

## Install as AI Agent Skills

The package includes nine portable, inspectable Agent Skills:

| Skill | Purpose |
| --- | --- |
| `restore-rehearsal` | Validate and execute disposable Compose restore/upgrade drills |
| `project-brain` | Build and query focused project evidence |
| `rehearse-init` | Initialize an existing project without overwriting work |
| `universal-inspector` | Inspect web, native, CLI, API, code, docs, and data surfaces |
| `change-impact` | Compare snapshots and select affected verification |
| `evidence-import` | Normalize external test, security, contract, and infrastructure evidence |
| `recovery-adapters` | Detect local exports and prepare offline recovery proposals |
| `release-readiness` | Check packages, compatibility, SBOMs, checksums, and release gates |
| `plugin-authoring` | Build bounded, explicit-trust extensions |

Install one skill or the full bundle into an agent's project-level skill directory:

```console
# Codex and other agents using the common .agents directory
rehearse skill install .agents/skills --name project-brain

# Claude Code
rehearse skill install .claude/skills --all

# GitHub Copilot
rehearse skill install .github/skills --all

rehearse skill list --json
rehearse skill doctor --json
```

The installer validates every bundled file, refuses overwrites, and rolls back an incomplete
multi-skill installation. Installing a skill supplies instructions; it does not grant an agent new
browser, desktop, Docker, network, or deployment capabilities.

## Inspect any project surface

The universal inspection workflow covers websites, native/mobile apps, CLIs/TUIs, APIs, source,
documentation, and data. Rehearse itself creates and validates local evidence plans; an agent uses
an available browser, desktop, emulator, terminal, or test tool to perform the authorized checks.
The CLI does not automatically open a target or take screenshots.

```console
rehearse inspect plan ./app --surface mixed --output inspection-plan.json --json
rehearse inspect validate inspection-plan.json --json
rehearse inspect validate inspection-report.json --verify-evidence --json
```

Plans cover layout, interaction states, accessibility, console/network summaries, contracts,
errors, automation, security boundaries, test evidence, documentation accuracy, privacy, lineage,
and scale as relevant. Reports can refer to local screenshots, video, test results, and observations
by relative path and SHA-256. Validation refuses traversal, symlink escape, secret-like text, and
tampered file evidence. See [universal inspection](docs/inspection.md).

## Prepare recovery with offline adapters

Built-in adapters cover PostgreSQL, MySQL/MariaDB, SQLite, MongoDB, object-storage exports, and a
managed-service export handoff:

```console
rehearse adapters list --json
rehearse adapters detect . --backup ./local-export --json
rehearse adapters propose postgres --backup ./backup.dump --format postgres-custom --json
```

Detection reads bounded filenames only—not backup contents, environment variables, databases,
buckets, or cloud APIs. Proposals identify required configuration, tools, and checks; they do not
execute a restore or generate secrets. Provider-native snapshots must first be exported locally by
the user. Synthetic starting points are under [`examples/templates/`](examples/templates/).

## Give an agent bounded local access

Start the local stdio MCP server for one existing brain:

```console
rehearse mcp serve project.rrbrain
```

It opens no socket and exposes bounded summary, query, context, finding, memory-index, snapshot,
diff, impact, import-index, lens, and recovery-adapter tools. The default server is read-only.
`--allow-write` adds only the validated `brain_remember` operation; destructive operations and raw
artifact import remain unavailable. Configure the command as a local stdio server in the agent host
you trust. The `.rrbrain` remains the source of truth.

## Extend Rehearse explicitly

The typed plugin API defines analyzer, artifact-importer, and restore-provider protocols. Built-ins
are registered directly; installed entry-point metadata can be listed and checked without importing
plugin code. An external plugin runs with the current user's privileges only after exact,
user-directed activation—it is not sandboxed and is never auto-loaded during scan, initialization,
or detection. See [plugin authoring](docs/plugin-sdk.md).

## Run a disposable restore drill

```text
read-only backup → isolated baseline → restore → checks → candidate images → checks → cleanup
```

The repository contains a synthetic Alpine example with no real backup, credential, or personal
data:

```console
rehearse validate examples/minimal/rehearse.yaml
rehearse run examples/minimal/rehearse.yaml --report rehearsal-report.json
```

The report is created with mode `0600`, and an existing report is never overwritten. Recipes use
argument arrays and subprocesses run with `shell=False`, but recipes are still trusted executable
instructions: their commands can do anything permitted inside the disposable containers. Read the
[recipe reference](docs/recipe.md) and [safety boundary](docs/safety.md) before writing one.

Before the first mutating Docker command, Rehearse renders the merged Compose model and fails closed
on unsupported risk. It removes host ports, makes networks internal by default, replaces
production-derived writable storage, rejects external or privileged resources and engine sockets,
and tears down only resources carrying the exact run label. Docker access remains effectively
root-equivalent; Rehearse is not a sandbox for hostile images or hostile recipes.

## Ten review lenses

Project Brain includes ten evidence-backed review queues:

1. Architecture Map
2. Change Impact and Contracts
3. Test Strategy and Verification
4. Reliability and Recovery
5. Security and Supply Chain
6. Performance and Operability
7. License and Attribution
8. Privacy and Data Governance
9. UX Flows and Product Content
10. Accessibility and Design System

These rules identify evidence and gaps. They are not autonomous design decisions, legal advice,
security assurance, accessibility certification, or a substitute for research with users.

Project Brain also has a different purpose from Obsidian and Graphify. [Obsidian](https://obsidian.md/help/plugins/graph)
is better for human-authored notes, while [Graphify](https://github.com/Graphify-Labs/graphify)
provides deeper AST/document extraction. Rehearse joins bounded project topology to tests,
recovery, imports, decisions, prior failures, and review evidence. It does not claim complete
language or runtime understanding.

## Privacy and consent boundary

Everything is local by default. The non-negotiable rules for the application and bundled skills
are:

- never upload API keys, credentials, tokens, private keys, or backup contents;
- before sending any other source, project metadata, report, screenshot, personal information, or
  customer data to an online service, state the destination and exact scope and obtain the user's
  explicit approval;
- purely local analysis needs no outbound-data approval;
- keep `.rrbrain`, exported HTML, inspection evidence, and reports outside source control by
  default; and
- treat external Python plugins as trusted local code, not a sandbox.

Add locally generated outputs to the project's `.gitignore` when their names match your workflow.
For example:

```gitignore
*.rrbrain
project-brain*.html
inspection-plan*.json
inspection-report*.json
inspection-evidence/
rehearsal-report*.json
```

`.rehearseignore` controls what Project Brain scans; it is not a Git privacy boundary. Review
`git status` before every commit because evidence can still contain private project metadata even
when Rehearse has minimized it.

Rehearse applies path exclusions, secret-pattern checks, bounded parsers, private file modes, and
content minimization, but no pattern matcher can recognize every secret or personal datum. Users
remain responsible for selecting project roots, ignore rules, evidence, and retention.

## Current scope and limitations

The `0.3.0` release includes:

- Git-optional Project Brain scanning, bounded snapshots/diffs/impact, durable memories, imported
  evidence, rehearsal runs, ten review lenses, and a six-view offline visual;
- eight built-in evidence importers and six offline recovery-adapter profiles;
- universal inspection plan/report schemas with local evidence verification;
- local stdio MCP access, a typed explicit-trust plugin foundation, and nine Agent Skills;
- safe static project initialization, synthetic project/recovery templates, benchmark tooling,
  compatibility/support policy, reproducible-build checks, SBOM/checksum/provenance workflow, and
  brain-only container/Homebrew preparation; and
- the original Linux Docker Compose restore-and-upgrade rehearsal engine.

It deliberately does not yet provide:

- backup creation or scheduling, production deployment, automatic rollback, or live cloud-provider
  mutation;
- automatic discovery of application-specific restore commands or credentials;
- complete AST/runtime coverage for every language and framework;
- automatic screenshots without a browser/desktop tool, or a hosted dashboard/sync service;
- a sandbox for untrusted recipes, containers, or Python plugins; or
- a paid service, warranty, SLA, or guarantee that a passing check proves production readiness.

See the [compatibility matrix](docs/compatibility.md), [support policy](docs/support-policy.md),
[supply-chain controls](docs/supply-chain.md), and [benchmarks](docs/benchmarks.md).

## Development

```console
make setup
make check
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), the
[third-party dependency inventory](THIRD_PARTY.md), [release procedure](docs/releasing.md), and
[changelog](CHANGELOG.md). Small synthetic fixtures and evidence-based reports from real project
shapes are especially valuable; never put private project data or secrets in a public issue.

Licensed under Apache-2.0.
