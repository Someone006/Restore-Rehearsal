"""Run bounded Project Brain benchmarks and emit one deterministic JSON result document."""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from benchmarks.generate_project import generate_project


def _run(command: list[str], *, cwd: Path) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    elapsed = time.perf_counter() - started
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
        detail = detail.replace(str(cwd), "<benchmark-workspace>")
        if len(detail) > 4_000:
            detail = "..." + detail[-3_997:]
        raise RuntimeError(f"benchmark command exited with status {completed.returncode}: {detail}")
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("benchmark command returned a non-object JSON value")
    return payload, elapsed


def run_benchmark(files: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="rehearse-benchmark-") as raw:
        workspace = Path(raw)
        project = generate_project(workspace / "project", files)
        first = workspace / "first.rrbrain"
        second = workspace / "second.rrbrain"
        executable = [sys.executable, "-m", "restore_rehearsal"]
        first_result, first_seconds = _run(
            [*executable, "brain", "build", str(project), "--output", str(first), "--json"],
            cwd=project,
        )
        second_result, second_seconds = _run(
            [*executable, "brain", "build", str(project), "--output", str(second), "--json"],
            cwd=project,
        )
        summary, query_seconds = _run(
            [*executable, "brain", "summary", str(first), "--json"], cwd=project
        )
        query, search_seconds = _run(
            [*executable, "brain", "query", str(first), "module_00001", "--json"],
            cwd=project,
        )
        first_digest = first_result.get("semantic_digest")
        if not isinstance(first_digest, str) or first_digest != second_result.get(
            "semantic_digest"
        ):
            raise RuntimeError("benchmark brain builds were not deterministic")
        return {
            "schema": "rehearse.dev/benchmark/v1",
            "files_requested": files,
            "brain_bytes": first.stat().st_size,
            "semantic_digest": first_digest,
            "counts": summary.get("counts", {}),
            "query_nodes": len(query.get("nodes", [])),
            "seconds": {
                "build_first": round(first_seconds, 6),
                "build_second": round(second_seconds, 6),
                "summary": round(query_seconds, 6),
                "query": round(search_seconds, 6),
            },
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=1_000)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    print(json.dumps(run_benchmark(arguments.files), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
