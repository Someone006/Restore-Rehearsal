"""Strict, intentionally small recipe schema."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from restore_rehearsal.errors import RecipeError

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
PULL_POLICIES = {"always", "missing", "never"}


@dataclass(frozen=True)
class ComposeSpec:
    files: tuple[Path, ...]
    profiles: tuple[str, ...]
    pull: str
    startup_timeout: int


@dataclass(frozen=True)
class BackupMount:
    service: str
    target: str


@dataclass(frozen=True)
class BackupInput:
    name: str
    path: Path
    mounts: tuple[BackupMount, ...]
    sha256: str | None = None


@dataclass(frozen=True)
class CommandStep:
    name: str
    service: str
    command: tuple[str, ...]
    timeout: int
    retries: int
    interval: float
    stdin_from: str | None = None


@dataclass(frozen=True)
class Check:
    name: str
    kind: str
    service: str
    command: tuple[str, ...]
    timeout: int
    retries: int
    interval: float


@dataclass(frozen=True)
class Candidate:
    images: dict[str, str]


@dataclass(frozen=True)
class SafetySpec:
    allow_egress: bool
    allow_read_only_binds: tuple[Path, ...]


@dataclass(frozen=True)
class Recipe:
    version: int
    name: str
    compose: ComposeSpec
    backups: tuple[BackupInput, ...]
    restore: tuple[CommandStep, ...]
    candidate: Candidate
    checks: tuple[Check, ...]
    safety: SafetySpec
    source_path: Path

    @property
    def backup_by_name(self) -> dict[str, BackupInput]:
        return {backup.name: backup for backup in self.backups}


def load_recipe(path: Path) -> Recipe:
    """Load and fully validate a recipe without executing any command."""

    source = path.expanduser().resolve(strict=True)
    if not source.is_file():
        raise RecipeError("recipe must be a regular file")
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RecipeError(f"could not read recipe: {exc}") from exc

    data = _mapping(raw, "recipe")
    _keys(
        data,
        required={"version", "name", "compose", "backups", "restore", "candidate", "checks"},
        optional={"safety"},
        where="recipe",
    )
    version = _integer(data["version"], "version", minimum=1, maximum=1)
    name = _name(data["name"], "name")
    base = source.parent

    compose_data = _mapping(data["compose"], "compose")
    _keys(
        compose_data,
        required={"files"},
        optional={"profiles", "pull", "startup_timeout"},
        where="compose",
    )
    compose_files = tuple(
        _existing_file(_path(base, item, "compose.files"), "compose.files")
        for item in _list(compose_data["files"], "compose.files", nonempty=True)
    )
    profiles = tuple(
        _name(item, "compose.profiles")
        for item in _list(compose_data.get("profiles", []), "compose.profiles")
    )
    pull = _string(compose_data.get("pull", "missing"), "compose.pull")
    if pull not in PULL_POLICIES:
        raise RecipeError("compose.pull must be one of: always, missing, never")
    startup_timeout = _integer(
        compose_data.get("startup_timeout", 120),
        "compose.startup_timeout",
        minimum=1,
        maximum=3600,
    )

    backups = tuple(
        _backup(item, base, index)
        for index, item in enumerate(_list(data["backups"], "backups", nonempty=True))
    )
    _unique((item.name for item in backups), "backup names")
    backup_names = {item.name for item in backups}

    restore = tuple(
        _command_step(item, index)
        for index, item in enumerate(_list(data["restore"], "restore", nonempty=True))
    )
    _unique((item.name for item in restore), "restore step names")
    for step in restore:
        if step.stdin_from is not None:
            if step.stdin_from not in backup_names:
                raise RecipeError(
                    f"restore step {step.name!r} references unknown backup {step.stdin_from!r}"
                )
            backup = next(item for item in backups if item.name == step.stdin_from)
            if not backup.path.is_file():
                raise RecipeError(
                    f"restore step {step.name!r} can use stdin only from a regular file"
                )

    candidate_data = _mapping(data["candidate"], "candidate")
    _keys(candidate_data, required={"images"}, optional=set(), where="candidate")
    image_data = _mapping(candidate_data["images"], "candidate.images")
    if not image_data:
        raise RecipeError("candidate.images must contain at least one service image")
    images: dict[str, str] = {}
    for service, image in image_data.items():
        service_name = _name(service, "candidate image service")
        image_name = _string(image, f"candidate.images.{service_name}")
        if any(character.isspace() for character in image_name):
            raise RecipeError(f"candidate image for {service_name!r} must not contain whitespace")
        images[service_name] = image_name

    checks = tuple(
        _check(item, index)
        for index, item in enumerate(_list(data["checks"], "checks", nonempty=True))
    )
    _unique((item.name for item in checks), "check names")

    safety_data = _mapping(data.get("safety", {}), "safety")
    _keys(
        safety_data,
        required=set(),
        optional={"allow_egress", "allow_read_only_binds"},
        where="safety",
    )
    allow_egress = _boolean(safety_data.get("allow_egress", False), "safety.allow_egress")
    allowed_binds = tuple(
        _existing_path(
            _path(base, item, "safety.allow_read_only_binds"), "safety.allow_read_only_binds"
        )
        for item in _list(
            safety_data.get("allow_read_only_binds", []),
            "safety.allow_read_only_binds",
        )
    )

    return Recipe(
        version=version,
        name=name,
        compose=ComposeSpec(
            files=compose_files,
            profiles=profiles,
            pull=pull,
            startup_timeout=startup_timeout,
        ),
        backups=backups,
        restore=restore,
        candidate=Candidate(images=images),
        checks=checks,
        safety=SafetySpec(
            allow_egress=allow_egress,
            allow_read_only_binds=allowed_binds,
        ),
        source_path=source,
    )


def verify_backup_digests(recipe: Recipe) -> None:
    """Fail before Docker mutation if a declared backup digest does not match."""

    for backup in recipe.backups:
        if backup.sha256 is None:
            continue
        digest = hashlib.sha256()
        try:
            with backup.path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise RecipeError(f"could not read backup {backup.name!r}: {exc}") from exc
        if digest.hexdigest() != backup.sha256.lower():
            raise RecipeError(f"backup {backup.name!r} does not match its declared sha256")


def _backup(raw: Any, base: Path, index: int) -> BackupInput:
    where = f"backups[{index}]"
    data = _mapping(raw, where)
    _keys(data, required={"name", "path"}, optional={"mounts", "sha256"}, where=where)
    name = _name(data["name"], f"{where}.name")
    path = _existing_path(_path(base, data["path"], f"{where}.path"), f"{where}.path")
    if path == Path(path.anchor):
        raise RecipeError(f"{where}.path must not be a filesystem root")
    mounts: list[BackupMount] = []
    for mount_index, raw_mount in enumerate(_list(data.get("mounts", []), f"{where}.mounts")):
        mount_where = f"{where}.mounts[{mount_index}]"
        mount_data = _mapping(raw_mount, mount_where)
        _keys(mount_data, required={"service", "target"}, optional=set(), where=mount_where)
        mounts.append(
            BackupMount(
                service=_name(mount_data["service"], f"{mount_where}.service"),
                target=_container_path(mount_data["target"], f"{mount_where}.target"),
            )
        )
    digest = data.get("sha256")
    if digest is not None:
        digest = _string(digest, f"{where}.sha256").lower()
        if not path.is_file():
            raise RecipeError(f"{where}.sha256 is supported only for regular files")
        if not SHA256_RE.fullmatch(digest):
            raise RecipeError(f"{where}.sha256 must be a 64-character hexadecimal digest")
    return BackupInput(name=name, path=path, mounts=tuple(mounts), sha256=digest)


def _command_step(raw: Any, index: int) -> CommandStep:
    where = f"restore[{index}]"
    data = _mapping(raw, where)
    _keys(
        data,
        required={"name", "service", "command"},
        optional={"timeout", "retries", "interval", "stdin_from"},
        where=where,
    )
    stdin_from = data.get("stdin_from")
    return CommandStep(
        name=_name(data["name"], f"{where}.name"),
        service=_name(data["service"], f"{where}.service"),
        command=_command(data["command"], f"{where}.command"),
        timeout=_integer(data.get("timeout", 300), f"{where}.timeout", minimum=1, maximum=86400),
        retries=_integer(data.get("retries", 1), f"{where}.retries", minimum=1, maximum=100),
        interval=_number(data.get("interval", 2), f"{where}.interval", minimum=0, maximum=300),
        stdin_from=None if stdin_from is None else _name(stdin_from, f"{where}.stdin_from"),
    )


def _check(raw: Any, index: int) -> Check:
    where = f"checks[{index}]"
    data = _mapping(raw, where)
    _keys(
        data,
        required={"name", "type", "service"},
        optional={"command", "timeout", "retries", "interval"},
        where=where,
    )
    kind = _string(data["type"], f"{where}.type")
    if kind not in {"exec", "health"}:
        raise RecipeError(f"{where}.type must be 'exec' or 'health'")
    command: tuple[str, ...] = ()
    if kind == "exec":
        if "command" not in data:
            raise RecipeError(f"{where}.command is required for an exec check")
        command = _command(data["command"], f"{where}.command")
    elif "command" in data:
        raise RecipeError(f"{where}.command is not valid for a health check")
    return Check(
        name=_name(data["name"], f"{where}.name"),
        kind=kind,
        service=_name(data["service"], f"{where}.service"),
        command=command,
        timeout=_integer(data.get("timeout", 30), f"{where}.timeout", minimum=1, maximum=3600),
        retries=_integer(data.get("retries", 5), f"{where}.retries", minimum=1, maximum=100),
        interval=_number(data.get("interval", 2), f"{where}.interval", minimum=0, maximum=300),
    )


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise RecipeError(f"{where} must be a mapping")
    return value


def _list(value: Any, where: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list):
        raise RecipeError(f"{where} must be a list")
    if nonempty and not value:
        raise RecipeError(f"{where} must not be empty")
    return value


def _keys(data: dict[str, Any], *, required: set[str], optional: set[str], where: str) -> None:
    missing = sorted(required - data.keys())
    unknown = sorted(data.keys() - required - optional)
    if missing:
        raise RecipeError(f"{where} is missing required key(s): {', '.join(missing)}")
    if unknown:
        raise RecipeError(f"{where} has unknown key(s): {', '.join(unknown)}")


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RecipeError(f"{where} must be a non-empty string without NUL bytes")
    return value


def _name(value: Any, where: str) -> str:
    result = _string(value, where)
    if not NAME_RE.fullmatch(result):
        raise RecipeError(
            f"{where} must begin with an alphanumeric character and contain only "
            "letters, numbers, '.', '_' or '-'"
        )
    return result


def _integer(value: Any, where: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise RecipeError(f"{where} must be an integer from {minimum} to {maximum}")
    return value


def _number(value: Any, where: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecipeError(f"{where} must be a number")
    result = float(value)
    if not minimum <= result <= maximum:
        raise RecipeError(f"{where} must be from {minimum} to {maximum}")
    return result


def _boolean(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise RecipeError(f"{where} must be true or false")
    return value


def _command(value: Any, where: str) -> tuple[str, ...]:
    command = tuple(_string(item, where) for item in _list(value, where, nonempty=True))
    return command


def _path(base: Path, value: Any, where: str) -> Path:
    raw = _string(value, where)
    path = Path(raw).expanduser()
    return path if path.is_absolute() else base / path


def _existing_file(path: Path, where: str) -> Path:
    resolved = _existing_path(path, where)
    if not resolved.is_file():
        raise RecipeError(f"{where} must reference a regular file")
    return resolved


def _existing_path(path: Path, where: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RecipeError(f"{where} does not exist or cannot be resolved") from exc
    if not (resolved.is_file() or resolved.is_dir()):
        raise RecipeError(f"{where} must reference a regular file or directory")
    return resolved


def _container_path(value: Any, where: str) -> str:
    raw = _string(value, where)
    path = PurePosixPath(raw)
    if not path.is_absolute() or path == PurePosixPath("/") or ".." in path.parts:
        raise RecipeError(f"{where} must be a normalized absolute container path below '/' ")
    return str(path)


def _unique(values: Any, what: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise RecipeError(f"{what} must be unique; duplicate(s): {', '.join(sorted(duplicates))}")
