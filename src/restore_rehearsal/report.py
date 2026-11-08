"""Allowlisted, share-safe rehearsal report format."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from restore_rehearsal.errors import EnvironmentError

REPORT_SCHEMA = "rehearse.dev/report/v1alpha1"


@dataclass
class StepResult:
    name: str
    service: str
    status: str
    attempts: int
    duration_ms: int
    exit_code: int | None


@dataclass
class StageResult:
    name: str
    status: str = "pending"
    duration_ms: int = 0
    steps: list[StepResult] = field(default_factory=list)


@dataclass
class RunReport:
    run_id: str
    recipe: str
    started_at: str
    status: str = "running"
    finished_at: str | None = None
    failed_phase: str | None = None
    transformations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    candidate_services: list[str] = field(default_factory=list)
    backup_inputs: list[dict[str, Any]] = field(default_factory=list)
    stages: list[StageResult] = field(default_factory=list)
    cleanup: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": REPORT_SCHEMA,
            "run_id": self.run_id,
            "recipe": self.recipe,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "failed_phase": self.failed_phase,
            "candidate_services": list(self.candidate_services),
            "backup_inputs": list(self.backup_inputs),
            "transformations": list(self.transformations),
            "warnings": list(self.warnings),
            "stages": [asdict(stage) for stage in self.stages],
            "cleanup": self.cleanup,
        }


def write_report(path: Path, report: RunReport) -> None:
    """Create a mode-0600 report and refuse to overwrite any existing path."""

    target = path.expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    try:
        parent = target.parent.resolve(strict=True)
    except OSError as exc:
        raise EnvironmentError(
            "report parent directory does not exist or cannot be resolved"
        ) from exc
    target = parent / target.name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(target, flags, 0o600)
    except FileExistsError as exc:
        raise EnvironmentError("report path already exists; refusing to overwrite it") from exc
    except OSError as exc:
        raise EnvironmentError("could not create report file") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report.to_dict(), stream, indent=2, sort_keys=True)
            stream.write("\n")
    except BaseException:
        try:
            target.unlink(missing_ok=True)
        finally:
            raise
