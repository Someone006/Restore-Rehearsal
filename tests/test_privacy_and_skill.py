from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from restore_rehearsal.errors import EnvironmentError
from restore_rehearsal.redaction import Redactor
from restore_rehearsal.report import RunReport, write_report
from restore_rehearsal.skill import install_skill, skill_text


def test_redacts_known_values_paths_and_common_credentials(tmp_path: Path) -> None:
    private = tmp_path / "private" / "backup.dump"
    fake_github_token = "ghp_" + ("x" * 30)
    redactor = Redactor(
        secret_values=["SYNTHETIC_CANARY_123"],
        private_paths=[private],
    )
    text = (
        f"{private} token=SYNTHETIC_CANARY_123 "
        f"https://demo:password@example.invalid {fake_github_token}"
    )

    result = redactor.redact(text)

    assert str(private) not in result
    assert "SYNTHETIC_CANARY_123" not in result
    assert "demo:password" not in result
    assert fake_github_token not in result


def test_report_is_private_allowlisted_and_never_overwritten(tmp_path: Path) -> None:
    report = RunReport(
        run_id="0123456789abcdef",
        recipe="synthetic",
        started_at="2026-01-01T00:00:00+00:00",
        status="passed",
        finished_at="2026-01-01T00:00:01+00:00",
    )
    path = tmp_path / "report.json"

    write_report(path, report)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema"] == "rehearse.dev/report/v1alpha1"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "environment" not in json.dumps(payload)
    with pytest.raises(EnvironmentError, match="refusing to overwrite"):
        write_report(path, report)


def test_skill_install_creates_portable_skill_without_overwrite(tmp_path: Path) -> None:
    target = tmp_path / ".agents" / "skills"

    install_skill(target)
    installed = target / "restore-rehearsal" / "SKILL.md"

    assert installed.read_text(encoding="utf-8") == skill_text()
    assert "Never publish raw Compose" in installed.read_text(encoding="utf-8")
    with pytest.raises(EnvironmentError, match="refusing to overwrite"):
        install_skill(target)


def test_repository_skill_matches_packaged_skill() -> None:
    repository_skill = Path(__file__).parents[1] / "skills" / "restore-rehearsal" / "SKILL.md"

    assert repository_skill.read_text(encoding="utf-8") == skill_text()
