"""Ten deterministic, evidence-backed project review lenses."""

from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath

from restore_rehearsal.brain.model import Finding, Graph, Node

LENSES: tuple[dict[str, str], ...] = (
    {
        "id": "architecture",
        "name": "Architecture Map",
        "category": "design",
        "description": "Maps components, entry points, dependencies, coupling, and architecture evidence.",
        "reference_url": "https://arxiv.org/abs/2601.10112",
    },
    {
        "id": "change-impact",
        "name": "Change Impact and Contracts",
        "category": "design",
        "description": "Surfaces dependency blast radius, contracts, schemas, and migration risk.",
        "reference_url": "https://arxiv.org/abs/2601.10112",
    },
    {
        "id": "test-strategy",
        "name": "Test Strategy and Verification",
        "category": "quality",
        "description": "Connects tests and CI evidence to the components they verify.",
        "reference_url": "https://arxiv.org/abs/2601.10112",
    },
    {
        "id": "reliability-recovery",
        "name": "Reliability and Recovery",
        "category": "operations",
        "description": "Checks service health, persistent data, recovery declarations, and managed-service assumptions.",
        "reference_url": "https://csrc.nist.gov/pubs/sp/800/218/final",
    },
    {
        "id": "security-supply-chain",
        "name": "Security and Supply Chain",
        "category": "security",
        "description": "Checks project security artifacts, workflow pinning, secrets, and privileged deployment settings.",
        "reference_url": "https://baseline.openssf.org/",
    },
    {
        "id": "performance-operability",
        "name": "Performance and Operability",
        "category": "operations",
        "description": "Checks health, observability, resource boundaries, and operational evidence.",
        "reference_url": "https://csrc.nist.gov/projects/ssdf",
    },
    {
        "id": "license-attribution",
        "name": "License and Attribution",
        "category": "legal",
        "description": "Issue-spots license, lockfile, attribution, and release-artifact evidence; not legal advice.",
        "reference_url": "https://www.apache.org/foundation/license-faq.html",
    },
    {
        "id": "privacy-data",
        "name": "Privacy and Data Governance",
        "category": "legal",
        "description": "Issue-spots data inventory, privacy documentation, and sensitive-file risks; not legal advice.",
        "reference_url": "https://www.nist.gov/privacy-framework",
    },
    {
        "id": "ux-flow",
        "name": "UX Flows and Product Content",
        "category": "ui-ux",
        "description": "Checks whether user-facing surfaces have journey and failure-state verification evidence.",
        "reference_url": "https://www.w3.org/WAI/standards-guidelines/wcag/",
    },
    {
        "id": "accessibility-design",
        "name": "Accessibility and Design System",
        "category": "ui-ux",
        "description": "Checks static accessibility and design-token evidence against a WCAG-oriented baseline.",
        "reference_url": "https://www.w3.org/TR/WCAG22/",
    },
)

MANAGED_DATA_DEPENDENCIES = {
    "@supabase/supabase-js",
    "boto3",
    "firebase",
    "firebase-admin",
    "google-cloud-firestore",
    "pymongo",
    "redis",
    "supabase",
}


def apply_reviews(graph: Graph) -> None:
    nodes = tuple(graph.nodes.values())
    paths = {node.path.lower(): node for node in nodes if node.path}
    kinds = Counter(node.kind for node in nodes)
    source_nodes = [node for node in nodes if node.kind == "source"]
    test_nodes = [node for node in nodes if node.kind == "test"]
    ui_nodes = [node for node in nodes if node.kind == "ui"]
    style_nodes = [node for node in nodes if node.kind == "style"]
    services = [node for node in nodes if node.kind == "service"]
    data_stores = [node for node in nodes if node.kind == "data-store"]
    rehearsals = [node for node in nodes if node.kind == "rehearsal"]
    dependencies = [node for node in nodes if node.kind == "dependency"]

    _architecture(graph, paths, source_nodes)
    _change_impact(graph, source_nodes)
    _test_strategy(graph, source_nodes, test_nodes)
    _reliability(graph, services, data_stores, rehearsals, dependencies, paths)
    _security(graph, nodes, paths)
    _operability(graph, services, dependencies, paths)
    _license(graph, kinds, paths)
    _privacy(graph, nodes, ui_nodes, data_stores, dependencies, paths)
    _ux(graph, ui_nodes, test_nodes, paths)
    _accessibility(graph, ui_nodes, style_nodes)

    if int(graph.metadata.get("unsafe_names_skipped", "0")):
        _add(
            graph,
            lens="security-supply-chain",
            rule="unsafe-filenames-skipped",
            severity="medium",
            title="Repository entries with unsafe names were skipped",
            description="Control or non-decodable filenames were excluded to prevent terminal and database injection.",
            remediation="Rename the affected entries to portable printable paths, then rebuild the brain.",
            confidence="extracted",
            node_key="project:root",
        )


