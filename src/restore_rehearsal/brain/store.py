"""Atomic, validated persistence for the one-file project brain."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import stat
import tempfile
import threading
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import quote

from restore_rehearsal import __version__
from restore_rehearsal.brain.model import (
    MAX_GRAPH_EDGES,
    MAX_GRAPH_FINDINGS,
    MAX_GRAPH_NODES,
    Graph,
)
from restore_rehearsal.brain.schema import (
    APPLICATION_ID,
    EXPECTED_TABLES,
    SCHEMA_SQL,
    SCHEMA_V1_SQL,
    SCHEMA_VERSION,
)
from restore_rehearsal.errors import EnvironmentError, SafetyError

MAX_BRAIN_BYTES = 256 * 1024 * 1024
MAX_MEMORY_BYTES = 16 * 1024
MAX_MEMORIES = 10_000
MAX_RUNS = 10_000
MAX_PERSISTENT_BYTES = 32 * 1024 * 1024
MAX_SNAPSHOTS = 20
MAX_SNAPSHOT_NODES = 200_000
MAX_SNAPSHOT_EDGES = 500_000
MAX_SNAPSHOT_FINDINGS = 100_000
MAX_SNAPSHOT_PERSISTENT_BYTES = 128 * 1024 * 1024
MAX_IMPORTS = 1_000
MAX_IMPORT_FRAGMENTS = 10_000
MAX_IMPORT_FRAGMENTS_PER_IMPORT = 500
MAX_IMPORT_PERSISTENT_BYTES = 32 * 1024 * 1024
SQLITE_STEPS = 10_000_000
SQLITE_FULL_CHECK_STEPS = 50_000_000

MEMORY_COLUMNS = (
    "id",
    "kind",
    "title",
    "body",
    "path",
    "status",
    "supersedes",
    "created_at",
    "content_sha256",
)
RUN_COLUMNS = (
    "run_id",
    "recipe",
    "status",
    "cleanup",
    "started_at",
    "finished_at",
    "report_sha256",
    "summary_json",
)
FINDING_STATE_COLUMNS = ("fingerprint", "status", "first_seen")
SNAPSHOT_COLUMNS = (
    "id",
    "sequence",
    "created_at",
    "label",
    "semantic_digest",
    "git_commit",
    "inventory_mode",
    "node_count",
    "edge_count",
    "finding_count",
)
SNAPSHOT_NODE_COLUMNS = (
    "snapshot_id",
    "node_key",
    "kind",
    "name",
    "path",
    "line",
    "language",
    "sha256",
    "properties_json",
    "content_digest",
)
SNAPSHOT_EDGE_COLUMNS = (
    "snapshot_id",
    "source_key",
    "target_key",
    "kind",
    "provenance",
    "evidence_path",
    "evidence_line",
    "details_json",
    "content_digest",
)
SNAPSHOT_FINDING_COLUMNS = (
    "snapshot_id",
    "fingerprint",
    "lens",
    "rule",
    "severity",
    "title",
    "description",
    "remediation",
    "confidence",
    "node_key",
    "evidence_path",
    "evidence_line",
    "status",
    "content_digest",
)
IMPORT_COLUMNS = (
    "id",
    "kind",
    "source_name",
    "sha256",
    "importer_id",
    "importer_version",
    "imported_at",
    "summary_json",
)
IMPORT_FRAGMENT_COLUMNS = (
    "import_id",
    "ordinal",
    "payload_json",
    "payload_sha256",
)
TABLE_LIMITS = {
    "metadata": 100,
    "nodes": MAX_GRAPH_NODES,
    "edges": MAX_GRAPH_EDGES,
    "findings": MAX_GRAPH_FINDINGS,
    "lenses": 10,
    "memories": MAX_MEMORIES,
    "runs": MAX_RUNS,
    "snapshots": MAX_SNAPSHOTS,
    "snapshot_nodes": MAX_SNAPSHOT_NODES,
    "snapshot_edges": MAX_SNAPSHOT_EDGES,
    "snapshot_findings": MAX_SNAPSHOT_FINDINGS,
    "imports": MAX_IMPORTS,
    "import_fragments": MAX_IMPORT_FRAGMENTS,
}
V1_TABLE_LIMITS = {
    key: value
    for key, value in TABLE_LIMITS.items()
    if key in {"metadata", "nodes", "edges", "findings", "lenses", "memories", "runs"}
}

_PROCESS_LOCK = threading.RLock()


@dataclass(frozen=True)
class PersistentState:
    memories: tuple[dict[str, Any], ...] = ()
    runs: tuple[dict[str, Any], ...] = ()
    finding_state: tuple[dict[str, str], ...] = ()
    snapshots: tuple[dict[str, Any], ...] = ()
    snapshot_nodes: tuple[dict[str, Any], ...] = ()
    snapshot_edges: tuple[dict[str, Any], ...] = ()
    snapshot_findings: tuple[dict[str, Any], ...] = ()
    imports: tuple[dict[str, Any], ...] = ()
    import_fragments: tuple[dict[str, Any], ...] = ()


def write_graph(
    path: Path,
    graph: Graph,
    lenses: tuple[dict[str, str], ...],
    *,
    snapshot_label: str | None = None,
) -> str:
    """Build privately beside the destination, validate, then atomically replace it."""

    graph.validate()
    destination = _safe_destination(path)
    with _directory_lock(destination.parent):
        return _write_graph_locked(destination, graph, lenses, snapshot_label=snapshot_label)


def _write_graph_locked(
    destination: Path,
    graph: Graph,
    lenses: tuple[dict[str, str], ...],
    *,
    snapshot_label: str | None,
) -> str:
    persistent = read_persistent_state(destination) if destination.exists() else PersistentState()
    parent = destination.parent
    descriptor, temporary_name = tempfile.mkstemp(
        dir=parent, prefix=f".{destination.name}.", suffix=".tmp"
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.chmod(0o600)
    now = _now()
    snapshot_id: str | None = None
    semantic_digest = graph.semantic_digest()
    try:
        connection = sqlite3.connect(temporary)
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript(SCHEMA_SQL)
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            metadata = {
                **graph.metadata,
                "format": "rehearse-project-brain",
                "schema_version": str(SCHEMA_VERSION),
                "generator_version": __version__,
                "generated_at": now,
                "semantic_digest": semantic_digest,
            }
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)", sorted(metadata.items())
            )
            connection.executemany(
                """INSERT INTO nodes(
                    key, kind, name, path, line, language, sha256, properties_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        node.key,
                        node.kind,
                        node.name,
                        node.path,
                        node.line,
                        node.language,
                        node.sha256,
                        _json(node.properties),
                    )
                    for node in sorted(graph.nodes.values(), key=lambda item: item.key)
                ],
            )
            connection.executemany(
                """INSERT INTO edges(
                    source_key, target_key, kind, provenance,
                    evidence_path, evidence_line, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        edge.source,
                        edge.target,
                        edge.kind,
                        edge.provenance,
                        edge.evidence_path or "",
                        edge.evidence_line or 0,
                        _json(edge.details),
                    )
                    for edge in sorted(graph.edges.values(), key=lambda item: item.identity)
                ],
            )
            previous_findings = {item["fingerprint"]: item for item in persistent.finding_state}
            connection.executemany(
                """INSERT INTO findings(
                    fingerprint, lens, rule, severity, title, description, remediation,
                    confidence, node_key, evidence_path, evidence_line, status,
                    first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        finding.fingerprint,
                        finding.lens,
                        finding.rule,
                        finding.severity,
                        finding.title,
                        finding.description,
                        finding.remediation,
                        finding.confidence,
                        finding.node_key,
                        finding.evidence_path,
                        finding.evidence_line,
                        previous_findings.get(finding.fingerprint, {}).get("status", "open"),
                        previous_findings.get(finding.fingerprint, {}).get("first_seen", now),
                        now,
                    )
                    for finding in sorted(
                        graph.findings.values(), key=lambda item: item.fingerprint
                    )
                ],
            )
            connection.executemany(
                """INSERT INTO lenses(id, name, category, description, reference_url)
                VALUES (:id, :name, :category, :description, :reference_url)""",
                sorted(lenses, key=lambda item: item["id"]),
            )
            _restore_persistent(connection, persistent)
            snapshot_id = _append_snapshot(
                connection,
                graph,
                semantic_digest=semantic_digest,
                created_at=now,
                label=snapshot_label,
            )
            _prune_snapshots(connection)
            connection.commit()
            _set_progress_budget(connection, SQLITE_FULL_CHECK_STEPS)
            _validate_connection(connection, full=True)
        finally:
            connection.close()
        _fsync_file(temporary)
        if temporary.stat().st_size > MAX_BRAIN_BYTES:
            raise SafetyError("generated project brain exceeds the supported size limit")
        os.replace(temporary, destination)
        _fsync_directory(parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        _remove_sidecars(temporary)
    if snapshot_id is None:
        raise SafetyError("project brain snapshot could not be recorded")
    return snapshot_id


def read_persistent_state(path: Path) -> PersistentState:
    version = _brain_schema_version(path)
    if version == SCHEMA_VERSION:
        context = open_brain(path)
    elif version == 1:
        context = _open_legacy_v1_brain(path)
    else:
        raise SafetyError("project brain schema is unsupported by this Rehearse version")
    with context as connection:
        memories = _bounded_rows(connection, "memories", MEMORY_COLUMNS, maximum=MAX_MEMORIES)
        runs = _bounded_rows(connection, "runs", RUN_COLUMNS, maximum=MAX_RUNS)
        finding_state = _bounded_rows(
            connection,
            "findings",
            FINDING_STATE_COLUMNS,
            maximum=MAX_GRAPH_FINDINGS,
        )
        total_bytes = _persistent_size(memories) + _persistent_size(runs)
        if total_bytes > MAX_PERSISTENT_BYTES:
            raise SafetyError("project brain persistent history exceeds the rebuild limit")
        if version == 1:
            return PersistentState(
                memories=memories,
                runs=runs,
                finding_state=finding_state,
            )
        snapshots = _bounded_rows(connection, "snapshots", SNAPSHOT_COLUMNS, maximum=MAX_SNAPSHOTS)
        snapshot_nodes = _bounded_rows(
            connection,
            "snapshot_nodes",
            SNAPSHOT_NODE_COLUMNS,
            maximum=MAX_SNAPSHOT_NODES,
        )
        snapshot_edges = _bounded_rows(
            connection,
            "snapshot_edges",
            SNAPSHOT_EDGE_COLUMNS,
            maximum=MAX_SNAPSHOT_EDGES,
        )
        snapshot_findings = _bounded_rows(
            connection,
            "snapshot_findings",
            SNAPSHOT_FINDING_COLUMNS,
            maximum=MAX_SNAPSHOT_FINDINGS,
        )
        snapshot_bytes = sum(
            _persistent_size(rows)
            for rows in (snapshots, snapshot_nodes, snapshot_edges, snapshot_findings)
        )
        if snapshot_bytes > MAX_SNAPSHOT_PERSISTENT_BYTES:
            raise SafetyError("project brain snapshot history exceeds the rebuild limit")
        imports = _bounded_rows(connection, "imports", IMPORT_COLUMNS, maximum=MAX_IMPORTS)
        import_fragments = _bounded_rows(
            connection,
            "import_fragments",
            IMPORT_FRAGMENT_COLUMNS,
            maximum=MAX_IMPORT_FRAGMENTS,
        )
        if (
            _persistent_size(imports) + _persistent_size(import_fragments)
            > MAX_IMPORT_PERSISTENT_BYTES
        ):
            raise SafetyError("project brain import history exceeds the rebuild limit")
    return PersistentState(
        memories=memories,
        runs=runs,
        finding_state=finding_state,
        snapshots=snapshots,
        snapshot_nodes=snapshot_nodes,
        snapshot_edges=snapshot_edges,
        snapshot_findings=snapshot_findings,
        imports=imports,
        import_fragments=import_fragments,
    )


@contextmanager
def open_brain(path: Path, *, writable: bool = False) -> Iterator[sqlite3.Connection]:
    brain = _existing_brain(path)
    expected = brain.stat()
    if not writable:
        _reject_sidecars(brain)
    lock = _directory_lock(brain.parent) if writable else nullcontext()
    with lock:
        try:
            if writable:
                connection = sqlite3.connect(brain)
            else:
                uri = f"file:{quote(str(brain))}?mode=ro&immutable=1"
                connection = sqlite3.connect(uri, uri=True)
        except sqlite3.DatabaseError as exc:
            raise SafetyError("project brain database could not be opened safely") from exc
        connection.row_factory = sqlite3.Row
        try:
            _verify_connection_identity(connection, brain, expected)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            if writable:
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("PRAGMA secure_delete=ON")
            else:
                connection.execute("PRAGMA query_only=ON")
            _set_progress_budget(connection, SQLITE_STEPS)
            _validate_connection(connection)
            yield connection
        except sqlite3.DatabaseError as exc:
            raise SafetyError(
                "project brain database is malformed or exceeded a query limit"
            ) from exc
        finally:
            connection.close()


def _brain_schema_version(path: Path) -> int:
    brain = _existing_brain(path)
    _reject_sidecars(brain)
    expected = brain.stat()
    uri = f"file:{quote(str(brain))}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.DatabaseError as exc:
        raise SafetyError("project brain database could not be opened safely") from exc
    try:
        _verify_connection_identity(connection, brain, expected)
        if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise SafetyError("file is not a Rehearse project brain")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if not isinstance(version, int):
            raise SafetyError("project brain schema version is invalid")
        return version
    except sqlite3.DatabaseError as exc:
        raise SafetyError("project brain database could not be inspected safely") from exc
    finally:
        connection.close()


@contextmanager
def _open_legacy_v1_brain(path: Path) -> Iterator[sqlite3.Connection]:
    brain = _existing_brain(path)
    _reject_sidecars(brain)
    expected = brain.stat()
    uri = f"file:{quote(str(brain))}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.DatabaseError as exc:
        raise SafetyError("legacy project brain could not be opened safely") from exc
    connection.row_factory = sqlite3.Row
    try:
        _verify_connection_identity(connection, brain, expected)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA query_only=ON")
        _set_progress_budget(connection, SQLITE_STEPS)
        _validate_connection(
            connection,
            schema_version=1,
            schema_sql=SCHEMA_V1_SQL,
            table_limits=V1_TABLE_LIMITS,
        )
        yield connection
    except sqlite3.DatabaseError as exc:
        raise SafetyError("legacy project brain is malformed or exceeded a query limit") from exc
    finally:
        connection.close()


def remember(
    path: Path,
    *,
    kind: str,
    title: str,
    body: str,
    related_path: str | None,
    supersedes: str | None = None,
) -> str:
    allowed = {"decision", "attempt", "failure", "fix", "note", "risk-acceptance"}
    if kind not in allowed:
        raise SafetyError(f"memory kind must be one of: {', '.join(sorted(allowed))}")
    if not title.strip() or len(title) > 200:
        raise SafetyError("memory title must contain 1 to 200 characters")
    safe_title = _safe_text(title)
    safe_body = _safe_text(body)
    encoded = safe_body.encode("utf-8")
    if not body.strip() or len(encoded) > MAX_MEMORY_BYTES:
        raise SafetyError("memory body must contain 1 to 16384 UTF-8 bytes")
    if _looks_sensitive(title) or _looks_sensitive(body):
        raise SafetyError("memory looks like it contains a credential or private key")
    if supersedes is not None and not re.fullmatch(r"[0-9a-f]{24}", supersedes):
        raise SafetyError("superseded memory identifier is invalid")
    created_at = _now()
    digest = hashlib.sha256(encoded).hexdigest()
    memory_id = hashlib.sha256(
        f"{kind}\x1f{safe_title}\x1f{digest}\x1f{created_at}\x1f{secrets.token_hex(8)}".encode()
    ).hexdigest()[:24]
    with open_brain(path, writable=True) as connection:
        if connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0] >= MAX_MEMORIES:
            raise SafetyError("project brain memory limit has been reached")
        if supersedes is not None:
            previous = connection.execute(
                "SELECT status FROM memories WHERE id=?", (supersedes,)
            ).fetchone()
            if previous is None or previous["status"] != "active":
                raise SafetyError("superseded memory does not exist or is not active")
        connection.execute(
            """INSERT INTO memories(
                id, kind, title, body, path, status, supersedes, created_at, content_sha256
            ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
            (
                memory_id,
                kind,
                safe_title,
                safe_body,
                related_path,
                supersedes,
                created_at,
                digest,
            ),
        )
        if supersedes is not None:
            connection.execute("UPDATE memories SET status='superseded' WHERE id=?", (supersedes,))
        connection.commit()
    return memory_id


def forget_memory(path: Path, memory_id: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{24}", memory_id):
        raise SafetyError("memory identifier is invalid")
    replacement = "[deleted]"
    digest = hashlib.sha256(replacement.encode()).hexdigest()
    with open_brain(path, writable=True) as connection:
        existing = connection.execute(
            "SELECT status FROM memories WHERE id=?", (memory_id,)
        ).fetchone()
        if existing is None:
            raise SafetyError("memory identifier was not found")
        connection.execute(
            """UPDATE memories SET title=?, body=?, path=NULL, status='deleted',
            content_sha256=? WHERE id=?""",
            (replacement, replacement, digest, memory_id),
        )
        connection.commit()
        _set_progress_budget(connection, SQLITE_FULL_CHECK_STEPS)
        connection.execute("VACUUM")


def record_run(path: Path, report: dict[str, Any], report_bytes: bytes) -> str:
    if report.get("schema") != "rehearse.dev/report/v1alpha1":
        raise SafetyError("unsupported Rehearse report schema")
    run_id = report.get("run_id")
    if not isinstance(run_id, str) or not __import__("re").fullmatch(r"[0-9a-f]{16}", run_id):
        raise SafetyError("report has an invalid run identifier")
    allowlisted = _safe_report_summary(report)
    summary_json = _json(allowlisted)
    if len(summary_json) > 128 * 1024:
        raise SafetyError("report summary exceeds the brain import limit")
    values = (
        run_id,
        _bounded_string(report.get("recipe"), "recipe", 100),
        _bounded_string(report.get("status"), "status", 20),
        _bounded_string(report.get("cleanup"), "cleanup", 20),
        _optional_string(report.get("started_at"), 64),
        _optional_string(report.get("finished_at"), 64),
        hashlib.sha256(report_bytes).hexdigest(),
        summary_json,
    )
    with open_brain(path, writable=True) as connection:
        existing = connection.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if (
            existing is None
            and connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] >= MAX_RUNS
        ):
            raise SafetyError("project brain rehearsal-run limit has been reached")
        connection.execute(
            """INSERT OR REPLACE INTO runs(
                run_id, recipe, status, cleanup, started_at, finished_at,
                report_sha256, summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )
        connection.commit()
    return run_id


def record_import(
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
    """Persist a bounded normalized import without retaining the source artifact."""

    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", kind):
        raise SafetyError("artifact import kind is invalid")
    safe_source_name = _safe_import_text(source_name, "source name", 200)
    if (
        safe_source_name in {".", ".."}
        or PurePosixPath(safe_source_name).name != safe_source_name
        or PureWindowsPath(safe_source_name).name != safe_source_name
    ):
        raise SafetyError("artifact import source name must not contain a path")
    safe_importer_id = _safe_import_identifier(importer_id, "importer identifier", 64)
    safe_importer_version = _safe_import_text(importer_version, "importer version", 32)
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise SafetyError("artifact import digest is invalid")
    if not isinstance(summary, dict):
        raise SafetyError("artifact import summary must be an object")
    if not isinstance(fragments, tuple) or len(fragments) > MAX_IMPORT_FRAGMENTS_PER_IMPORT:
        raise SafetyError("artifact import fragment count exceeds the safety limit")
    summary_json = _bounded_import_json(summary, maximum=128 * 1024)
    fragment_rows: list[tuple[int, str, str]] = []
    total_bytes = len(summary_json.encode("utf-8"))
    for ordinal, fragment in enumerate(fragments):
        if not isinstance(fragment, dict):
            raise SafetyError("artifact import fragment must be an object")
        payload = _bounded_import_json(fragment, maximum=128 * 1024)
        total_bytes += len(payload.encode("utf-8"))
        if total_bytes > MAX_IMPORT_PERSISTENT_BYTES:
            raise SafetyError("artifact import exceeds the persistent byte limit")
        fragment_rows.append(
            (ordinal, payload, hashlib.sha256(payload.encode("utf-8")).hexdigest())
        )
    import_id = hashlib.sha256(
        f"{kind}\x1f{source_sha256}\x1f{safe_importer_id}\x1f{safe_importer_version}".encode()
    ).hexdigest()[:24]
    with open_brain(path, writable=True) as connection:
        existing = connection.execute("SELECT 1 FROM imports WHERE id=?", (import_id,)).fetchone()
        if existing is not None:
            return import_id
        if connection.execute("SELECT COUNT(*) FROM imports").fetchone()[0] >= MAX_IMPORTS:
            raise SafetyError("project brain artifact-import limit has been reached")
        current_fragments = connection.execute("SELECT COUNT(*) FROM import_fragments").fetchone()[
            0
        ]
        if current_fragments + len(fragment_rows) > MAX_IMPORT_FRAGMENTS:
            raise SafetyError("project brain import-fragment limit has been reached")
        current_bytes = connection.execute(
            """SELECT
                COALESCE((SELECT SUM(length(summary_json)) FROM imports), 0) +
                COALESCE((SELECT SUM(length(payload_json)) FROM import_fragments), 0)"""
        ).fetchone()[0]
        if current_bytes + total_bytes > MAX_IMPORT_PERSISTENT_BYTES:
            raise SafetyError("project brain import history exceeds the persistent byte limit")
        connection.execute(
            """INSERT INTO imports(
                id, kind, source_name, sha256, importer_id, importer_version,
                imported_at, summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                import_id,
                kind,
                safe_source_name,
                source_sha256,
                safe_importer_id,
                safe_importer_version,
                _now(),
                summary_json,
            ),
        )
        connection.executemany(
            """INSERT INTO import_fragments(
                import_id, ordinal, payload_json, payload_sha256
            ) VALUES (?, ?, ?, ?)""",
            [
                (import_id, ordinal, payload, payload_sha256)
                for ordinal, payload, payload_sha256 in fragment_rows
            ],
        )
        connection.commit()
    return import_id


def list_imports(path: Path) -> list[dict[str, Any]]:
    with open_brain(path) as connection:
        rows = connection.execute(
            """SELECT id, kind, source_name, sha256, importer_id, importer_version,
                imported_at, summary_json,
                (SELECT COUNT(*) FROM import_fragments f WHERE f.import_id=imports.id)
                    AS fragment_count
            FROM imports ORDER BY imported_at DESC, id DESC LIMIT ?""",
            (MAX_IMPORTS,),
        )
        return [
            {
                "id": row["id"],
                "kind": row["kind"],
                "source_name": row["source_name"],
                "sha256": row["sha256"],
                "importer_id": row["importer_id"],
                "importer_version": row["importer_version"],
                "imported_at": row["imported_at"],
                "summary": load_import_object(row["summary_json"], what="import summary"),
                "fragment_count": row["fragment_count"],
            }
            for row in rows
        ]


def get_import(path: Path, import_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{24}", import_id):
        raise SafetyError("artifact import identifier is invalid")
    with open_brain(path) as connection:
        row = connection.execute(
            """SELECT id, kind, source_name, sha256, importer_id, importer_version,
                imported_at, summary_json FROM imports WHERE id=?""",
            (import_id,),
        ).fetchone()
        if row is None:
            raise SafetyError("artifact import identifier was not found")
        fragments = [
            load_import_object(
                item["payload_json"],
                what="import fragment",
                expected_sha256=item["payload_sha256"],
            )
            for item in connection.execute(
                """SELECT payload_json, payload_sha256 FROM import_fragments
                WHERE import_id=? ORDER BY ordinal LIMIT ?""",
                (import_id, MAX_IMPORT_FRAGMENTS_PER_IMPORT),
            )
        ]
        return {
            "id": row["id"],
            "kind": row["kind"],
            "source_name": row["source_name"],
            "sha256": row["sha256"],
            "importer_id": row["importer_id"],
            "importer_version": row["importer_version"],
            "imported_at": row["imported_at"],
            "summary": load_import_object(row["summary_json"], what="import summary"),
            "fragments": fragments,
        }


def remove_import(path: Path, import_id: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{24}", import_id):
        raise SafetyError("artifact import identifier is invalid")
    with open_brain(path, writable=True) as connection:
        cursor = connection.execute("DELETE FROM imports WHERE id=?", (import_id,))
        if cursor.rowcount != 1:
            raise SafetyError("artifact import identifier was not found")
        connection.commit()


def doctor(path: Path) -> dict[str, Any]:
    with open_brain(path) as connection:
        _set_progress_budget(connection, SQLITE_FULL_CHECK_STEPS)
        _validate_connection(connection, full=True)
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in sorted(EXPECTED_TABLES)
        }
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    return {"valid": True, "schema_version": SCHEMA_VERSION, "counts": counts, "metadata": metadata}


def _restore_persistent(connection: sqlite3.Connection, state: PersistentState) -> None:
    for table, columns, rows in (
        ("snapshots", SNAPSHOT_COLUMNS, state.snapshots),
        ("snapshot_nodes", SNAPSHOT_NODE_COLUMNS, state.snapshot_nodes),
        ("snapshot_edges", SNAPSHOT_EDGE_COLUMNS, state.snapshot_edges),
        ("snapshot_findings", SNAPSHOT_FINDING_COLUMNS, state.snapshot_findings),
        ("imports", IMPORT_COLUMNS, state.imports),
        ("import_fragments", IMPORT_FRAGMENT_COLUMNS, state.import_fragments),
        ("memories", MEMORY_COLUMNS, state.memories),
        ("runs", RUN_COLUMNS, state.runs),
    ):
        if not rows:
            continue
        placeholders = ",".join("?" for _ in columns)
        connection.executemany(
            f"INSERT INTO {table}({','.join(columns)}) VALUES ({placeholders})",
            [tuple(item[column] for column in columns) for item in rows],
        )


def _append_snapshot(
    connection: sqlite3.Connection,
    graph: Graph,
    *,
    semantic_digest: str,
    created_at: str,
    label: str | None,
) -> str:
    latest = connection.execute(
        "SELECT id, sequence, semantic_digest FROM snapshots ORDER BY sequence DESC LIMIT 1"
    ).fetchone()
    if latest is not None and latest["semantic_digest"] == semantic_digest:
        return str(latest["id"])
    safe_label = _snapshot_label(label)
    sequence = 1 if latest is None else int(latest["sequence"]) + 1
    previous_id = "" if latest is None else str(latest["id"])
    snapshot_id = hashlib.sha256(
        f"{sequence}\x1f{semantic_digest}\x1f{previous_id}".encode()
    ).hexdigest()[:24]
    connection.execute(
        """INSERT INTO snapshots(
            id, sequence, created_at, label, semantic_digest, git_commit, inventory_mode,
            node_count, edge_count, finding_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            snapshot_id,
            sequence,
            created_at,
            safe_label,
            semantic_digest,
            graph.metadata.get("git_commit", "none"),
            graph.metadata.get("inventory_mode", "unknown"),
            len(graph.nodes),
            len(graph.edges),
            len(graph.findings),
        ),
    )
    connection.executemany(
        """INSERT INTO snapshot_nodes(
            snapshot_id, node_key, kind, name, path, line, language, sha256,
            properties_json, content_digest
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                snapshot_id,
                node.key,
                node.kind,
                node.name,
                node.path,
                node.line,
                node.language,
                node.sha256,
                _json(node.properties),
                _digest_json(
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
                ),
            )
            for node in sorted(graph.nodes.values(), key=lambda item: item.key)
        ],
    )
    finding_statuses = dict(
        connection.execute("SELECT fingerprint, status FROM findings ORDER BY fingerprint")
    )
    connection.executemany(
        """INSERT INTO snapshot_edges(
            snapshot_id, source_key, target_key, kind, provenance,
            evidence_path, evidence_line, details_json, content_digest
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                snapshot_id,
                edge.source,
                edge.target,
                edge.kind,
                edge.provenance,
                edge.evidence_path or "",
                edge.evidence_line or 0,
                _json(edge.details),
                _digest_json(
                    {
                        "source": edge.source,
                        "target": edge.target,
                        "kind": edge.kind,
                        "provenance": edge.provenance,
                        "evidence_path": edge.evidence_path,
                        "evidence_line": edge.evidence_line,
                        "details": edge.details,
                    }
                ),
            )
            for edge in sorted(graph.edges.values(), key=lambda item: item.identity)
        ],
    )
    connection.executemany(
        """INSERT INTO snapshot_findings(
            snapshot_id, fingerprint, lens, rule, severity, title, description,
            remediation, confidence, node_key, evidence_path, evidence_line, status,
            content_digest
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                snapshot_id,
                finding.fingerprint,
                finding.lens,
                finding.rule,
                finding.severity,
                finding.title,
                finding.description,
                finding.remediation,
                finding.confidence,
                finding.node_key,
                finding.evidence_path,
                finding.evidence_line,
                finding_statuses.get(finding.fingerprint, "open"),
                _digest_json(
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
                        "status": finding_statuses.get(finding.fingerprint, "open"),
                    }
                ),
            )
            for finding in sorted(graph.findings.values(), key=lambda item: item.fingerprint)
        ],
    )
    return snapshot_id


def _prune_snapshots(connection: sqlite3.Connection) -> None:
    while True:
        counts = {
            "snapshots": connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0],
            "nodes": connection.execute("SELECT COUNT(*) FROM snapshot_nodes").fetchone()[0],
            "edges": connection.execute("SELECT COUNT(*) FROM snapshot_edges").fetchone()[0],
            "findings": connection.execute("SELECT COUNT(*) FROM snapshot_findings").fetchone()[0],
        }
        if (
            counts["snapshots"] <= MAX_SNAPSHOTS
            and counts["nodes"] <= MAX_SNAPSHOT_NODES
            and counts["edges"] <= MAX_SNAPSHOT_EDGES
            and counts["findings"] <= MAX_SNAPSHOT_FINDINGS
        ):
            return
        oldest = connection.execute("SELECT id FROM snapshots ORDER BY sequence LIMIT 1").fetchone()
        if oldest is None or counts["snapshots"] <= 1:
            raise SafetyError("one project snapshot exceeds the history safety limits")
        connection.execute("DELETE FROM snapshots WHERE id=?", (oldest["id"],))


def _snapshot_label(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise SafetyError("snapshot label must contain 1 to 200 characters")
    if _looks_sensitive(value):
        raise SafetyError("snapshot label looks like it contains a credential")
    return _safe_text(value.strip())


def _digest_json(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _safe_report_summary(report: dict[str, Any]) -> dict[str, Any]:
    services = report.get("candidate_services", [])
    if not isinstance(services, list) or len(services) > 100:
        raise SafetyError("report candidate service list is invalid")
    safe_services = [_bounded_string(value, "candidate service", 100) for value in services]
    stages = report.get("stages", [])
    if not isinstance(stages, list) or len(stages) > 20:
        raise SafetyError("report stage list is invalid")
    safe_stages: list[dict[str, Any]] = []
    for raw_stage in stages:
        if not isinstance(raw_stage, dict):
            raise SafetyError("report stage is invalid")
        raw_steps = raw_stage.get("steps", [])
        if not isinstance(raw_steps, list) or len(raw_steps) > 200:
            raise SafetyError("report step list is invalid")
        safe_steps = []
        for raw_step in raw_steps:
            if not isinstance(raw_step, dict):
                raise SafetyError("report step is invalid")
            safe_steps.append(
                {
                    "name": _bounded_string(raw_step.get("name"), "step name", 200),
                    "service": _bounded_string(raw_step.get("service"), "step service", 100),
                    "status": _bounded_string(raw_step.get("status"), "step status", 20),
                    "attempts": _bounded_integer(raw_step.get("attempts"), 0, 1000),
                    "exit_code": _optional_integer(raw_step.get("exit_code"), -255, 255),
                }
            )
        safe_stages.append(
            {
                "name": _bounded_string(raw_stage.get("name"), "stage name", 100),
                "status": _bounded_string(raw_stage.get("status"), "stage status", 20),
                "duration_ms": _bounded_integer(
                    raw_stage.get("duration_ms"), 0, 7 * 24 * 60 * 60 * 1000
                ),
                "steps": safe_steps,
            }
        )
    failed_phase = report.get("failed_phase")
    return {
        "candidate_services": safe_services,
        "stages": safe_stages,
        "failed_phase": (
            None if failed_phase is None else _bounded_string(failed_phase, "failed phase", 100)
        ),
    }


def _validate_connection(
    connection: sqlite3.Connection,
    *,
    full: bool = False,
    schema_version: int = SCHEMA_VERSION,
    schema_sql: str = SCHEMA_SQL,
    table_limits: dict[str, int] = TABLE_LIMITS,
) -> None:
    application_id = connection.execute("PRAGMA application_id").fetchone()[0]
    user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if application_id != APPLICATION_ID:
        raise SafetyError("file is not a Rehearse project brain")
    if user_version != schema_version:
        raise SafetyError("project brain schema is unsupported by this Rehearse version")
    if _schema_objects(connection) != _expected_schema_objects(schema_sql):
        raise SafetyError("project brain contains an unexpected database schema")
    for table, maximum in table_limits.items():
        count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if not isinstance(count, int) or count > maximum:
            raise SafetyError(f"project brain table exceeds the {table} row limit")
    if full:
        result = connection.execute("PRAGMA integrity_check(1)").fetchone()[0]
        if result != "ok":
            raise SafetyError("project brain failed its SQLite integrity check")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SafetyError("project brain failed its relationship integrity check")
        if schema_version == SCHEMA_VERSION:
            _validate_snapshot_history(connection)
            _validate_import_history(connection)


@lru_cache(maxsize=2)
def _expected_schema_objects(
    schema_sql: str,
) -> tuple[tuple[str, str, str, str | None], ...]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(schema_sql)
        return _schema_objects(connection)
    finally:
        connection.close()


def _schema_objects(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, str, str, str | None], ...]:
    rows = connection.execute(
        """SELECT type, name, tbl_name, sql FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"""
    )
    return tuple(tuple(row) for row in rows)


def _bounded_rows(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    *,
    maximum: int,
) -> tuple[dict[str, Any], ...]:
    rows = connection.execute(
        f"SELECT {','.join(columns)} FROM {table} ORDER BY {columns[0]} LIMIT ?",
        (maximum + 1,),
    )
    result = tuple(dict(row) for row in rows)
    if len(result) > maximum:
        raise SafetyError(f"project brain table exceeds the {table} rebuild limit")
    for item in result:
        if any(value is not None and not isinstance(value, (str, int)) for value in item.values()):
            raise SafetyError(f"project brain contains an invalid {table} row")
    return result


def _persistent_size(rows: tuple[dict[str, Any], ...]) -> int:
    try:
        return sum(
            len(json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
            for row in rows
        )
    except (TypeError, UnicodeError) as exc:
        raise SafetyError("project brain persistent history is malformed") from exc


def _set_progress_budget(connection: sqlite3.Connection, steps: int) -> None:
    remaining = steps

    def stop() -> int:
        nonlocal remaining
        remaining -= 1_000
        return 1 if remaining <= 0 else 0

    connection.set_progress_handler(stop, 1_000)


def _verify_connection_identity(
    connection: sqlite3.Connection,
    brain: Path,
    expected: os.stat_result,
) -> None:
    row = connection.execute("PRAGMA database_list").fetchone()
    if row is None or row[1] != "main" or not row[2]:
        raise SafetyError("project brain database identity could not be verified")
    try:
        current_link = brain.lstat()
        opened = Path(row[2]).stat()
    except OSError as exc:
        raise SafetyError("project brain changed while it was being opened") from exc
    identities = {
        (expected.st_dev, expected.st_ino),
        (current_link.st_dev, current_link.st_ino),
        (opened.st_dev, opened.st_ino),
    }
    if len(identities) != 1 or stat.S_ISLNK(current_link.st_mode):
        raise SafetyError("project brain changed while it was being opened")


def _reject_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        if os.path.lexists(f"{path}{suffix}"):
            raise SafetyError("project brain has a SQLite sidecar; recover or rebuild it first")


def _safe_destination(path: Path) -> Path:
    destination = path.expanduser()
    if not destination.is_absolute():
        destination = Path.cwd() / destination
    try:
        parent = destination.parent.resolve(strict=True)
    except OSError as exc:
        raise EnvironmentError("brain destination parent does not exist") from exc
    destination = parent / destination.name
    if destination.exists() or destination.is_symlink():
        info = destination.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SafetyError("brain destination must be a regular, singly linked file")
        if info.st_size > MAX_BRAIN_BYTES:
            raise SafetyError("existing brain exceeds the supported size limit")
    return destination


def _existing_brain(path: Path) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        info = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise EnvironmentError("project brain does not exist or cannot be resolved") from exc
    if stat.S_ISLNK(info.st_mode) or not resolved.is_file() or info.st_nlink != 1:
        raise SafetyError("project brain must be a regular, singly linked file")
    if info.st_size > MAX_BRAIN_BYTES:
        raise SafetyError("project brain exceeds the supported size limit")
    return resolved


def _looks_sensitive(value: str) -> bool:
    patterns = (
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b",
        r"\bglpat-[A-Za-z0-9_-]{20,}\b",
        r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b",
        r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}",
        r"(?i)\b(?:password|passwd|token|secret|api[_-]?key|authorization)"
        r"\s*[:=]\s*\S+",
        r"(?i)(?:https?|postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://"
        r"[^/@\s:]+:[^/@\s]+@",
    )
    return any(re.search(pattern, value) for pattern in patterns)


def _safe_text(value: str) -> str:
    return "".join(
        character
        if character in "\n\t" or not unicodedata.category(character).startswith("C")
        else "?"
        for character in value
    )


def _bounded_string(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise SafetyError(f"report {name} is invalid")
    if _looks_sensitive(value):
        raise SafetyError(f"report {name} looks sensitive")
    return _safe_text(value)


def _optional_string(value: Any, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise SafetyError("report timestamp is invalid")
    return _safe_text(value)


def _bounded_integer(value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise SafetyError("report integer field is invalid")
    return value


def _optional_integer(value: Any, minimum: int, maximum: int) -> int | None:
    if value is None:
        return None
    return _bounded_integer(value, minimum, maximum)


def _safe_import_identifier(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9.-]*", value):
        raise SafetyError(f"artifact import {name} is invalid")
    if len(value) > maximum:
        raise SafetyError(f"artifact import {name} is invalid")
    return value


def _safe_import_text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise SafetyError(f"artifact import {name} is invalid")
    safe = _safe_text(value.strip())
    if _looks_sensitive(safe):
        raise SafetyError(f"artifact import {name} looks sensitive")
    return safe


def load_import_object(
    raw: str,
    *,
    what: str,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Decode one canonical normalized import object and verify its stored digest."""

    if not isinstance(raw, str) or len(raw.encode("utf-8", errors="replace")) > 128 * 1024:
        raise SafetyError(f"project brain {what} is malformed")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError, UnicodeError, RecursionError) as exc:
        raise SafetyError(f"project brain {what} is malformed") from exc
    if not isinstance(value, dict):
        raise SafetyError(f"project brain {what} is malformed")
    canonical = _bounded_import_json(value, maximum=128 * 1024)
    if canonical != raw:
        raise SafetyError(f"project brain {what} is not canonical")
    if expected_sha256 is not None:
        actual = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if not secrets.compare_digest(actual, expected_sha256):
            raise SafetyError(f"project brain {what} failed its digest check")
    return value


def _bounded_import_json(value: Any, *, maximum: int) -> str:
    _validate_import_value(value)
    try:
        encoded = _json(value)
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise SafetyError("artifact import contains invalid JSON values") from exc
    if len(encoded.encode("utf-8")) > maximum:
        raise SafetyError("artifact import JSON exceeds the safety limit")
    if _looks_sensitive(encoded):
        raise SafetyError("artifact import looks like it contains a credential")
    return encoded


def _validate_import_value(value: Any, *, depth: int = 0) -> None:
    if depth > 20:
        raise SafetyError("artifact import JSON exceeds the nesting limit")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SafetyError("artifact import JSON number is invalid")
        return
    if isinstance(value, str):
        if (
            len(value) > 16_384
            or _looks_sensitive(value)
            or any(
                character not in "\n\t" and unicodedata.category(character).startswith("C")
                for character in value
            )
        ):
            raise SafetyError("artifact import contains unsafe text")
        return
    if isinstance(value, list):
        if len(value) > 10_000:
            raise SafetyError("artifact import JSON exceeds the item limit")
        for item in value:
            _validate_import_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 10_000:
            raise SafetyError("artifact import JSON exceeds the item limit")
        sensitive_keys = {
            "accesskey",
            "accesstoken",
            "apikey",
            "authorization",
            "clientsecret",
            "connectionstring",
            "credential",
            "credentials",
            "password",
            "privatekey",
            "refreshtoken",
            "secret",
            "secretaccesskey",
            "sessiontoken",
            "token",
        }
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or not key
                or len(key) > 200
                or any(unicodedata.category(character).startswith("C") for character in key)
            ):
                raise SafetyError("artifact import JSON key is invalid")
            normalized_key = re.sub(r"[^a-z0-9]", "", key.casefold())
            if normalized_key in sensitive_keys:
                raise SafetyError("artifact import JSON contains a sensitive field")
            _validate_import_value(item, depth=depth + 1)
        return
    raise SafetyError("artifact import contains a non-JSON value")


