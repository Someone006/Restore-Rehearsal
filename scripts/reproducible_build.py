"""Build release distributions twice and refuse non-reproducible output."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_version(project_root: Path) -> str:
    with (project_root / "pyproject.toml").open("rb") as source:
        return str(tomllib.load(source)["project"]["version"])


def source_date_epoch(project_root: Path) -> str:
    configured = os.environ.get("SOURCE_DATE_EPOCH")
    if configured:
        if not configured.isdigit():
            raise ValueError("SOURCE_DATE_EPOCH must contain only decimal digits")
        return configured
    result = subprocess.run(
        ["git", "-C", str(project_root), "log", "-1", "--format=%ct"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    value = result.stdout.strip()
    if not value.isdigit():
        raise RuntimeError("Git did not return a valid commit timestamp")
    return value


def distribution_hashes(directory: Path) -> dict[str, str]:
    artifacts = sorted((*directory.glob("*.whl"), *directory.glob("*.tar.gz")))
    if not artifacts:
        raise RuntimeError("build produced no wheel or source distribution")
    return {path.name: sha256_file(path) for path in artifacts}


def _build(project_root: Path, output: Path, epoch: str) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONHASHSEED": "0",
            "SOURCE_DATE_EPOCH": epoch,
            "TZ": "UTC",
        }
    )
    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(output), str(project_root)],
        check=True,
        env=environment,
        timeout=300,
    )


def reproducible_build(
    project_root: Path,
    output: Path,
    *,
    expected_version: str | None = None,
) -> dict[str, str]:
    root = project_root.resolve(strict=True)
    version = project_version(root)
    if expected_version is not None and version != expected_version:
        raise RuntimeError(
            f"tag/package version mismatch: expected {expected_version}, found {version}"
        )
    epoch = source_date_epoch(root)
    with (
        tempfile.TemporaryDirectory(prefix="rehearse-build-a-") as first_raw,
        tempfile.TemporaryDirectory(prefix="rehearse-build-b-") as second_raw,
    ):
        first = Path(first_raw)
        second = Path(second_raw)
        _build(root, first, epoch)
        _build(root, second, epoch)
        first_hashes = distribution_hashes(first)
        second_hashes = distribution_hashes(second)
        if first_hashes != second_hashes:
            names = sorted(set(first_hashes) | set(second_hashes))
            changed = [name for name in names if first_hashes.get(name) != second_hashes.get(name)]
            raise RuntimeError("release distributions are not reproducible: " + ", ".join(changed))
        output.mkdir(parents=True, exist_ok=True)
        if any(output.iterdir()):
            raise RuntimeError("release output directory must be empty")
        for name in sorted(first_hashes):
            shutil.copyfile(first / name, output / name)
    return first_hashes


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-version")
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    hashes = reproducible_build(
        arguments.project_root,
        arguments.output,
        expected_version=arguments.expected_version,
    )
    for name, digest in sorted(hashes.items()):
        print(f"{digest}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
