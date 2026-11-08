"""Public project-brain operations used by the CLI and agent skills."""

from __future__ import annotations

import json
import os
import stat
import unicodedata
from collections import deque
from pathlib import Path
from typing import Any

from restore_rehearsal.brain.extract import extract_graph
from restore_rehearsal.brain.inventory import inventory_repository
from restore_rehearsal.brain.review import LENSES, apply_reviews
from restore_rehearsal.brain.store import (
    doctor,
    forget_memory,
    open_brain,
    record_run,
    remember,
    write_graph,
)
from restore_rehearsal.errors import SafetyError

MAX_QUERY_LENGTH = 500
MAX_QUERY_RESULTS = 50
MAX_CONTEXT_NODES = 100
MAX_CONTEXT_EDGES = 250
MAX_CONTEXT_FINDINGS = 200
MAX_REPORT_BYTES = 512 * 1024


def build_brain(root: Path, output: Path) -> dict[str, Any]:
    inventory = inventory_repository(root)
    graph = extract_graph(inventory)
    apply_reviews(graph)
    write_graph(output, graph, LENSES)
    return {
        "brain": output.name,
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "findings": len(graph.findings),
        "semantic_digest": graph.semantic_digest(),
    }


def brain_summary(path: Path) -> dict[str, Any]:
    with open_brain(path) as connection:
        _bound_connection(connection)
        node_kinds = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT kind, COUNT(*) FROM nodes GROUP BY kind ORDER BY kind"
            )
        }
        findings = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT severity, COUNT(*) FROM findings WHERE status='open' "
                "GROUP BY severity ORDER BY severity"
            )
        }
        metadata = dict(
            connection.execute(
                "SELECT key, value FROM metadata WHERE key IN "
                "('format', 'schema_version', 'generator_version', 'generated_at', "
                "'semantic_digest', 'git_commit')"
            )
        )
        counts = {
            "nodes": connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
            "edges": connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
            "findings": connection.execute(
                "SELECT COUNT(*) FROM findings WHERE status='open'"
            ).fetchone()[0],
            "memories": connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
            "runs": connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
        }
    return {
        "metadata": metadata,
        "counts": counts,
        "node_kinds": node_kinds,
        "open_findings_by_severity": findings,
    }


def brain_query(path: Path, query: str, *, limit: int = 20) -> dict[str, Any]:
    needle = _query_text(query)
    result_limit = min(max(limit, 1), MAX_QUERY_RESULTS)
    pattern = f"%{_escape_like(needle)}%"
    with open_brain(path) as connection:
        _bound_connection(connection)
        nodes = [
            _node_row(row)
            for row in connection.execute(
                """SELECT key, kind, name, path, line, language, properties_json
                FROM nodes
                WHERE key LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\'
                   OR COALESCE(path, '') LIKE ? ESCAPE '\\' OR kind LIKE ? ESCAPE '\\'
                ORDER BY CASE WHEN name = ? THEN 0 ELSE 1 END, kind, name, key
                LIMIT ?""",
                (pattern, pattern, pattern, pattern, needle, result_limit),
            )
        ]
        findings = [
            dict(row)
            for row in connection.execute(
                """SELECT fingerprint, lens, severity, title, description, remediation,
                    confidence, node_key, evidence_path, evidence_line, status
                FROM findings
                WHERE title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\'
                   OR lens LIKE ? ESCAPE '\\' OR rule LIKE ? ESCAPE '\\'
                ORDER BY CASE severity
                    WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2 ELSE 3 END, title
                LIMIT ?""",
                (pattern, pattern, pattern, pattern, result_limit),
            )
        ]
        memories = [
            dict(row)
            for row in connection.execute(
                """SELECT id, kind, title, body, path, status, created_at
                FROM memories
                WHERE title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\'
                   OR COALESCE(path, '') LIKE ? ESCAPE '\\'
                ORDER BY created_at DESC LIMIT ?""",
                (pattern, pattern, pattern, result_limit),
            )
        ]
    return {
        "query": needle,
        "limit": result_limit,
        "nodes": nodes,
        "findings": findings,
        "memories": memories,
        "truncated": any(len(items) == result_limit for items in (nodes, findings, memories)),
    }


