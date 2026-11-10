from __future__ import annotations

import builtins
import hashlib
import sqlite3
from pathlib import Path

import pytest

from restore_rehearsal.brain import store as brain_store
from restore_rehearsal.brain.api import (
    brain_diff,
    brain_doctor,
    brain_impact,
    brain_import,
    brain_imports,
    brain_memories,
    brain_query,
    brain_record_import,
    brain_remove_import,
    brain_snapshots,
    build_brain,
)
from restore_rehearsal.brain.schema import APPLICATION_ID, SCHEMA_V1_SQL
from restore_rehearsal.errors import SafetyError


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _project(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "project"
    root.mkdir()
    _write(root / "app.py", "def value():\n    return 1\n")
    _write(root / "tests" / "test_app.py", "from app import value\n")
    return root, tmp_path / "project.rrbrain"


def test_snapshots_suppress_duplicates_and_diff_changes(tmp_path: Path) -> None:
    root, brain = _project(tmp_path)

    first = build_brain(root, brain, label="baseline")
    duplicate = build_brain(root, brain)
    assert first["snapshot_id"] == duplicate["snapshot_id"]
    assert len(brain_snapshots(brain)) == 1

    _write(root / "app.py", "def value():\n    return 2\n")
    _write(root / "new.py", "VALUE = 3\n")
    second = build_brain(root, brain, label="candidate")
    snapshots = brain_snapshots(brain)
    result = brain_diff(brain)

    assert second["snapshot_id"] != first["snapshot_id"]
    assert [item["label"] for item in snapshots] == ["candidate", "baseline"]
    assert result["from"]["id"] == first["snapshot_id"]
    assert result["to"]["id"] == second["snapshot_id"]
    assert result["nodes"]["counts"]["added"] >= 1
    assert result["nodes"]["counts"]["modified"] >= 1
    assert any(item["key"] == "file:new.py" for item in result["nodes"]["added"])
    assert any(item["key"] == "file:app.py" for item in result["nodes"]["modified"])


def test_change_impact_is_bounded_and_relationship_derived(tmp_path: Path) -> None:
    root, brain = _project(tmp_path)
    build_brain(root, brain)
    _write(root / "app.py", "def value():\n    return 9\n")
    build_brain(root, brain)

    result = brain_impact(brain, depth=2)

    assert result["confidence"] == "relationship-derived"
    assert result["changed_node_count"] >= 1
    assert any(item["key"] == "file:app.py" and item["changed"] for item in result["nodes"])
    assert "tests" in result["groups"]
    with pytest.raises(SafetyError, match="depth"):
        brain_impact(brain, depth=4)


def test_snapshot_retention_prunes_oldest_complete_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, brain = _project(tmp_path)
    monkeypatch.setattr(brain_store, "MAX_SNAPSHOTS", 2)
    for value in range(3):
        _write(root / "app.py", f"VALUE = {value}\n")
        build_brain(root, brain, label=f"version-{value}")

    snapshots = brain_snapshots(brain)

    assert len(snapshots) == 2
    assert [item["label"] for item in snapshots] == ["version-2", "version-1"]


def test_normalized_import_survives_rebuild_and_can_be_removed(tmp_path: Path) -> None:
    root, brain = _project(tmp_path)
    build_brain(root, brain)
    source_digest = hashlib.sha256(b"synthetic normalized artifact").hexdigest()

    import_id = brain_record_import(
        brain,
        kind="sarif",
        source_name="results.sarif",
        source_sha256=source_digest,
        importer_id="rehearse.sarif",
        importer_version="1.0",
        summary={"results": 1, "levels": {"warning": 1}},
        fragments=({"finding": {"rule": "demo", "severity": "medium"}},),
    )
    duplicate = brain_record_import(
        brain,
        kind="sarif",
        source_name="renamed.sarif",
        source_sha256=source_digest,
        importer_id="rehearse.sarif",
        importer_version="1.0",
        summary={"results": 1},
    )
    assert duplicate == import_id

    build_brain(root, brain)
    imports = brain_imports(brain)

    assert len(imports) == 1
    assert imports[0]["id"] == import_id
    assert imports[0]["summary"]["results"] == 1
    assert imports[0]["fragment_count"] == 1
    assert brain_import(brain, import_id)["fragments"][0]["finding"]["rule"] == "demo"
    assert brain_query(brain, "sarif")["imports"][0]["id"] == import_id
    brain_remove_import(brain, import_id)
    assert brain_imports(brain) == []


def test_v1_brain_is_atomically_migrated_during_rebuild(tmp_path: Path) -> None:
    root, brain = _project(tmp_path)
    connection = sqlite3.connect(brain)
    try:
        connection.executescript(SCHEMA_V1_SQL)
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute("PRAGMA user_version=1")
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('format', 'rehearse-project-brain')"
        )
        body = "Legacy architectural decision"
        connection.execute(
            """INSERT INTO memories(
                id, kind, title, body, path, status, supersedes, created_at, content_sha256
            ) VALUES (?, 'decision', 'Legacy decision', ?, NULL, 'active', NULL, ?, ?)""",
            (
                "a" * 24,
                body,
                "2026-01-01T00:00:00+00:00",
                hashlib.sha256(body.encode()).hexdigest(),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    brain.chmod(0o600)

    build_brain(root, brain)

    assert brain_doctor(brain)["schema_version"] == 2
    assert brain_memories(brain)[0]["title"] == "Legacy decision"
    assert len(brain_snapshots(brain)) == 1


def test_snapshot_label_rejects_credentials(tmp_path: Path) -> None:
    root, brain = _project(tmp_path)
    with pytest.raises(SafetyError, match="label looks"):
        build_brain(root, brain, label="token=synthetic-secret-value-123456")


def test_normalized_import_rejects_paths_and_sensitive_fields(tmp_path: Path) -> None:
    root, brain = _project(tmp_path)
    build_brain(root, brain)
    digest = hashlib.sha256(b"artifact").hexdigest()
    with pytest.raises(SafetyError, match="must not contain a path"):
        brain_record_import(
            brain,
            kind="ci",
            source_name="/private/results.json",
            source_sha256=digest,
            importer_id="rehearse.ci",
            importer_version="1",
            summary={"jobs": 1},
        )
    with pytest.raises(SafetyError, match="sensitive field"):
        brain_record_import(
            brain,
            kind="ci",
            source_name="results.json",
            source_sha256=digest,
            importer_id="rehearse.ci",
            importer_version="1",
            summary={"token": "synthetic"},
        )


def test_directory_lock_is_portable_when_fcntl_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_import = builtins.__import__

    def without_fcntl(name: str, *args: object, **kwargs: object) -> object:
        if name == "fcntl":
            raise ImportError("synthetic Windows platform")
        return original_import(name, *args, **kwargs)

    def unexpected_open(*args: object, **kwargs: object) -> int:
        raise AssertionError("the no-fcntl path must not open a directory descriptor")

    monkeypatch.setattr(builtins, "__import__", without_fcntl)
    monkeypatch.setattr(brain_store.os, "open", unexpected_open)

    with brain_store._directory_lock(tmp_path):
        pass
