"""Built-in, offline-only recovery-adapter proposals.

Detection examines bounded path metadata only.  It never opens backup contents,
contacts a provider, reads environment variables, or executes project code.
"""

from __future__ import annotations

import os
import re
import stat
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from restore_rehearsal.errors import EnvironmentError, SafetyError

MAX_DETECTION_ENTRIES = 5_000
MAX_DETECTION_DEPTH = 3
MAX_EVIDENCE = 10
VCS_DIRECTORIES = {".git", ".hg", ".svn"}


@dataclass(frozen=True)
class RecoveryAdapter:
    id: str
    name: str
    description: str
    supported_formats: tuple[str, ...]
    restore_tool: str
    validation_checks: tuple[str, ...]
    required_configuration: tuple[str, ...]
    compatible_managed_exports: tuple[str, ...] = ()
    restorable_offline: bool = True
    network_calls: bool = False
    generated_secrets: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdapterProposal:
    adapter_id: str
    confidence: int
    detected_format: str
    backup_kind: str
    evidence: tuple[str, ...]
    restorable_offline: bool
    required_configuration: tuple[str, ...]
    restore_tool: str
    validation_checks: tuple[str, ...]
    notes: tuple[str, ...]
    network_calls: bool = False
    generated_secrets: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_ADAPTERS = (
    RecoveryAdapter(
        id="postgres",
        name="PostgreSQL logical backup",
        description="Restore a local SQL or pg_dump artifact into disposable PostgreSQL.",
        supported_formats=("postgres-sql", "postgres-custom", "postgres-directory"),
        restore_tool="psql or pg_restore inside the disposable database service",
        validation_checks=(
            "database accepts a local connection",
            "expected schemas and relations exist",
            "application-defined integrity query passes",
        ),
        required_configuration=(
            "disposable database service name",
            "database name",
            "backup format",
        ),
        compatible_managed_exports=(
            "Amazon RDS PostgreSQL logical export",
            "Google Cloud SQL PostgreSQL logical export",
            "Azure Database for PostgreSQL logical export",
        ),
    ),
    RecoveryAdapter(
        id="mysql",
        name="MySQL or MariaDB logical backup",
        description="Restore a local SQL artifact into disposable MySQL or MariaDB.",
        supported_formats=("mysql-sql",),
        restore_tool="mysql client inside the disposable database service",
        validation_checks=(
            "database accepts a local connection",
            "expected tables exist",
            "application-defined integrity query passes",
        ),
        required_configuration=(
            "disposable database service name",
            "database name",
            "SQL character set when non-default",
        ),
        compatible_managed_exports=(
            "Amazon RDS MySQL or MariaDB logical export",
            "Google Cloud SQL MySQL logical export",
            "Azure Database for MySQL logical export",
        ),
    ),
    RecoveryAdapter(
        id="sqlite",
        name="SQLite database file",
        description="Copy a local SQLite file into isolated storage and run integrity checks.",
        supported_formats=("sqlite-database",),
        restore_tool="sqlite3 inside a disposable utility service",
        validation_checks=(
            "PRAGMA quick_check returns ok",
            "expected tables exist",
            "application-defined query passes",
        ),
        required_configuration=("backup database filename",),
    ),
    RecoveryAdapter(
        id="mongodb",
        name="MongoDB logical backup",
        description="Restore a local mongodump directory or archive into disposable MongoDB.",
        supported_formats=("mongodb-bson-directory", "mongodb-archive"),
        restore_tool="mongorestore inside the disposable database service",
        validation_checks=(
            "database accepts a local connection",
            "expected collections exist",
            "application-defined document query passes",
        ),
        required_configuration=(
            "disposable database service name",
            "database name when the dump does not declare it",
        ),
        compatible_managed_exports=("MongoDB Atlas logical export",),
    ),
    RecoveryAdapter(
        id="object-storage",
        name="Object-storage export",
        description="Mirror a local object export into disposable S3-compatible storage.",
        supported_formats=(
            "object-directory",
            "s3-export",
            "gcs-export",
            "azure-blob-export",
        ),
        restore_tool="filesystem copy or MinIO client inside disposable services",
        validation_checks=(
            "expected object count is present",
            "sample object hashes match local evidence",
            "application-defined object lookup passes",
        ),
        required_configuration=(
            "local export directory",
            "disposable bucket name",
        ),
        compatible_managed_exports=(
            "Amazon S3 local export",
            "Google Cloud Storage local export",
            "Azure Blob Storage local export",
        ),
    ),
    RecoveryAdapter(
        id="managed-export",
        name="Managed-service export handoff",
        description=(
            "Classify a local managed-service export and hand it to a compatible offline adapter."
        ),
        supported_formats=(
            "provider-logical-export",
            "provider-native-snapshot",
            "provider-parquet-export",
        ),
        restore_tool="provider export conversion followed by a matching offline adapter",
        validation_checks=(
            "export manifest identifies its engine and format",
            "artifact is locally available before rehearsal",
            "converted logical backup passes an engine-specific rehearsal",
        ),
        required_configuration=(
            "provider name",
            "database engine or object-store type",
            "local export format",
        ),
        restorable_offline=False,
    ),
)

