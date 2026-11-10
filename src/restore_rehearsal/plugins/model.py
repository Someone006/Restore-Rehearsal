"""Typed contracts shared by built-in and explicitly trusted plugins."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from restore_rehearsal.brain.model import Edge, Finding, Graph, Node
from restore_rehearsal.errors import SafetyError

MAX_IMPORT_NODES = 10_000
MAX_IMPORT_EDGES = 40_000
MAX_IMPORT_FINDINGS = 10_000
MAX_IMPORT_SUMMARY_BYTES = 64 * 1024
MAX_IMPORT_PAYLOAD_BYTES = 2 * 1024 * 1024
PLUGIN_API_VERSION = "1"


@dataclass(frozen=True)
class AnalysisContext:
    """Bounded project facts supplied to an analyzer.

    External plugins are trusted Python code and can still access the host on their own. Rehearse
    never discovers or loads them automatically.
    """

    project_name: str
    files: tuple[str, ...]
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ImportContext:
    source_name: str
    source_sha256: str
    namespace: str

    @classmethod
    def from_bytes(cls, source_name: str, kind: str, data: bytes) -> ImportContext:
        digest = hashlib.sha256(data).hexdigest()
        safe_kind = re.sub(r"[^a-z0-9-]", "-", kind.lower()).strip("-") or "artifact"
        return cls(
            source_name=source_name,
            source_sha256=digest,
            namespace=f"import:{safe_kind}:{digest[:16]}",
        )


@dataclass(frozen=True)
class ImportBundle:
    kind: str
    importer_id: str
    importer_version: str
    source_name: str
    source_sha256: str
    nodes: tuple[Node, ...] = ()
    edges: tuple[Edge, ...] = ()
    findings: tuple[Finding, ...] = ()
    summary: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", self.kind):
            raise SafetyError("import bundle kind is invalid")
        if not re.fullmatch(r"[a-z][a-z0-9.-]{0,99}", self.importer_id):
            raise SafetyError("importer identifier is invalid")
        if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,31}", self.importer_version):
            raise SafetyError("importer version is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise SafetyError("import source digest is invalid")
        if (
            not self.source_name
            or len(self.source_name) > 300
            or "/" in self.source_name
            or "\\" in self.source_name
            or any(
                unicodedata.category(character).startswith("C") for character in self.source_name
            )
        ):
            raise SafetyError("import source name is invalid")
        if len(self.nodes) > MAX_IMPORT_NODES:
            raise SafetyError("artifact exceeds the import node limit")
        if len(self.edges) > MAX_IMPORT_EDGES:
            raise SafetyError("artifact exceeds the import edge limit")
        if len(self.findings) > MAX_IMPORT_FINDINGS:
            raise SafetyError("artifact exceeds the import finding limit")
        try:
            encoded_summary = json.dumps(
                self.summary, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise SafetyError("artifact import summary is not valid JSON") from exc
        if len(encoded_summary) > MAX_IMPORT_SUMMARY_BYTES:
            raise SafetyError("artifact import summary exceeds the size limit")

        graph = Graph()
        try:
            for node in self.nodes:
                graph.add_node(node)
            for edge in self.edges:
                graph.add_edge(edge)
            for finding in self.findings:
                graph.add_finding(finding)
            graph.validate()
        except (TypeError, ValueError) as exc:
            raise SafetyError("artifact produced an invalid graph fragment") from exc

    def to_payload(self) -> dict[str, Any]:
        self.validate()
        return {
            "format": "rehearse.dev/import-bundle/v1",
            "kind": self.kind,
            "importer_id": self.importer_id,
            "importer_version": self.importer_version,
            "source_name": self.source_name,
            "source_sha256": self.source_sha256,
            "summary": self.summary,
            "nodes": [
                {
                    "key": item.key,
                    "kind": item.kind,
                    "name": item.name,
                    "path": item.path,
                    "line": item.line,
                    "language": item.language,
                    "sha256": item.sha256,
                    "properties": item.properties,
                }
                for item in self.nodes
            ],
            "edges": [
                {
                    "source": item.source,
                    "target": item.target,
                    "kind": item.kind,
                    "provenance": item.provenance,
                    "evidence_path": item.evidence_path,
                    "evidence_line": item.evidence_line,
                    "details": item.details,
                }
                for item in self.edges
            ],
            "findings": [
                {
                    "lens": item.lens,
                    "rule": item.rule,
                    "severity": item.severity,
                    "title": item.title,
                    "description": item.description,
                    "remediation": item.remediation,
                    "confidence": item.confidence,
                    "node_key": item.node_key,
                    "evidence_path": item.evidence_path,
                    "evidence_line": item.evidence_line,
                }
                for item in self.findings
            ],
        }

    def to_json(self) -> str:
        encoded = json.dumps(
            self.to_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        if len(encoded.encode("utf-8")) > MAX_IMPORT_PAYLOAD_BYTES:
            raise SafetyError("normalized artifact exceeds the import payload limit")
        return encoded

    @classmethod
    def from_payload(cls, raw: Any) -> ImportBundle:
        data = _mapping(raw)
        if data.get("format") != "rehearse.dev/import-bundle/v1":
            raise SafetyError("normalized artifact format is unsupported")
        try:
            bundle = cls(
                kind=str(data["kind"]),
                importer_id=str(data["importer_id"]),
                importer_version=str(data["importer_version"]),
                source_name=str(data["source_name"]),
                source_sha256=str(data["source_sha256"]),
                summary=_mapping(data.get("summary")),
                nodes=tuple(
                    Node(
                        key=str(item["key"]),
                        kind=str(item["kind"]),
                        name=str(item["name"]),
                        path=_optional_string(item.get("path")),
                        line=_optional_integer(item.get("line")),
                        language=_optional_string(item.get("language")),
                        sha256=_optional_string(item.get("sha256")),
                        properties=_mapping(item.get("properties")),
                    )
                    for item in _mapping_sequence(data.get("nodes"), MAX_IMPORT_NODES)
                ),
                edges=tuple(
                    Edge(
                        source=str(item["source"]),
                        target=str(item["target"]),
                        kind=str(item["kind"]),
                        provenance=str(item["provenance"]),
                        evidence_path=_optional_string(item.get("evidence_path")),
                        evidence_line=_optional_integer(item.get("evidence_line")),
                        details=_mapping(item.get("details")),
                    )
                    for item in _mapping_sequence(data.get("edges"), MAX_IMPORT_EDGES)
                ),
                findings=tuple(
                    Finding(
                        lens=str(item["lens"]),
                        rule=str(item["rule"]),
                        severity=str(item["severity"]),
                        title=str(item["title"]),
                        description=str(item["description"]),
                        remediation=str(item["remediation"]),
                        confidence=str(item["confidence"]),
                        node_key=_optional_string(item.get("node_key")),
                        evidence_path=_optional_string(item.get("evidence_path")),
                        evidence_line=_optional_integer(item.get("evidence_line")),
                    )
                    for item in _mapping_sequence(data.get("findings"), MAX_IMPORT_FINDINGS)
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SafetyError("normalized artifact payload is malformed") from exc
        bundle.validate()
        bundle.to_json()
        return bundle


@dataclass(frozen=True)
class PluginDescriptor:
    id: str
    kind: str
    version: str
    description: str
    api_version: str
    source: str
    enabled: bool


@runtime_checkable
class AnalyzerPlugin(Protocol):
    id: str
    version: str
    description: str
    api_version: str

    def analyze(self, context: AnalysisContext) -> ImportBundle: ...


@runtime_checkable
class ArtifactImporter(Protocol):
    id: str
    version: str
    description: str
    kind: str
    api_version: str

    def detect(self, source_name: str, data: bytes) -> int: ...

    def parse(self, context: ImportContext, data: bytes) -> ImportBundle: ...


@runtime_checkable
class RestoreProvider(Protocol):
    id: str
    version: str
    description: str
    api_version: str

    def inspect(self, context: AnalysisContext) -> dict[str, Any]: ...


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise SafetyError("normalized artifact mapping is malformed")
    return value


def _mapping_sequence(value: Any, maximum: int) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise SafetyError("normalized artifact collection exceeds its limit")
    return tuple(_mapping(item) for item in value)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SafetyError("normalized artifact text field is malformed")
    return value


def _optional_integer(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise SafetyError("normalized artifact integer field is malformed")
    return value
