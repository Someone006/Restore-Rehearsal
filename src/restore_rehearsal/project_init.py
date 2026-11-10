"""Static, local-only project detection and safe starter configuration."""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from restore_rehearsal.brain.inventory import inventory_repository
from restore_rehearsal.errors import EnvironmentError, SafetyError
from restore_rehearsal.importers.common import read_artifact, yaml_documents

PROJECT_CONFIG_NAME = "rehearse-project.yaml"
IGNORE_NAME = ".rehearseignore"

DEFAULT_REHEARSEIGNORE = """# Project Brain privacy exclusions (Git-compatible syntax)
# Add project-specific generated, customer, or local-only paths below.
.env
.env.*
*.key
*.pem
*.p12
*.pfx
backups/
credentials/
secrets/
project.rrbrain
project-brain*.html
"""


@dataclass(frozen=True)
class ProjectProfile:
    name: str
    inventory_mode: str
    ecosystems: tuple[str, ...]
    frameworks: tuple[str, ...]
    manifests: tuple[str, ...]
    compose_files: tuple[str, ...]
    ci_systems: tuple[str, ...]
    database_hints: tuple[str, ...]
    infrastructure: tuple[str, ...]
    test_paths: tuple[str, ...]
    recovery_artifacts: tuple[str, ...]
    suggested_importers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_project(root: Path) -> ProjectProfile:
    """Inspect bounded project metadata without executing project code."""

    inventory = inventory_repository(root)
    paths = tuple(item.path for item in inventory.files if not item.is_symlink)
    path_set = set(paths)
    manifests = sorted(
        path
        for path in paths
        if PurePosixPath(path).name.lower()
        in {
            "cargo.toml",
            "composer.json",
            "gemfile",
            "go.mod",
            "package.json",
            "pom.xml",
            "pyproject.toml",
            "requirements.txt",
            "setup.cfg",
            "setup.py",
        }
        or PurePosixPath(path).suffix.lower() in {".csproj", ".fsproj"}
    )
    ecosystems = _ecosystems(manifests)
    compose_files = tuple(
        sorted(
            path
            for path in paths
            if PurePosixPath(path).name.lower()
            in {"compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"}
        )
    )
    texts = {item.path: item.text or "" for item in inventory.files if item.text is not None}
    frameworks = _frameworks(texts)
    ci_systems = _ci_systems(path_set)
    database_hints = _database_hints(paths, texts)
    infrastructure = tuple(
        sorted(
            path
            for path in paths
            if PurePosixPath(path).suffix.lower() in {".tf", ".tfvars"}
            or "kubernetes" in path.lower()
            or "/k8s/" in f"/{path.lower()}/"
            or PurePosixPath(path).name.lower() in {"chart.yaml", "kustomization.yaml"}
        )
    )[:500]
    test_paths = tuple(sorted(path for path in paths if _is_test_path(path)))[:500]
    recovery_artifacts = tuple(sorted(path for path in paths if _is_recovery_artifact(path)))[:500]
    suggested_importers = _suggested_importers(paths)
    name = _safe_project_name(inventory.root.name)
    return ProjectProfile(
        name=name,
        inventory_mode=inventory.mode,
        ecosystems=ecosystems,
        frameworks=frameworks,
        manifests=tuple(manifests[:500]),
        compose_files=compose_files[:100],
        ci_systems=ci_systems,
        database_hints=database_hints,
        infrastructure=infrastructure,
        test_paths=test_paths,
        recovery_artifacts=recovery_artifacts,
        suggested_importers=suggested_importers,
    )


