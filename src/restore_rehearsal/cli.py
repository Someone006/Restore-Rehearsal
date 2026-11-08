"""Command-line interface for Restore Rehearsal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from restore_rehearsal import __version__
from restore_rehearsal.brain.api import (
    brain_context,
    brain_doctor,
    brain_findings,
    brain_forget,
    brain_memories,
    brain_query,
    brain_record_run,
    brain_remember,
    brain_summary,
    build_brain,
    lens_catalog,
)
from restore_rehearsal.brain.visual import write_visualization
from restore_rehearsal.engine import prepare, run_rehearsal
from restore_rehearsal.errors import ExitCode, RehearsalError
from restore_rehearsal.recipe import load_recipe
from restore_rehearsal.redaction import Redactor
from restore_rehearsal.skill import SKILL_NAMES, install_skill, skill_text


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

    brain = subparsers.add_parser(
        "brain", help="build and query a local one-file project evidence graph"
    )
    brain_commands = brain.add_subparsers(dest="brain_command", required=True)
    brain_build = brain_commands.add_parser(
        "build", help="scan a Git repository and atomically build or update its brain"
    )
    brain_build.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    brain_build.add_argument("--output", "-o", type=Path, default=Path("project.rrbrain"))
    brain_build.add_argument("--json", action="store_true")

    brain_summary_parser = brain_commands.add_parser(
        "summary", help="show graph and evidence counts"
    )
    brain_summary_parser.add_argument("brain", type=Path)
    brain_summary_parser.add_argument("--json", action="store_true")

    brain_query_parser = brain_commands.add_parser(
        "query", help="search artifacts, findings, and memories"
    )
    brain_query_parser.add_argument("brain", type=Path)
    brain_query_parser.add_argument("query_text")
    brain_query_parser.add_argument("--limit", type=int, default=20)
    brain_query_parser.add_argument("--json", action="store_true")

    brain_context_parser = brain_commands.add_parser(
        "context", help="return a bounded relationship neighborhood for an agent"
    )
    brain_context_parser.add_argument("brain", type=Path)
    brain_context_parser.add_argument(
        "target", help="exact node key, relative path, or unique name"
    )
    brain_context_parser.add_argument("--depth", type=int, default=2)
    brain_context_parser.add_argument("--json", action="store_true")

    brain_findings_parser = brain_commands.add_parser(
        "findings", help="list unresolved evidence-backed review findings"
    )
    brain_findings_parser.add_argument("brain", type=Path)
    brain_findings_parser.add_argument("--lens")
    brain_findings_parser.add_argument("--severity")
    brain_findings_parser.add_argument("--json", action="store_true")

    brain_remember_parser = brain_commands.add_parser(
        "remember", help="append a decision, attempt, failure, fix, note, or risk acceptance"
    )
    brain_remember_parser.add_argument("brain", type=Path)
    brain_remember_parser.add_argument(
        "--kind",
        required=True,
        choices=("decision", "attempt", "failure", "fix", "note", "risk-acceptance"),
    )
    brain_remember_parser.add_argument("--title", required=True)
    brain_remember_parser.add_argument("--path", dest="related_path")
    brain_remember_parser.add_argument(
        "--supersedes", help="active memory ID replaced by this new evidence"
    )

    brain_memories_parser = brain_commands.add_parser(
        "memories", help="list durable decisions, attempts, failures, fixes, and notes"
    )
    brain_memories_parser.add_argument("brain", type=Path)
    brain_memories_parser.add_argument(
        "--kind",
        choices=("decision", "attempt", "failure", "fix", "note", "risk-acceptance"),
    )
    brain_memories_parser.add_argument("--status", choices=("active", "superseded", "deleted"))
    brain_memories_parser.add_argument("--json", action="store_true")

    brain_forget_parser = brain_commands.add_parser(
        "forget", help="securely redact one deliberately recorded memory"
    )
    brain_forget_parser.add_argument("brain", type=Path)
    brain_forget_parser.add_argument("memory_id")

    brain_run_parser = brain_commands.add_parser(
        "record-run", help="import a share-safe Rehearse JSON report"
    )
    brain_run_parser.add_argument("brain", type=Path)
    brain_run_parser.add_argument("report", type=Path)

    brain_visual_parser = brain_commands.add_parser(
        "visualize", help="write a self-contained offline HTML graph"
    )
    brain_visual_parser.add_argument("brain", type=Path)
    brain_visual_parser.add_argument(
        "--output", "-o", type=Path, default=Path("project-brain.html")
    )
    brain_visual_parser.add_argument("--json", action="store_true")

    brain_doctor_parser = brain_commands.add_parser(
        "doctor", help="validate the SQLite format and relationship integrity"
    )
    brain_doctor_parser.add_argument("brain", type=Path)
    brain_doctor_parser.add_argument("--json", action="store_true")

    brain_lenses = brain_commands.add_parser("lenses", help="list the ten bundled review lenses")
    brain_lenses.add_argument("--json", action="store_true")

    skill = subparsers.add_parser("skill", help="show or install the portable Agent Skill")
    skill_commands = skill.add_subparsers(dest="skill_command", required=True)
    skill_commands.add_parser("list", help="list the bundled Agent Skills")
    show = skill_commands.add_parser("show", help="print a bundled SKILL.md")
    show.add_argument("--name", choices=SKILL_NAMES, default="restore-rehearsal")
    install = skill_commands.add_parser(
        "install", help="install into an agent's skills directory without overwriting"
    )
    install.add_argument(
        "target",
        type=Path,
        help="parent skills directory, such as .agents/skills or .github/skills",
    )
    install.add_argument("--name", choices=SKILL_NAMES, default="restore-rehearsal")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "skill":
        try:
            if arguments.skill_command == "list":
                for name in SKILL_NAMES:
                    print(name)
            elif arguments.skill_command == "show":
                print(skill_text(arguments.name), end="")
            else:
                install_skill(arguments.target, name=arguments.name)
                print(f"Agent Skill installed: {arguments.name}")
            return int(ExitCode.OK)
        except RehearsalError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return int(exc.exit_code)

    if arguments.command == "brain":
        return _run_brain(arguments)

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


def _run_brain(arguments: argparse.Namespace) -> int:
    private_paths = [Path.cwd().resolve()]
    for attribute in ("root", "brain", "report", "output"):
        value = getattr(arguments, attribute, None)
        if isinstance(value, Path):
            private_paths.append(value.expanduser().resolve(strict=False))
    redactor = Redactor(private_paths=private_paths)
    try:
        command = arguments.brain_command
        if command == "build":
            value = build_brain(arguments.root, arguments.output)
        elif command == "summary":
            value = brain_summary(arguments.brain)
        elif command == "query":
            value = brain_query(arguments.brain, arguments.query_text, limit=arguments.limit)
        elif command == "context":
            value = brain_context(arguments.brain, arguments.target, depth=arguments.depth)
        elif command == "findings":
            value = brain_findings(
                arguments.brain, lens=arguments.lens, severity=arguments.severity
            )
        elif command == "remember":
            if sys.stdin.isatty():
                raise RehearsalError("pipe the memory body on standard input")
            memory_id = brain_remember(
                arguments.brain,
                kind=arguments.kind,
                title=arguments.title,
                body=sys.stdin.read(),
                related_path=arguments.related_path,
                supersedes=arguments.supersedes,
            )
            value = {"recorded": True, "memory_id": memory_id}
        elif command == "memories":
            value = brain_memories(arguments.brain, kind=arguments.kind, status=arguments.status)
        elif command == "forget":
            brain_forget(arguments.brain, arguments.memory_id)
            value = {"forgotten": True, "memory_id": arguments.memory_id}
        elif command == "record-run":
            run_id = brain_record_run(arguments.brain, arguments.report)
            value = {"recorded": True, "run_id": run_id}
        elif command == "visualize":
            value = write_visualization(arguments.brain, arguments.output)
        elif command == "doctor":
            value = brain_doctor(arguments.brain)
        elif command == "lenses":
            value = list(lens_catalog())
        else:
            raise RehearsalError("unknown brain command")
        if getattr(arguments, "json", False) or command in {
            "query",
            "context",
            "findings",
            "memories",
            "doctor",
        }:
            print(json.dumps(value, indent=2, sort_keys=True))
        elif command == "lenses":
            for lens in value:
                print(f"{lens['id']}: {lens['name']} [{lens['category']}]")
        elif isinstance(value, dict):
            print("project brain: ok")
            for key, item in value.items():
                if key != "semantic_digest":
                    print(f"  {key}: {item}")
        return int(ExitCode.OK)
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
