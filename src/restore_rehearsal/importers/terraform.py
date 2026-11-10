"""Terraform JSON/static importer that never stores state or plan values."""

from __future__ import annotations

import re
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

BLOCK_RE = re.compile(
    r'(?m)^\s*(resource|data|module|provider)\s+"([^"\r\n]{1,200})"'
    r'(?:\s+"([^"\r\n]{1,200})")?\s*\{'
)
REFERENCE_RE = re.compile(r"\b(?:resource\.)?([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)\b")


class TerraformImporter:
    api_version = "1"
    id = "builtin.terraform"
    version = "1"
    description = "Import Terraform structure and plan actions without raw or sensitive values"
    kind = "terraform"

    def detect(self, source_name: str, data: bytes) -> int:
        lowered = source_name.lower()
        if lowered.endswith(".tf"):
            return 100
        if "tfplan" in lowered or "terraform" in lowered:
            return 95
        head = data[:8192]
        if b'"format_version"' in head and (
            b'"resource_changes"' in head or b'"planned_values"' in head
        ):
            return 90
        return 0

    def parse(self, context: ImportContext, data: bytes) -> ImportBundle:
        if context.source_name.lower().endswith(".tf"):
            return self._static(context, data)
        return self._json(context, data)

    def _json(self, context: ImportContext, data: bytes) -> ImportBundle:
        document = mapping(json_document(data))
        if "format_version" not in document:
            raise SafetyError("Terraform JSON is missing format_version")
        builder = FragmentBuilder(context, self.kind, self.id, self.version)
        root = builder.add_node(
            "terraform-plan",
            kind="infrastructure-plan",
            name="Terraform plan",
            properties={
                "format_version": safe_text(document.get("format_version"), 50),
                "terraform_version": safe_text(
                    document.get("terraform_version"), 50, fallback="unknown"
                ),
            },
        )
        resources: dict[str, str] = {}
        actions_summary: dict[str, int] = {}
        destructive = 0
        changes = sequence(document.get("resource_changes"), maximum=20_000)
        for index, raw_change in enumerate(changes):
            change = mapping(raw_change)
            address = safe_text(change.get("address"), 500, fallback=f"resource-{index}")
            resource_type = safe_text(change.get("type"), 100, fallback="resource")
            provider = _provider_name(change.get("provider_name"))
            change_data = mapping(change.get("change"))
            raw_actions = sequence(change_data.get("actions"), maximum=10)
            actions = [
                safe_text(action, 30).lower() for action in raw_actions if isinstance(action, str)
            ]
            action_key = "+".join(actions) or "unknown"
            actions_summary[action_key] = actions_summary.get(action_key, 0) + 1
            node_key = builder.add_node(
                f"resource:{address}",
                kind="infrastructure-resource",
                name=address,
                properties={
                    "resource_type": resource_type,
                    "provider": provider,
                    "actions": actions,
                    "mode": safe_text(change.get("mode"), 30, fallback="managed"),
                },
            )
            resources[address] = node_key
            builder.add_edge(root, node_key, kind="contains")
            if "delete" in actions:
                destructive += 1
                builder.add_finding(
                    lens="reliability-recovery",
                    rule="terraform-destructive-change",
                    severity="high",
                    title=f"Terraform plan deletes or replaces: {address}",
                    description=(
                        "The imported Terraform action summary includes delete. Raw before/after "
                        "values were intentionally not stored."
                    ),
                    remediation=(
                        "Review the original local plan, replacement behavior, backups, and "
                        "rollback "
                        "procedure before applying it."
                    ),
                    node_key=node_key,
                )
        for raw_resource in _value_resources(document, maximum=20_000 - len(resources)):
            resource = mapping(raw_resource)
            address = safe_text(resource.get("address"), 500, fallback=f"resource-{len(resources)}")
            if address in resources:
                continue
            resource_type = safe_text(resource.get("type"), 100, fallback="resource")
            node_key = builder.add_node(
                f"resource:{address}",
                kind="infrastructure-resource",
                name=address,
                properties={
                    "resource_type": resource_type,
                    "provider": _provider_name(resource.get("provider_name")),
                    "actions": ["present"],
                    "mode": safe_text(resource.get("mode"), 30, fallback="managed"),
                    "values_discarded": True,
                },
            )
            resources[address] = node_key
            actions_summary["present"] = actions_summary.get("present", 0) + 1
            builder.add_edge(root, node_key, kind="contains")
        _configuration_edges(builder, document, resources)
        return builder.bundle(
            {
                "format": "terraform-show-json-plan" if changes else "terraform-show-json-state",
                "resources": len(resources),
                "destructive_changes": destructive,
                "actions": dict(sorted(actions_summary.items())),
                "values_discarded": True,
            }
        )

    def _static(self, context: ImportContext, data: bytes) -> ImportBundle:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SafetyError("Terraform configuration must be UTF-8") from exc
        if len(text) > 16 * 1024 * 1024:
            raise SafetyError("Terraform configuration exceeds the import size limit")
        builder = FragmentBuilder(context, self.kind, self.id, self.version)
        root = builder.add_node(
            "terraform-config",
            kind="infrastructure-config",
            name=safe_text(context.source_name, 300),
        )
        by_address: dict[str, str] = {}
        blocks = list(BLOCK_RE.finditer(text))[:20_000]
        for index, match in enumerate(blocks):
            block_kind, first, second = match.groups()
            address = f"{first}.{second}" if second else f"{block_kind}.{first}"
            node_key = builder.add_node(
                f"block:{index}:{block_kind}:{address}",
                kind=(
                    "infrastructure-resource"
                    if block_kind in {"resource", "data"}
                    else "infrastructure-module"
                ),
                name=address,
                path=context.source_name,
                properties={"block_kind": block_kind, "type": first},
            )
            by_address[address] = node_key
            builder.add_edge(root, node_key, kind="contains", evidence_path=context.source_name)
        # Only derive references between known block addresses; no expression values are stored.
        for match in REFERENCE_RE.finditer(text[:8_000_000]):
            address = f"{match.group(1)}.{match.group(2)}"
            target = by_address.get(address)
            if target is not None:
                builder.add_edge(root, target, kind="references", evidence_path=context.source_name)
        return builder.bundle(
            {
                "format": "terraform-static",
                "blocks": len(blocks),
                "values_discarded": True,
            }
        )