_BY_ID = {adapter.id: adapter for adapter in _ADAPTERS}


def adapter_catalog() -> tuple[dict[str, Any], ...]:
    """Return deterministic JSON-safe metadata for every built-in adapter."""

    return tuple(adapter.to_dict() for adapter in _ADAPTERS)


def get_adapter(adapter_id: str) -> RecoveryAdapter:
    """Return one built-in adapter or reject an unknown identifier."""

    if not isinstance(adapter_id, str) or adapter_id not in _BY_ID:
        raise SafetyError("unknown recovery adapter")
    return _BY_ID[adapter_id]


def propose_adapter(
    adapter_id: str,
    *,
    backup: Path | None = None,
    detected_format: str = "user-selected",
) -> AdapterProposal:
    """Create a safe proposal for an explicitly selected built-in adapter."""

    adapter = get_adapter(adapter_id)
    if not isinstance(detected_format, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9.-]{0,63}", detected_format
    ):
        raise SafetyError("detected backup format is invalid")
    kind = "unspecified"
    evidence: tuple[str, ...] = ()
    if backup is not None:
        source, kind = _safe_source(backup)
        evidence = tuple(_evidence_names(source, kind)[:MAX_EVIDENCE])
    return _proposal(
        adapter,
        confidence=100,
        detected_format=detected_format,
        backup_kind=kind,
        evidence=evidence,
    )


def detect_adapters(root: Path, backup: Path | None = None) -> tuple[AdapterProposal, ...]:
    """Suggest adapters using bounded filenames only, without opening backup contents."""

    project = _safe_root(root)
    if backup is None:
        evidence = _walk_names(project)
        backup_kind = "project-artifacts"
    else:
        source, backup_kind = _safe_source(backup)
        evidence = _evidence_names(source, backup_kind)
    lowered = tuple(value.lower() for value in evidence)
    matches: dict[str, tuple[int, str]] = {}

    def match(adapter_id: str, confidence: int, detected_format: str) -> None:
        previous = matches.get(adapter_id)
        if previous is None or confidence > previous[0]:
            matches[adapter_id] = (confidence, detected_format)

    if any(_ends(value, ".sqlite", ".sqlite3", ".db") for value in lowered):
        match("sqlite", 95, "sqlite-database")
    if any(_ends(value, ".bson") for value in lowered):
        match("mongodb", 98, "mongodb-bson-directory")
    if any("mongo" in value and _ends(value, ".archive", ".gz") for value in lowered):
        match("mongodb", 92, "mongodb-archive")
    if any(_ends(value, ".dump", ".pgdump", ".backup") for value in lowered):
        match("postgres", 95, "postgres-custom")
    if any(("postgres" in value or "pg_dump" in value) and ".sql" in value for value in lowered):
        match("postgres", 90, "postgres-sql")
    if any(("mysql" in value or "mariadb" in value) and ".sql" in value for value in lowered):
        match("mysql", 90, "mysql-sql")
    if any(_ends(value, ".sql", ".sql.gz") for value in lowered):
        match("postgres", 55, "sql-ambiguous")
        match("mysql", 55, "sql-ambiguous")

    object_tokens = ("s3", "gcs", "blob", "object", "minio", "bucket")
    if any(any(token in value for token in object_tokens) for value in lowered):
        format_name = "object-directory" if backup_kind == "directory" else "object-export"
        match("object-storage", 85, format_name)
    if backup_kind == "directory" and not matches:
        match("object-storage", 35, "object-directory-ambiguous")

    provider_tokens = (
        "rds",
        "cloudsql",
        "cloud-sql",
        "azure",
        "bigquery",
        "dynamodb",
        "cosmos",
        "provider-export",
    )
    if any(any(token in value for token in provider_tokens) for value in lowered) or any(
        _ends(value, ".parquet") for value in lowered
    ):
        format_name = (
            "provider-parquet-export"
            if any(_ends(value, ".parquet") for value in lowered)
            else "provider-logical-export"
        )
        match("managed-export", 88, format_name)

    safe_evidence = tuple(sorted(dict.fromkeys(evidence))[:MAX_EVIDENCE])
    proposals = [
        _proposal(
            get_adapter(adapter_id),
            confidence=confidence,
            detected_format=detected_format,
            backup_kind=backup_kind,
            evidence=safe_evidence,
        )
        for adapter_id, (confidence, detected_format) in matches.items()
    ]
    return tuple(sorted(proposals, key=lambda item: (-item.confidence, item.adapter_id)))


