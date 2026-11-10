# Compatibility matrix

Rehearse is an alpha project. This matrix describes what the automated suite exercises; it is not
a claim that every project, database, or operating-system configuration is supported.

| Capability | Linux | macOS | Windows | Docker required |
| --- | --- | --- | --- | --- |
| Project Brain filesystem scan/query/visual export | CI-tested | wheel smoke-tested | wheel smoke-tested | No |
| Agent Skill listing and installation | CI-tested | wheel smoke-tested | wheel smoke-tested | No |
| Recipe parsing and static validation | CI-tested | package-compatible | package-compatible | Validation invokes Compose |
| Disposable restore and candidate-upgrade drill | CI-tested | Not currently claimed | Not currently claimed | Docker Compose v2 |
| Brain-only OCI image | CI build/release prepared | Runs through a Linux container | Runs through a Linux container | Container runtime |

Supported Python versions are 3.11 and 3.12. Project Brain does not require Git. Git-aware mode is
used when a readable Git work tree is present; otherwise the bounded filesystem inventory is used.

Restore behavior depends on Docker Engine, Compose v2, the declared images, and the application
recipe. The integration suite uses synthetic Alpine fixtures on Linux. A passing drill demonstrates
only the declared backup, restore steps, candidate images, and checks in that disposable run.

## Data-format compatibility

- `rehearse.yaml`: version 1
- share-safe run report: `rehearse.dev/report/v1alpha1`
- `.rrbrain`: schema version is validated exactly and migrated only through documented atomic paths
- benchmark result: `rehearse.dev/benchmark/v1`

Unknown major/schema versions fail closed. See [support policy](support-policy.md) for the release
window and [commercial readiness](commercial-readiness.md) for adoption guidance.
