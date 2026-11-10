"""Bounded parsing and graph-fragment helpers for local artifact imports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

import yaml
from yaml.events import AliasEvent

from restore_rehearsal.brain.model import Edge, Finding, Node
from restore_rehearsal.errors import SafetyError
from restore_rehearsal.plugins.model import (
    MAX_IMPORT_EDGES,
    MAX_IMPORT_FINDINGS,
    MAX_IMPORT_NODES,
    ImportBundle,
    ImportContext,
)
from restore_rehearsal.redaction import Redactor

MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_DOCUMENT_ITEMS = 200_000
MAX_DOCUMENT_DEPTH = 40
MAX_XML_ELEMENTS = 50_000


class NoAliasSafeLoader(yaml.SafeLoader):
    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(AliasEvent):
            raise yaml.YAMLError("aliases are not allowed")
        return super().compose_node(parent, index)


def read_artifact(path: Path, *, maximum: int = MAX_ARTIFACT_BYTES) -> bytes:
    source = path.expanduser()
    try:
        info = source.lstat()
    except OSError as exc:
        raise SafetyError("artifact could not be inspected") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SafetyError("artifact must be a regular non-symlink file")
    if info.st_size > maximum:
        raise SafetyError("artifact exceeds the import size limit")
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise SafetyError("artifact could not be opened safely") from exc
    try:
        actual = os.fstat(descriptor)
        if (
            not stat.S_ISREG(actual.st_mode)
            or actual.st_dev != info.st_dev
            or actual.st_ino != info.st_ino
            or actual.st_size != info.st_size
        ):
            raise SafetyError("artifact changed while it was being imported")
        chunks: list[bytes] = []
        remaining = actual.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise SafetyError("artifact changed while it was being imported")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def json_document(data: bytes) -> Any:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise SafetyError("artifact is not valid UTF-8 JSON") from exc
    _bound_document(value)
    return value


def yaml_documents(data: bytes) -> tuple[Any, ...]:
    try:
        text = data.decode("utf-8")
        documents = tuple(yaml.load_all(text, Loader=NoAliasSafeLoader))
    except yaml.YAMLError as exc:
        if "aliases are not allowed" in str(exc):
            raise SafetyError("YAML aliases are not allowed") from exc
        raise SafetyError("artifact is not valid bounded UTF-8 YAML") from exc
    except (UnicodeDecodeError, RecursionError) as exc:
        raise SafetyError("artifact is not valid bounded UTF-8 YAML") from exc
    _bound_document(documents)
    return documents


def xml_document(data: bytes) -> ET.Element:
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)", data, re.IGNORECASE):
        raise SafetyError("XML declarations and entities are not allowed")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise SafetyError("artifact is not valid XML") from exc
    count = 0
    stack = [(root, 0)]
    while stack:
        element, depth = stack.pop()
        count += 1
        if count > MAX_XML_ELEMENTS or depth > MAX_DOCUMENT_DEPTH:
            raise SafetyError("XML artifact exceeds structural limits")
        stack.extend((child, depth + 1) for child in list(element))
    return root


def safe_text(value: Any, maximum: int = 300, *, fallback: str = "unknown") -> str:
    if value is None:
        return fallback
    text = unicodedata.normalize("NFKC", Redactor().redact(value))
    text = " ".join(text.split())
    text = "".join(
        character for character in text if not unicodedata.category(character).startswith("C")
    )
    text = text.strip()
    if not text:
        return fallback
    return text[:maximum]


def safe_relative_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 4_000:
        return None
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return None
    decoded = unquote(parsed.path).replace("\\", "/")
    decoded = decoded.removeprefix("./")
    path = PurePosixPath(decoded)
    if (
        not decoded
        or path.is_absolute()
        or ".." in path.parts
        or any(unicodedata.category(ch).startswith("C") for ch in decoded)
    ):
        return None
    return path.as_posix()[:1000]


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) and all(isinstance(key, str) for key in value) else {}


def sequence(value: Any, *, maximum: int = 10_000) -> list[Any]:
    return value[:maximum] if isinstance(value, list) else []


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def stable_id(*values: Any) -> str:
    joined = "\x1f".join(str(value) for value in values)
    return hashlib.sha256(joined.encode("utf-8", errors="replace")).hexdigest()[:24]


@dataclass
class FragmentBuilder:
    context: ImportContext
    kind: str
    importer_id: str
    importer_version: str
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: dict[tuple[str, str, str, str, str, int], Edge] = field(default_factory=dict)
    findings: dict[str, Finding] = field(default_factory=dict)

    def add_node(
        self,
        local_id: str,
        *,
        kind: str,
        name: Any,
        path: str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> str:
        key = f"{self.context.namespace}:{stable_id(local_id)}"
        node = Node(
            key=key,
            kind=kind,
            name=safe_text(name),
            path=safe_relative_path(path) if path is not None else None,
            properties=_safe_properties(properties or {}),
        )
        existing = self.nodes.get(key)
        if existing is not None and existing != node:
            raise SafetyError("artifact contains conflicting normalized nodes")
        if existing is None and len(self.nodes) >= MAX_IMPORT_NODES:
            raise SafetyError("artifact exceeds the import node limit")
        self.nodes[key] = node
        return key

    def add_edge(
        self,
        source: str,
        target: str,
        *,
        kind: str,
        evidence_path: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        edge = Edge(
            source=source,
            target=target,
            kind=kind,
            provenance="extracted",
            evidence_path=safe_relative_path(evidence_path) if evidence_path else None,
            details=_safe_properties({"imported": True, **(details or {})}),
        )
        if edge.identity not in self.edges and len(self.edges) >= MAX_IMPORT_EDGES:
            raise SafetyError("artifact exceeds the import edge limit")
        self.edges[edge.identity] = edge

    def add_finding(
        self,
        *,
        lens: str,
        rule: str,
        severity: str,
        title: Any,
        description: Any,
        remediation: Any,
        node_key: str | None = None,
        evidence_path: str | None = None,
    ) -> None:
        finding = Finding(
            lens=lens,
            rule=rule,
            severity=severity,
            title=safe_text(title, 300),
            description=safe_text(description, 2000),
            remediation=safe_text(remediation, 2000),
            confidence="extracted",
            node_key=node_key,
            evidence_path=safe_relative_path(evidence_path) if evidence_path else None,
        )
        if finding.fingerprint not in self.findings and len(self.findings) >= MAX_IMPORT_FINDINGS:
            raise SafetyError("artifact exceeds the import finding limit")
        self.findings[finding.fingerprint] = finding

    def bundle(self, summary: dict[str, Any]) -> ImportBundle:
        bundle = ImportBundle(
            kind=self.kind,
            importer_id=self.importer_id,
            importer_version=self.importer_version,
            source_name=safe_text(Path(self.context.source_name).name, 300),
            source_sha256=self.context.source_sha256,
            nodes=tuple(sorted(self.nodes.values(), key=lambda item: item.key)),
            edges=tuple(sorted(self.edges.values(), key=lambda item: item.identity)),
            findings=tuple(sorted(self.findings.values(), key=lambda item: item.fingerprint)),
            summary=_safe_properties(summary),
        )
        bundle.validate()
        return bundle


def _bound_document(value: Any) -> None:
    count = 0
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > MAX_DOCUMENT_ITEMS or depth > MAX_DOCUMENT_DEPTH:
            raise SafetyError("artifact exceeds structural limits")
        if isinstance(item, dict):
            stack.extend((key, depth + 1) for key in item)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str) and len(item) > 2 * 1024 * 1024:
            raise SafetyError("artifact contains an oversized text value")


def _safe_properties(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key, raw_value in list(value.items())[:100]:
        key = safe_text(raw_key, 100)
        result[key] = _safe_property_value(raw_value, depth=0)
    return result


def _safe_property_value(value: Any, *, depth: int) -> Any:
    if depth >= 4:
        return safe_text(value, 300)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return max(min(value, 2**53), -(2**53))
    if isinstance(value, float):
        return safe_text(value, 100)
    if isinstance(value, str):
        return safe_text(value, 2000)
    if isinstance(value, (list, tuple)):
        return [_safe_property_value(item, depth=depth + 1) for item in list(value)[:100]]
    if isinstance(value, dict):
        return {
            safe_text(key, 100): _safe_property_value(item, depth=depth + 1)
            for key, item in list(value.items())[:100]
        }
    return safe_text(value, 300)
