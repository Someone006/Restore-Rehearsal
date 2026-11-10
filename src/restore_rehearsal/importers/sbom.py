"""CycloneDX and SPDX JSON software-bill-of-materials importer."""

from __future__ import annotations

from typing import Any

from restore_rehearsal.errors import SafetyError
from restore_rehearsal.importers.common import (
    FragmentBuilder,
    json_document,
    mapping,
    safe_text,
    sequence,
)
from restore_rehearsal.plugins.model import ImportBundle, ImportContext


class SbomImporter:
    api_version = "1"
    id = "builtin.sbom"
    version = "1"
    description = "Import CycloneDX or SPDX JSON component and dependency evidence"
    kind = "sbom"

    def detect(self, source_name: str, data: bytes) -> int:
        lowered = source_name.lower()
        if any(token in lowered for token in ("sbom", "bom.json", "spdx")):
            return 95
        head = data[:4096]
        if b'"bomFormat"' in head or b'"spdxVersion"' in head:
            return 90
        return 0

    def parse(self, context: ImportContext, data: bytes) -> ImportBundle:
        document = mapping(json_document(data))
        if str(document.get("bomFormat", "")).lower() == "cyclonedx":
            if str(document.get("specVersion", "")) not in {"1.4", "1.5", "1.6"}:
                raise SafetyError("unsupported CycloneDX specification version")
            return self._cyclonedx(context, document)
        if str(document.get("spdxVersion", "")) in {"SPDX-2.2", "SPDX-2.3"}:
            return self._spdx(context, document)
        raise SafetyError("unsupported SBOM JSON format")

    def _cyclonedx(self, context: ImportContext, document: dict[str, Any]) -> ImportBundle:
        builder = FragmentBuilder(context, self.kind, self.id, self.version)
        by_ref: dict[str, str] = {}
        missing_licenses = 0
        for index, raw_component in enumerate(sequence(document.get("components"), maximum=10_000)):
            component = mapping(raw_component)
            name = safe_text(component.get("name"), 300, fallback=f"component-{index}")
            version = safe_text(component.get("version"), 100, fallback="unknown")
            reference = safe_text(component.get("bom-ref"), 500, fallback=f"{name}@{version}")
            licenses = _cyclonedx_licenses(component)
            node_key = builder.add_node(
                f"component:{reference}",
                kind="dependency",
                name=name,
                properties={
                    "version": version,
                    "ecosystem": safe_text(component.get("type"), 50, fallback="library"),
                    "licenses": licenses,
                },
            )
            by_ref[reference] = node_key
            if not licenses:
                builder.add_finding(
                    lens="license-attribution",
                    rule="sbom-component-license-missing",
                    severity="low",
                    title=f"SBOM component has no declared license: {name}",
                    description="The imported SBOM did not declare a license for this component.",
                    remediation=(
                        "Verify the component license from authoritative package metadata and "
                        "update the SBOM."
                    ),
                    node_key=node_key,
                )
                missing_licenses += 1
        dependency_edges = 0
        for raw_dependency in sequence(document.get("dependencies"), maximum=20_000):
            dependency = mapping(raw_dependency)
            source = by_ref.get(safe_text(dependency.get("ref"), 500))
            if source is None:
                continue
            for target_ref in sequence(dependency.get("dependsOn"), maximum=1_000):
                target = by_ref.get(safe_text(target_ref, 500))
                if target is not None:
                    builder.add_edge(source, target, kind="depends-on")
                    dependency_edges += 1
        return builder.bundle(
            {
                "format": "CycloneDX",
                "components": len(by_ref),
                "dependency_edges": dependency_edges,
                "missing_licenses": missing_licenses,
            }
        )

    def _spdx(self, context: ImportContext, document: dict[str, Any]) -> ImportBundle:
        builder = FragmentBuilder(context, self.kind, self.id, self.version)
        by_id: dict[str, str] = {}
        missing_licenses = 0
        for index, raw_package in enumerate(sequence(document.get("packages"), maximum=10_000)):
            package = mapping(raw_package)
            name = safe_text(package.get("name"), 300, fallback=f"package-{index}")
            spdx_id = safe_text(package.get("SPDXID"), 500, fallback=name)
            version = safe_text(package.get("versionInfo"), 100, fallback="unknown")
            license_value = safe_text(
                package.get("licenseConcluded") or package.get("licenseDeclared"),
                300,
                fallback="NOASSERTION",
            )
            node_key = builder.add_node(
                f"package:{spdx_id}",
                kind="dependency",
                name=name,
                properties={"version": version, "licenses": [license_value]},
            )
            by_id[spdx_id] = node_key
            if license_value in {"NOASSERTION", "NONE", "unknown"}:
                builder.add_finding(
                    lens="license-attribution",
                    rule="sbom-component-license-missing",
                    severity="low",
                    title=f"SBOM package has no resolved license: {name}",
                    description="The imported SPDX document did not resolve a package license.",
                    remediation="Verify the package license and regenerate the SPDX document.",
                    node_key=node_key,
                )
                missing_licenses += 1
        relationship_count = 0
        for raw_relationship in sequence(document.get("relationships"), maximum=40_000):
            relationship = mapping(raw_relationship)
            relationship_type = safe_text(
                relationship.get("relationshipType"), 100, fallback="OTHER"
            ).upper()
            if relationship_type not in {"DEPENDS_ON", "DEPENDENCY_OF", "CONTAINS"}:
                continue
            left = by_id.get(safe_text(relationship.get("spdxElementId"), 500))
            right = by_id.get(safe_text(relationship.get("relatedSpdxElement"), 500))
            if left is not None and right is not None:
                if relationship_type == "DEPENDENCY_OF":
                    left, right = right, left
                builder.add_edge(left, right, kind="depends-on")
                relationship_count += 1
        return builder.bundle(
            {
                "format": "SPDX",
                "packages": len(by_id),
                "dependency_edges": relationship_count,
                "missing_licenses": missing_licenses,
            }
        )


def _cyclonedx_licenses(component: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for raw_license in sequence(component.get("licenses"), maximum=50):
        license_wrapper = mapping(raw_license)
        license_data = mapping(license_wrapper.get("license"))
        value = (
            license_data.get("id") or license_data.get("name") or license_wrapper.get("expression")
        )
        if value:
            result.append(safe_text(value, 300))
    return sorted(set(result))[:50]
