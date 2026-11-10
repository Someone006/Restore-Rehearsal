from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from restore_rehearsal.brain import inventory as brain_inventory
from restore_rehearsal.brain.api import (
    brain_context,
    brain_doctor,
    brain_findings,
    brain_forget,
    brain_memories,
    brain_query,
    brain_record_run,
    brain_remember,
    brain_summary,
    build_brain,
    lens_catalog,
)
from restore_rehearsal.brain.schema import SCHEMA_VERSION
from restore_rehearsal.brain.visual import write_visualization
from restore_rehearsal.errors import EnvironmentError, SafetyError

IGNORED_CANARY = "SYNTHETIC_IGNORED_SECRET_CANARY"
SENSITIVE_CANARY = "SYNTHETIC_TRACKED_KEY_CANARY"
OUTSIDE_CANARY = "SYNTHETIC_OUTSIDE_SYMLINK_CANARY"
LOCAL_SECRET_CANARY = "SYNTHETIC_LOCAL_API_SECRET_CANARY"


@pytest.fixture
def brain_project(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "project"
    root.mkdir()
    _write(root / ".gitignore", ".env\nignored-secret.txt\n*.rrbrain\n")
    _write(root / ".env", f"TOKEN={IGNORED_CANARY}\n")
    _write(root / "ignored-secret.txt", IGNORED_CANARY)
    _write(root / "README.md", "# Synthetic project\n")
    _write(root / "LICENSE", "Apache License\nVersion 2.0\n")
    _write(root / "SECURITY.md", "# Security\nUse private reporting.\n")
    _write(
        root / "pyproject.toml",
        """[project]
name = "synthetic-project"
version = "0.0.1"
dependencies = ["PyYAML>=6"]
""",
    )
    _write(root / "uv.lock", "version = 1\n")
    _write(
        root / "src" / "util.py",
        "def helper() -> str:\n    return 'synthetic'\n",
    )
    _write(
        root / "src" / "app.py",
        "from util import helper\n\ndef run() -> str:\n    return helper()\n",
    )
    _write(
        root / "tests" / "test_app.py",
        "from app import run\n\ndef test_run():\n    assert run() == 'synthetic'\n",
    )
    _write(
        root / "compose.yaml",
        """services:
  app:
    image: alpine:3.21
    healthcheck:
      test: [CMD, "true"]
    volumes:
      - app-data:/data
volumes:
  app-data: {}
""",
    )
    _write(
        root / "rehearse.yaml",
        """version: 1
name: synthetic
checks:
  - name: smoke
    type: exec
    service: app
    command: ["true"]
""",
    )
    _write(
        root / ".github" / "workflows" / "ci.yml",
        """name: CI
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
      - run: pytest
""",
    )
    _write(
        root / "web" / "<img onerror=alert(1)>.html",
        "<!doctype html><html><body><img src='synthetic.png'></body></html>\n",
    )
    _write(root / "web" / "theme.css", "body { color: #fff; background: #000; }\n")
    _write(root / "secrets" / "demo.key", SENSITIVE_CANARY)
    outside = tmp_path / "outside.py"
    _write(outside, f"VALUE = {OUTSIDE_CANARY!r}\n")
    (root / "escape.py").symlink_to(outside)
    _git(root, "init", "--initial-branch=main")
    _git(root, "add", "-A")
    brain = tmp_path / "project.rrbrain"
    return root, brain


def test_build_query_context_and_deterministic_digest(
    brain_project: tuple[Path, Path],
) -> None:
    root, brain = brain_project

    first = build_brain(root, brain)
    second = build_brain(root, brain)
    query = brain_query(brain, "app.py")
    context = brain_context(brain, "file:src/app.py", depth=1)

    assert first["semantic_digest"] == second["semantic_digest"]
    assert first["nodes"] > 10
    assert any(item["path"] == "src/app.py" for item in query["nodes"])
    assert context["target"] == "file:src/app.py"
    assert any(item["path"] == "src/util.py" for item in context["nodes"])
    assert any(item["kind"] == "imports" for item in context["edges"])
    assert stat.S_IMODE(brain.stat().st_mode) == 0o600
    assert not Path(f"{brain}-wal").exists()
    assert not Path(f"{brain}-shm").exists()
    assert not Path(f"{brain}-journal").exists()


def test_plain_local_folder_builds_without_git_and_honors_ignore_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plain-local-project"
    root.mkdir()
    _write(root / ".gitignore", "ignored-root.txt\n*.tmp\n!important.tmp\n")
    _write(root / ".rehearseignore", "generated/\n")
    _write(root / "README.md", "# Plain local project\n")
    _write(root / "src" / ".gitignore", "private.py\n")
    _write(root / "src" / "app.py", "from .util import helper\n")
    _write(root / "src" / "util.py", "def helper():\n    return 1\n")
    _write(root / "src" / "private.py", LOCAL_SECRET_CANARY)
    _write(root / ".github" / "workflows" / "ci.yml", "jobs: {}\n")
    _write(root / "ignored-root.txt", LOCAL_SECRET_CANARY)
    _write(root / "cache.tmp", LOCAL_SECRET_CANARY)
    _write(root / "important.tmp", "keep this project artifact\n")
    _write(root / "generated" / "bundle.js", LOCAL_SECRET_CANARY)
    _write(root / ".env.local", f"API_KEY={LOCAL_SECRET_CANARY}\n")
    _write(root / ".aws" / "credentials", LOCAL_SECRET_CANARY)
    outside = tmp_path / "outside-local.py"
    _write(outside, f"VALUE = {OUTSIDE_CANARY!r}\n")
    (root / "outside-link.py").symlink_to(outside)
    brain = tmp_path / "plain-local.rrbrain"

    first = build_brain(root, brain)
    second = build_brain(root, brain)
    summary = brain_summary(brain)
    raw = brain.read_bytes()

    assert first["semantic_digest"] == second["semantic_digest"]
    assert summary["metadata"]["inventory_mode"] == "filesystem"
    assert summary["metadata"]["git_commit"] == "none"
    assert int(summary["metadata"]["inventory_ignored_entries"]) >= 4
    assert brain_query(brain, "src/app.py")["nodes"]
    assert brain_query(brain, "important.tmp")["nodes"]
    assert brain_query(brain, ".github/workflows/ci.yml")["nodes"]
    assert not brain_query(brain, "private.py")["nodes"]
    assert not brain_query(brain, "ignored-root.txt")["nodes"]
    assert not brain_query(brain, "generated/bundle.js")["nodes"]
    assert LOCAL_SECRET_CANARY.encode() not in raw
    assert OUTSIDE_CANARY.encode() not in raw
    findings = brain_findings(brain, lens="security-supply-chain")
    assert any("Sensitive-looking path is present" in item["title"] for item in findings)
    assert all("is tracked" not in item["title"] for item in findings)


def test_open_file_stat_matching_and_windows_binary_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_dev=1,
        st_ino=2,
        st_size=4,
        st_mtime_ns=10,
        st_ctime_ns=20,
    )
    different_handle_identity = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_dev=3,
        st_ino=4,
        st_size=4,
        st_mtime_ns=10,
        st_ctime_ns=20,
    )
    wrong_size = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_dev=1,
        st_ino=2,
        st_size=5,
        st_mtime_ns=10,
        st_ctime_ns=20,
    )

    assert not brain_inventory._stat_matches_open_file(expected, different_handle_identity)
    assert not brain_inventory._stat_matches_open_file(expected, wrong_size)
    monkeypatch.setattr(brain_inventory.os, "O_BINARY", 1 << 28, raising=False)
    assert brain_inventory._read_only_flags() & (1 << 28)