def _architecture(graph: Graph, paths: dict[str, Node], sources: list[Node]) -> None:
    if len(sources) >= 20 and not any("architecture" in path for path in paths):
        _add(
            graph,
            lens="architecture",
            rule="missing-architecture-record",
            severity="medium",
            title="No architecture record was found",
            description="The project has at least 20 source files but no architecture-named documentation artifact.",
            remediation="Document major components, boundaries, data stores, and dependency direction with evidence links.",
            confidence="rule-derived",
            node_key="project:root",
        )
    incoming = Counter(
        edge.target for edge in graph.edges.values() if edge.kind in {"imports", "depends-on"}
    )
    for key, count in sorted(incoming.items()):
        if count >= 10 and key in graph.nodes:
            node = graph.nodes[key]
            _add(
                graph,
                lens="architecture",
                rule="high-fan-in",
                severity="low",
                title=f"High fan-in around {node.name}",
                description=f"At least {count} extracted dependency edges point to this artifact.",
                remediation="Confirm that the artifact is an intentional stable boundary and has focused tests.",
                confidence="extracted",
                node_key=key,
                evidence_path=node.path,
                evidence_line=node.line,
            )


def _change_impact(graph: Graph, sources: list[Node]) -> None:
    migration_nodes = [node for node in sources if node.path and "migration" in node.path.lower()]
    tested_targets = {edge.target for edge in graph.edges.values() if edge.kind == "tests"}
    for node in migration_nodes:
        if node.key not in tested_targets:
            _add(
                graph,
                lens="change-impact",
                rule="migration-without-linked-test",
                severity="medium",
                title=f"Migration has no linked test evidence: {node.name}",
                description="No extracted test relationship reaches this migration artifact.",
                remediation="Add a migration compatibility fixture and a rollback or forward-fix verification.",
                confidence="heuristic",
                node_key=node.key,
                evidence_path=node.path,
            )


def _test_strategy(graph: Graph, sources: list[Node], tests: list[Node]) -> None:
    if sources and not tests:
        _add(
            graph,
            lens="test-strategy",
            rule="no-tests-found",
            severity="high",
            title="No test artifacts were found",
            description="Source artifacts exist, but the deterministic inventory found no conventional test files.",
            remediation="Add a minimal unit/integration test layer and run it in CI.",
            confidence="extracted",
            node_key="project:root",
        )
    elif len(sources) >= 20 and len(tests) * 10 < len(sources):
        _add(
            graph,
            lens="test-strategy",
            rule="thin-test-surface",
            severity="medium",
            title="Test evidence is thin relative to the source surface",
            description=f"The graph contains {len(sources)} source files and {len(tests)} test files.",
            remediation="Review critical paths and add boundary, failure, and contract tests where evidence is missing.",
            confidence="heuristic",
            node_key="project:root",
        )
    ci_jobs = [node for node in graph.nodes.values() if node.kind == "ci-job"]
    if tests and ci_jobs and not any(node.properties.get("has_tests") for node in ci_jobs):
        _add(
            graph,
            lens="test-strategy",
            rule="tests-not-detected-in-ci",
            severity="medium",
            title="Tests were not detected in CI jobs",
            description="Test files exist, but common test-runner commands were not extracted from CI jobs.",
            remediation="Run the project test suite in a required CI job or document the custom test command.",
            confidence="heuristic",
            node_key="project:root",
        )


def _reliability(
    graph: Graph,
    services: list[Node],
    stores: list[Node],
    rehearsals: list[Node],
    dependencies: list[Node],
    paths: dict[str, Node],
) -> None:
    if stores and not rehearsals:
        _add(
            graph,
            lens="reliability-recovery",
            rule="persistent-data-without-rehearsal",
            severity="high",
            title="Persistent data has no declared restore rehearsal",
            description="Compose data-store nodes exist, but no version-1 Rehearse recipe was found.",
            remediation="Declare a read-only backup input, restore procedure, candidate images, and meaningful checks.",
            confidence="extracted",
            node_key=stores[0].key,
        )
    for service in services:
        if not service.properties.get("has_healthcheck"):
            _add(
                graph,
                lens="reliability-recovery",
                rule="service-without-healthcheck",
                severity="medium",
                title=f"Service has no declared healthcheck: {service.name}",
                description="The Compose service lacks healthcheck evidence for readiness and recovery drills.",
                remediation="Add a healthcheck that verifies readiness, then include a domain-level Rehearse check.",
                confidence="extracted",
                node_key=service.key,
            )
    managed = [node for node in dependencies if node.name.lower() in MANAGED_DATA_DEPENDENCIES]
    recovery_docs = any(
        any(word in path for word in ("recovery", "backup", "runbook")) for path in paths
    )
    if managed and not recovery_docs:
        _add(
            graph,
            lens="reliability-recovery",
            rule="managed-data-recovery-contract-missing",
            severity="medium",
            title="Managed-data dependency lacks recovery-contract evidence",
            description="A dependency commonly used for managed data was found without a recovery or backup runbook.",
            remediation="Document ownership, export/restore method, retention, RPO/RTO, and provider restore-drill evidence.",
            confidence="heuristic",
            node_key=managed[0].key,
        )


