"""Typed logical graph model used before SQLite persistence."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from restore_rehearsal.errors import SafetyError

MAX_GRAPH_NODES = 50_000
MAX_GRAPH_EDGES = 200_000
MAX_GRAPH_FINDINGS = 20_000


@dataclass(frozen=True)
class Node:
    key: str
    kind: str
    name: str
    path: str | None = None
    line: int | None = None
    language: str | None = None
    sha256: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    kind: str
    provenance: str
    evidence_path: str | None = None
    evidence_line: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def identity(self) -> tuple[str, str, str, str, str, int]:
        return (
            self.source,
            self.target,
            self.kind,
            self.provenance,
            self.evidence_path or "",
            self.evidence_line or 0,
        )


@dataclass(frozen=True)
class Finding:
    lens: str
    rule: str
    severity: str
    title: str
    description: str
    remediation: str
    confidence: str
    node_key: str | None = None
    evidence_path: str | None = None
    evidence_line: int | None = None

    @property
    def fingerprint(self) -> str:
        canonical = "\x1f".join(
            (
                self.lens,
                self.rule,
                self.node_key or "",
                self.evidence_path or "",
                str(self.evidence_line or 0),
            )
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: dict[tuple[str, str, str, str, str, int], Edge] = field(default_factory=dict)
    findings: dict[str, Finding] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)

    def add_node(self, node: Node) -> None:
        _validate_text(node.key, "node key", 600)
        _validate_slug(node.kind, "node kind")
        _validate_text(node.name, "node name", 300)
        if node.path is not None:
            _validate_text(node.path, "node path", 1000)
        _validate_json_value(node.properties, "node properties")
        existing = self.nodes.get(node.key)
        if existing is not None and existing != node:
            raise ValueError(f"conflicting graph node: {node.key}")
        if existing is None and len(self.nodes) >= MAX_GRAPH_NODES:
            raise SafetyError(f"project graph exceeds the {MAX_GRAPH_NODES}-node safety limit")
        self.nodes[node.key] = node

    def add_edge(self, edge: Edge) -> None:
        _validate_text(edge.source, "edge source", 600)
        _validate_text(edge.target, "edge target", 600)
        _validate_slug(edge.kind, "edge kind")
        if edge.evidence_path is not None:
            _validate_text(edge.evidence_path, "edge evidence path", 1000)
        _validate_json_value(edge.details, "edge details")
        if edge.source == edge.target:
            return
        if edge.identity not in self.edges and len(self.edges) >= MAX_GRAPH_EDGES:
            raise SafetyError(f"project graph exceeds the {MAX_GRAPH_EDGES}-edge safety limit")
        self.edges[edge.identity] = edge

    def add_finding(self, finding: Finding) -> None:
        _validate_slug(finding.lens, "finding lens")
        _validate_slug(finding.rule, "finding rule")
        _validate_text(finding.title, "finding title", 300)
        _validate_text(finding.description, "finding description", 2000)
        _validate_text(finding.remediation, "finding remediation", 2000)
        if finding.fingerprint not in self.findings and len(self.findings) >= MAX_GRAPH_FINDINGS:
            raise SafetyError(
                f"project graph exceeds the {MAX_GRAPH_FINDINGS}-finding safety limit"
            )
        self.findings[finding.fingerprint] = finding

    def validate(self) -> None:
        allowed_provenance = {"extracted", "inferred", "declared", "runtime-verified"}
        for edge in self.edges.values():
            if edge.source not in self.nodes or edge.target not in self.nodes:
                raise ValueError(f"edge endpoint is missing: {edge.source} -> {edge.target}")
            if edge.provenance not in allowed_provenance:
                raise ValueError(f"invalid edge provenance: {edge.provenance}")
            _validate_json_value(edge.details, "edge details")
        for finding in self.findings.values():
            if finding.node_key is not None and finding.node_key not in self.nodes:
                raise ValueError(f"finding node is missing: {finding.node_key}")
        for node in self.nodes.values():
            _validate_json_value(node.properties, "node properties")

    def semantic_digest(self) -> str:
        payload = {
            "nodes": [
                {
                    "key": node.key,
                    "kind": node.kind,
                    "name": node.name,
                    "path": node.path,
                    "line": node.line,
                    "language": node.language,
                    "sha256": node.sha256,
                    "properties": node.properties,
                }
                for node in sorted(self.nodes.values(), key=lambda item: item.key)
            ],
            "edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "kind": edge.kind,
                    "provenance": edge.provenance,
                    "evidence_path": edge.evidence_path,
                    "evidence_line": edge.evidence_line,
                    "details": edge.details,
                }
                for edge in sorted(self.edges.values(), key=lambda item: item.identity)
            ],
            "findings": [
                {
                    "fingerprint": finding.fingerprint,
                    "lens": finding.lens,
                    "rule": finding.rule,
                    "severity": finding.severity,
                    "title": finding.title,
                    "description": finding.description,
                    "remediation": finding.remediation,
                    "confidence": finding.confidence,
                    "node_key": finding.node_key,
                    "evidence_path": finding.evidence_path,
                    "evidence_line": finding.evidence_line,
                }
                for finding in sorted(self.findings.values(), key=lambda item: item.fingerprint)
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def _validate_text(value: str, what: str, maximum: int) -> None:
    if (
        not value
        or len(value) > maximum
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise ValueError(f"invalid {what}")


def _validate_slug(value: str, what: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value):
        raise ValueError(f"invalid {what}")


def _validate_json_value(value: Any, what: str, *, depth: int = 0) -> None:
    if depth > 5:
        raise ValueError(f"invalid {what}")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, str):
        if len(value) > 2_000 or any(
            unicodedata.category(character).startswith("C") for character in value
        ):
            raise ValueError(f"invalid {what}")
        return
    if isinstance(value, list):
        if len(value) > 500:
            raise ValueError(f"invalid {what}")
        for item in value:
            _validate_json_value(item, what, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 500:
            raise ValueError(f"invalid {what}")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 100:
                raise ValueError(f"invalid {what}")
            _validate_json_value(key, what, depth=depth + 1)
            _validate_json_value(item, what, depth=depth + 1)
        return
    raise ValueError(f"invalid {what}")
