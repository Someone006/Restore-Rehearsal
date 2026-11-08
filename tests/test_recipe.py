from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from restore_rehearsal.errors import RecipeError
from restore_rehearsal.recipe import load_recipe, verify_backup_digests


def test_loads_minimal_recipe_with_defaults(recipe_factory) -> None:
    recipe = recipe_factory()

    assert recipe.name == "test-app"
    assert recipe.compose.pull == "never"
    assert recipe.restore[0].command[0] == "cp"
    assert recipe.backups[0].path.is_absolute()
    assert recipe.safety.allow_egress is False


def test_rejects_unknown_key(recipe_factory, tmp_path: Path) -> None:
    recipe_factory()
    path = tmp_path / "rehearse.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "mystery: true\n", encoding="utf-8")

    with pytest.raises(RecipeError, match="unknown key"):
        load_recipe(path)


def test_rejects_non_array_command(recipe_factory, tmp_path: Path) -> None:
    recipe_factory()
    path = tmp_path / "rehearse.yaml"
    content = path.read_text(encoding="utf-8").replace(
        "command: [cp, /rehearsal-input/backup.txt, /data/restored.txt]",
        "command: cp /rehearsal-input/backup.txt /data/restored.txt",
    )
    path.write_text(content, encoding="utf-8")

    with pytest.raises(RecipeError, match="must be a list"):
        load_recipe(path)


def test_verifies_declared_backup_digest(recipe_factory, tmp_path: Path) -> None:
    recipe_factory()
    path = tmp_path / "rehearse.yaml"
    digest = hashlib.sha256((tmp_path / "backup.txt").read_bytes()).hexdigest()
    content = path.read_text(encoding="utf-8").replace(
        "    mounts:\n", f"    sha256: {digest}\n    mounts:\n"
    )
    path.write_text(content, encoding="utf-8")

    verify_backup_digests(load_recipe(path))


def test_digest_mismatch_fails_before_execution(recipe_factory, tmp_path: Path) -> None:
    recipe_factory()
    path = tmp_path / "rehearse.yaml"
    content = path.read_text(encoding="utf-8").replace(
        "    mounts:\n", f"    sha256: {'f' * 64}\n    mounts:\n"
    )
    path.write_text(content, encoding="utf-8")

    with pytest.raises(RecipeError, match="does not match"):
        verify_backup_digests(load_recipe(path))


@pytest.mark.parametrize("target", ["relative/path", "/", "/tmp/../bad"])
def test_rejects_unsafe_container_target(recipe_factory, tmp_path: Path, target: str) -> None:
    recipe_factory()
    path = tmp_path / "rehearse.yaml"
    content = path.read_text(encoding="utf-8").replace("/rehearsal-input/backup.txt", target, 1)
    path.write_text(content, encoding="utf-8")

    with pytest.raises(RecipeError, match="container path"):
        load_recipe(path)
