"""Bounded SARIF 2.1.0 importer."""

from __future__ import annotations

from typing import Any

from restore_rehearsal.errors import SafetyError
from restore_rehearsal.importers.common import (
    FragmentBuilder,
    json_document,
    mapping,
    safe_relative_path,
    safe_text,
    sequence,
)
from restore_rehearsal.plugins.model import ImportBundle, ImportContext


class SarifImporter:
    api_version = "1"
    id = "builtin.sarif"
    version = "1"
    description = "Import bounded SARIF 2.1.0 static-analysis results"
    kind = "sarif"

    def detect(self, source_name: str, data: bytes) -> int:
        if source_name.lower().endswith((".sarif", ".sarif.json")):
            return 100
        head = data[:4096]
        return 70 if b'"runs"' in head and b'"version"' in head else 0

    def parse(self, context: ImportContext, data: bytes) -> ImportBundle:
        document = mapping(json_document(data))
        if document.get("version") != "2.1.0":
            raise SafetyError("artifact is not SARIF 2.1.0")
        builder = FragmentBuilder(context, self.kind, self.id, self.version)
        run_count = result_count = rule_count = 0
        for run_index, raw_run in enumerate(sequence(document.get("runs"), maximum=100)):
            run = mapping(raw_run)
            driver = mapping(mapping(run.get("tool")).get("driver"))
            tool_name = safe_text(driver.get("name"), 200, fallback="SARIF tool")
            tool_key = builder.add_node(
                f"tool:{run_index}:{tool_name}",
                kind="analysis-tool",
                name=tool_name,
                properties={
                    "version": safe_text(driver.get("version"), 100, fallback="unknown"),
                    "format": "SARIF 2.1.0",
                },
            )
            run_count += 1
            rules: dict[str, str] = {}
            for raw_rule in sequence(driver.get("rules"), maximum=10_000):
                rule = mapping(raw_rule)
                rule_id = safe_text(rule.get("id"), 200)
                rule_key = builder.add_node(
                    f"rule:{run_index}:{rule_id}",
                    kind="analysis-rule",
                    name=rule_id,
                    properties={"tool": tool_name},
                )
                rules[rule_id] = rule_key
                builder.add_edge(tool_key, rule_key, kind="declares")
                rule_count += 1

            for result_index, raw_result in enumerate(sequence(run.get("results"), maximum=10_000)):
                result = mapping(raw_result)
                rule_id = safe_text(result.get("ruleId"), 200, fallback="unclassified")
                level = safe_text(result.get("level"), 20, fallback="warning").lower()
                message = safe_text(
                    mapping(result.get("message")).get("text"),
                    500,
                    fallback=f"{rule_id} finding",
                )
                location = _location(result)
                result_key = builder.add_node(
                    f"result:{run_index}:{result_index}:{rule_id}:{location or ''}",
                    kind="analysis-result",
                    name=f"{rule_id}: {message}"[:300],
                    path=location,
                    properties={"level": level, "tool": tool_name, "rule": rule_id},
                )
                builder.add_edge(tool_key, result_key, kind="reports", evidence_path=location)
                if rule_id in rules:
                    builder.add_edge(
                        rules[rule_id], result_key, kind="identifies", evidence_path=location
                    )
                builder.add_finding(
                    lens="security-supply-chain",
                    rule="imported-sarif-result",
                    severity=_severity(level),
                    title=f"{tool_name}: {rule_id}",
                    description=message,
                    remediation=(
                        "Review the referenced analyzer rule and verify the finding in the local "
                        "project."
                    ),
                    node_key=result_key,
                    evidence_path=location,
                )
                result_count += 1
        return builder.bundle({"runs": run_count, "rules": rule_count, "results": result_count})


def _location(result: dict[str, Any]) -> str | None:
    locations = sequence(result.get("locations"), maximum=20)
    if not locations:
        return None
    physical = mapping(mapping(locations[0]).get("physicalLocation"))
    uri = mapping(physical.get("artifactLocation")).get("uri")
    return safe_relative_path(uri)


def _severity(level: str) -> str:
    return {
        "error": "high",
        "warning": "medium",
        "note": "low",
        "none": "low",
    }.get(level, "medium")