def render_project_config(profile: ProjectProfile) -> str:
    """Render deterministic declarative configuration; never invent restore commands."""

    artifact_entries = [{"type": kind, "paths": []} for kind in profile.suggested_importers]
    data: dict[str, Any] = {
        "version": 1,
        "project": {"name": profile.name},
        "brain": {"output": "project.rrbrain"},
        "artifacts": artifact_entries,
        "plugins": {"enabled": []},
        "recovery": {
            "status": "needs-user-input",
            "suggested_adapters": list(profile.database_hints),
            "note": (
                "Review backup paths, image versions, restore commands, and checks before "
                "creating an executable rehearse.yaml."
            ),
        },
        "detected": {
            "inventory_mode": profile.inventory_mode,
            "ecosystems": list(profile.ecosystems),
            "frameworks": list(profile.frameworks),
            "manifests": list(profile.manifests),
            "compose_files": list(profile.compose_files),
            "ci_systems": list(profile.ci_systems),
            "infrastructure": list(profile.infrastructure),
            "test_paths": list(profile.test_paths),
            "recovery_artifacts": list(profile.recovery_artifacts),
        },
        "privacy": {"ignore_file": IGNORE_NAME, "outbound": "local-only"},
    }
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=False)


def initialize_project(
    root: Path,
    *,
    output: Path | None = None,
    ignore_output: Path | None = None,
    create_ignore: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Detect a project and atomically create starter files without overwriting."""

    profile = detect_project(root)
    project_root = root.expanduser().resolve(strict=True)
    config_target = _target_inside(
        project_root, output if output is not None else Path(PROJECT_CONFIG_NAME)
    )
    ignore_target = _target_inside(
        project_root, ignore_output if ignore_output is not None else Path(IGNORE_NAME)
    )
    targets = [config_target, *([ignore_target] if create_ignore else [])]
    if len(set(targets)) != len(targets):
        raise SafetyError("initialization output paths must be different")
    for target in targets:
        if target.exists() or target.is_symlink():
            raise EnvironmentError(
                f"{target.name} already exists; refusing to overwrite project files"
            )
    result = {
        "profile": profile.to_dict(),
        "config": config_target.name,
        "ignore": ignore_target.name if create_ignore else None,
        "dry_run": dry_run,
    }
    if dry_run:
        result["config_text"] = render_project_config(profile)
        result["ignore_text"] = DEFAULT_REHEARSEIGNORE if create_ignore else None
        return result

    created: list[Path] = []
    try:
        _exclusive_atomic_write(config_target, render_project_config(profile))
        created.append(config_target)
        if create_ignore:
            _exclusive_atomic_write(ignore_target, DEFAULT_REHEARSEIGNORE)
            created.append(ignore_target)
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return result


def load_project_config(path: Path) -> dict[str, Any]:
    """Load the generated declarative project configuration with strict keys and paths."""

    raw = read_artifact(path, maximum=1024 * 1024)
    documents = yaml_documents(raw)
    if len(documents) != 1 or not isinstance(documents[0], dict):
        raise SafetyError("project configuration must contain one YAML mapping")
    data = documents[0]
    _exact_keys(
        data,
        required={"version", "project", "brain", "artifacts", "plugins", "recovery"},
        optional={"detected", "privacy"},
        where="project configuration",
    )
    if data.get("version") != 1:
        raise SafetyError("unsupported project configuration version")
    project = _string_mapping(data.get("project"), "project")
    _exact_keys(project, required={"name"}, optional=set(), where="project")
    if _safe_project_name(str(project.get("name"))) != project.get("name"):
        raise SafetyError("project.name is invalid")
    brain = _string_mapping(data.get("brain"), "brain")
    _exact_keys(brain, required={"output"}, optional=set(), where="brain")
    _config_relative_path(brain.get("output"), "brain.output")
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) > 1_000:
        raise SafetyError("artifacts must be a bounded list")
    normalized_artifacts: list[dict[str, Any]] = []
    for index, raw_artifact in enumerate(artifacts):
        artifact = _string_mapping(raw_artifact, f"artifacts[{index}]")
        _exact_keys(
            artifact,
            required={"type", "paths"},
            optional=set(),
            where=f"artifacts[{index}]",
        )
        kind = artifact.get("type")
        if not isinstance(kind, str) or kind not in {
            "ci",
            "coverage",
            "junit",
            "kubernetes",
            "openapi",
            "sarif",
            "sbom",
            "terraform",
        }:
            raise SafetyError(f"artifacts[{index}].type is unknown")
        paths = artifact.get("paths")
        if not isinstance(paths, list) or len(paths) > 1_000:
            raise SafetyError(f"artifacts[{index}].paths must be a bounded list")
        normalized_artifacts.append(
            {
                "type": kind,
                "paths": [
                    _config_relative_path(item, f"artifacts[{index}].paths") for item in paths
                ],
            }
        )
    plugins = _string_mapping(data.get("plugins"), "plugins")
    _exact_keys(plugins, required={"enabled"}, optional=set(), where="plugins")
    enabled = plugins.get("enabled")
    if (
        not isinstance(enabled, list)
        or len(enabled) > 100
        or not all(isinstance(item, str) and 1 <= len(item) <= 100 for item in enabled)
    ):
        raise SafetyError("plugins.enabled must be a bounded list of identifiers")
    recovery = _string_mapping(data.get("recovery"), "recovery")
    _exact_keys(
        recovery,
        required={"status", "suggested_adapters", "note"},
        optional=set(),
        where="recovery",
    )
    if recovery.get("status") != "needs-user-input":
        raise SafetyError("recovery.status must remain needs-user-input until reviewed")
    adapters = recovery.get("suggested_adapters")
    if (
        not isinstance(adapters, list)
        or len(adapters) > 100
        or not all(isinstance(item, str) and 1 <= len(item) <= 100 for item in adapters)
    ):
        raise SafetyError("recovery.suggested_adapters is invalid")
    note = recovery.get("note")
    if not isinstance(note, str) or not note or len(note) > 2_000:
        raise SafetyError("recovery.note is invalid")
    # Detected/privacy sections are informational and are not interpreted as instructions.
    return {
        "version": 1,
        "project": {"name": project["name"]},
        "brain": {"output": brain["output"]},
        "artifacts": normalized_artifacts,
        "plugins": {"enabled": list(enabled)},
        "recovery": {
            "status": recovery["status"],
            "suggested_adapters": list(adapters),
            "note": note,
        },
    }


def _target_inside(root: Path, value: Path) -> Path:
    expanded = value.expanduser()
    candidate = expanded if expanded.is_absolute() else root / expanded
    try:
        parent = candidate.parent.resolve(strict=True)
    except OSError as exc:
        raise EnvironmentError("initialization output parent does not exist") from exc
    target = parent / candidate.name
    if not target.is_relative_to(root) or target == root:
        raise SafetyError("initialization output must stay inside the selected project")
    return target


def _exact_keys(
    data: dict[str, Any], *, required: set[str], optional: set[str], where: str
) -> None:
    if not all(isinstance(key, str) for key in data):
        raise SafetyError(f"{where} keys must be text")
    missing = sorted(required - data.keys())
    unknown = sorted(data.keys() - required - optional)
    if missing:
        raise SafetyError(f"{where} is missing required keys: {', '.join(missing)}")
    if unknown:
        raise SafetyError(f"{where} has unknown keys: {', '.join(unknown)}")


def _string_mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise SafetyError(f"{where} must be a mapping")
    return value


def _config_relative_path(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1_000:
        raise SafetyError(f"{where} must be a relative path or pattern")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or normalized.startswith("~"):
        raise SafetyError(f"{where} must stay inside the project")
    return normalized


def _exclusive_atomic_write(path: Path, text: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    temporary.chmod(0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise EnvironmentError(
                f"{path.name} already exists; refusing to overwrite project files"
            ) from exc
        except OSError as exc:
            raise EnvironmentError("could not create initialization output") from exc
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _ecosystems(manifests: list[str]) -> tuple[str, ...]:
    result: set[str] = set()
    for path in manifests:
        name = PurePosixPath(path).name.lower()
        suffix = PurePosixPath(path).suffix.lower()
        if name in {"pyproject.toml", "requirements.txt", "setup.py", "setup.cfg"}:
            result.add("python")
        elif name == "package.json":
            result.add("javascript-typescript")
        elif name == "cargo.toml":
            result.add("rust")
        elif name == "go.mod":
            result.add("go")
        elif name == "gemfile":
            result.add("ruby")
        elif name == "composer.json":
            result.add("php")
        elif name == "pom.xml":
            result.add("java")
        elif suffix in {".csproj", ".fsproj"}:
            result.add("dotnet")
    return tuple(sorted(result))


def _frameworks(texts: dict[str, str]) -> tuple[str, ...]:
    signatures = {
        "django": ("django",),
        "fastapi": ("fastapi",),
        "flask": ("flask",),
        "nextjs": ('"next"', "next.config"),
        "react": ('"react"',),
        "svelte": ('"svelte"',),
        "vue": ('"vue"',),
        "rails": ("rails",),
        "spring": ("spring-boot",),
    }
    haystack = "\n".join(
        text.lower()
        for path, text in sorted(texts.items())
        if PurePosixPath(path).name.lower()
        in {"pyproject.toml", "requirements.txt", "package.json", "gemfile", "pom.xml"}
    )[:2_000_000]
    return tuple(
        sorted(
            name
            for name, tokens in signatures.items()
            if any(token in haystack for token in tokens)
        )
    )


def _ci_systems(paths: set[str]) -> tuple[str, ...]:
    lowered = {path.lower() for path in paths}
    systems: set[str] = set()
    if any(path.startswith(".github/workflows/") for path in lowered):
        systems.add("github-actions")
    if ".gitlab-ci.yml" in lowered:
        systems.add("gitlab-ci")
    if "jenkinsfile" in lowered:
        systems.add("jenkins")
    if ".circleci/config.yml" in lowered:
        systems.add("circleci")
    if "azure-pipelines.yml" in lowered:
        systems.add("azure-pipelines")
    return tuple(sorted(systems))


def _database_hints(paths: tuple[str, ...], texts: dict[str, str]) -> tuple[str, ...]:
    hints: set[str] = set()
    joined_paths = "\n".join(path.lower() for path in paths)
    compose_text = "\n".join(
        text.lower()
        for path, text in texts.items()
        if PurePosixPath(path).name.lower()
        in {"compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"}
    )[:2_000_000]
    haystack = f"{joined_paths}\n{compose_text}"
    for name, tokens in {
        "postgresql": ("postgres", ".dump", ".backup"),
        "mysql": ("mysql", "mariadb"),
        "sqlite": ("sqlite", ".sqlite", ".db"),
        "mongodb": ("mongo", "bson", "mongodump"),
        "object-storage": ("minio", "object-export", "s3-export"),
    }.items():
        if any(token in haystack for token in tokens):
            hints.add(name)
    return tuple(sorted(hints))


def _suggested_importers(paths: tuple[str, ...]) -> tuple[str, ...]:
    result: set[str] = set()
    for path in paths:
        lowered = path.lower()
        name = PurePosixPath(lowered).name
        if lowered.endswith((".sarif", ".sarif.json")):
            result.add("sarif")
        if "sbom" in name or "spdx" in name or name == "bom.json":
            result.add("sbom")
        if name.endswith(".xml") and any(
            token in name for token in ("junit", "test", "coverage", "cobertura")
        ):
            result.add("coverage" if "coverage" in name or "cobertura" in name else "junit")
        if name in {"openapi.json", "openapi.yaml", "openapi.yml", "swagger.json", "swagger.yaml"}:
            result.add("openapi")
        if lowered.endswith(".tf") or "tfplan" in name:
            result.add("terraform")
        if (
            "k8s" in lowered
            or "kubernetes" in lowered
            or name in {"chart.yaml", "kustomization.yaml"}
        ):
            result.add("kubernetes")
        if any(token in name for token in ("ci-result", "check-results", "workflow-results")):
            result.add("ci")
    return tuple(sorted(result))


def _is_test_path(path: str) -> bool:
    lowered = f"/{path.lower()}/"
    name = PurePosixPath(path).name.lower()
    return (
        "/test/" in lowered
        or "/tests/" in lowered
        or "/spec/" in lowered
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
    )


def _is_recovery_artifact(path: str) -> bool:
    lowered = path.lower()
    name = PurePosixPath(lowered).name
    return (
        PurePosixPath(lowered).suffix in {".backup", ".bson", ".db", ".dump", ".sqlite"}
        or name.endswith(".sql")
        or any(
            part in {"backup", "backups", "dump", "dumps", "exports"}
            for part in PurePosixPath(lowered).parts
        )
    )


def _safe_project_name(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "_.-" else "-" for character in value
    )
    return normalized.strip("-.")[:63] or "project"


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
