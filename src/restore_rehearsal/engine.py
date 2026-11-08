"""Restore → baseline checks → candidate upgrade → checks state machine."""

from __future__ import annotations

import re
import secrets
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from restore_rehearsal.compose import CommandRunner, ComposeClient, write_private_json
from restore_rehearsal.errors import ExitCode, RehearsalError
from restore_rehearsal.isolation import IsolationPlan, build_isolation_plan
from restore_rehearsal.recipe import Check, CommandStep, Recipe, verify_backup_digests
from restore_rehearsal.report import RunReport, StageResult, StepResult, write_report


@dataclass(frozen=True)
class PreparedRun:
    run_id: str
    project: str
    client: ComposeClient
    plan: IsolationPlan


@dataclass(frozen=True)
class RunOutcome:
    report: RunReport
    exit_code: ExitCode


def prepare(recipe: Recipe, runner: CommandRunner | None = None) -> PreparedRun:
    """Resolve and validate everything possible without mutating Docker."""

    verify_backup_digests(recipe)
    run_id = secrets.token_hex(8)
    slug = re.sub(r"[^a-z0-9]+", "-", recipe.name.lower()).strip("-")[:20] or "run"
    project = f"rr-{slug}-{run_id}"
    client = ComposeClient(
        runner or CommandRunner(),
        project=project,
        run_id=run_id,
        compose=recipe.compose,
    )
    source_model = client.resolve()
    plan = build_isolation_plan(source_model, recipe, project=project, run_id=run_id)
    client.assert_project_absent()
    return PreparedRun(run_id=run_id, project=project, client=client, plan=plan)


def run_rehearsal(
    recipe: Recipe,
    *,
    report_path: Path | None = None,
    runner: CommandRunner | None = None,
) -> RunOutcome:
    prepared = prepare(recipe, runner)
    report = _new_report(recipe, prepared)
    result_code = ExitCode.OK
    resources_may_exist = False
    active_config: Path | None = None

    with tempfile.TemporaryDirectory(prefix="restore-rehearsal-") as temporary:
        workspace = Path(temporary)
        workspace.chmod(0o700)
        baseline_path = workspace / "baseline.json"
        candidate_path = workspace / "candidate.json"
        write_private_json(baseline_path, prepared.plan.baseline)
        write_private_json(candidate_path, prepared.plan.candidate)
        active_config = candidate_path

        try:
            restore_stage = StageResult(name="restore")
            report.stages.append(restore_stage)
            restore_started = time.monotonic()
            restore_services = tuple(dict.fromkeys(step.service for step in recipe.restore))
            resources_may_exist = True
            prepared.client.start(baseline_path, services=restore_services, wait=False)
            for step in recipe.restore:
                step_result = _run_restore_step(prepared.client, baseline_path, recipe, step)
                restore_stage.steps.append(step_result)
                if step_result.status != "passed":
                    restore_stage.status = "failed"
                    report.failed_phase = "restore"
                    result_code = ExitCode.REHEARSAL_FAILED
                    break
            else:
                restore_stage.status = "passed"
            restore_stage.duration_ms = _elapsed_ms(restore_started)

            if result_code == ExitCode.OK:
                baseline_stage = _run_checked_stage(
                    "baseline", prepared.client, baseline_path, recipe.checks
                )
                report.stages.append(baseline_stage)
                if baseline_stage.status != "passed":
                    report.failed_phase = "baseline"
                    result_code = ExitCode.REHEARSAL_FAILED

            if result_code == ExitCode.OK:
                candidate_stage = _run_checked_stage(
                    "candidate", prepared.client, candidate_path, recipe.checks
                )
                report.stages.append(candidate_stage)
                if candidate_stage.status != "passed":
                    report.failed_phase = "candidate"
                    result_code = ExitCode.REHEARSAL_FAILED
        except RehearsalError:
            _mark_active_stage_failed(report)
            report.failed_phase = report.stages[-1].name if report.stages else "startup"
            result_code = ExitCode.REHEARSAL_FAILED
        finally:
            if resources_may_exist and active_config is not None:
                try:
                    prepared.client.cleanup(active_config)
                    report.cleanup = "passed"
                except RehearsalError:
                    report.cleanup = "failed"
                    report.warnings.append(
                        "automatic cleanup was refused or failed; inspect resources by the "
                        "report run_id"
                    )
                    result_code = ExitCode.ENVIRONMENT_ERROR
            else:
                report.cleanup = "not_needed"

    report.status = "passed" if result_code == ExitCode.OK else "failed"
    report.finished_at = _now()
    if report_path is not None:
        write_report(report_path, report)
    return RunOutcome(report=report, exit_code=result_code)


