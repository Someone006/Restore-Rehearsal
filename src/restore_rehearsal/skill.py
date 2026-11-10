"""Inspect, validate, and install bundled portable Agent Skills safely."""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from restore_rehearsal import __version__
from restore_rehearsal.errors import EnvironmentError

DEFAULT_SKILL = "restore-rehearsal"
SKILL_FILENAME = "SKILL.md"


@dataclass(frozen=True)
class SkillSpec:
    """Trusted metadata for one bundled, independently installable skill."""

    name: str
    description: str
    references: int = 0


SKILL_SPECS = (
    SkillSpec(DEFAULT_SKILL, "Run disposable backup restore and upgrade drills."),
    SkillSpec("project-brain", "Build and query a local project evidence graph.", 10),
    SkillSpec("rehearse-init", "Initialize Rehearse safely in an existing project.", 2),
    SkillSpec(
        "universal-inspector",
        "Inspect websites, apps, CLIs, APIs, code, documents, and data with visual evidence.",
        7,
    ),
    SkillSpec(
        "change-impact", "Compare brain snapshots and trace affected contracts and tests.", 3
    ),
    SkillSpec(
        "evidence-import", "Import bounded external test, security, and project evidence.", 3
    ),
    SkillSpec("recovery-adapters", "Plan and run safe provider-specific recovery adapters.", 3),
    SkillSpec("release-readiness", "Verify packaging, provenance, and release evidence.", 3),
    SkillSpec(
        "plugin-authoring", "Build deterministic Rehearse analyzers, importers, and adapters.", 3
    ),
)
SKILL_NAMES = tuple(spec.name for spec in SKILL_SPECS)
_SKILLS_BY_NAME = {spec.name: spec for spec in SKILL_SPECS}


def skill_catalog() -> tuple[dict[str, Any], ...]:
    """Return stable, JSON-serializable metadata for every bundled skill."""

    return tuple(
        {
            "name": spec.name,
            "description": spec.description,
            "references": spec.references,
            "version": __version__,
        }
        for spec in SKILL_SPECS
    )


def skill_text(name: str = DEFAULT_SKILL) -> str:
    resource = _skill_root(name).joinpath(SKILL_FILENAME)
    try:
        return resource.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise EnvironmentError("the bundled Agent Skill could not be read") from exc


def install_skill(target_root: Path, *, name: str = DEFAULT_SKILL) -> None:
    """Create TARGET/NAME from bundled resources, refusing every overwrite."""

    install_skills(target_root, names=(name,))


def install_all_skills(target_root: Path) -> tuple[str, ...]:
    """Install every bundled skill as one all-or-nothing operation."""

    return install_skills(target_root)


def install_skills(
    target_root: Path, *, names: tuple[str, ...] | list[str] | str | None = None
) -> tuple[str, ...]:
    """Install selected skills atomically, refusing any existing destination."""

    if names is None:
        selected = SKILL_NAMES
    elif isinstance(names, str):
        selected = (names,)
    else:
        selected = tuple(names)
    if not selected:
        raise EnvironmentError("select at least one Agent Skill")
    if len(set(selected)) != len(selected):
        raise EnvironmentError("duplicate Agent Skill name")
    for name in selected:
        _skill_spec(name)

    target = target_root.expanduser()
    try:
        target.mkdir(parents=True, exist_ok=True)
        target = target.resolve(strict=True)
    except OSError as exc:
        raise EnvironmentError("the Agent Skill target directory could not be created") from exc
    if not target.is_dir():
        raise EnvironmentError("the Agent Skill target must be a directory")

    destinations = tuple(target / name for name in selected)
    if any(destination.exists() or destination.is_symlink() for destination in destinations):
        raise EnvironmentError(
            "an Agent Skill with this name already exists; refusing to overwrite it"
        )

    created: list[Path] = []
    try:
        for name, skill_directory in zip(selected, destinations, strict=True):
            try:
                skill_directory.mkdir(mode=0o755)
            except OSError as exc:
                raise EnvironmentError("the Agent Skill directory could not be created") from exc
            created.append(skill_directory)
            for relative, resource in _resource_files(_skill_root(name)):
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
            for skill_directory in reversed(created):
                _remove_incomplete_skill(skill_directory)
        finally:
            raise
    return selected


