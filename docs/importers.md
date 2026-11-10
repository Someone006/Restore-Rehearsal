# Evidence importers

Rehearse imports bounded facts from local analysis, test, contract, infrastructure, and CI
artifacts. Import is static: the parser does not execute the artifact, project code, embedded
commands, or remote references.

## Commands

List built-ins, let Rehearse detect a type, or select one explicitly:

```console
rehearse brain importers --json
rehearse brain import project.rrbrain build/results.sarif --json
rehearse brain import project.rrbrain build/results.sarif --type sarif --json
```

Detection uses the basename and bounded document signatures. If the result is ambiguous, select an
explicit type rather than renaming or weakening validation.

Inspect the stored index or one normalized record:

```console
rehearse brain imports project.rrbrain --json
rehearse brain show-import project.rrbrain IMPORT_ID --json
```

Removal requires one exact identifier:

```console
rehearse brain remove-import project.rrbrain IMPORT_ID --json
```

This removes the normalized import and its fragments from the brain. It does not delete or modify
the source artifact. Agents must not remove evidence merely because it is old or inconvenient; the
user should request or approve the exact deletion.

## Built-in formats

| Type | Accepted input | Retained evidence | Deliberately omitted |
| --- | --- | --- | --- |
| `sarif` | SARIF 2.1.0 JSON | tool/version, rules, result level/message, safe relative location, findings | invocations, environments, code flows, fixes, embedded source, absolute/remote locations |
| `sbom` | CycloneDX 1.4–1.6 JSON; SPDX 2.2–2.3 JSON | components/packages, versions, declared licenses, dependency relationships | external references/URLs, supplier/contact identity, hashes not needed by the graph, arbitrary properties |
| `junit` | bounded JUnit-style XML | suites, test names/classes, status, duration summaries, safe relative file paths | stdout/stderr, failure/exception bodies, system properties, environment data |
| `coverage` | LCOV text; Cobertura/coverage.py XML | safe relative files, line totals/covered counts/percentages, low-coverage findings | source bodies, branch execution details, absolute paths, arbitrary tool extensions |
| `openapi` | OpenAPI 3.x or Swagger 2.0 JSON/YAML | contract version/title, paths, methods, operation IDs, schemas, local references | servers, examples/defaults, security schemes/values, remote references, request/response bodies |
| `terraform` | Terraform plan/state JSON or bounded static `.tf` configuration | resource addresses/types, action summaries, bounded known references, deletion findings | before/after/state values, variables, outputs, credentials, provider configuration values |
| `kubernetes` | bounded JSON/YAML manifests | kinds/names/namespaces, workloads, images, service-account/storage/service relationships | Secret/ConfigMap data, annotations, environment values, command arguments, credential-bearing fields |
| `ci` | allowlisted JSON check/job summary | pipeline/job names, status/conclusion, safe relative paths, failure findings | logs, step output, environments, secrets, tokens, arbitrary provider payloads |

The table is a content-minimization contract, not a guarantee that every producer variant is
understood. Unsupported versions and malformed shapes fail closed.

## What is persisted

The original artifact is read once and is not copied into `.rrbrain`. Rehearse stores:

- the artifact basename and SHA-256 digest;
- artifact kind and importer ID/version;
- import timestamp and bounded summary counts;
- content-minimized nodes, relationships, and findings split into bounded fragments; and
- a stable import ID for later inspection or exact removal.

Normalized imports are stored beside the canonical scanner graph. Their facts retain imported
provenance and are not silently merged into scanner-produced topology. `brain query` searches
import summaries, while the offline Evidence and Timeline views expose import history. Consumers
that use fragments must continue to distinguish imported claims from independently extracted
evidence.

## Parser and privacy boundary

Core limits include a 16 MiB source artifact, bounded document item/depth counts, at most 50,000 XML
elements, at most 10,000 normalized nodes, 40,000 edges, and 10,000 findings, plus bounded summary
and persisted-fragment sizes. Exceeding a limit is a refusal, not partial success.

The reader requires one regular non-symlink file and checks that its identity and size do not change
while it is open. JSON must be UTF-8. XML declarations/entities and YAML aliases are refused.
Remote URLs and absolute/traversal paths are discarded rather than fetched. Text is normalized,
bounded, and passed through the shared redactor before persistence.

These controls reduce accidental retention but cannot recognize every secret or personal datum.
Inspect the producer configuration and use synthetic or minimized artifacts when possible. Never
upload API keys, credentials, tokens, private keys, or backups. Any other outbound artifact or
project/customer data requires explicit user approval for a named destination and scope.

## Duplicate and replacement behavior

An import identity is derived from the normalized metadata and source digest. Re-importing evidence
does not authorize deleting an older record. Use `imports` to compare provenance and remove one
exact stale record only when that retention decision is intentional.

External importer plugins use the same `ImportBundle` validation contract, but they are trusted
Python code and can access the host outside the parser boundary. See [plugin authoring](plugin-sdk.md)
before installing or activating one.
