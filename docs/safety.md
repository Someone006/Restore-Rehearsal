# Safety model and threat boundary

Rehearse is designed to prevent an ordinary restore drill from accidentally mutating the source
Compose project, production volumes, backup input, or unrelated Docker resources. It assumes the
operator, recipe, backup, and images are trusted. Docker daemon access is root-equivalent, so this
tool is not a security boundary for malicious code.

## Invariants

### Production resources are not selected

- A random project name contains 64 bits of entropy.
- All resource names are rewritten into that project namespace.
- Every container, volume, and network receives the exact `dev.rehearse.run` ownership label.
- External volumes and networks are refused.
- Fixed `container_name` values and published host ports are removed.
- Cleanup compares all project resources with the exact run label before invoking `down --volumes`.
  Any mismatch makes cleanup fail closed.

### Host data is immutable by construction

- Backup files/directories must exist before the run and are injected read-only.
- Optional file digests are verified before Docker mutation.
- Writable host directory binds become empty, run-scoped volumes.
- Host file binds are refused unless replaced by a declared backup mount or explicitly allowlisted
  while already read-only.
- Container-engine sockets are always refused.
- Rehearse never edits the source Compose files or recipe.

### Containers have a reduced host reach

- Networks are rewritten and internal by default.
- Published ports are removed.
- `privileged`, added capabilities, devices, GPU devices, custom runtimes, lifecycle hooks,
  engine-API socket injection, `extra_hosts`, builds, custom security/cgroup/sysctl settings, and
  host/container PID, IPC, network, UTS, or user namespaces are refused.
- Custom network drivers, pinned IP ranges, and volume driver options are refused.
- Compose configs and secrets are not supported in the alpha safety model.

Internal networking is a guardrail, not a hardened sandbox. Containers still share the Docker host
kernel. Setting `allow_egress: true` also permits the rehearsal workload to contact network services.

### Secrets do not enter reports

The resolved Compose model can contain interpolated environment secrets. Rehearse keeps it in
memory and mode-`0600` files inside a mode-`0700` temporary directory. Those files are deleted on
exit. Reports are assembled from an allowlist and omit:

- environment values;
- absolute host and backup paths;
- restore/check command arrays and output;
- container logs and raw inspection data; and
- source or candidate image references.

Reports use mode `0600`, never overwrite an existing path, and are never uploaded. Console errors
are generic and pass through pattern/path redaction. Do not put credentials directly in recipe
command arguments: process argument lists may be visible to other privileged host users.

## Refusal is intentional

Unsupported features fail before the first mutating Docker command. Do not work around a refusal by
pointing the generated run at a production volume or network. Prefer a dedicated rehearsal Compose
file that preserves application semantics using empty volumes and explicit read-only inputs.

## Failure and interruption

After any attempted startup, cleanup runs in a `finally` path. Cleanup failure does not turn a
failed drill into a success. If ownership cannot be proven, resources are left in place and the
report identifies the random `run_id`; manually inspect exact labels before removal. Never delete by
a partial project-name match or a broad Docker prune command.

## What a pass does not prove

A pass cannot show that:

- the backup contains records your checks never inspect;
- every production dependency or network condition was reproduced;
- a future image with the same mutable tag is identical;
- recovery time objectives are met unless measured externally; or
- production rollout and rollback will be safe.

Use digest-pinned images, meaningful data-integrity checks, and periodic drills. Treat Rehearse as
evidence about one declared recovery path, not a recovery guarantee.