def _proposal(
    adapter: RecoveryAdapter,
    *,
    confidence: int,
    detected_format: str,
    backup_kind: str,
    evidence: tuple[str, ...],
) -> AdapterProposal:
    notes = [
        "The proposal uses local artifacts only and performs no provider or network calls.",
        "Generated configuration must contain identifiers only; keep credentials in the "
        "disposable service environment, never in a recipe or report.",
    ]
    if not adapter.restorable_offline:
        notes.append(
            "Provider-native snapshots need a user-created local logical export or conversion "
            "before Rehearse can validate them offline."
        )
    return AdapterProposal(
        adapter_id=adapter.id,
        confidence=confidence,
        detected_format=detected_format,
        backup_kind=backup_kind,
        evidence=evidence,
        restorable_offline=adapter.restorable_offline,
        required_configuration=adapter.required_configuration,
        restore_tool=adapter.restore_tool,
        validation_checks=adapter.validation_checks,
        notes=tuple(notes),
        network_calls=False,
        generated_secrets=False,
    )


def _safe_root(root: Path) -> Path:
    source = root.expanduser()
    try:
        info = source.lstat()
    except OSError as exc:
        raise EnvironmentError("adapter detection root could not be inspected") from exc
    if stat.S_ISLNK(info.st_mode):
        raise SafetyError("adapter detection root must not be a symlink")
    try:
        resolved = source.resolve(strict=True)
    except OSError as exc:
        raise EnvironmentError("adapter detection root could not be resolved") from exc
    if not resolved.is_dir():
        raise EnvironmentError("adapter detection root must be a directory")
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise SafetyError("adapter detection requires a dedicated project directory")
    return resolved


def _safe_source(source: Path) -> tuple[Path, str]:
    candidate = source.expanduser()
    try:
        info = candidate.lstat()
    except OSError as exc:
        raise EnvironmentError("backup artifact could not be inspected") from exc
    if stat.S_ISLNK(info.st_mode):
        raise SafetyError("backup artifact must not be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise EnvironmentError("backup artifact could not be resolved") from exc
    if resolved.is_file():
        return resolved, "file"
    if resolved.is_dir():
        if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
            raise SafetyError("backup artifact must be a dedicated file or directory")
        return resolved, "directory"
    raise SafetyError("backup artifact must be a regular file or directory")


def _evidence_names(source: Path, kind: str) -> list[str]:
    if kind == "file":
        return [source.name] if _safe_name(source.name) else []
    return _walk_names(source)


def _walk_names(root: Path) -> list[str]:
    root_device = root.stat().st_dev
    names: list[str] = []
    seen = 0
    stack: list[tuple[Path, PurePosixPath, int]] = [(root, PurePosixPath(), 0)]
    while stack:
        directory, relative_directory, depth = stack.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: os.fsencode(entry.name))
        except OSError as exc:
            raise SafetyError("adapter detection could not read a project directory") from exc
        child_directories: list[tuple[Path, PurePosixPath, int]] = []
        for entry in entries:
            seen += 1
            if seen > MAX_DETECTION_ENTRIES:
                raise SafetyError(
                    f"adapter detection exceeds the {MAX_DETECTION_ENTRIES}-entry limit"
                )
            if not _safe_name(entry.name):
                continue
            relative = relative_directory / entry.name
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise SafetyError("adapter detection entry changed during inspection") from exc
            if stat.S_ISLNK(info.st_mode):
                continue
            if stat.S_ISDIR(info.st_mode):
                if entry.name in VCS_DIRECTORIES or info.st_dev != root_device:
                    continue
                if depth < MAX_DETECTION_DEPTH:
                    child_directories.append((Path(entry.path), relative, depth + 1))
                continue
            if stat.S_ISREG(info.st_mode) and _looks_relevant(relative.as_posix().lower()):
                names.append(relative.as_posix())
        stack.extend(reversed(child_directories))
    return sorted(names)


def _looks_relevant(value: str) -> bool:
    suffixes = (
        ".archive",
        ".backup",
        ".bson",
        ".db",
        ".dump",
        ".gz",
        ".json",
        ".manifest",
        ".parquet",
        ".pgdump",
        ".sql",
        ".sqlite",
        ".sqlite3",
        ".tar",
        ".tgz",
        ".zip",
    )
    tokens = (
        "azure",
        "blob",
        "bucket",
        "cloud-sql",
        "cloudsql",
        "cosmos",
        "dynamodb",
        "export",
        "gcs",
        "mariadb",
        "minio",
        "mongo",
        "mysql",
        "object",
        "postgres",
        "rds",
    )
    return value.endswith(suffixes) or any(token in value for token in tokens)


def _safe_name(value: str) -> bool:
    return bool(value) and not any(
        unicodedata.category(character).startswith("C") for character in value
    )


def _ends(value: str, *suffixes: str) -> bool:
    return value.endswith(suffixes)