def _security(graph: Graph, nodes: tuple[Node, ...], paths: dict[str, Node]) -> None:
    if "security.md" not in paths:
        _add(
            graph,
            lens="security-supply-chain",
            rule="missing-security-policy",
            severity="medium",
            title="No SECURITY.md policy was found",
            description="The repository does not expose a conventional vulnerability-reporting policy.",
            remediation="Add supported versions, a private reporting route, response expectations, and scope.",
            confidence="extracted",
            node_key="project:root",
        )
    for node in nodes:
        if node.kind == "sensitive-file":
            tracked = bool(node.properties.get("tracked"))
            inventory_mode = graph.metadata.get("inventory_mode", "git")
            title = (
                f"Sensitive-looking path is tracked: {node.name}"
                if tracked
                else f"Sensitive-looking path is present: {node.name}"
            )
            description = (
                "The scanner intentionally did not read this version-controlled file because its "
                "path resembles credentials, keys, or backups."
                if tracked
                else "The scanner intentionally did not read this local file because its path "
                "resembles credentials, keys, or backups."
            )
            _add(
                graph,
                lens="security-supply-chain",
                rule=("tracked-sensitive-path" if tracked else "local-sensitive-path-present"),
                severity="critical" if tracked else "high",
                title=title,
                description=description,
                remediation=(
                    "Verify it contains no secret, remove it from version history if needed, and "
                    "use a secret manager or safe fixture."
                    if inventory_mode == "git" and tracked
                    else "Verify it is intentionally inside the project, keep it out of online "
                    "agent context, and use a secret manager or safe fixture."
                ),
                confidence="extracted",
                node_key=node.key,
                evidence_path=node.path,
            )
        if (
            node.kind == "dependency"
            and node.properties.get("ecosystem") == "github-actions"
            and not node.properties.get("pinned")
        ):
            _add(
                graph,
                lens="security-supply-chain",
                rule="unpinned-github-action",
                severity="high",
                title=f"GitHub Action is not commit-pinned: {node.name}",
                description="The workflow reference does not end in a full 40-character commit SHA.",
                remediation="Pin the action to a reviewed commit and retain a version comment for maintainability.",
                confidence="extracted",
                node_key=node.key,
            )
        if node.kind == "service" and node.properties.get("privileged"):
            _add(
                graph,
                lens="security-supply-chain",
                rule="privileged-service",
                severity="critical",
                title=f"Compose service requests privileged mode: {node.name}",
                description="Privileged containers substantially weaken the host boundary.",
                remediation="Remove privileged mode and grant only the smallest capability or device required.",
                confidence="extracted",
                node_key=node.key,
            )


def _operability(
    graph: Graph, services: list[Node], dependencies: list[Node], paths: dict[str, Node]
) -> None:
    observability_names = {
        "opentelemetry-api",
        "prometheus-client",
        "sentry-sdk",
        "@sentry/node",
        "@opentelemetry/api",
    }
    has_observability = any(node.name.lower() in observability_names for node in dependencies)
    has_ops_docs = any(
        any(word in path for word in ("runbook", "operations", "observability")) for path in paths
    )
    if services and not has_observability and not has_ops_docs:
        _add(
            graph,
            lens="performance-operability",
            rule="missing-operability-evidence",
            severity="low",
            title="No observability dependency or operations runbook was found",
            description="Service nodes exist without common telemetry packages or operations-named documentation.",
            remediation="Document logs, metrics, traces, alerts, dependency timeouts, and operational ownership.",
            confidence="heuristic",
            node_key="project:root",
        )


