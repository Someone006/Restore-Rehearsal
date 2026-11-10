# Recipe reference

The recipe schema is versioned separately from the Python package. Version `1` is alpha and may
change before a stable release. Unknown keys are rejected so spelling mistakes cannot silently
weaken a drill.

## Top level

| Key | Required | Meaning |
| --- | --- | --- |
| `version` | yes | Must currently be `1`. |
| `name` | yes | Short run label; letters, numbers, `.`, `_`, and `-`. |
| `compose` | yes | Compose inputs and startup behavior. |
| `backups` | yes | One or more existing file/directory inputs. |
| `restore` | yes | One or more container command steps. |
| `candidate` | yes | Service-to-image replacements. |
| `checks` | yes | Health or container command checks. |
| `safety` | no | Explicit safety-boundary choices. |

Paths are resolved relative to the recipe file, not the caller's working directory.

## `compose`

```yaml
compose:
  files: [compose.yaml, compose.rehearsal.yaml]
  profiles: [worker]
  pull: missing
  startup_timeout: 120
```

- `files` is an ordered, non-empty list passed to Compose for its normal merge and interpolation.
- `profiles` enables named Compose profiles. Environment-selected profiles are not relied upon.
- `pull` is `always`, `missing`, or `never` and is passed to `docker compose up`.
- `startup_timeout` is the `--wait` timeout in seconds, from 1 to 3,600.

Rehearse calls `docker compose config --format json`, validates the result in memory, and writes a
fully rewritten model to a private temporary directory. It does not use a fragile override to try
to reset ports or resource names.

## `backups`

```yaml
backups:
  - name: postgres
    path: ./backups/latest.dump
    sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
    mounts:
      - service: db
        target: /rehearsal-input/latest.dump
```

Each source must already exist and be a regular file or directory. Files may include an expected
SHA-256; it is verified before any Docker mutation. Every declared mount is injected read-only with
host-path auto-creation disabled.

A file can instead be streamed to a restore process without mounting it:

```yaml
restore:
  - name: import-sql
    service: db
    command: [mysql, --database, app]
    stdin_from: database-sql
```

`stdin_from` must name a regular-file backup.

## `restore`

```yaml
restore:
  - name: restore-database
    service: db
    command: [pg_restore, --dbname=app, /rehearsal-input/latest.dump]
    timeout: 300
    retries: 3
    interval: 2
```

Rehearse starts the distinct restore services and their Compose dependencies without waiting for
application health. It then executes steps in order. `command` must be a non-empty argument array;
there is no implicit host shell. `timeout` applies to each attempt. `retries` defaults to 1 and
`interval` to 2 seconds.

If shell behavior is truly required, putting a shell executable in the command array is explicit
recipe code—for example `[sh, -ceu, '...']`. Review such recipes carefully.

## `candidate`

```yaml
candidate:
  images:
    app: ghcr.io/example/app@sha256:...
```

Every key must be an existing service. Only its image is replaced. Digest-pinned images are
recommended; mutable tags produce a warning. Candidate startup reuses the isolated volumes filled
by the baseline restore.

## `checks`

Exec check:

```yaml
checks:
  - name: smoke-test
    type: exec
    service: app
    command: [/app/bin/smoke-test]
    timeout: 15
    retries: 10
    interval: 2
```

Health check:

```yaml
checks:
  - name: database-health
    type: health
    service: db
    timeout: 10
    retries: 6
    interval: 2
```

The same checks run against the restored baseline and the candidate stack. An exec check succeeds
only with exit code 0. A health check requires a running container and, when the image declares a
healthcheck, status `healthy`.

## `safety`

```yaml
safety:
  allow_egress: false
  allow_read_only_binds:
    - ./config/application.yaml
```

Networks are internal unless `allow_egress` is explicitly true. Original binds are not reused by
default: writable directory binds become fresh named volumes, while file binds are rejected.
`allow_read_only_binds` permits only exact, existing paths that the source Compose model already
mounts read-only. The safer option is usually to declare an explicit backup mount.

## Lifecycle and exit codes

1. Parse and validate the recipe and backup digest.
2. Render, validate, and rewrite the Compose model.
3. Start restore services on current images and execute restore steps.
4. Start the full baseline and run checks.
5. Apply candidate images to the same isolated data and run checks.
6. Verify resource ownership labels and clean up.

| Code | Meaning |
| --- | --- |
| `0` | Rehearsal and cleanup passed. |
| `1` | Restore, baseline, or candidate rehearsal failed. |
| `2` | Recipe was invalid. |
| `3` | Compose configuration was refused as unsafe/unsupported. |
| `4` | Docker, cleanup, or another required environment operation failed. |
| `5` | Unexpected internal failure. |
| `130` | Interrupted by the user. |

The JSON report schema is `rehearse.dev/report/v1alpha1`. It contains allowlisted status and timing
fields, never the resolved Compose model, absolute input paths, commands, or captured command output.