def _provider_name(value: Any) -> str:
    text = safe_text(value, 200, fallback="unknown")
    return text.rsplit("/", 1)[-1]


def _configuration_edges(
    builder: FragmentBuilder,
    document: dict[str, Any],
    resources: dict[str, str],
) -> None:
    configuration = mapping(document.get("configuration"))
    root_module = mapping(configuration.get("root_module"))
    for raw_resource in sequence(root_module.get("resources"), maximum=20_000):
        resource = mapping(raw_resource)
        source = resources.get(safe_text(resource.get("address"), 500))
        if source is None:
            continue
        expressions = mapping(resource.get("expressions"))
        for raw_expression in list(expressions.values())[:500]:
            expression = mapping(raw_expression)
            for raw_reference in sequence(expression.get("references"), maximum=500):
                reference = safe_text(raw_reference, 500)
                pieces = reference.split(".")
                candidates = [reference, ".".join(pieces[:2])]
                target = next((resources[item] for item in candidates if item in resources), None)
                if target is not None and source != target:
                    builder.add_edge(source, target, kind="depends-on")


def _value_resources(document: dict[str, Any], *, maximum: int) -> list[dict[str, Any]]:
    if maximum <= 0:
        return []
    values = mapping(document.get("planned_values") or document.get("values"))
    root = mapping(values.get("root_module"))
    if not root:
        return []
    result: list[dict[str, Any]] = []
    stack: list[tuple[dict[str, Any], int]] = [(root, 0)]
    while stack and len(result) < maximum:
        module, depth = stack.pop()
        if depth > 30:
            raise SafetyError("Terraform module nesting exceeds the import limit")
        for raw_resource in sequence(module.get("resources"), maximum=maximum - len(result)):
            result.append(mapping(raw_resource))
            if len(result) >= maximum:
                break
        for child in reversed(sequence(module.get("child_modules"), maximum=5_000)):
            stack.append((mapping(child), depth + 1))
    return result