def test_brain_fsync_reopens_owned_temporary_file_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "brain.tmp"
    target.write_bytes(b"sqlite placeholder")
    opened_flags: list[int] = []
    original_open = brain_inventory.os.open

    def recording_open(path: str | bytes | Path, flags: int, mode: int = 0o777) -> int:
        opened_flags.append(flags)
        return original_open(path, flags, mode)

    from restore_rehearsal.brain import store as brain_store

    monkeypatch.setattr(brain_store.os, "open", recording_open)
    brain_store._fsync_file(target)

    assert opened_flags
    assert opened_flags[0] & os.O_RDWR


def test_git_project_subdirectory_can_be_scanned(
    brain_project: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _ = brain_project
    brain = tmp_path / "subdirectory.rrbrain"

    build_brain(root / "src", brain)
    summary = brain_summary(brain)

    assert summary["metadata"]["inventory_mode"] == "git"
    assert summary["metadata"]["git_commit"] == "unborn"
    assert brain_query(brain, "app.py")["nodes"]
    assert not brain_query(brain, "compose.yaml")["nodes"]


def test_git_project_honors_root_and_nested_rehearseignore(
    brain_project: tuple[Path, Path],
) -> None:
    root, brain = brain_project
    existing_ignore = (root / ".gitignore").read_text(encoding="utf-8")
    _write(root / ".gitignore", f"{existing_ignore}.rehearseignore\n")
    _write(root / "private-for-brain" / "credentials.txt", LOCAL_SECRET_CANARY)
    _write(root / "src" / "generated.py", LOCAL_SECRET_CANARY)
    _git(root, "add", "-A")
    _write(root / ".rehearseignore", "private-for-brain/\n")
    _write(root / "src" / ".rehearseignore", "generated.py\n")

    build_brain(root, brain)
    summary = brain_summary(brain)
    raw = brain.read_bytes()

    assert summary["metadata"]["inventory_mode"] == "git"
    assert int(summary["metadata"]["inventory_ignored_entries"]) >= 2
    assert not brain_query(brain, "private-for-brain/credentials.txt")["nodes"]
    assert not brain_query(brain, "src/generated.py")["nodes"]
    assert LOCAL_SECRET_CANARY.encode() not in raw


def test_git_project_tolerates_a_tracked_file_deleted_before_scan(
    brain_project: tuple[Path, Path],
) -> None:
    root, brain = brain_project
    deleted = root / "deleted-before-scan.py"
    _write(deleted, "VALUE = 1\n")
    _git(root, "add", "-A")
    deleted.unlink()

    build_brain(root, brain)

    assert not brain_query(brain, "deleted-before-scan.py")["nodes"]


def test_root_ignore_pattern_budget_is_enforced_before_compilation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ignore-budget-project"
    root.mkdir()
    _write(root / ".rehearseignore", "first.tmp\nsecond.tmp\n")
    monkeypatch.setattr(brain_inventory, "MAX_IGNORE_PATTERNS", 1)

    with pytest.raises(SafetyError, match="ignore rules exceed"):
        build_brain(root, tmp_path / "ignore-budget.rrbrain")


def test_plain_local_folder_does_not_require_git_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "no-git-command-project"
    root.mkdir()
    _write(root / "app.py", "VALUE = 1\n")
    brain = tmp_path / "no-git-command.rrbrain"
    monkeypatch.setenv("PATH", str(tmp_path / "missing-bin"))

    build_brain(root, brain)

    assert brain_summary(brain)["metadata"]["inventory_mode"] == "filesystem"


def test_filesystem_root_is_not_accepted_as_a_project(tmp_path: Path) -> None:
    with pytest.raises(SafetyError, match="dedicated project directory"):
        build_brain(Path("/"), tmp_path / "root.rrbrain")


def test_memory_and_run_survive_atomic_rebuild(brain_project: tuple[Path, Path]) -> None:
    root, brain = brain_project
    build_brain(root, brain)
    memory_id = brain_remember(
        brain,
        kind="failure",
        title="Direct migration attempt",
        body="The direct path failed the synthetic compatibility fixture.",
        related_path="src/app.py",
    )
    report = root.parent / "report.json"
    report.write_text(
        json.dumps(
            {
                "schema": "rehearse.dev/report/v1alpha1",
                "run_id": "0123456789abcdef",
                "recipe": "synthetic",
                "status": "passed",
                "cleanup": "passed",
                "started_at": "2026-01-01T00:00:00+00:00",
                "finished_at": "2026-01-01T00:00:01+00:00",
                "candidate_services": ["app"],
                "stages": [
                    {
                        "name": "candidate",
                        "status": "passed",
                        "duration_ms": 1_000,
                        "steps": [],
                    }
                ],
                "failed_phase": None,
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    brain_record_run(brain, report)

    build_brain(root, brain)
    summary = brain_summary(brain)
    recalled = brain_query(brain, "compatibility fixture")

    assert summary["counts"]["memories"] == 1
    assert summary["counts"]["runs"] == 1
    assert recalled["memories"][0]["id"] == memory_id


def test_memory_can_supersede_and_securely_forget(brain_project: tuple[Path, Path]) -> None:
    root, brain = brain_project
    build_brain(root, brain)
    first = brain_remember(
        brain,
        kind="decision",
        title="Initial approach",
        body="Use the initial synthetic approach.",
        related_path="src/app.py",
    )
    forgotten_canary = "SYNTHETIC_MEMORY_TO_FORGET_4182"
    second = brain_remember(
        brain,
        kind="decision",
        title="Replacement approach",
        body=forgotten_canary,
        related_path="src/app.py",
        supersedes=first,
    )

    assert brain_memories(brain, status="superseded")[0]["id"] == first
    assert brain_memories(brain, status="active")[0]["id"] == second

    brain_forget(brain, second)

    assert brain_memories(brain, status="deleted")[0]["title"] == "[deleted]"
    assert forgotten_canary.encode() not in brain.read_bytes()


def test_ignored_sensitive_and_symlink_content_never_enters_brain(
    brain_project: tuple[Path, Path],
) -> None:
    root, brain = brain_project

    build_brain(root, brain)
    raw = brain.read_bytes()
    symlink = brain_query(brain, "escape.py")
    sensitive = brain_query(brain, "demo.key")

    assert IGNORED_CANARY.encode() not in raw
    assert SENSITIVE_CANARY.encode() not in raw
    assert OUTSIDE_CANARY.encode() not in raw
    assert symlink["nodes"][0]["kind"] == "symlink"
    assert sensitive["nodes"][0]["kind"] == "sensitive-file"
    assert not brain_query(brain, ".env")["nodes"]


def test_output_symlink_is_refused_without_changing_target(
    brain_project: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _ = brain_project
    target = tmp_path / "unrelated.txt"
    target.write_text("keep-me", encoding="utf-8")
    brain_link = tmp_path / "linked.rrbrain"
    brain_link.symlink_to(target)

    with pytest.raises(SafetyError, match="regular, singly linked"):
        build_brain(root, brain_link)

    assert target.read_text(encoding="utf-8") == "keep-me"


def test_wrong_and_corrupt_sqlite_are_refused(tmp_path: Path) -> None:
    wrong = tmp_path / "wrong.rrbrain"
    connection = sqlite3.connect(wrong)
    connection.execute("CREATE TABLE unrelated(value TEXT)")
    connection.commit()
    connection.close()
    corrupt = tmp_path / "corrupt.rrbrain"
    corrupt.write_bytes(b"not-a-sqlite-database")

    with pytest.raises(SafetyError):
        brain_doctor(wrong)
    with pytest.raises(SafetyError):
        brain_doctor(corrupt)


def test_lookalike_schema_with_expected_table_names_is_refused(tmp_path: Path) -> None:
    lookalike = tmp_path / "lookalike.rrbrain"
    connection = sqlite3.connect(lookalike)
    for table in ("metadata", "nodes", "edges", "findings", "lenses", "memories", "runs"):
        connection.execute(f"CREATE TABLE {table}(wrong_column TEXT)")
    connection.execute("PRAGMA application_id=0x52524252")
    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    connection.commit()
    connection.close()

    with pytest.raises(SafetyError, match="unexpected database schema"):
        brain_doctor(lookalike)


def test_report_symlink_is_refused(brain_project: tuple[Path, Path]) -> None:
    root, brain = brain_project
    build_brain(root, brain)
    report = root.parent / "report.json"
    report.write_text("{}", encoding="utf-8")
    linked = root.parent / "linked-report.json"
    linked.symlink_to(report)

    with pytest.raises(SafetyError, match="regular, singly linked"):
        brain_record_run(brain, linked)


def test_visualization_escapes_labels_is_private_and_never_overwrites(
    brain_project: tuple[Path, Path], tmp_path: Path
) -> None:
    root, brain = brain_project
    build_brain(root, brain)
    output = tmp_path / "brain.html"

    result = write_visualization(brain, output)
    html = output.read_text(encoding="utf-8")

    assert result["nodes"] > 0
    assert "<img onerror=alert(1)>" not in html
    assert "\\u003cimg onerror=alert(1)\\u003e" in html
    assert "connect-src 'none'" in html
    assert "'unsafe-inline'" not in html
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    with pytest.raises(EnvironmentError, match="refusing to overwrite"):
        write_visualization(brain, output)


def test_exact_ten_review_lenses() -> None:
    lenses = lens_catalog()

    assert len(lenses) == 10
    assert {item["id"] for item in lenses} == {
        "architecture",
        "change-impact",
        "test-strategy",
        "reliability-recovery",
        "security-supply-chain",
        "performance-operability",
        "license-attribution",
        "privacy-data",
        "ux-flow",
        "accessibility-design",
    }


@pytest.mark.parametrize(
    "query",
    ["' OR 1=1 --", "%", "_", "x'; DROP TABLE nodes; --"],
)
def test_query_is_parameterized_and_wildcards_are_literal(
    brain_project: tuple[Path, Path], query: str
) -> None:
    root, brain = brain_project
    build_brain(root, brain)

    result = brain_query(brain, query, limit=100_000)

    assert result["limit"] == 50
    assert len(result["nodes"]) <= 50
    assert all(
        query in "\n".join(str(node.get(field) or "") for field in ("key", "name", "path", "kind"))
        for node in result["nodes"]
    )
    assert brain_doctor(brain)["schema_version"] == SCHEMA_VERSION


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
        shell=False,
    )
