"""Generate a deterministic, non-secret synthetic project for Project Brain benchmarks."""

from __future__ import annotations

import argparse
from pathlib import Path

MAX_BENCHMARK_FILES = 20_000


def generate_project(destination: Path, files: int) -> Path:
    if not 1 <= files <= MAX_BENCHMARK_FILES:
        raise ValueError(f"files must be between 1 and {MAX_BENCHMARK_FILES}")
    root = destination.expanduser().resolve(strict=False)
    if root.exists():
        if not root.is_dir() or any(root.iterdir()):
            raise RuntimeError("benchmark destination must not exist or must be empty")
    else:
        root.mkdir(parents=True)
    metadata_files = min(files, 2)
    if metadata_files >= 1:
        (root / ".rehearseignore").write_text("ignored/\n", encoding="utf-8")
    if metadata_files >= 2:
        (root / "README.md").write_text("# Synthetic Rehearse benchmark\n", encoding="utf-8")
    remaining = files - metadata_files
    package_count = max(1, min(100, (remaining + 199) // 200))
    index = 0
    for package in range(package_count if remaining else 0):
        directory = root / "src" / f"component_{package:03d}"
        directory.mkdir(parents=True, exist_ok=True)
        while remaining and index % package_count == package:
            target = directory / f"module_{index:05d}.py"
            dependency = max(0, index - 1)
            target.write_text(
                f'"""Synthetic module {index}."""\n'
                f"PREVIOUS = {dependency}\n\n"
                f"def value_{index:05d}() -> int:\n"
                f"    return {index}\n",
                encoding="utf-8",
            )
            index += 1
            remaining -= 1
            if index % package_count != package:
                break
    # Fill any remainder using round-robin paths. This path is reached when the requested count is
    # not an even multiple of the component count.
    while remaining:
        package = index % package_count
        directory = root / "src" / f"component_{package:03d}"
        target = directory / f"module_{index:05d}.py"
        target.write_text(
            f"def value_{index:05d}() -> int:\n    return {index}\n",
            encoding="utf-8",
        )
        index += 1
        remaining -= 1
    return root


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--files", type=int, default=1_000)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    print(generate_project(arguments.destination, arguments.files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
