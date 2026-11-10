from __future__ import annotations

import stat
from pathlib import Path

import pytest

import restore_rehearsal.skill as skill_module
from restore_rehearsal import __version__
from restore_rehearsal.errors import EnvironmentError
from restore_rehearsal.skill import (
    SKILL_NAMES,
    install_all_skills,
    install_skill,
    install_skills,
    skill_catalog,
    skill_doctor,
)

EXPECTED_SKILLS = {
    "restore-rehearsal",
    "project-brain",
    "rehearse-init",
    "universal-inspector",
    "change-impact",
    "evidence-import",
    "recovery-adapters",
    "release-readiness",
    "plugin-authoring",
}


def _tree(root: Path) -> dict[Path, bytes]:
    return {item.relative_to(root): item.read_bytes() for item in root.rglob("*") if item.is_file()}


def test_all_nine_skills_are_valid_and_packaged_identically() -> None:
    repository = Path(__file__).parents[1] / "skills"
    packaged = Path(__file__).parents[1] / "src" / "restore_rehearsal" / "skill"

    assert set(SKILL_NAMES) == EXPECTED_SKILLS
    assert set(item.name for item in repository.iterdir() if item.is_dir()) == EXPECTED_SKILLS
    assert set(item.name for item in packaged.iterdir() if item.is_dir()) == EXPECTED_SKILLS
    for name in SKILL_NAMES:
        assert _tree(repository / name) == _tree(packaged / name)
        assert not any(item.is_symlink() for item in (repository / name).rglob("*"))

    report = skill_doctor()
    assert report["ok"] is True
    assert report["skill_count"] == 9
    assert report["issues"] == []
    assert all(item["ok"] for item in report["skills"])


def test_skill_catalog_is_stable_and_versioned() -> None:
    catalog = skill_catalog()

    assert tuple(item["name"] for item in catalog) == SKILL_NAMES
    assert all(item["version"] == __version__ for item in catalog)
    assert all(item["description"] and item["references"] >= 0 for item in catalog)


def test_install_all_skills_copies_exact_private_safe_bundle(tmp_path: Path) -> None:
    target = tmp_path / ".agents" / "skills"
    installed = install_all_skills(target)

    assert installed == SKILL_NAMES
    repository = Path(__file__).parents[1] / "skills"
    for name in SKILL_NAMES:
        assert _tree(target / name) == _tree(repository / name)
        mode = stat.S_IMODE((target / name / "SKILL.md").stat().st_mode)
        assert mode & 0o022 == 0
        assert mode & 0o400


def test_multi_skill_install_preflights_every_destination(tmp_path: Path) -> None:
    target = tmp_path / "skills"
    target.mkdir()
    (target / "project-brain").mkdir()

    with pytest.raises(EnvironmentError, match="refusing to overwrite"):
        install_all_skills(target)

    assert {item.name for item in target.iterdir()} == {"project-brain"}


def test_multi_skill_install_rolls_back_partial_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = skill_module._resource_files

    def broken(root: object):
        if getattr(root, "name", "") == "project-brain":
            raise OSError("synthetic resource failure")
        yield from original(root)  # type: ignore[arg-type]

    monkeypatch.setattr(skill_module, "_resource_files", broken)
    target = tmp_path / "skills"

    with pytest.raises(OSError, match="synthetic resource failure"):
        install_skills(target, names=("restore-rehearsal", "project-brain"))

    assert target.exists()
    assert not list(target.iterdir())


def test_install_rejects_empty_duplicate_and_unknown_selections(tmp_path: Path) -> None:
    with pytest.raises(EnvironmentError, match="select at least one"):
        install_skills(tmp_path, names=())
    with pytest.raises(EnvironmentError, match="duplicate"):
        install_skills(tmp_path, names=("project-brain", "project-brain"))
    with pytest.raises(EnvironmentError, match="unknown"):
        install_skill(tmp_path, name="not-a-skill")


def test_every_skill_has_the_absolute_outbound_privacy_guard() -> None:
    repository = Path(__file__).parents[1] / "skills"
    for name in SKILL_NAMES:
        text = (repository / name / "SKILL.md").read_text(encoding="utf-8")
        assert "API keys" in text
        assert "private keys" in text
        assert "backup" in text.lower()
        assert "explicit" in text.lower()
