from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from restore_rehearsal.brain.model import Node
from restore_rehearsal.cli import _bundle_fragments, main
from restore_rehearsal.errors import ExitCode, SafetyError
from restore_rehearsal.plugins import ImportBundle


def test_init_dry_run_create_check_and_no_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "plain-project"
    root.mkdir()
    _write(root / "pyproject.toml", '[project]\nname = "demo"\n')
    _write(root / "src" / "demo.py", "def demo():\n    return True\n")

    assert main(["init", str(root), "--dry-run", "--json"]) == ExitCode.OK
    preview = json.loads(capsys.readouterr().out)
    assert preview["dry_run"] is True
    assert preview["profile"]["inventory_mode"] == "filesystem"
    assert not (root / "rehearse-project.yaml").exists()

    assert main(["init", str(root), "--json"]) == ExitCode.OK
    created = json.loads(capsys.readouterr().out)
    assert created["config"] == "rehearse-project.yaml"
    assert (root / "rehearse-project.yaml").exists()
    assert (root / ".rehearseignore").exists()

    assert main(["init", str(root), "--check", "--json"]) == ExitCode.OK
    checked = json.loads(capsys.readouterr().out)
    assert checked["valid"] is True
    assert checked["project"]["project"]["name"] == "plain-project"

    assert main(["init", str(root)]) == ExitCode.ENVIRONMENT_ERROR
    error = capsys.readouterr().err
    assert "refusing to overwrite" in error
    assert str(root) not in error


def test_init_custom_paths_and_no_ignore(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "project"
    (root / "config").mkdir(parents=True)
    _write(root / "README.md", "demo\n")

    assert (
        main(
            [
                "init",
                str(root),
                "--output",
                "config/project.yaml",
                "--no-ignore",
                "--json",
            ]
        )
        == ExitCode.OK
    )
    result = json.loads(capsys.readouterr().out)
    assert result["ignore"] is None
    assert (root / "config" / "project.yaml").exists()
    assert not (root / ".rehearseignore").exists()

    missing = tmp_path / "missing"
    assert main(["init", str(missing), "--check"]) == ExitCode.ENVIRONMENT_ERROR
    error = capsys.readouterr().err
    assert "could not be inspected" in error
    assert str(missing) not in error


def test_plugin_list_doctor_and_explicit_activation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["plugin", "list", "--builtins-only", "--json"]) == ExitCode.OK
    catalog = json.loads(capsys.readouterr().out)
    assert {item["kind"] for item in catalog} == {"importer"}
    assert all(item["source"] == "builtin" for item in catalog)

    assert (
        main(
            [
                "plugin",
                "doctor",
                "builtin.sarif",
                "--kind",
                "importer",
                "--json",
            ]
        )
        == ExitCode.OK
    )
    diagnosis = json.loads(capsys.readouterr().out)
    assert diagnosis["compatible"] is True
    assert diagnosis["executed"] is False

    loaded = False

    class ExternalImporter:
        api_version = "1"
        id = "external-demo"
        version = "1.0"
        description = "Synthetic explicit importer"
        kind = "synthetic"

        def detect(self, source_name: str, data: bytes) -> int:
            return 0

        def parse(self, context: object, data: bytes) -> object:
            raise NotImplementedError

    class FakeEntryPoint:
        name = "external-demo"
        dist = SimpleNamespace(name="external-package", version="1.0")

        def load(self) -> object:
            nonlocal loaded
            loaded = True
            return ExternalImporter

    class FakeEntryPoints:
        def select(self, *, group: str) -> list[FakeEntryPoint]:
            return [FakeEntryPoint()] if group == "rehearse.importers" else []

    monkeypatch.setattr(
        "restore_rehearsal.plugins.registry.metadata.entry_points",
        lambda: FakeEntryPoints(),
    )
    assert (
        main(
            [
                "plugin",
                "activate",
                "external-demo",
                "--kind",
                "importer",
                "--json",
            ]
        )
        == ExitCode.OK
    )
    activated = json.loads(capsys.readouterr().out)
    assert loaded is True
    assert activated["enabled"] is True
    assert activated["explicitly_activated"] is True


def test_brain_history_import_and_importer_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source = root / "app.py"
    _write(source, "def value():\n    return 1\n")
    brain = tmp_path / "project.rrbrain"

    assert main(["brain", "build", str(root), "--output", str(brain), "--json"]) == ExitCode.OK
    first = json.loads(capsys.readouterr().out)
    assert first["snapshot_id"]

    _write(source, "def value():\n    return 2\n")
    assert (
        main(
            [
                "brain",
                "build",
                str(root),
                "--output",
                str(brain),
                "--label",
                "changed",
                "--json",
            ]
        )
        == ExitCode.OK
    )
    second = json.loads(capsys.readouterr().out)
    assert second["snapshot_id"] != first["snapshot_id"]

    assert main(["brain", "snapshots", str(brain), "--json"]) == ExitCode.OK
    snapshots = json.loads(capsys.readouterr().out)
    assert len(snapshots) == 2
    assert snapshots[0]["label"] == "changed"

    assert main(["brain", "diff", str(brain), "--json"]) == ExitCode.OK
    difference = json.loads(capsys.readouterr().out)
    assert difference["from"]["id"] == first["snapshot_id"]
    assert difference["to"]["id"] == second["snapshot_id"]

    assert main(["brain", "impact", str(brain), "--depth", "1", "--json"]) == ExitCode.OK
    impact = json.loads(capsys.readouterr().out)
    assert impact["depth"] == 1
    assert impact["changed_node_count"] >= 1

    assert main(["brain", "importers", "--json"]) == ExitCode.OK
    importers = json.loads(capsys.readouterr().out)
    assert len(importers) == 8

    artifact = tmp_path / "ci-result.json"
    _write(
        artifact,
        json.dumps(
            {
                "schema": "rehearse.dev/ci-result/v1",
                "name": "CI",
                "jobs": [{"name": "tests", "status": "passed", "duration_seconds": 2}],
            }
        ),
    )
    assert (
        main(["brain", "import", str(brain), str(artifact), "--type", "ci", "--json"])
        == ExitCode.OK
    )
    imported = json.loads(capsys.readouterr().out)
    import_id = imported["import_id"]

    assert main(["brain", "show-import", str(brain), import_id, "--json"]) == ExitCode.OK
    shown = json.loads(capsys.readouterr().out)
    assert shown["kind"] == "ci"
    assert shown["fragments"]

    assert main(["brain", "remove-import", str(brain), import_id, "--json"]) == ExitCode.OK
    assert json.loads(capsys.readouterr().out)["removed"] is True
    assert main(["brain", "imports", str(brain), "--json"]) == ExitCode.OK
    assert json.loads(capsys.readouterr().out) == []


def test_fragmenter_rejects_one_oversized_normalized_item() -> None:
    bundle = ImportBundle(
        kind="synthetic",
        importer_id="builtin.synthetic",
        importer_version="1",
        source_name="artifact.json",
        source_sha256="a" * 64,
        nodes=(
            Node(
                key="synthetic:node",
                kind="artifact",
                name="synthetic",
                properties={f"field-{index}": "x" * 2_000 for index in range(70)},
            ),
        ),
    )

    with pytest.raises(SafetyError, match="storage limit"):
        _bundle_fragments(bundle)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
