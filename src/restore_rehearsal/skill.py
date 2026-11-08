"""Install the bundled portable Agent Skill without overwriting user files."""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path

from restore_rehearsal.errors import EnvironmentError

SKILL_DIRECTORY = "restore-rehearsal"
SKILL_FILENAME = "SKILL.md"


def skill_text() -> str:
    resource = files("restore_rehearsal").joinpath("skill", SKILL_FILENAME)
    try:
        return resource.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise EnvironmentError("the bundled Agent Skill could not be read") from exc


def install_skill(target_root: Path) -> None:
    """Create TARGET/restore-rehearsal/SKILL.md, refusing every overwrite."""

    target = target_root.expanduser()
    try:
        target.mkdir(parents=True, exist_ok=True)
        target = target.resolve(strict=True)
    except OSError as exc:
        raise EnvironmentError("the Agent Skill target directory could not be created") from exc
    if not target.is_dir():
        raise EnvironmentError("the Agent Skill target must be a directory")

    skill_directory = target / SKILL_DIRECTORY
    try:
        skill_directory.mkdir(mode=0o755)
    except FileExistsError as exc:
        raise EnvironmentError(
            "an Agent Skill with this name already exists; refusing to overwrite it"
        ) from exc
    except OSError as exc:
        raise EnvironmentError("the Agent Skill directory could not be created") from exc

    destination = skill_directory / SKILL_FILENAME
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(destination, flags, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(skill_text())
    except BaseException:
        try:
            destination.unlink(missing_ok=True)
            skill_directory.rmdir()
        finally:
            raise