def brain_context(path: Path, target: str, *, depth: int = 2) -> dict[str, Any]:
    if not 0 <= depth <= 3:
        raise SafetyError("brain context depth must be from 0 to 3")
    target_text = _query_text(target)
    with open_brain(path) as connection:
        _bound_connection(connection)
        row = connection.execute(
            """SELECT key, kind, name, path, line, language, properties_json
            FROM nodes WHERE key = ? OR path = ? ORDER BY key LIMIT 2""",
            (target_text, target_text),
        ).fetchall()
        if not row:
            matches = connection.execute(
                """SELECT key, kind, name, path, line, language, properties_json
                FROM nodes WHERE name = ? ORDER BY key LIMIT 2""",
                (target_text,),
            ).fetchall()
            row = matches
        if not row:
            raise SafetyError("brain context target was not found")
        if len(row) > 1:
            raise SafetyError("brain context target is ambiguous; use its node key or path")
        root_key = row[0]["key"]
        seen = {root_key}
        queue: deque[tuple[str, int]] = deque([(root_key, 0)])
        edge_rows: dict[tuple[Any, ...], dict[str, Any]] = {}
        traversal_truncated = False
        while queue and len(seen) < MAX_CONTEXT_NODES and len(edge_rows) < MAX_CONTEXT_EDGES:
            current, level = queue.popleft()
            if level >= depth:
                continue
            for edge in connection.execute(
                """SELECT source_key, target_key, kind, provenance,
                    evidence_path, evidence_line, details_json
                FROM edges WHERE source_key = ? OR target_key = ?
                ORDER BY source_key, target_key, kind LIMIT ?""",
                (current, current, MAX_CONTEXT_EDGES),
            ):
                neighbor = (
                    edge["target_key"] if edge["source_key"] == current else edge["source_key"]
                )
                if neighbor not in seen and len(seen) >= MAX_CONTEXT_NODES:
                    traversal_truncated = True
                    break
                identity = tuple(edge)
                edge_rows[identity] = _edge_row(edge)
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, level + 1))
                if len(edge_rows) >= MAX_CONTEXT_EDGES:
                    traversal_truncated = True
                    break
        placeholders = ",".join("?" for _ in seen)
        nodes = [
            _node_row(item)
            for item in connection.execute(
                f"""SELECT key, kind, name, path, line, language, properties_json
                FROM nodes WHERE key IN ({placeholders}) ORDER BY kind, name, key""",
                sorted(seen),
            )
        ]
        finding_rows = [
            dict(item)
            for item in connection.execute(
                f"""SELECT fingerprint, lens, severity, title, description, remediation,
                    confidence, node_key, evidence_path, evidence_line, status
                FROM findings WHERE node_key IN ({placeholders}) AND status='open'
                ORDER BY lens, severity, title LIMIT ?""",
                [*sorted(seen), MAX_CONTEXT_FINDINGS + 1],
            )
        ]
        findings_truncated = len(finding_rows) > MAX_CONTEXT_FINDINGS
        findings = finding_rows[:MAX_CONTEXT_FINDINGS]
        related_paths = sorted({item["path"] for item in nodes if item["path"]})
        memories: list[dict[str, Any]] = []
        if related_paths:
            path_placeholders = ",".join("?" for _ in related_paths)
            memories = [
                dict(item)
                for item in connection.execute(
                    f"""SELECT id, kind, title, body, path, status, created_at
                    FROM memories WHERE path IN ({path_placeholders}) AND status='active'
                    ORDER BY created_at DESC LIMIT 50""",
                    related_paths,
                )
            ]
    return {
        "target": root_key,
        "depth": depth,
        "nodes": nodes,
        "edges": list(edge_rows.values()),
        "findings": findings,
        "memories": memories,
        "truncated": (
            traversal_truncated
            or findings_truncated
            or len(seen) >= MAX_CONTEXT_NODES
            or len(edge_rows) >= MAX_CONTEXT_EDGES
        ),
    }


def brain_findings(
    path: Path, *, lens: str | None = None, severity: str | None = None
) -> list[dict[str, Any]]:
    valid_lenses = {item["id"] for item in LENSES}
    if lens is not None and lens not in valid_lenses:
        raise SafetyError("unknown review lens")
    if severity is not None and severity not in {"low", "medium", "high", "critical"}:
        raise SafetyError("finding severity must be low, medium, high, or critical")
    clauses = ["status='open'"]
    values: list[str] = []
    if lens:
        clauses.append("lens=?")
        values.append(lens)
    if severity:
        clauses.append("severity=?")
        values.append(severity)
    with open_brain(path) as connection:
        _bound_connection(connection)
        return [
            dict(row)
            for row in connection.execute(
                f"""SELECT fingerprint, lens, rule, severity, title, description,
                    remediation, confidence, node_key, evidence_path, evidence_line, status
                FROM findings WHERE {" AND ".join(clauses)}
                ORDER BY CASE severity
                    WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2 ELSE 3 END, lens, title LIMIT 500""",
                values,
            )
        ]


