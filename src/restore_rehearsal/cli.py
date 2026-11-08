"""Command-line interface for Restore Rehearsal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from restore_rehearsal import __version__
from restore_rehearsal.engine import prepare, run_rehearsal
from restore_rehearsal.errors import ExitCode, RehearsalError
from restore_rehearsal.recipe import load_recipe
from restore_rehearsal.redaction import Redactor
from restore_rehearsal.skill import install_skill, skill_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rehearse",
        description=(
            "Restore a backup into an isolated Compose stack, then test a candidate upgrade."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate",
        help="render, safety-check, and show the plan without creating Docker resources",
    )
    validate.add_argument("recipe", type=Path)
    validate.add_argument("--json", action="store_true", help="print a machine-readable summary")

    run = subparsers.add_parser("run", help="execute the restore and upgrade rehearsal")
    run.add_argument("recipe", type=Path)
    run.add_argument(
        "--report",
        type=Path,
        help="create a private JSON report (existing files are never overwritten)",
    )
    run.add_argument("--json", action="store_true", help="print the share-safe report as JSON")

    skill = subparsers.add_parser("skill", help="show or install the portable Agent Skill")
    skill_commands = skill.add_subparsers(dest="skill_command", required=True)
    skill_commands.add_parser("show", help="print the bundled SKILL.md")
    install = skill_commands.add_parser(
        "install", help="install into an agent's skills directory without overwriting"
    )
    install.add_argument(
        "target",
        type=Path,
        help="parent skills directory, such as .agents/skills or .github/skills",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "skill":
        try:
            if arguments.skill_command == "show":
                print(skill_text(), end="")
            else:
                install_skill(arguments.target)
                print("Agent Skill installed without overwriting existing files")
            return int(ExitCode.OK)
        except RehearsalError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return int(exc.exit_code)

    recipe_path: Path = arguments.recipe
    unresolved_recipe = recipe_path.expanduser().resolve(strict=False)
    redactor = Redactor(
        private_paths=[unresolved_recipe, unresolved_recipe.parent, Path.cwd().resolve()]
    )
    try:
        recipe = load_recipe(recipe_path)
        redactor = Redactor(
            private_paths=[
                recipe.source_path,
                recipe.source_path.parent,
                *(item.path for item in recipe.backups),
                *recipe.compose.files,
            ]
        )
        if arguments.command == "validate":
            prepared = prepare(recipe)
            summary = {
                "valid": True,
                "recipe": recipe.name,
                "candidate_services": sorted(recipe.candidate.images),
                "transformations": list(prepared.plan.transformations),
                "warnings": list(prepared.plan.warnings),
            }
            if arguments.json:
                print(json.dumps(summary, indent=2, sort_keys=True))
            else:
                print(f"valid: {recipe.name}")
                print(f"candidate services: {', '.join(summary['candidate_services'])}")
                for item in summary["transformations"]:
                    print(f"  isolate: {item}")
                for item in summary["warnings"]:
                    print(f"  warning: {item}")
            return int(ExitCode.OK)

        outcome = run_rehearsal(recipe, report_path=arguments.report)
        if arguments.json:
            print(json.dumps(outcome.report.to_dict(), indent=2, sort_keys=True))
        else:
            print(f"rehearsal: {outcome.report.status}")
            for stage in outcome.report.stages:
                print(f"  {stage.name}: {stage.status}")
                for step in stage.steps:
                    print(f"    {step.name}: {step.status} ({step.attempts} attempt(s))")
            print(f"  cleanup: {outcome.report.cleanup}")
            if arguments.report is not None:
                print("  report: created with private file permissions")
        return int(outcome.exit_code)
    except RehearsalError as exc:
        print(f"error: {redactor.redact(exc)}", file=sys.stderr)
        return int(exc.exit_code)
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130
    except Exception:
        print("error: unexpected internal failure", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)


if __name__ == "__main__":
    raise SystemExit(main())