def _load_snapshot_object(raw: str, what: str) -> dict[str, Any]:
    if not isinstance(raw, str) or len(raw) > 16_384:
        raise SafetyError(f"project brain {what} is malformed")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError, UnicodeError, RecursionError) as exc:
        raise SafetyError(f"project brain {what} is malformed") from exc
    if not isinstance(value, dict) or _json(value) != raw:
        raise SafetyError(f"project brain {what} is malformed")
    return value


def _validate_snapshot_history(connection: sqlite3.Connection) -> None:
    for row in connection.execute(
        """SELECT snapshot_id, node_key, kind, name, path, line, language, sha256,
            properties_json, content_digest FROM snapshot_nodes
        ORDER BY snapshot_id, node_key"""
    ):
        properties = _load_snapshot_object(row["properties_json"], "snapshot node properties")
        expected = _digest_json(
            {
                "key": row["node_key"],
                "kind": row["kind"],
                "name": row["name"],
                "path": row["path"],
                "line": row["line"],
                "language": row["language"],
                "sha256": row["sha256"],
                "properties": properties,
            }
        )
        if not secrets.compare_digest(expected, row["content_digest"]):
            raise SafetyError("project brain snapshot node failed its digest check")
    for row in connection.execute(
        """SELECT snapshot_id, source_key, target_key, kind, provenance,
            evidence_path, evidence_line, details_json, content_digest
        FROM snapshot_edges
        ORDER BY snapshot_id, source_key, target_key, kind, provenance,
            evidence_path, evidence_line"""
    ):
        details = _load_snapshot_object(row["details_json"], "snapshot edge details")
        expected = _digest_json(
            {
                "source": row["source_key"],
                "target": row["target_key"],
                "kind": row["kind"],
                "provenance": row["provenance"],
                "evidence_path": row["evidence_path"] or None,
                "evidence_line": row["evidence_line"] or None,
                "details": details,
            }
        )
        if not secrets.compare_digest(expected, row["content_digest"]):
            raise SafetyError("project brain snapshot edge failed its digest check")
    for row in connection.execute(
        """SELECT snapshot_id, fingerprint, lens, rule, severity, title, description,
            remediation, confidence, node_key, evidence_path, evidence_line, status,
            content_digest FROM snapshot_findings
        ORDER BY snapshot_id, fingerprint"""
    ):
        expected = _digest_json(
            {
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
        )
        if not secrets.compare_digest(expected, row["content_digest"]):
            raise SafetyError("project brain snapshot finding failed its digest check")
    for row in connection.execute(
        """SELECT id, node_count, edge_count, finding_count FROM snapshots ORDER BY sequence"""
    ):
        actual = (
            connection.execute(
                "SELECT COUNT(*) FROM snapshot_nodes WHERE snapshot_id=?", (row["id"],)
            ).fetchone()[0],
            connection.execute(
                "SELECT COUNT(*) FROM snapshot_edges WHERE snapshot_id=?", (row["id"],)
            ).fetchone()[0],
            connection.execute(
                "SELECT COUNT(*) FROM snapshot_findings WHERE snapshot_id=?", (row["id"],)
            ).fetchone()[0],
        )
        expected = (row["node_count"], row["edge_count"], row["finding_count"])
        if actual != expected:
            raise SafetyError("project brain snapshot counts are inconsistent")


def _validate_import_history(connection: sqlite3.Connection) -> None:
    for row in connection.execute(
        """SELECT id, kind, sha256, importer_id, importer_version, summary_json
        FROM imports ORDER BY id"""
    ):
        load_import_object(row["summary_json"], what="import summary")
        identifier_material = (
            f"{row['kind']}\x1f{row['sha256']}\x1f{row['importer_id']}\x1f{row['importer_version']}"
        )
        expected_id = hashlib.sha256(identifier_material.encode()).hexdigest()[:24]
        if not secrets.compare_digest(expected_id, row["id"]):
            raise SafetyError("project brain artifact import identifier is inconsistent")
    for row in connection.execute(
        """SELECT payload_json, payload_sha256 FROM import_fragments
        ORDER BY import_id, ordinal"""
    ):
        load_import_object(
            row["payload_json"],
            what="import fragment",
            expected_sha256=row["payload_sha256"],
        )


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _fsync_file(path: Path) -> None:
    flags = os.O_RDWR | int(getattr(os, "O_BINARY", 0))
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _directory_lock(path: Path) -> Iterator[None]:
    try:
        import fcntl
    except ImportError:
        # Windows has no fcntl and opening a directory with os.open is not
        # portable there. Atomic replace and SQLite's own locking still apply.
        yield
        return

    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _remove_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{path}{suffix}")
        try:
            if sidecar.exists() and sidecar.is_file():
                sidecar.unlink()
        except OSError:
            pass
