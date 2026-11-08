"""Atomic, validated persistence for the one-file project brain."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import tempfile
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
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
    SCHEMA_VERSION,
)
from restore_rehearsal.errors import EnvironmentError, SafetyError

MAX_BRAIN_BYTES = 256 * 1024 * 1024
MAX_MEMORY_BYTES = 16 * 1024
MAX_MEMORIES = 10_000
MAX_RUNS = 10_000
MAX_PERSISTENT_BYTES = 32 * 1024 * 1024
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
TABLE_LIMITS = {
    "metadata": 100,
    "nodes": MAX_GRAPH_NODES,
    "edges": MAX_GRAPH_EDGES,
    "findings": MAX_GRAPH_FINDINGS,
    "lenses": 10,
    "memories": MAX_MEMORIES,
    "runs": MAX_RUNS,
}


@dataclass(frozen=True)
class PersistentState:
    memories: tuple[dict[str, Any], ...] = ()
    runs: tuple[dict[str, Any], ...] = ()
    finding_state: tuple[dict[str, str], ...] = ()


def write_graph(path: Path, graph: Graph, lenses: tuple[dict[str, str], ...]) -> None:
    """Build privately beside the destination, validate, then atomically replace it."""

    graph.validate()
    destination = _safe_destination(path)
    with _directory_lock(destination.parent):
        _write_graph_locked(destination, graph, lenses)


def _write_graph_locked(
    destination: Path, graph: Graph, lenses: tuple[dict[str, str], ...]
) -> None:
    persistent = read_persistent_state(destination) if destination.exists() else PersistentState()
    parent = destination.parent
    descriptor, temporary_name = tempfile.mkstemp(
        dir=parent, prefix=f".{destination.name}.", suffix=".tmp"
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.chmod(0o600)
    now = _now()
    try:
        connection = sqlite3.connect(temporary)
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
                "semantic_digest": graph.semantic_digest(),
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
            connection.commit()
            _set_progress_budget(connection, SQLITE_FULL_CHECK_STEPS)
            _validate_connection(connection, full=True)
        finally:
            connection.close()
        _fsync_file(temporary)
        os.replace(temporary, destination)
        _fsync_directory(parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        _remove_sidecars(temporary)


def read_persistent_state(path: Path) -> PersistentState:
    with open_brain(path) as connection:
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
    return PersistentState(memories=memories, runs=runs, finding_state=finding_state)


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
    if state.memories:
        placeholders = ",".join("?" for _ in MEMORY_COLUMNS)
        connection.executemany(
            f"INSERT INTO memories({','.join(MEMORY_COLUMNS)}) VALUES ({placeholders})",
            [tuple(item[column] for column in MEMORY_COLUMNS) for item in state.memories],
        )
    if state.runs:
        placeholders = ",".join("?" for _ in RUN_COLUMNS)
        connection.executemany(
            f"INSERT INTO runs({','.join(RUN_COLUMNS)}) VALUES ({placeholders})",
            [tuple(item[column] for column in RUN_COLUMNS) for item in state.runs],
        )


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


def _validate_connection(connection: sqlite3.Connection, *, full: bool = False) -> None:
    application_id = connection.execute("PRAGMA application_id").fetchone()[0]
    user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if application_id != APPLICATION_ID:
        raise SafetyError("file is not a Rehearse project brain")
    if user_version != SCHEMA_VERSION:
        raise SafetyError("project brain schema is unsupported by this Rehearse version")
    if _schema_objects(connection) != _expected_schema_objects():
        raise SafetyError("project brain contains an unexpected database schema")
    for table, maximum in TABLE_LIMITS.items():
        count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if not isinstance(count, int) or count > maximum:
            raise SafetyError(f"project brain table exceeds the {table} row limit")
    if full:
        result = connection.execute("PRAGMA integrity_check(1)").fetchone()[0]
        if result != "ok":
            raise SafetyError("project brain failed its SQLite integrity check")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SafetyError("project brain failed its relationship integrity check")


@lru_cache(maxsize=1)
def _expected_schema_objects() -> tuple[tuple[str, str, str, str | None], ...]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SCHEMA_SQL)
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


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
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
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        try:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except ImportError:
            pass
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except ImportError:
            pass
        os.close(descriptor)


def _remove_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{path}{suffix}")
        try:
            if sidecar.exists() and sidecar.is_file():
                sidecar.unlink()
        except OSError:
            pass