def brain_remember(
    path: Path,
    *,
    kind: str,
    title: str,
    body: str,
    related_path: str | None,
    supersedes: str | None = None,
) -> str:
    if related_path is not None:
        related_path = _safe_relative_path(related_path)
    return remember(
        path,
        kind=kind,
        title=title,
        body=body,
        related_path=related_path,
        supersedes=supersedes,
    )


def brain_memories(
    path: Path, *, kind: str | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    valid_kinds = {"decision", "attempt", "failure", "fix", "note", "risk-acceptance"}
    valid_statuses = {"active", "superseded", "deleted"}
    if kind is not None and kind not in valid_kinds:
        raise SafetyError("unknown memory kind")
    if status is not None and status not in valid_statuses:
        raise SafetyError("unknown memory status")
    clauses: list[str] = []
    values: list[str] = []
    if kind is not None:
        clauses.append("kind=?")
        values.append(kind)
    if status is not None:
        clauses.append("status=?")
        values.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with open_brain(path) as connection:
        _bound_connection(connection)
        return [
            dict(row)
            for row in connection.execute(
                f"""SELECT id, kind, title, path, status, supersedes, created_at
                FROM memories {where} ORDER BY created_at DESC, id DESC LIMIT 500""",
                values,
            )
        ]


def brain_forget(path: Path, memory_id: str) -> None:
    forget_memory(path, memory_id)


def brain_record_run(path: Path, report_path: Path) -> str:
    raw = _read_regular_file(report_path, maximum=MAX_REPORT_BYTES)
    try:
        report = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SafetyError("report is not valid UTF-8 JSON") from exc
    if not isinstance(report, dict):
        raise SafetyError("report must be a JSON object")
    return record_run(path, report, raw)


def brain_doctor(path: Path) -> dict[str, Any]:
    return doctor(path)


def lens_catalog() -> tuple[dict[str, str], ...]:
    return LENSES


def _node_row(row: Any) -> dict[str, Any]:
    return {
        "key": row["key"],
        "kind": row["kind"],
        "name": row["name"],
        "path": row["path"],
        "line": row["line"],
        "language": row["language"],
        "properties": json.loads(row["properties_json"]),
    }


def _edge_row(row: Any) -> dict[str, Any]:
    return {
        "source": row["source_key"],
        "target": row["target_key"],
        "kind": row["kind"],
        "provenance": row["provenance"],
        "evidence_path": row["evidence_path"] or None,
        "evidence_line": row["evidence_line"] or None,
        "details": json.loads(row["details_json"]),
    }


def _query_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_QUERY_LENGTH:
        raise SafetyError(f"brain query must contain 1 to {MAX_QUERY_LENGTH} characters")
    return "".join(
        character
        if character in "\n\t" or not unicodedata.category(character).startswith("C")
        else "?"
        for character in value.strip()
    )


def _safe_relative_path(value: str) -> str:
    if len(value) > 500 or any(
        unicodedata.category(character).startswith("C") for character in value
    ):
        raise SafetyError("memory path is invalid")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise SafetyError("memory path must be relative and contained in the project")
    return candidate.as_posix()


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _bound_connection(connection: Any) -> None:
    budget = 1_000_000

    def stop() -> int:
        nonlocal budget
        budget -= 1_000
        return 1 if budget <= 0 else 0

    connection.set_progress_handler(stop, 1_000)


def _read_regular_file(path: Path, *, maximum: int) -> bytes:
    source = path.expanduser()
    if not source.is_absolute():
        source = Path.cwd() / source
    try:
        expected = source.lstat()
    except OSError as exc:
        raise SafetyError("report import file could not be inspected") from exc
    if (
        stat.S_ISLNK(expected.st_mode)
        or not stat.S_ISREG(expected.st_mode)
        or expected.st_nlink != 1
    ):
        raise SafetyError("report import requires a regular, singly linked file")
    if expected.st_size > maximum:
        raise SafetyError("report exceeds the brain import size limit")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise SafetyError("report import file changed while it was being opened") from exc
    try:
        actual = os.fstat(descriptor)
        if (
            not stat.S_ISREG(actual.st_mode)
            or actual.st_nlink != 1
            or actual.st_dev != expected.st_dev
            or actual.st_ino != expected.st_ino
            or actual.st_size != expected.st_size
        ):
            raise SafetyError("report import file changed while it was being opened")
        chunks: list[bytes] = []
        remaining = actual.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise SafetyError("report import file changed while it was being read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise SafetyError("report exceeds the brain import size limit")
        return b"".join(chunks)
    finally:
        os.close(descriptor)
