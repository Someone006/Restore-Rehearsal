"""Inspect and install bundled portable Agent Skills without overwriting files."""

from __future__ import annotations

import os
from collections.abc import Iterator
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from restore_rehearsal.errors import EnvironmentError

DEFAULT_SKILL = "restore-rehearsal"
SKILL_NAMES = (DEFAULT_SKILL, "project-brain")
SKILL_FILENAME = "SKILL.md"


def skill_text(name: str = DEFAULT_SKILL) -> str:
    resource = _skill_root(name).joinpath(SKILL_FILENAME)
    try:
        return resource.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise EnvironmentError("the bundled Agent Skill could not be read") from exc


def install_skill(target_root: Path, *, name: str = DEFAULT_SKILL) -> None:
    """Create TARGET/NAME from bundled resources, refusing every overwrite."""

    resource_root = _skill_root(name)

    target = target_root.expanduser()
    try:
        target.mkdir(parents=True, exist_ok=True)
        target = target.resolve(strict=True)
    except OSError as exc:
        raise EnvironmentError("the Agent Skill target directory could not be created") from exc
    if not target.is_dir():
        raise EnvironmentError("the Agent Skill target must be a directory")

    skill_directory = target / name
    try:
        skill_directory.mkdir(mode=0o755)
    except FileExistsError as exc:
        raise EnvironmentError(
            "an Agent Skill with this name already exists; refusing to overwrite it"
        ) from exc
    except OSError as exc:
        raise EnvironmentError("the Agent Skill directory could not be created") from exc

    try:
        for relative, resource in _resource_files(resource_root):
            destination = skill_directory.joinpath(*relative.parts)
            destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(destination, flags, 0o644)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(resource.read_bytes())
    except BaseException:
        try:
            _remove_incomplete_skill(skill_directory)
        finally:
            raise


def _skill_root(name: str) -> Traversable:
    if name not in SKILL_NAMES:
        raise EnvironmentError("unknown bundled Agent Skill")
    base = files("restore_rehearsal").joinpath("skill")
    return base if name == DEFAULT_SKILL else base.joinpath(name)


def _resource_files(root: Traversable) -> Iterator[tuple[Path, Traversable]]:
    def visit(directory: Traversable, prefix: Path) -> Iterator[tuple[Path, Traversable]]:
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            relative = prefix / child.name
            if child.is_dir():
                yield from visit(child, relative)
            elif child.is_file():
                yield relative, child

    yield from visit(root, Path())


def _remove_incomplete_skill(directory: Path) -> None:
    if not directory.exists() or directory.is_symlink():
        return
    for child in sorted(directory.rglob("*"), key=lambda path: len(path.parts), reverse=True):
        if child.is_symlink() or child.is_file():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            child.rmdir()
    directory.rmdir()
