"""Export exact direct runtime dependencies for vulnerability auditing."""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path
from typing import Any


def _locked_packages(project_root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    root = project_root.resolve(strict=True)
    with (root / "uv.lock").open("rb") as source:
        lock = tomllib.load(source)
    packages = {str(item["name"]): item for item in lock["package"]}
    try:
        project = packages["restore-rehearsal"]
    except KeyError as exc:
        raise RuntimeError("uv.lock does not contain restore-rehearsal") from exc
    return project, packages


def render_audit_requirements(project_root: Path) -> str:
    """Return deterministic pinned requirements for the project's direct runtime dependencies."""

    project, packages = _locked_packages(project_root)
    names = sorted(str(item["name"]) for item in project.get("dependencies", []))
    lines: list[str] = []
    for name in names:
        try:
            version = str(packages[name]["version"])
        except KeyError as exc:
            raise RuntimeError(
                f"uv.lock is missing a version for runtime dependency {name}"
            ) from exc
        lines.append(f"{name}=={version}")
    if not lines:
        raise RuntimeError("uv.lock has no direct runtime dependencies to audit")
    return "".join(f"{line}\n" for line in lines)


def write_audit_requirements(project_root: Path, output: Path) -> Path:
    """Write the audit input once, refusing to replace an existing file."""

    destination = output.expanduser().resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8", newline="\n") as target:
            target.write(render_audit_requirements(project_root))
    except FileExistsError as exc:
        raise RuntimeError("audit requirements output exists; refusing to overwrite it") from exc
    return destination


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    print(write_audit_requirements(arguments.project_root, arguments.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