def _license(graph: Graph, kinds: Counter[str], paths: dict[str, Node]) -> None:
    if not kinds["license"]:
        _add(
            graph,
            lens="license-attribution",
            rule="missing-project-license",
            severity="high",
            title="No project license file was found",
            description="Automated review cannot establish redistribution terms without a conventional license artifact.",
            remediation="Have the project owner select an appropriate license and obtain qualified advice where needed.",
            confidence="extracted",
            node_key="project:root",
        )
    lock_names = {
        "uv.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "cargo.lock",
        "go.sum",
    }
    has_dependencies = any(node.kind == "dependency" for node in graph.nodes.values())
    if has_dependencies and not any(PurePosixPath(path).name in lock_names for path in paths):
        _add(
            graph,
            lens="license-attribution",
            rule="dependency-lock-missing",
            severity="medium",
            title="Dependencies were found without a recognized lockfile",
            description="Reproducible dependency and attribution review is harder without resolved versions.",
            remediation="Commit the ecosystem lockfile and generate a dependency license/SBOM inventory.",
            confidence="heuristic",
            node_key="project:root",
        )


def _privacy(
    graph: Graph,
    nodes: tuple[Node, ...],
    ui: list[Node],
    stores: list[Node],
    dependencies: list[Node],
    paths: dict[str, Node],
) -> None:
    has_privacy_doc = any("privacy" in path for path in paths)
    if (ui or stores) and not has_privacy_doc:
        _add(
            graph,
            lens="privacy-data",
            rule="privacy-evidence-missing",
            severity="low",
            title="User/data surfaces lack privacy-document evidence",
            description="UI or persistent-data artifacts exist, but no privacy-named documentation was found.",
            remediation="Inventory collected data, purposes, storage, transfers, retention, deletion, and user controls; seek legal review as applicable.",
            confidence="heuristic",
            node_key="project:root",
        )


def _ux(graph: Graph, ui: list[Node], tests: list[Node], paths: dict[str, Node]) -> None:
    if not ui:
        return
    e2e = any(
        node.path and any(word in node.path.lower() for word in ("e2e", "playwright", "cypress"))
        for node in tests
    )
    if not e2e:
        _add(
            graph,
            lens="ux-flow",
            rule="ui-without-journey-tests",
            severity="medium",
            title="UI surfaces have no detected end-to-end journey tests",
            description="User-interface files exist, but no test path references e2e, Playwright, or Cypress.",
            remediation="Add task-based journeys covering success, validation, loading, empty, error, recovery, and destructive-action flows.",
            confidence="heuristic",
            node_key=ui[0].key,
            evidence_path=ui[0].path,
        )


def _accessibility(graph: Graph, ui: list[Node], styles: list[Node]) -> None:
    for node in ui:
        if node.path and node.path.endswith(".html"):
            if node.properties.get("html_has_lang") is False:
                _add(
                    graph,
                    lens="accessibility-design",
                    rule="html-language-missing",
                    severity="medium",
                    title=f"HTML document lacks a language declaration: {node.name}",
                    description="The static parser found no non-empty lang attribute on the html element.",
                    remediation="Declare the page language and verify language changes for assistive technology.",
                    confidence="extracted",
                    node_key=node.key,
                    evidence_path=node.path,
                )
            missing_alt = int(node.properties.get("images_missing_alt", 0))
            if missing_alt:
                _add(
                    graph,
                    lens="accessibility-design",
                    rule="image-alt-missing",
                    severity="high",
                    title=f"Images without alt attributes: {missing_alt}",
                    description="Static HTML inspection found img elements without alt attributes.",
                    remediation="Add meaningful alternative text or an empty alt for verified decorative images.",
                    confidence="extracted",
                    node_key=node.key,
                    evidence_path=node.path,
                )
    if styles and sum(int(node.properties.get("custom_properties", 0)) for node in styles) == 0:
        literal_colors = sum(int(node.properties.get("literal_colors", 0)) for node in styles)
        if literal_colors >= 5:
            _add(
                graph,
                lens="accessibility-design",
                rule="design-token-evidence-missing",
                severity="low",
                title="Repeated literal colors lack detected design-token evidence",
                description=f"Style files contain at least {literal_colors} literal colors and no CSS custom properties.",
                remediation="Centralize semantic color tokens, then test contrast and interaction states against WCAG 2.2.",
                confidence="heuristic",
                node_key=styles[0].key,
                evidence_path=styles[0].path,
            )


def _add(
    graph: Graph,
    *,
    lens: str,
    rule: str,
    severity: str,
    title: str,
    description: str,
    remediation: str,
    confidence: str,
    node_key: str | None = None,
    evidence_path: str | None = None,
    evidence_line: int | None = None,
) -> None:
    graph.add_finding(
        Finding(
            lens=lens,
            rule=rule,
            severity=severity,
            title=title,
            description=description,
            remediation=remediation,
            confidence=confidence,
            node_key=node_key,
            evidence_path=evidence_path,
            evidence_line=evidence_line,
        )
    )
