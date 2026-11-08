# Rehearse

**Rehearse recovery before production, and give people and AI agents a local evidence graph of the
whole project.**

Rehearse is an open-source, local-first CLI with two connected capabilities:

- **Restore Rehearsal** restores a declared backup into a disposable Docker Compose stack, runs
  checks before and after a candidate upgrade, records share-safe evidence, and removes its stack.
- **Project Brain** scans a Git project without executing it and stores architecture, dependencies,
  tests, deployment, recovery evidence, decisions, failed attempts, and review findings in one
  private `.rrbrain` SQLite file with an offline visual graph.

```text
read-only backup → isolated baseline → restore → checks → candidate images → checks → cleanup
```

> [!IMPORTANT]
> Rehearse `0.2` is alpha software. It may be used commercially under Apache-2.0, but it should not
> yet be the sole production release gate, disaster-recovery control, accessibility certification,
> or legal/compliance decision. See [commercial readiness](docs/commercial-readiness.md).

## Who actually needs this?

The restore executor is deliberately specialized. It is useful when someone:

- runs stateful applications with Docker Compose;
- already creates backups but has no realistic staging environment;
- worries about database migrations or skipped intermediate releases; and
- wants machine-readable evidence before a manual or automated update.

The underlying problem is well supported, although adoption of this particular solution remains a
hypothesis. Self-hosters repeatedly report broken upgrades, untested backups, and manual update
anxiety in [a 1,700+ vote homelab recovery discussion](https://www.reddit.com/r/homelab/comments/1s829jo/selfhosting_reality_happened_sometimes/),
[a request for safer update management](https://www.reddit.com/r/selfhosted/comments/1vy6ol8/updating_self_hosted_apps/),
and Hacker News discussions about [self-hosting operational burden](https://news.ycombinator.com/item?id=45528342)
and [restore complexity in deployment platforms](https://news.ycombinator.com/item?id=43555996).
Rehearse targets the narrow restore-drill gap; it is not another backup engine, updater, or container
dashboard.

Project Brain has a wider audience. Stateless applications still get architecture, change-impact,
test, security, operability, license, privacy, UX, and accessibility evidence. For managed databases,
the brain records the dependency and flags missing ownership, export/restore method, retention,
RPO/RTO, and provider drill evidence. It does **not** pretend to restore a managed service until a
real provider adapter performs that drill.

The need for a project evidence graph is promising rather than proven. In one evaluation, the
[Repository Intelligence Graph study](https://arxiv.org/abs/2601.10112) reported better agent
accuracy and completion time from deterministic build/test evidence. The event-oriented memory is
informed by [PROJECTMEM](https://arxiv.org/abs/2606.12329). Rehearse still needs feedback and field
trials on real projects before stronger adoption claims are justified.

## Install as a standalone app

Requirements: Linux, Python 3.11 or newer, Docker Engine, and Docker Compose v2.

From a clone of this repository:

```console
uv tool install .
rehearse --version
```

`pipx install .` also works. For development, use `uv sync --extra dev` followed by
`uv run rehearse --version`.

No telemetry is collected. Rehearse never uploads reports or backup data. Docker may contact image
registries according to the recipe's `pull` policy.

## Install as an AI Agent Skill

The package includes two portable Agent Skills: [Restore Rehearsal](skills/restore-rehearsal/SKILL.md)
and [Project Brain](skills/project-brain/SKILL.md). Install either into an agent's project-level
skill directory:

```console
# Codex / agents using the common .agents directory
rehearse skill install .agents/skills --name project-brain

# Claude Code
rehearse skill install .claude/skills --name project-brain

# GitHub Copilot
rehearse skill install .github/skills --name project-brain
```

Use `--name restore-rehearsal` for the recovery skill. `rehearse skill list` lists both bundles and
`rehearse skill show --name project-brain` prints one for inspection. The installer includes all ten
Project Brain lens references and refuses every overwrite.

The agent guardrail is explicit: API keys, credentials, tokens, private keys, and backup contents
must never be uploaded. Before an agent sends any other source, project metadata, or personal or
customer information to an online service, it must explain the destination and scope and obtain
the user's explicit approval. Local analysis does not need that approval.

## Build the one-file Project Brain

Run at the top of any Git project; Docker is not required:

```console
rehearse brain build . --output project.rrbrain
rehearse brain summary project.rrbrain --json
rehearse brain query project.rrbrain "database recovery" --json
rehearse brain context project.rrbrain src/service.py --depth 2 --json
rehearse brain findings project.rrbrain --json
rehearse brain visualize project.rrbrain --output project-brain.html
```

The scanner honors Git ignore rules, inventories every remaining project file, parses common source,
manifest, workflow, Compose, recipe, HTML, and style formats, and does not execute repository code.
Known credential, key, environment, and backup paths are represented only as sensitive metadata and
are never opened. Source bodies are used only during the bounded local scan; they are not persisted
in the brain. The database stores relative paths, hashes, symbols, relationships, evidence, findings,
explicit memories, and allowlisted run results. It contains no telemetry or remote calls.

The ten evidence-backed review lenses are:

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

This is not an Obsidian-style note-link graph: [Obsidian](https://obsidian.md/help/plugins/graph)
is better for human-authored knowledge. It also does not claim deeper AST coverage than
[Graphify](https://github.com/Graphify-Labs/graphify). Rehearse's distinct job is joining project
topology to tests, recovery runs, prior failures, decisions, and review evidence. Read the full
[Project Brain design and privacy model](docs/brain.md).

## Try the synthetic example

The repository includes an Alpine example containing only the text `known-good`—no real backup,
credential, or personal data.

```console
rehearse validate examples/minimal/rehearse.yaml
rehearse run examples/minimal/rehearse.yaml --report rehearsal-report.json
```

The run proves two separate properties:

1. The synthetic backup restores into an empty named volume and passes on Alpine 3.20.
2. The same restored volume still passes after the service is recreated on Alpine 3.21.

The report is created with mode `0600`, and an existing report is never overwritten.

## Recipe at a glance

```yaml
version: 1
name: my-app

compose:
  files: [compose.yaml]
  pull: missing
  startup_timeout: 120

backups:
  - name: database
    path: ./backups/latest.dump
    sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
    mounts:
      - service: db
        target: /rehearsal-input/latest.dump

restore:
  - name: restore-database
    service: db
    command: [pg_restore, --dbname=app, /rehearsal-input/latest.dump]
    timeout: 300
    retries: 3

candidate:
  images:
    app: ghcr.io/example/app@sha256:REPLACE_WITH_A_REAL_DIGEST

checks:
  - name: database-health
    type: health
    service: db
  - name: application-smoke-test
    type: exec
    service: app
    command: [/app/bin/smoke-test]
    retries: 10
    interval: 2
```

Commands are argument arrays and Rehearse always launches subprocesses with `shell=False`.
Recipes are still trusted executable instructions: a command can do anything permitted inside its
isolated container. See [the complete recipe reference](docs/recipe.md).

## Safety model

Before the first mutating Docker command, Rehearse renders the fully merged Compose model and fails
closed on unsupported risk. It then:

- uses a high-entropy Compose project name and exact run labels;
- replaces production-derived volume and network names;
- injects backup inputs read-only and never changes their source path;
- removes published host ports and makes networks internal by default;
- rejects external resources, builds, privileged containers, added capabilities, devices, host or
  container namespaces, container-engine sockets, custom runtimes, configs, and secrets;
- converts ordinary writable directory binds into fresh run-scoped volumes;
- keeps resolved Compose data in mode-`0600` temporary files and deletes them; and
- runs `down --volumes` only after every project resource carries the exact run label.

Docker access is effectively root-equivalent. Rehearse prevents accidental reach-back into
production; it is not a sandbox for hostile images or hostile recipes. Read the full
[threat boundary and refusal list](docs/safety.md).

## Current scope

Included in `0.2.0`:

- Docker Compose v2 on Linux;
- file or directory backup inputs, always mounted read-only;
- optional SHA-256 verification for backup files;
- container command and health checks with bounded retries;
- one candidate image set;
- JSON reports with allowlisted fields;
- one-file Project Brain builds, bounded queries/context, durable memories, run import, and offline
  visual graphs;
- deterministic extractors for Git inventory, Python and JS/TS relationships, manifests, Compose,
  GitHub Actions, tests, Rehearse recipes, static HTML accessibility, and CSS design tokens;
- ten evidence-backed review lenses for engineering, recovery, design, legal issue-spotting, and
  UI/UX; and
- two installable Agent Skills with all lens references.

Deliberately not included yet:

- creating or scheduling backups;
- automatic production deployment or rollback;
- Kubernetes, Swarm, hosted dashboards, or release-note parsing;
- implicit discovery of application-specific restore commands; or
- support for every language, artifact format, cloud-provider restore API, or Compose feature.

## Development

```console
make setup
make check
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the
[changelog](CHANGELOG.md). Restore procedures are application-specific, so small, synthetic
integration recipes are especially valuable contributions.

Licensed under Apache-2.0.
