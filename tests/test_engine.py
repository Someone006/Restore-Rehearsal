from __future__ import annotations

import json

from restore_rehearsal.compose import CommandResult, CommandRunner
from restore_rehearsal.engine import run_rehearsal
from restore_rehearsal.errors import ExitCode


class EngineRunner(CommandRunner):
    def __init__(self, model: dict, *, fail_first_exec: bool = False) -> None:
        self.model = model
        self.fail_first_exec = fail_first_exec
        self.exec_count = 0
        self.calls: list[list[str]] = []
        self.rendered_models: list[dict] = []

    def run(self, argv, **kwargs) -> CommandResult:
        call = list(argv)
        self.calls.append(call)
        if call[-3:] == ["config", "--format", "json"]:
            return CommandResult(0, json.dumps(self.model), "")
        if "up" in call:
            config = call[call.index("--file") + 1]
            with open(config, encoding="utf-8") as stream:
                self.rendered_models.append(json.load(stream))
            return CommandResult(0, "", "")
        if "exec" in call:
            self.exec_count += 1
            if self.fail_first_exec and self.exec_count == 1:
                return CommandResult(7, "SYNTHETIC_CANARY_123", "token=SYNTHETIC_CANARY_123")
            return CommandResult(0, "", "")
        return CommandResult(0, "", "")


def test_engine_runs_restore_baseline_candidate_and_cleanup(
    compose_model: dict, recipe_factory
) -> None:
    runner = EngineRunner(compose_model)

    outcome = run_rehearsal(recipe_factory(), runner=runner)

    assert outcome.exit_code == ExitCode.OK
    assert [stage.name for stage in outcome.report.stages] == ["restore", "baseline", "candidate"]
    assert all(stage.status == "passed" for stage in outcome.report.stages)
    assert outcome.report.cleanup == "passed"
    assert sum("up" in call for call in runner.calls) == 3
    assert sum("exec" in call for call in runner.calls) == 3
    assert any("down" in call for call in runner.calls)
    assert runner.rendered_models[0]["services"]["app"]["image"] == "example/app:1"
    assert runner.rendered_models[-1]["services"]["app"]["image"].endswith("a" * 64)


def test_restore_failure_skips_candidate_but_cleans_and_never_reports_output(
    compose_model: dict, recipe_factory
) -> None:
    runner = EngineRunner(compose_model, fail_first_exec=True)

    outcome = run_rehearsal(recipe_factory(), runner=runner)
    serialized = json.dumps(outcome.report.to_dict())

    assert outcome.exit_code == ExitCode.REHEARSAL_FAILED
    assert [stage.name for stage in outcome.report.stages] == ["restore"]
    assert outcome.report.cleanup == "passed"
    assert any("down" in call for call in runner.calls)
    assert "SYNTHETIC_CANARY_123" not in serialized
