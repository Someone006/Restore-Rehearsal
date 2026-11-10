# Plugin SDK

Rehearse exposes typed alpha contracts for artifact importers, analyzers, and restore providers.
The SDK is intended for local, bounded extensions whose output can be independently validated. It
is not a sandbox: an activated Python plugin has the current user's filesystem, process, and
network privileges.

## Trust and loading model

Built-in importers are registered directly. Third-party plugins use Python entry points:

| Plugin kind | Entry-point group |
| --- | --- |
| Artifact importer | `rehearse.importers` |
| Analyzer | `rehearse.analyzers` |
| Restore provider | `rehearse.restore_providers` |

Rehearse never imports external entry points during startup, project scanning, initialization,
detection, or `plugin list`. Discovery reads bounded, terminal-safe package metadata only and marks
external entries inactive.

```console
rehearse plugin list --json
rehearse plugin doctor example-importer --kind importer --json
```

Loading code requires an exact, user-directed command:

```console
rehearse plugin activate example-importer --kind importer --json
```

Activation checks the entry-point name, plugin ID, API version, version, description, and duplicate
registration. It does not persist trust or enable automatic loading on a later command. Review and
pin the distribution before every environment where it may run.

## Public contracts

Import from `restore_rehearsal.plugins`:

```python
from restore_rehearsal.plugins import (
    PLUGIN_API_VERSION,
    AnalysisContext,
    AnalyzerPlugin,
    ArtifactImporter,
    ImportBundle,
    ImportContext,
    PluginRegistry,
    RestoreProvider,
)
```

The package ships a `py.typed` marker. API version `1` is the current alpha contract; package
semantic versioning and the compatibility policy still apply.

An `ArtifactImporter` declares `id`, `version`, `description`, `kind`, and `api_version`, plus:

```python
def detect(self, source_name: str, data: bytes) -> int: ...
def parse(self, context: ImportContext, data: bytes) -> ImportBundle: ...
```

`detect` returns a confidence from 0 to 100 and must be deterministic, fast, and side-effect free.
`parse` receives bounded bytes already read from one regular local file. It must not follow paths,
resolve remote references, execute embedded commands, consult environment credentials, or retain
the original body.

`AnalyzerPlugin` consumes a bounded `AnalysisContext` and returns an `ImportBundle`.
`RestoreProvider` inspects the same content-minimized context and returns JSON-safe proposal facts.
Neither contract authorizes execution of project code, cloud API calls, or a live restore.

## ImportBundle requirements

Normalized output contains a kind/importer identity, source basename and SHA-256, bounded summary,
and tuples of Project Brain nodes, edges, and findings. Validation enforces:

- identifier and version syntax;
- a basename rather than an absolute/private source path;
- graph endpoint and field integrity;
- at most 10,000 nodes, 40,000 edges, and 10,000 findings;
- bounded JSON depth, text, item, summary, and total payload sizes; and
- finite JSON numbers and JSON-serializable values.

The Project Brain storage layer applies additional canonical-JSON, secret-pattern, sensitive-key,
fragment-size, digest, count, and persistent-history checks. Passing type validation does not make
arbitrary properties appropriate: emit only facts required for the user's review.

Never include source bodies, raw logs, environment dumps, authorization headers, request/response
bodies, Terraform values, Kubernetes secret/config values, personal records, or arbitrary provider
metadata. Use safe relative evidence paths and preserve provenance.

## Packaging example

Declare an entry point in the extension's `pyproject.toml`:

```toml
[project.entry-points."rehearse.importers"]
example-importer = "example_package.importer:ExampleImporter"
```

The target may be a plugin instance or a class with a no-argument constructor. Its `id` must
exactly match `example-importer`; mismatches are refused.

Test at least deterministic success, unknown/malformed input, maximum sizes, deep nesting,
traversal/absolute/remote paths, duplicate identities, missing edge endpoints, control characters,
secret canaries, and exception conversion. Confirm that no raw source/log/credential canary enters
the returned bundle, `.rrbrain`, console output, or inspection evidence.

## Versioning and failure behavior

Plugins with an unsupported `api_version` fail registration. Exceptions during explicit external
activation are converted to a generic safety error so plugin internals or paths are not printed.
Importer parsing failures should raise `SafetyError` with a bounded, non-sensitive explanation.

External extensions are outside Rehearse's core support and security boundary. Rehearse does not
verify their publisher, signature, behavior, dependencies, or network activity. Pin hashes, review
source and transitive dependencies, run in a constrained local environment, and remove an extension
that requests credentials or broader authority than its declared job needs.
