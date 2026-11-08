from __future__ import annotations

from pathlib import Path

import pytest

from restore_rehearsal.compose import CommandResult, ComposeClient
from restore_rehearsal.errors import CleanupError
from restore_rehearsal.recipe import ComposeSpec


class RecordingRunner:
    def __init__(self, outputs: list[CommandResult] | None = None) -> None:
        self.outputs = list(outputs or [])
        self.calls: list[list[str]] = []

    def run(self, argv, **kwargs) -> CommandResult:
        self.calls.append(list(argv))
        if self.outputs:
            return self.outputs.pop(0)
        return CommandResult(0, "", "")


def client(runner: RecordingRunner, tmp_path: Path) -> ComposeClient:
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    return ComposeClient(
        runner,
        project="rr-test-app-0123456789abcdef",
        run_id="0123456789abcdef",
        compose=ComposeSpec(files=(compose_file,), profiles=(), pull="never", startup_timeout=30),
    )


def test_cleanup_verifies_all_resource_labels_before_exact_project_down(tmp_path: Path) -> None:
    runner = RecordingRunner()
    compose_client = client(runner, tmp_path)
    config = tmp_path / "isolated.json"
    config.write_text("{}", encoding="utf-8")

    compose_client.cleanup(config)

    down = runner.calls[-1]
    assert down[:3] == ["docker", "compose", "--project-name"]
    assert "rr-test-app-0123456789abcdef" in down
    assert down[-5:] == ["down", "--volumes", "--remove-orphans", "--timeout", "10"]
    assert all("prune" not in call and "image" not in call for call in runner.calls)
    assert sum("label=dev.rehearse.run=0123456789abcdef" in call for call in runner.calls) == 3


def test_cleanup_refuses_resource_without_run_label(tmp_path: Path) -> None:
    runner = RecordingRunner(
        [
            CommandResult(0, "container-one\n", ""),
            CommandResult(0, "", ""),
        ]
    )
    compose_client = client(runner, tmp_path)
    config = tmp_path / "isolated.json"
    config.write_text("{}", encoding="utf-8")

    with pytest.raises(CleanupError, match="lacks the exact"):
        compose_client.cleanup(config)

    assert not any("down" in call for call in runner.calls)


def test_start_uses_generated_config_and_no_shell(tmp_path: Path) -> None:
    runner = RecordingRunner()
    compose_client = client(runner, tmp_path)
    config = tmp_path / "isolated.json"
    config.write_text("{}", encoding="utf-8")

    compose_client.start(config, services=("app",), wait=False)

    call = runner.calls[-1]
    assert str(config) in call
    assert call[-1] == "app"
    assert "up" in call
    assert "--pull" in call
