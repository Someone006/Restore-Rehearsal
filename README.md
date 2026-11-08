# Rehearse

**Prove that a Docker Compose backup can be restored and survive a candidate upgrade—without
touching the production project.**

Rehearse is an open-source disaster-recovery drill for stateful Compose applications. It restores
a declared backup into fresh, isolated volumes using the current images, runs your checks, applies
candidate image changes, runs the checks again, writes a share-safe report, and removes everything
it created.

```text
read-only backup → isolated baseline → restore → checks → candidate images → checks → cleanup
```

> [!IMPORTANT]
> Rehearse is alpha software. A passed drill proves only that the supplied backup, recipe, images,
> Compose definition, and checks worked in that rehearsal environment. It is not a guarantee of
> complete recoverability and it never deploys to production.

## Who actually needs this?

Not every project. The initial user is someone who:

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

The same package includes a portable [`SKILL.md`](skills/restore-rehearsal/SKILL.md). Install it
into an agent's project-level skill directory:

```console
# Codex / agents using the common .agents directory
rehearse skill install .agents/skills

# Claude Code
rehearse skill install .claude/skills

# GitHub Copilot
rehearse skill install .github/skills
```

The installer creates `restore-rehearsal/SKILL.md` below the selected directory and refuses to
overwrite an existing skill. `rehearse skill show` prints the bundled skill for other agents. The
skill teaches an agent to validate first, protect backup contents, avoid weakening refusals, and
keep production rollout outside the rehearsal.

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

Included in `0.1.0`:

- Docker Compose v2 on Linux;
- file or directory backup inputs, always mounted read-only;
- optional SHA-256 verification for backup files;
- container command and health checks with bounded retries;
- one candidate image set;
- JSON reports with allowlisted fields; and
- a portable Agent Skill.

Deliberately not included yet:

- creating or scheduling backups;
- automatic production deployment or rollback;
- Kubernetes, Swarm, a web dashboard, or release-note parsing;
- implicit discovery of application-specific restore commands; or
- support for every Compose feature.

## Development

```console
make setup
make check
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the
[changelog](CHANGELOG.md). Restore procedures are application-specific, so small, synthetic
integration recipes are especially valuable contributions.

Licensed under Apache-2.0.