def skill_doctor() -> dict[str, Any]:
    """Validate names, frontmatter, references, and packaged resource counts."""

    issues: list[str] = []
    results: list[dict[str, Any]] = []
    total_files = 0
    for spec in SKILL_SPECS:
        skill_issues: list[str] = []
        resource_files: tuple[tuple[Path, Traversable], ...] = ()
        try:
            resource_files = tuple(_resource_files(_skill_root(spec.name)))
            by_name = {relative.as_posix(): resource for relative, resource in resource_files}
            total_files += len(resource_files)
            skill_resource = by_name.get(SKILL_FILENAME)
            if skill_resource is None:
                skill_issues.append("missing SKILL.md")
                skill_text_value = ""
            else:
                skill_text_value = skill_resource.read_text(encoding="utf-8")
            declared_name = _frontmatter_value(skill_text_value, "name")
            if declared_name != spec.name:
                skill_issues.append("frontmatter name does not match the registry")
            if _frontmatter_value(skill_text_value, "license") != "Apache-2.0":
                skill_issues.append("license must be Apache-2.0")
            if not _frontmatter_value(skill_text_value, "description"):
                skill_issues.append("frontmatter description is required")
            if not _frontmatter_value(skill_text_value, "compatibility"):
                skill_issues.append("frontmatter compatibility is required")
            if _metadata_version(skill_text_value) != __version__:
                skill_issues.append("metadata version does not match the package")
            reference_files = {
                relative.as_posix()
                for relative, _ in resource_files
                if relative.parts[:1] == ("references",) and relative.suffix == ".md"
            }
            if len(reference_files) != spec.references:
                skill_issues.append(
                    f"expected {spec.references} reference files, found {len(reference_files)}"
                )
            linked_references = set(_linked_references(skill_text_value))
            for reference in linked_references:
                if reference not in reference_files:
                    skill_issues.append(f"missing linked reference: {reference}")
            for reference in sorted(reference_files - linked_references):
                skill_issues.append(f"unlinked reference: {reference}")
            for relative, resource in resource_files:
                if relative.is_absolute() or ".." in relative.parts:
                    skill_issues.append("unsafe bundled resource path")
                    continue
                if relative.suffix.lower() != ".md":
                    skill_issues.append(f"unexpected non-Markdown resource: {relative}")
                    continue
                resource_text = resource.read_text(encoding="utf-8")
                if _contains_disallowed_control(resource_text):
                    skill_issues.append(f"control character in resource: {relative}")
        except (OSError, UnicodeError) as exc:
            skill_issues.append(f"could not read bundled resources: {type(exc).__name__}")
        issues.extend(f"{spec.name}: {issue}" for issue in skill_issues)
        results.append(
            {
                "name": spec.name,
                "ok": not skill_issues,
                "files": len(resource_files),
                "references": spec.references,
            }
        )
    return {
        "ok": not issues,
        "skill_count": len(SKILL_SPECS),
        "file_count": total_files,
        "skills": results,
        "issues": issues,
    }


def _skill_root(name: str) -> Traversable:
    _skill_spec(name)
    base = files("restore_rehearsal").joinpath("skill")
    return base.joinpath(name)


def _skill_spec(name: str) -> SkillSpec:
    if not isinstance(name, str):
        raise EnvironmentError("unknown bundled Agent Skill")
    try:
        return _SKILLS_BY_NAME[name]
    except KeyError as exc:
        raise EnvironmentError("unknown bundled Agent Skill") from exc


def _frontmatter_value(text: str, key: str) -> str | None:
    match = re.match(r"\A---\n(.*?)\n---(?:\n|\Z)", text, flags=re.DOTALL)
    if match is None:
        return None
    value = re.search(rf"(?m)^{re.escape(key)}:\s*['\"]?([^'\"\n]+)", match.group(1))
    return value.group(1).strip() if value else None


def _linked_references(text: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            set(
                re.findall(
                    r"\]\((references/[A-Za-z0-9._/-]+\.md)(?:#[^)]+)?\)",
                    text,
                )
            )
        )
    )


def _metadata_version(text: str) -> str | None:
    match = re.match(r"\A---\n(.*?)\n---(?:\n|\Z)", text, flags=re.DOTALL)
    if match is None:
        return None
    value = re.search(
        r"(?m)^metadata:\s*$\n(?:^[ \t]+.*\n)*?^[ \t]+version:\s*['\"]?([^'\"\n]+)",
        match.group(1) + "\n",
    )
    return value.group(1).strip() if value else None


def _contains_disallowed_control(text: str) -> bool:
    return any(
        unicodedata.category(character).startswith("C") and character not in {"\n", "\r", "\t"}
        for character in text
    )


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
