from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from restore_rehearsal.engine import run_rehearsal
from restore_rehearsal.recipe import load_recipe

pytestmark = pytest.mark.docker


def _image_present(image: str) -> bool:
    return (
        subprocess.run(
            ["docker", "image", "inspect", image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def test_synthetic_restore_upgrade_and_cleanup() -> None:
    if os.environ.get("RUN_DOCKER_TESTS") != "1":
        pytest.skip("set RUN_DOCKER_TESTS=1 to run the local Docker drill")
    for image in ("alpine:3.20", "alpine:3.21"):
        if not _image_present(image):
            pytest.skip(f"preload {image}; integration tests never pull implicitly")

    root = Path(__file__).parents[2]
    recipe_path = root / "examples" / "minimal" / "rehearse.yaml"
    backup = root / "examples" / "minimal" / "backup.txt"
    before = (backup.read_bytes(), backup.stat().st_mode, backup.stat().st_mtime_ns)

    outcome = run_rehearsal(load_recipe(recipe_path))
    payload = json.dumps(outcome.report.to_dict())
    after = (backup.read_bytes(), backup.stat().st_mode, backup.stat().st_mtime_ns)

    assert outcome.report.status == "passed"
    assert outcome.report.cleanup == "passed"
    assert before == after
    assert str(backup) not in payload
    assert "known-good" not in payload
    run_label = f"label=dev.rehearse.run={outcome.report.run_id}"
    for command in (
        ["docker", "ps", "-aq", "--filter", run_label],
        ["docker", "volume", "ls", "-q", "--filter", run_label],
        ["docker", "network", "ls", "-q", "--filter", run_label],
    ):
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        assert result.stdout.strip() == ""
