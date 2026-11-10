# Release procedure

The release pipeline is designed to fail before publication and to keep publishing authority
separate from ordinary CI.

## One-time repository administration

An administrator—not a workflow—must:

1. create a protected `release` environment with required reviewers;
2. configure PyPI Trusted Publishing for this repository and environment;
3. set `REHEARSE_PYPI_PUBLISH=true` only after the PyPI project is controlled;
4. set `REHEARSE_GHCR_PUBLISH=true` only after GHCR visibility and retention are decided;
5. enable immutable releases, artifact attestations, branch protection, private vulnerability
   reporting, and required CI/security checks as appropriate; and
6. create a separate Homebrew tap if Homebrew distribution is wanted.

No API token belongs in the repository. PyPI publishing uses GitHub OIDC. The container job uses the
short-lived repository token and is disabled unless the repository variable is set.

## Release checklist

1. Confirm a clean tree and review every dependency/license change.
2. Update the package version and changelog; run unit tests, installed-wheel smoke tests, the real
   Docker integration test, and relevant benchmarks.
3. Create an annotated `vMAJOR.MINOR.PATCH` tag pointing at the reviewed commit and push it.
4. The `Release` workflow checks tag/package agreement, tests, builds twice with the same
   `SOURCE_DATE_EPOCH`, compares hashes, creates SPDX and CycloneDX SBOMs, writes `SHA256SUMS`,
   smoke-tests the wheel, and produces GitHub provenance attestations.
5. The workflow creates or updates a GitHub prerelease. PyPI and GHCR jobs run only when their
   explicit repository variables and protected environment are configured.
6. Verify the published tag, source archive, checksums, attestation, installation command, and the
   release page from a clean environment.

Manual dispatch verifies and packages an existing version tag but does not publish a GitHub release,
PyPI package, or container. It is useful for rehearsing the pipeline.

## Local rehearsal

```console
export SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)"
uv run python -m scripts.reproducible_build --project-root . --output /tmp/rehearse-release --expected-version 0.3.0
uv run python -m scripts.generate_homebrew_formula --project-root . --sdist /tmp/rehearse-release/restore_rehearsal-0.3.0.tar.gz --url https://github.com/Someone006/Restore-Rehearsal/releases/download/v0.3.0/restore_rehearsal-0.3.0.tar.gz --version 0.3.0 --output /tmp/rehearse-release/restore-rehearsal.rb
uv run python -m scripts.generate_release_metadata --project-root . --artifacts /tmp/rehearse-release
cd /tmp/rehearse-release
sha256sum --check SHA256SUMS
```

Use a new empty output directory. The scripts refuse mismatched versions, missing artifacts, invalid
epochs, and non-reproducible results.