def _new_report(recipe: Recipe, prepared: PreparedRun) -> RunReport:
    backups = []
    for backup in recipe.backups:
        item = {
            "name": backup.name,
            "kind": "file" if backup.path.is_file() else "directory",
            "digest_verified": backup.sha256 is not None,
        }
        if backup.sha256 is not None:
            item["sha256"] = backup.sha256
        backups.append(item)
    return RunReport(
        run_id=prepared.run_id,
        recipe=recipe.name,
        started_at=_now(),
        transformations=list(prepared.plan.transformations),
        warnings=list(prepared.plan.warnings),
        candidate_services=sorted(recipe.candidate.images),
        backup_inputs=backups,
    )


def _run_restore_step(
    client: ComposeClient,
    config: Path,
    recipe: Recipe,
    step: CommandStep,
) -> StepResult:
    started = time.monotonic()
    result = None
    stdin_path = recipe.backup_by_name[step.stdin_from].path if step.stdin_from else None
    for attempt in range(1, step.retries + 1):
        result = client.exec(
            config,
            step.service,
            step.command,
            timeout=step.timeout,
            stdin_path=stdin_path,
        )
        if result.returncode == 0:
            return StepResult(
                name=step.name,
                service=step.service,
                status="passed",
                attempts=attempt,
                duration_ms=_elapsed_ms(started),
                exit_code=0,
            )
        if attempt < step.retries:
            time.sleep(step.interval)
    return StepResult(
        name=step.name,
        service=step.service,
        status="failed",
        attempts=step.retries,
        duration_ms=_elapsed_ms(started),
        exit_code=None if result is None else result.returncode,
    )


def _run_checked_stage(
    name: str,
    client: ComposeClient,
    config: Path,
    checks: tuple[Check, ...],
) -> StageResult:
    stage = StageResult(name=name)
    started = time.monotonic()
    try:
        client.start(config, wait=True)
    except RehearsalError:
        stage.status = "failed"
        stage.duration_ms = _elapsed_ms(started)
        return stage
    for check in checks:
        result = _run_check(client, config, check)
        stage.steps.append(result)
        if result.status != "passed":
            stage.status = "failed"
            stage.duration_ms = _elapsed_ms(started)
            return stage
    stage.status = "passed"
    stage.duration_ms = _elapsed_ms(started)
    return stage


def _run_check(client: ComposeClient, config: Path, check: Check) -> StepResult:
    started = time.monotonic()
    result = None
    for attempt in range(1, check.retries + 1):
        if check.kind == "health":
            result = client.health(config, check.service, timeout=check.timeout)
        else:
            result = client.exec(
                config,
                check.service,
                check.command,
                timeout=check.timeout,
            )
        if result.returncode == 0:
            return StepResult(
                name=check.name,
                service=check.service,
                status="passed",
                attempts=attempt,
                duration_ms=_elapsed_ms(started),
                exit_code=0,
            )
        if attempt < check.retries:
            time.sleep(check.interval)
    return StepResult(
        name=check.name,
        service=check.service,
        status="failed",
        attempts=check.retries,
        duration_ms=_elapsed_ms(started),
        exit_code=None if result is None else result.returncode,
    )


def _mark_active_stage_failed(report: RunReport) -> None:
    if report.stages and report.stages[-1].status in {"pending", "running"}:
        report.stages[-1].status = "failed"


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
