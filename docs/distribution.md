# Distribution options

## Python application

The canonical artifact is a universal Python wheel. `uv tool` and `pipx` install it into an isolated
environment. PyPI publication is prepared through trusted publishing but remains disabled until a
repository administrator configures the protected environment and PyPI project.

## Brain-only container

The OCI image runs as numeric user `65532`, has no project or credential baked into it, and exposes
the `rehearse` CLI as its entrypoint. Build the wheel first:

```console
uv run python -m build
docker build --build-arg VERSION=0.3.0 --build-arg PACKAGE_VERSION=0.3.0 --tag rehearse:local .
docker run --rm rehearse:local --version
```

`PACKAGE_VERSION` selects one exact wheel even when an ignored local `dist/` directory contains
artifacts from older releases. A missing matching wheel fails the build.

Builds behind an intercepting HTTPS proxy may supply its trusted CA as an ephemeral BuildKit secret
with `--secret id=proxy_ca,src=/path/to/combined-ca.pem`. The certificate is used only by pip in
that build step and is not copied into an image layer. Never disable TLS verification.

Mount a project read-only and a separate output directory writable by container user `65532` for a
Project Brain build. Never mount credentials or backups merely to scan a project.

The container is intentionally marketed for Project Brain and static recipe work—not for restore
orchestration. Mounting the host Docker socket gives the container root-equivalent control over the
daemon, and backup/Compose host paths do not automatically map safely across the container boundary.
Install the host CLI for restore drills.

## Homebrew

The formula generator under `scripts/` converts an immutable tagged source distribution and
`uv.lock` into a fully hashed formula. See [the tap instructions](../packaging/homebrew/README.md).
Tagged GitHub release bundles include the generated `restore-rehearsal.rb`; the project does not
create or mutate an external tap automatically.

## Verification

Release bundles contain:

- wheel and source distribution;
- a pinned `restore-rehearsal.rb` Homebrew formula;
- `SHA256SUMS`;
- CycloneDX 1.6 and SPDX 2.3 SBOMs; and
- GitHub build-provenance attestations.

Verify checksums before installation and use `gh attestation verify` when consuming GitHub-hosted
artifacts. An SBOM describes included packages; it is not a vulnerability or license-compliance
certification.
