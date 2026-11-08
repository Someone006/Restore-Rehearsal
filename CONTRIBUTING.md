# Contributing

Thank you for helping make restore drills boring and repeatable.

## Development setup

Requirements are Python 3.11+, `uv`, Docker Engine, and Docker Compose v2.

```console
uv sync --extra dev
make check
```

Unit tests must not require Docker or network access. Keep real Docker tests explicitly marked
`docker` and use only synthetic data and public fixture images.

## Safety changes

Changes to Compose rewriting, mounts, networking, labels, subprocess execution, report fields, or
cleanup need tests for both the permitted path and the fail-closed path. In particular:

- never invoke a host shell;
- never log a fully rendered Compose model;
- never include raw container output or host paths in reports;
- never broaden cleanup selection; and
- never weaken a refusal silently.

Use fake runners for orchestration tests. Any example must contain no real credential, backup,
person name, account identifier, or private host path.

## Pull requests

Keep changes focused, explain the user failure mode they address, add tests and documentation, and
include a changelog entry for user-visible behavior. By participating, you agree to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).
