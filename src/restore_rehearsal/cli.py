"""Command-line interface for Restore Rehearsal."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from restore_rehearsal import __version__
from restore_rehearsal.adapters import adapter_catalog, detect_adapters, propose_adapter
from restore_rehearsal.brain.api import (
    brain_context,
    brain_diff,
    brain_doctor,
    brain_findings,
    brain_forget,
    brain_impact,
    brain_import,
    brain_imports,
    brain_memories,
    brain_query,
    brain_record_import,
    brain_record_run,
    brain_remember,
    brain_remove_import,
    brain_snapshots,
    brain_summary,
    build_brain,
    lens_catalog,
)
from restore_rehearsal.brain.visual import write_visualization
from restore_rehearsal.engine import prepare, run_rehearsal
from restore_rehearsal.errors import (
    EnvironmentError as RehearsalEnvironmentError,
)
from restore_rehearsal.errors import ExitCode, RehearsalError, SafetyError
from restore_rehearsal.importers import import_file, importer_catalog
from restore_rehearsal.inspection import (
    SURFACES,
    build_inspection_plan,
    load_inspection_json,
    verify_inspection_evidence,
    write_inspection_json,
)
from restore_rehearsal.mcp_server import serve_mcp
from restore_rehearsal.plugins import ImportBundle, PluginRegistry, plugin_catalog, plugin_doctor
from restore_rehearsal.project_init import initialize_project, load_project_config
from restore_rehearsal.recipe import load_recipe
from restore_rehearsal.redaction import Redactor
from restore_rehearsal.skill import (
    DEFAULT_SKILL,
    SKILL_NAMES,
    install_all_skills,
    install_skill,
    skill_catalog,
    skill_doctor,
    skill_text,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rehearse",
        description=(
            "Build local project evidence, inspect changes, and rehearse isolated recovery."
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

    initialize = subparsers.add_parser(
        "init", help="detect any local project and create private starter configuration"
    )
    initialize.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    initialize.add_argument(
        "--output", type=Path, default=Path("rehearse-project.yaml"), help="config path inside ROOT"
    )
    initialize.add_argument(
        "--ignore-output",
        type=Path,
        default=Path(".rehearseignore"),
        help="ignore path inside ROOT",
    )
    initialize.add_argument(
        "--no-ignore", action="store_true", help="do not create a .rehearseignore starter"
    )
    initialize_mode = initialize.add_mutually_exclusive_group()
    initialize_mode.add_argument(
        "--dry-run", action="store_true", help="detect and print the proposed files without writing"
    )
    initialize_mode.add_argument(
        "--check", action="store_true", help="validate an existing --output config without writing"
    )
    initialize.add_argument("--json", action="store_true")

    brain = subparsers.add_parser(
        "brain", help="build and query a local one-file project evidence graph"
    )
    brain_commands = brain.add_subparsers(dest="brain_command", required=True)
    brain_build = brain_commands.add_parser(
        "build", help="scan any local project folder and atomically build or update its brain"
    )
    brain_build.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    brain_build.add_argument("--output", "-o", type=Path, default=Path("project.rrbrain"))
    brain_build.add_argument("--label", help="optional private label for a new snapshot")
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

    brain_snapshots_parser = brain_commands.add_parser(
        "snapshots", help="list bounded structural history stored in the brain"
    )
    brain_snapshots_parser.add_argument("brain", type=Path)
    brain_snapshots_parser.add_argument("--json", action="store_true")

    brain_diff_parser = brain_commands.add_parser("diff", help="compare two structural snapshots")
    brain_diff_parser.add_argument("brain", type=Path)
    brain_diff_parser.add_argument("--from", dest="from_snapshot")
    brain_diff_parser.add_argument("--to", dest="to_snapshot")
    brain_diff_parser.add_argument("--json", action="store_true")

    brain_impact_parser = brain_commands.add_parser(
        "impact", help="trace bounded relationship impact from snapshot changes"
    )
    brain_impact_parser.add_argument("brain", type=Path)
    brain_impact_parser.add_argument("--from", dest="from_snapshot")
    brain_impact_parser.add_argument("--to", dest="to_snapshot")
    brain_impact_parser.add_argument("--depth", type=int, default=2)
    brain_impact_parser.add_argument("--json", action="store_true")

    brain_import_parser = brain_commands.add_parser(
        "import", help="normalize and persist a bounded local evidence artifact"
    )
    brain_import_parser.add_argument("brain", type=Path)
    brain_import_parser.add_argument("artifact", type=Path)
    brain_import_parser.add_argument(
        "--type",
        dest="import_type",
        default="auto",
        help="auto, sarif, sbom, junit, coverage, openapi, terraform, kubernetes, or ci",
    )
    brain_import_parser.add_argument("--json", action="store_true")

    brain_imports_parser = brain_commands.add_parser(
        "imports", help="list persistent normalized evidence imports"
    )
    brain_imports_parser.add_argument("brain", type=Path)
    brain_imports_parser.add_argument("--json", action="store_true")

    brain_show_import_parser = brain_commands.add_parser(
        "show-import", help="show one normalized evidence import"
    )
    brain_show_import_parser.add_argument("brain", type=Path)
    brain_show_import_parser.add_argument("import_id")
    brain_show_import_parser.add_argument("--json", action="store_true")

    brain_remove_import_parser = brain_commands.add_parser(
        "remove-import", help="delete one exact normalized import by identifier"
    )
    brain_remove_import_parser.add_argument("brain", type=Path)
    brain_remove_import_parser.add_argument("import_id")
    brain_remove_import_parser.add_argument("--json", action="store_true")

    brain_importers_parser = brain_commands.add_parser(
        "importers", help="list built-in bounded evidence importers"
    )
    brain_importers_parser.add_argument("--json", action="store_true")

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

    inspect = subparsers.add_parser(
        "inspect", help="plan and validate local evidence for any project surface"
    )
    inspect_commands = inspect.add_subparsers(dest="inspect_command", required=True)
    inspect_plan = inspect_commands.add_parser(
        "plan", help="create a local-only inspection plan without opening the target"
    )
    inspect_plan.add_argument("target")
    inspect_plan.add_argument("--surface", choices=("auto", *SURFACES), default="auto")
    inspect_plan.add_argument("--output", "-o", type=Path)
    inspect_plan.add_argument("--json", action="store_true")
    inspect_validate = inspect_commands.add_parser(
        "validate", help="validate an inspection plan or report"
    )
    inspect_validate.add_argument("document", type=Path)
    inspect_validate.add_argument(
        "--verify-evidence",
        action="store_true",
        help="also verify every file-backed evidence digest in a report",
    )
    inspect_validate.add_argument("--json", action="store_true")

    adapters = subparsers.add_parser(
        "adapters", help="inspect offline recovery-adapter capabilities"
    )
    adapter_commands = adapters.add_subparsers(dest="adapter_command", required=True)
    adapter_list = adapter_commands.add_parser("list", help="list built-in recovery adapters")
    adapter_list.add_argument("--json", action="store_true")
    adapter_detect = adapter_commands.add_parser(
        "detect", help="suggest adapters from bounded local filenames"
    )
    adapter_detect.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    adapter_detect.add_argument("--backup", type=Path)
    adapter_detect.add_argument("--json", action="store_true")
    adapter_propose = adapter_commands.add_parser(
        "propose", help="create a non-executing offline restore proposal"
    )
    adapter_propose.add_argument("adapter_id")
    adapter_propose.add_argument("--backup", type=Path)
    adapter_propose.add_argument("--format", dest="detected_format", default="user-selected")
    adapter_propose.add_argument("--json", action="store_true")

    mcp = subparsers.add_parser("mcp", help="serve bounded Project Brain tools over local stdio")
    mcp_commands = mcp.add_subparsers(dest="mcp_command", required=True)
    mcp_serve = mcp_commands.add_parser("serve", help="start the local stdio MCP server")
    mcp_serve.add_argument("brain", type=Path)
    mcp_serve.add_argument(
        "--allow-write",
        action="store_true",
        help="expose safe memory writes; destructive operations remain unavailable",
    )

    skill = subparsers.add_parser("skill", help="show or install the portable Agent Skill")
    skill_commands = skill.add_subparsers(dest="skill_command", required=True)
    skill_list = skill_commands.add_parser("list", help="list the bundled Agent Skills")
    skill_list.add_argument("--json", action="store_true")
    show = skill_commands.add_parser("show", help="print a bundled SKILL.md")
    show.add_argument("--name", choices=SKILL_NAMES, default=DEFAULT_SKILL)
    install = skill_commands.add_parser(
        "install", help="install into an agent's skills directory without overwriting"
    )
    install.add_argument(
        "target",
        type=Path,
        help="parent skills directory, such as .agents/skills or .github/skills",
    )
    install_choice = install.add_mutually_exclusive_group()
    install_choice.add_argument("--name", choices=SKILL_NAMES, default=DEFAULT_SKILL)
    install_choice.add_argument("--all", action="store_true", help="install all bundled skills")
    skill_doctor_parser = skill_commands.add_parser(
        "doctor", help="validate every bundled skill and reference"
    )
    skill_doctor_parser.add_argument("--json", action="store_true")

    plugin = subparsers.add_parser(
        "plugin", help="inspect plugins or explicitly activate trusted third-party code"
    )
    plugin_commands = plugin.add_subparsers(dest="plugin_command", required=True)
    plugin_list = plugin_commands.add_parser(
        "list", help="list plugin metadata without loading external code"
    )
    plugin_list.add_argument(
        "--builtins-only", action="store_true", help="omit installed third-party entry points"
    )
    plugin_list.add_argument("--json", action="store_true")
    plugin_doctor_parser = plugin_commands.add_parser(
        "doctor", help="check one plugin's metadata without loading external code"
    )
    plugin_doctor_parser.add_argument("plugin_id")
    plugin_doctor_parser.add_argument(
        "--kind", choices=("importer", "analyzer", "restore-provider")
    )
    plugin_doctor_parser.add_argument("--json", action="store_true")
    plugin_activate = plugin_commands.add_parser(
        "activate", help="explicitly load and validate one trusted external plugin"
    )
    plugin_activate.add_argument("plugin_id")
    plugin_activate.add_argument(
        "--kind", required=True, choices=("importer", "analyzer", "restore-provider")
    )
    plugin_activate.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "skill":
        try:
            if arguments.skill_command == "list":
                if arguments.json:
                    print(json.dumps(skill_catalog(), indent=2, sort_keys=True))
                else:
                    for item in skill_catalog():
                        print(f"{item['name']}: {item['description']}")
            elif arguments.skill_command == "show":
                print(skill_text(arguments.name), end="")
            elif arguments.skill_command == "doctor":
                report = skill_doctor()
                if arguments.json:
                    print(json.dumps(report, indent=2, sort_keys=True))
                else:
                    print(f"Agent Skills: {'ok' if report['ok'] else 'invalid'}")
                    print(f"  skills: {report['skill_count']}")
                    print(f"  files: {report['file_count']}")
                    for issue in report["issues"]:
                        print(f"  issue: {issue}")
                if not report["ok"]:
                    return int(ExitCode.UNSAFE_CONFIGURATION)
            else:
                if arguments.all:
                    installed = install_all_skills(arguments.target)
                    print(f"Agent Skills installed: {len(installed)}")
                else:
                    install_skill(arguments.target, name=arguments.name)
                    print(f"Agent Skill installed: {arguments.name}")
            return int(ExitCode.OK)
        except RehearsalError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return int(exc.exit_code)
        except KeyboardInterrupt:
            print("error: interrupted", file=sys.stderr)
            return 130
        except Exception:
            print("error: unexpected internal failure", file=sys.stderr)
            return int(ExitCode.INTERNAL_ERROR)

    if arguments.command == "brain":
        return _run_brain(arguments)

    if arguments.command == "init":
        return _run_init(arguments)

    if arguments.command == "plugin":
        return _run_plugin(arguments)

    if arguments.command == "inspect":
        return _run_inspect(arguments)

    if arguments.command == "adapters":
        return _run_adapters(arguments)

    if arguments.command == "mcp":
        try:
            serve_mcp(arguments.brain, allow_write=arguments.allow_write)
            return int(ExitCode.OK)
        except RehearsalError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return int(exc.exit_code)
        except KeyboardInterrupt:
            print("error: interrupted", file=sys.stderr)
            return 130
        except Exception:
            print("error: unexpected internal failure", file=sys.stderr)
            return int(ExitCode.INTERNAL_ERROR)

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
    for attribute in ("root", "brain", "report", "output", "artifact"):
        value = getattr(arguments, attribute, None)
        if isinstance(value, Path):
            private_paths.append(value.expanduser().resolve(strict=False))
    redactor = Redactor(private_paths=private_paths)
    try:
        command = arguments.brain_command
        if command == "build":
            value = build_brain(arguments.root, arguments.output, label=arguments.label)
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
                raise SafetyError("pipe the memory body on standard input")
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
        elif command == "snapshots":
            value = brain_snapshots(arguments.brain)
        elif command == "diff":
            value = brain_diff(
                arguments.brain,
                from_snapshot=arguments.from_snapshot,
                to_snapshot=arguments.to_snapshot,
            )
        elif command == "impact":
            value = brain_impact(
                arguments.brain,
                from_snapshot=arguments.from_snapshot,
                to_snapshot=arguments.to_snapshot,
                depth=arguments.depth,
            )
        elif command == "import":
            bundle = import_file(arguments.artifact, kind=arguments.import_type)
            import_id = brain_record_import(
                arguments.brain,
                kind=bundle.kind,
                source_name=bundle.source_name,
                source_sha256=bundle.source_sha256,
                importer_id=bundle.importer_id,
                importer_version=bundle.importer_version,
                summary=bundle.summary,
                fragments=_bundle_fragments(bundle),
            )
            value = {
                "recorded": True,
                "import_id": import_id,
                "kind": bundle.kind,
                "summary": bundle.summary,
            }
        elif command == "imports":
            value = brain_imports(arguments.brain)
        elif command == "show-import":
            value = brain_import(arguments.brain, arguments.import_id)
        elif command == "remove-import":
            brain_remove_import(arguments.brain, arguments.import_id)
            value = {"removed": True, "import_id": arguments.import_id}
        elif command == "importers":
            value = list(importer_catalog())
        elif command == "visualize":
            value = write_visualization(arguments.brain, arguments.output)
        elif command == "doctor":
            value = brain_doctor(arguments.brain)
        elif command == "lenses":
            value = list(lens_catalog())
        else:
            raise SafetyError("unknown brain command")
        if getattr(arguments, "json", False) or command in {
            "query",
            "context",
            "findings",
            "memories",
            "doctor",
            "snapshots",
            "diff",
            "impact",
            "import",
            "imports",
            "show-import",
            "remove-import",
            "importers",
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


def _bundle_fragments(bundle: ImportBundle) -> tuple[dict[str, Any], ...]:
    """Split normalized evidence into store-sized fragments without retaining raw input."""

    # Enforce the public normalized-payload bound before splitting the same data
    # into smaller SQLite rows. The raw source artifact is never retained.
    bundle.to_json()
    payload = bundle.to_payload()
    fragments: list[dict[str, Any]] = []
    maximum = 120 * 1024
    for section in ("nodes", "edges", "findings"):
        current: list[dict[str, Any]] = []
        for item in payload[section]:
            single = {
                "format": "rehearse.dev/import-fragment/v1",
                "section": section,
                "items": [item],
            }
            if len(_compact_json_bytes(single)) > maximum:
                raise SafetyError("normalized import item exceeds the storage limit")
            candidate = {
                "format": "rehearse.dev/import-fragment/v1",
                "section": section,
                "items": [*current, item],
            }
            size = len(_compact_json_bytes(candidate))
            if size > maximum and current:
                fragments.append(
                    {
                        "format": "rehearse.dev/import-fragment/v1",
                        "section": section,
                        "items": current,
                    }
                )
                current = [item]
            else:
                current.append(item)
        if current:
            fragments.append(
                {
                    "format": "rehearse.dev/import-fragment/v1",
                    "section": section,
                    "items": current,
                }
            )
    return tuple(fragments)


def _compact_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _run_init(arguments: argparse.Namespace) -> int:
    private_paths = [
        Path.cwd().resolve(),
        arguments.root.expanduser().resolve(strict=False),
        arguments.output.expanduser().resolve(strict=False),
        arguments.ignore_output.expanduser().resolve(strict=False),
    ]
    redactor = Redactor(private_paths=private_paths)
    try:
        if arguments.check:
            try:
                root = arguments.root.expanduser().resolve(strict=True)
            except OSError as exc:
                raise RehearsalEnvironmentError(
                    "initialization project could not be inspected"
                ) from exc
            candidate = arguments.output.expanduser()
            unresolved = candidate if candidate.is_absolute() else root / candidate
            try:
                config_path = unresolved.parent.resolve(strict=True) / unresolved.name
            except OSError as exc:
                raise RehearsalEnvironmentError(
                    "initialization config could not be inspected"
                ) from exc
            if not config_path.is_relative_to(root) or config_path == root:
                raise SafetyError("initialization output must stay inside the selected project")
            value: dict[str, Any] = {
                "valid": True,
                "config": config_path.name,
                "project": load_project_config(config_path),
            }
        else:
            value = initialize_project(
                arguments.root,
                output=arguments.output,
                ignore_output=arguments.ignore_output,
                create_ignore=not arguments.no_ignore,
                dry_run=arguments.dry_run,
            )
        if arguments.json or arguments.dry_run or arguments.check:
            print(json.dumps(value, indent=2, sort_keys=True))
        else:
            print("project initialization: created privately")
            print(f"  config: {value['config']}")
            if value["ignore"] is not None:
                print(f"  ignore: {value['ignore']}")
            profile = value["profile"]
            print(f"  inventory: {profile['inventory_mode']}")
            print(f"  ecosystems: {', '.join(profile['ecosystems']) or 'none detected'}")
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


def _run_plugin(arguments: argparse.Namespace) -> int:
    try:
        if arguments.plugin_command == "list":
            value: Any = list(plugin_catalog(include_external=not arguments.builtins_only))
        elif arguments.plugin_command == "doctor":
            value = plugin_doctor(arguments.plugin_id, kind=arguments.kind)
        else:
            descriptor = PluginRegistry().activate_external(arguments.kind, arguments.plugin_id)
            value = {**asdict(descriptor), "explicitly_activated": True}
        if getattr(arguments, "json", False):
            print(json.dumps(value, indent=2, sort_keys=True))
        elif isinstance(value, list):
            for item in value:
                status = "enabled" if item["enabled"] else "inactive"
                print(f"{item['kind']}/{item['id']}: {item['version']} [{status}]")
        else:
            print(f"plugin: {value['kind']}/{value['id']}")
            print(f"  source: {value['source']}")
            print(f"  compatible: {value.get('compatible', True)}")
            print(f"  executed: {value.get('executed', value.get('explicitly_activated', False))}")
        return int(ExitCode.OK)
    except RehearsalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return int(exc.exit_code)
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130
    except Exception:
        print("error: unexpected internal failure", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)


def _run_inspect(arguments: argparse.Namespace) -> int:
    try:
        if arguments.inspect_command == "plan":
            value = build_inspection_plan(arguments.target, surface=arguments.surface)
            if arguments.output is not None:
                write_inspection_json(arguments.output, value)
            if arguments.json or arguments.output is None:
                print(json.dumps(value, indent=2, sort_keys=True))
            else:
                print("inspection plan: created privately")
                print(f"  output: {arguments.output.name}")
                print(f"  surface: {value['surface']}")
                print(f"  checks: {len(value['checks'])}")
        else:
            document = load_inspection_json(arguments.document)
            value: dict[str, object] = {
                "valid": True,
                "schema": document["schema"],
                "surface": document["surface"],
            }
            if arguments.verify_evidence:
                value["evidence"] = verify_inspection_evidence(arguments.document)
            if arguments.json:
                print(json.dumps(value, indent=2, sort_keys=True))
            else:
                print("inspection document: valid")
                for key, item in value.items():
                    if key != "valid":
                        print(f"  {key}: {item}")
        return int(ExitCode.OK)
    except RehearsalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return int(exc.exit_code)
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130
    except Exception:
        print("error: unexpected internal failure", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)


def _run_adapters(arguments: argparse.Namespace) -> int:
    try:
        if arguments.adapter_command == "list":
            value = list(adapter_catalog())
        elif arguments.adapter_command == "detect":
            value = [item.to_dict() for item in detect_adapters(arguments.root, arguments.backup)]
        else:
            value = propose_adapter(
                arguments.adapter_id,
                backup=arguments.backup,
                detected_format=arguments.detected_format,
            ).to_dict()
        if getattr(arguments, "json", False):
            print(json.dumps(value, indent=2, sort_keys=True))
        elif isinstance(value, list):
            for item in value:
                identifier = item.get("id", item.get("adapter_id", "adapter"))
                name = item.get("name", item.get("detected_format", ""))
                print(f"{identifier}: {name}")
        else:
            print(f"adapter proposal: {value['adapter_id']}")
            print(f"  confidence: {value['confidence']}")
            print(f"  format: {value['detected_format']}")
            print(f"  offline: {value['restorable_offline']}")
        return int(ExitCode.OK)
    except RehearsalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return int(exc.exit_code)
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130
    except Exception:
        print("error: unexpected internal failure", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)


if __name__ == "__main__":
    raise SystemExit(main())
