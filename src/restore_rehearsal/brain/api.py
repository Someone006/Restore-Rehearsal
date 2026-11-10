"""Public project-brain operations used by the CLI and agent skills."""

from __future__ import annotations

import hashlib
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
    get_import,
    list_imports,
    load_import_object,
    open_brain,
    record_import,
    record_run,
    remember,
    remove_import,
    write_graph,
)
from restore_rehearsal.errors import SafetyError

MAX_QUERY_LENGTH = 500
MAX_QUERY_RESULTS = 50
MAX_CONTEXT_NODES = 100
MAX_CONTEXT_EDGES = 250
MAX_CONTEXT_FINDINGS = 200
MAX_REPORT_BYTES = 512 * 1024
MAX_DIFF_ITEMS = 2_000
MAX_IMPACT_NODES = 300
MAX_IMPACT_EDGES = 800


def build_brain(root: Path, output: Path, *, label: str | None = None) -> dict[str, Any]:
    inventory = inventory_repository(root)
    graph = extract_graph(inventory)
    apply_reviews(graph)
    snapshot_id = write_graph(output, graph, LENSES, snapshot_label=label)
    return {
        "brain": output.name,
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "findings": len(graph.findings),
        "snapshot_id": snapshot_id,
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
                "'semantic_digest', 'git_commit', 'inventory_mode', "
                "'inventory_files', 'inventory_ignored_entries')"
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
            "snapshots": connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0],
            "imports": connection.execute("SELECT COUNT(*) FROM imports").fetchone()[0],
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
        imports = [
            {
                **dict(row),
                "summary": load_import_object(row["summary_json"], what="import summary"),
            }
            for row in connection.execute(
                """SELECT id, kind, source_name, importer_id, importer_version,
                    imported_at, summary_json
                FROM imports
                WHERE kind LIKE ? ESCAPE '\\' OR source_name LIKE ? ESCAPE '\\'
                   OR importer_id LIKE ? ESCAPE '\\' OR summary_json LIKE ? ESCAPE '\\'
                ORDER BY imported_at DESC, id DESC LIMIT ?""",
                (pattern, pattern, pattern, pattern, result_limit),
            )
        ]
        for item in imports:
            item.pop("summary_json", None)
    return {
        "query": needle,
        "limit": result_limit,
        "nodes": nodes,
        "findings": findings,
        "memories": memories,
        "imports": imports,
        "truncated": any(
            len(items) == result_limit for items in (nodes, findings, memories, imports)
        ),
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


def brain_snapshots(path: Path) -> list[dict[str, Any]]:
    with open_brain(path) as connection:
        _bound_connection(connection)
        return [
            _snapshot_row(row)
            for row in connection.execute(
                """SELECT id, sequence, created_at, label, semantic_digest, git_commit,
                    inventory_mode, node_count, edge_count, finding_count
                FROM snapshots ORDER BY sequence DESC LIMIT 20"""
            )
        ]


def brain_diff(
    path: Path,
    *,
    from_snapshot: str | int | None = None,
    to_snapshot: str | int | None = None,
) -> dict[str, Any]:
    with open_brain(path) as connection:
        _bound_connection(connection)
        before, after = _snapshot_pair(connection, from_snapshot, to_snapshot)
        return _snapshot_diff(connection, before, after)


def brain_impact(
    path: Path,
    *,
    from_snapshot: str | int | None = None,
    to_snapshot: str | int | None = None,
    depth: int = 2,
) -> dict[str, Any]:
    if not 0 <= depth <= 3:
        raise SafetyError("brain impact depth must be from 0 to 3")
    with open_brain(path) as connection:
        _bound_connection(connection)
        before, after = _snapshot_pair(connection, from_snapshot, to_snapshot)
        before_nodes = _snapshot_node_map(connection, before["id"])
        after_nodes = _snapshot_node_map(connection, after["id"])
        directly_changed = set(before_nodes) ^ set(after_nodes) | {
            key
            for key in set(before_nodes) & set(after_nodes)
            if before_nodes[key]["content_digest"] != after_nodes[key]["content_digest"]
        }
        before_edges = _snapshot_edge_map(connection, before["id"])
        after_edges = _snapshot_edge_map(connection, after["id"])
        changed_edge_ids = set(before_edges) ^ set(after_edges) | {
            identity
            for identity in set(before_edges) & set(after_edges)
            if before_edges[identity]["content_digest"] != after_edges[identity]["content_digest"]
        }
        before_findings = _snapshot_finding_map(connection, before["id"])
        after_findings = _snapshot_finding_map(connection, after["id"])
        changed_finding_ids = set(before_findings) ^ set(after_findings) | {
            fingerprint
            for fingerprint in set(before_findings) & set(after_findings)
            if before_findings[fingerprint]["content_digest"]
            != after_findings[fingerprint]["content_digest"]
        }
        relationship_changed: set[str] = set()
        for identity in changed_edge_ids:
            edge = after_edges.get(identity) or before_edges[identity]
            relationship_changed.update((edge["source_key"], edge["target_key"]))
        finding_changed = {
            item["node_key"]
            for fingerprint in changed_finding_ids
            if (item := after_findings.get(fingerprint) or before_findings[fingerprint])["node_key"]
            is not None
        }
        changed = directly_changed | relationship_changed | finding_changed
        all_edges = {**before_edges, **after_edges}
        adjacency: dict[str, list[tuple[tuple[Any, ...], dict[str, Any], str]]] = {}
        for identity, edge in all_edges.items():
            adjacency.setdefault(edge["source_key"], []).append(
                (identity, edge, edge["target_key"])
            )
            adjacency.setdefault(edge["target_key"], []).append(
                (identity, edge, edge["source_key"])
            )
        selected = set(sorted(changed)[:MAX_IMPACT_NODES])
        queue: deque[tuple[str, int]] = deque((key, 0) for key in sorted(selected))
        selected_edges: dict[tuple[Any, ...], dict[str, Any]] = {}
        truncated = len(changed) > len(selected)
        while queue:
            current, level = queue.popleft()
            if level >= depth:
                continue
            for identity, edge, neighbor in sorted(
                adjacency.get(current, ()), key=lambda item: item[0]
            ):
                if identity not in selected_edges and len(selected_edges) >= MAX_IMPACT_EDGES:
                    truncated = True
                    break
                if neighbor not in selected:
                    if len(selected) >= MAX_IMPACT_NODES:
                        truncated = True
                        continue
                    selected.add(neighbor)
                    queue.append((neighbor, level + 1))
                selected_edges[identity] = _public_snapshot_edge(edge)
        nodes: list[dict[str, Any]] = []
        groups: dict[str, list[str]] = {
            "components": [],
            "services": [],
            "tests": [],
            "deployment": [],
            "recovery": [],
            "contracts": [],
            "other": [],
        }
        for key in sorted(selected):
            raw = after_nodes.get(key) or before_nodes.get(key)
            if raw is None:
                continue
            node = _public_snapshot_node(raw)
            reasons = []
            if key in directly_changed:
                reasons.append("node")
            if key in relationship_changed:
                reasons.append("relationship")
            if key in finding_changed:
                reasons.append("finding")
            node["changed"] = bool(reasons)
            node["change_reasons"] = reasons
            node["state"] = (
                "added"
                if key not in before_nodes
                else "removed"
                if key not in after_nodes
                else "modified"
                if key in changed
                else "related"
            )
            nodes.append(node)
            groups[_impact_group(node)].append(key)
        return {
            "from": _snapshot_row(before),
            "to": _snapshot_row(after),
            "depth": depth,
            "changed_node_count": len(changed),
            "direct_node_change_count": len(directly_changed),
            "changed_relationship_count": len(changed_edge_ids),
            "changed_finding_count": len(changed_finding_ids),
            "nodes": nodes,
            "edges": [selected_edges[key] for key in sorted(selected_edges)],
            "groups": groups,
            "confidence": "relationship-derived",
            "note": (
                "Impact is inferred from extracted relationships and may omit runtime or dynamic "
                "dependencies."
            ),
            "truncated": truncated,
        }


def brain_record_import(
    path: Path,
    *,
    kind: str,
    source_name: str,
    source_sha256: str,
    importer_id: str,
    importer_version: str,
    summary: dict[str, Any],
    fragments: tuple[dict[str, Any], ...] = (),
) -> str:
    return record_import(
        path,
        kind=kind,
        source_name=source_name,
        source_sha256=source_sha256,
        importer_id=importer_id,
        importer_version=importer_version,
        summary=summary,
        fragments=fragments,
    )


def brain_imports(path: Path) -> list[dict[str, Any]]:
    return list_imports(path)


def brain_import(path: Path, import_id: str) -> dict[str, Any]:
    return get_import(path, import_id)


def brain_remove_import(path: Path, import_id: str) -> None:
    remove_import(path, import_id)


def lens_catalog() -> tuple[dict[str, str], ...]:
    return LENSES


def _snapshot_pair(
    connection: Any,
    from_snapshot: str | int | None,
    to_snapshot: str | int | None,
) -> tuple[Any, Any]:
    rows = connection.execute(
        """SELECT id, sequence, created_at, label, semantic_digest, git_commit,
            inventory_mode, node_count, edge_count, finding_count
        FROM snapshots ORDER BY sequence"""
    ).fetchall()
    if len(rows) < 2:
        raise SafetyError("brain diff requires at least two distinct snapshots")
    by_id = {row["id"]: row for row in rows}
    by_sequence = {row["sequence"]: row for row in rows}

    def resolve(value: str | int | None, *, default: Any) -> Any:
        if value is None:
            return default
        if isinstance(value, bool):
            raise SafetyError("snapshot reference is invalid")
        if isinstance(value, int):
            row = by_sequence.get(value)
        elif isinstance(value, str) and value == "latest":
            row = rows[-1]
        elif isinstance(value, str) and value.isdigit():
            row = by_sequence.get(int(value))
        elif isinstance(value, str) and len(value) == 24:
            row = by_id.get(value)
        else:
            row = None
        if row is None:
            raise SafetyError("snapshot reference was not found")
        return row

    after = resolve(to_snapshot, default=rows[-1])
    after_index = rows.index(after)
    if from_snapshot is None:
        if after_index == 0:
            raise SafetyError("selected snapshot has no earlier snapshot to compare")
        before = rows[after_index - 1]
    else:
        before = resolve(from_snapshot, default=rows[-2])
    if before["id"] == after["id"]:
        raise SafetyError("brain diff snapshots must be distinct")
    if before["sequence"] >= after["sequence"]:
        raise SafetyError("brain diff from-snapshot must be earlier than to-snapshot")
    return before, after


def _snapshot_diff(connection: Any, before: Any, after: Any) -> dict[str, Any]:
    before_nodes = _snapshot_node_map(connection, before["id"])
    after_nodes = _snapshot_node_map(connection, after["id"])
    before_edges = _snapshot_edge_map(connection, before["id"])
    after_edges = _snapshot_edge_map(connection, after["id"])
    before_findings = _snapshot_finding_map(connection, before["id"])
    after_findings = _snapshot_finding_map(connection, after["id"])
    node_section = _diff_section(
        before_nodes, after_nodes, public=_public_snapshot_node, key_name="key"
    )
    edge_section = _diff_section(
        before_edges, after_edges, public=_public_snapshot_edge, key_name="identity"
    )
    finding_section = _diff_section(
        before_findings,
        after_findings,
        public=_public_snapshot_finding,
        key_name="fingerprint",
    )
    return {
        "from": _snapshot_row(before),
        "to": _snapshot_row(after),
        "nodes": node_section,
        "edges": edge_section,
        "findings": finding_section,
        "truncated": any(
            section["truncated"] for section in (node_section, edge_section, finding_section)
        ),
    }


def _diff_section(
    before: dict[Any, dict[str, Any]],
    after: dict[Any, dict[str, Any]],
    *,
    public: Any,
    key_name: str,
) -> dict[str, Any]:
    added_keys = sorted(set(after) - set(before))
    removed_keys = sorted(set(before) - set(after))
    modified_keys = sorted(
        key
        for key in set(before) & set(after)
        if before[key]["content_digest"] != after[key]["content_digest"]
    )
    added = [public(after[key]) for key in added_keys[:MAX_DIFF_ITEMS]]
    removed = [public(before[key]) for key in removed_keys[:MAX_DIFF_ITEMS]]
    modified = [
        {
            key_name: _json_safe_identity(key),
            "before": public(before[key]),
            "after": public(after[key]),
        }
        for key in modified_keys[:MAX_DIFF_ITEMS]
    ]
    return {
        "counts": {
            "added": len(added_keys),
            "removed": len(removed_keys),
            "modified": len(modified_keys),
        },
        "added": added,
        "removed": removed,
        "modified": modified,
        "truncated": any(
            len(keys) > MAX_DIFF_ITEMS for keys in (added_keys, removed_keys, modified_keys)
        ),
    }


def _snapshot_node_map(connection: Any, snapshot_id: str) -> dict[str, dict[str, Any]]:
    return {
        row["node_key"]: dict(row)
        for row in connection.execute(
            """SELECT node_key, kind, name, path, line, language, sha256,
                properties_json, content_digest
            FROM snapshot_nodes WHERE snapshot_id=? ORDER BY node_key""",
            (snapshot_id,),
        )
    }


def _snapshot_edge_map(connection: Any, snapshot_id: str) -> dict[tuple[Any, ...], dict[str, Any]]:
    result: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in connection.execute(
        """SELECT source_key, target_key, kind, provenance, evidence_path, evidence_line,
            details_json, content_digest FROM snapshot_edges WHERE snapshot_id=?
        ORDER BY source_key, target_key, kind, provenance, evidence_path, evidence_line""",
        (snapshot_id,),
    ):
        item = dict(row)
        identity = (
            item["source_key"],
            item["target_key"],
            item["kind"],
            item["provenance"],
            item["evidence_path"],
            item["evidence_line"],
        )
        result[identity] = item
    return result


def _snapshot_finding_map(connection: Any, snapshot_id: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in connection.execute(
        """SELECT fingerprint, lens, rule, severity, title, description, remediation,
            confidence, node_key, evidence_path, evidence_line, status, content_digest
        FROM snapshot_findings WHERE snapshot_id=? ORDER BY fingerprint""",
        (snapshot_id,),
    ):
        item = dict(row)
        result[item["fingerprint"]] = item
    return result


def _snapshot_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "sequence": row["sequence"],
        "created_at": row["created_at"],
        "label": row["label"],
        "semantic_digest": row["semantic_digest"],
        "git_commit": row["git_commit"],
        "inventory_mode": row["inventory_mode"],
        "counts": {
            "nodes": row["node_count"],
            "edges": row["edge_count"],
            "findings": row["finding_count"],
        },
    }


def _public_snapshot_node(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": row["node_key"],
        "kind": row["kind"],
        "name": row["name"],
        "path": row["path"],
        "line": row["line"],
        "language": row["language"],
        "sha256": row["sha256"],
        "properties": _snapshot_json_object(row["properties_json"], "node properties"),
    }


def _public_snapshot_edge(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": row["source_key"],
        "target": row["target_key"],
        "kind": row["kind"],
        "provenance": row["provenance"],
        "evidence_path": row["evidence_path"] or None,
        "evidence_line": row["evidence_line"] or None,
        "details": _snapshot_json_object(row["details_json"], "edge details"),
    }


def _public_snapshot_finding(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "fingerprint": row["fingerprint"],
        "lens": row["lens"],
        "rule": row["rule"],
        "severity": row["severity"],
        "title": row["title"],
        "description": row["description"],
        "remediation": row["remediation"],
        "confidence": row["confidence"],
        "node_key": row["node_key"],
        "evidence_path": row["evidence_path"],
        "evidence_line": row["evidence_line"],
        "status": row["status"],
    }


def _json_safe_identity(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value


def _stable_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _snapshot_json_object(raw: str, what: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError, UnicodeError, RecursionError) as exc:
        raise SafetyError(f"project brain {what} is malformed") from exc
    if not isinstance(value, dict):
        raise SafetyError(f"project brain {what} is malformed")
    return value


def _impact_group(node: dict[str, Any]) -> str:
    kind = str(node.get("kind", "")).lower()
    path = str(node.get("path") or "").lower()
    if kind == "test" or any(part in path for part in ("test", "spec")):
        return "tests"
    if kind in {"service", "data-store"}:
        return "services"
    if kind in {"component", "package", "module"}:
        return "components"
    if kind in {"workflow", "deployment", "infrastructure"} or any(
        part in path for part in (".github/workflows", "terraform", "kubernetes", "helm")
    ):
        return "deployment"
    if kind in {"rehearsal", "backup", "recovery"} or "rehearse" in path:
        return "recovery"
    if kind in {"api", "route", "schema", "contract"} or any(
        part in path for part in ("openapi", "swagger", "schema")
    ):
        return "contracts"
    return "other"


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
    budget = 10_000_000

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
