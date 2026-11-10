from __future__ import annotations

import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path

import pytest

from benchmarks.generate_project import generate_project
from benchmarks.run import _run
from scripts.export_audit_requirements import render_audit_requirements, write_audit_requirements
from scripts.generate_homebrew_formula import render_formula, write_formula
from scripts.generate_release_metadata import write_release_metadata
from scripts.reproducible_build import distribution_hashes, project_version, sha256_file


def _root() -> Path:
    return Path(__file__).parents[1]


def test_release_metadata_is_deterministic_and_self_verifying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1")
    version = project_version(_root())
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    artifacts = {
        f"restore_rehearsal-{version}-py3-none-any.whl": b"synthetic wheel",
        f"restore_rehearsal-{version}.tar.gz": b"synthetic source",
    }
    for directory in (first, second):
        for name, content in artifacts.items():
            (directory / name).write_bytes(content)
        write_release_metadata(_root(), directory)

    assert {item.name: item.read_bytes() for item in first.iterdir()} == {
        item.name: item.read_bytes() for item in second.iterdir()
    }
    cyclonedx = json.loads(next(first.glob("*.cyclonedx.json")).read_text(encoding="utf-8"))
    spdx = json.loads(next(first.glob("*.spdx.json")).read_text(encoding="utf-8"))
    assert cyclonedx["bomFormat"] == "CycloneDX"
    assert cyclonedx["metadata"]["timestamp"] == "1970-01-01T00:00:01Z"
    assert spdx["spdxVersion"] == "SPDX-2.3"
    manifest = (first / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    for line in manifest:
        digest, name = line.split("  ", 1)
        assert digest == sha256_file(first / name)


def test_audit_requirements_are_locked_and_refuse_overwrite(tmp_path: Path) -> None:
    rendered = render_audit_requirements(_root())

    assert rendered == "pathspec==0.12.1\npyyaml==6.0.3\n"
    output = tmp_path / "runtime.txt"
    assert write_audit_requirements(_root(), output) == output
    assert output.read_text(encoding="utf-8") == rendered
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        write_audit_requirements(_root(), output)


def test_container_selects_one_exact_package_version() -> None:
    dockerfile = (_root() / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG PACKAGE_VERSION=0.3.0" in dockerfile
    assert "restore_rehearsal-${PACKAGE_VERSION}-py3-none-any.whl" in dockerfile
    assert "COPY dist/*.whl" not in dockerfile


def test_homebrew_formula_uses_locked_sources_and_refuses_overwrite(tmp_path: Path) -> None:
    version = project_version(_root())
    sdist = tmp_path / f"restore_rehearsal-{version}.tar.gz"
    sdist.write_bytes(b"synthetic immutable source archive")
    url = (
        f"https://github.com/Someone006/Restore-Rehearsal/releases/download/v{version}/{sdist.name}"
    )

    formula = render_formula(_root(), sdist, url, version)

    assert f'url "{url}"' in formula
    assert f'sha256 "{hashlib.sha256(sdist.read_bytes()).hexdigest()}"' in formula
    assert 'resource "pathspec"' in formula
    assert 'resource "pyyaml"' in formula
    assert "virtualenv_install_with_resources" in formula
    output = tmp_path / "Formula" / "restore-rehearsal.rb"
    assert write_formula(_root(), sdist, url, version, output) == output
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        write_formula(_root(), sdist, url, version, output)
    with pytest.raises(ValueError, match="without credentials"):
        render_formula(_root(), sdist, "https://user:secret@example.invalid/a", version)


def test_distribution_hashes_and_project_version_are_bounded(tmp_path: Path) -> None:
    with (_root() / "pyproject.toml").open("rb") as source:
        expected = tomllib.load(source)["project"]["version"]
    assert project_version(_root()) == expected
    wheel = tmp_path / "package.whl"
    source = tmp_path / "package.tar.gz"
    wheel.write_bytes(b"wheel")
    source.write_bytes(b"source")
    assert distribution_hashes(tmp_path) == {
        "package.tar.gz": sha256_file(source),
        "package.whl": sha256_file(wheel),
    }


def test_synthetic_benchmark_project_is_deterministic_and_bounded(tmp_path: Path) -> None:
    first = generate_project(tmp_path / "first", 37)
    second = generate_project(tmp_path / "second", 37)
    first_files = {
        path.relative_to(first): path.read_bytes() for path in first.rglob("*") if path.is_file()
    }
    second_files = {
        path.relative_to(second): path.read_bytes() for path in second.rglob("*") if path.is_file()
    }
    assert first_files == second_files
    assert len(first_files) == 37
    assert len(list((first / "src").rglob("*.py"))) == 35
    with pytest.raises(RuntimeError, match="must be empty"):
        generate_project(first, 1)
    with pytest.raises(ValueError, match="between"):
        generate_project(tmp_path / "too-many", 20_001)

    boundary = generate_project(tmp_path / "boundary", 20_000)
    assert len([path for path in boundary.rglob("*") if path.is_file()]) == 20_000


def test_benchmark_failure_surfaces_bounded_redacted_diagnostic(tmp_path: Path) -> None:
    command = [
        sys.executable,
        "-c",
        "import sys; print(sys.argv[1] + 'x' * 5000, file=sys.stderr); raise SystemExit(7)",
        str(tmp_path),
    ]
    with pytest.raises(RuntimeError, match="status 7") as captured:
        _run(command, cwd=tmp_path)
    message = str(captured.value)
    assert str(tmp_path) not in message
    assert len(message) < 4_100


def test_workflow_actions_are_pinned_and_container_is_non_root() -> None:
    workflows = list((_root() / ".github" / "workflows").glob("*.yml"))
    uses = []
    for workflow in workflows:
        uses.extend(re.findall(r"uses:\s*([^\s#]+)", workflow.read_text(encoding="utf-8")))
    assert uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", value) for value in uses)
    security_workflow = (_root() / ".github" / "workflows" / "security.yml").read_text(
        encoding="utf-8"
    )
    assert "pip-audit --strict --requirement" in security_workflow
    assert "pip-audit --strict --skip-editable" not in security_workflow
    release_workflow = (_root() / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert "python -m scripts.generate_homebrew_formula" in release_workflow
    assert "working-directory: release" in release_workflow
    assert "sha256sum --check SHA256SUMS" in release_workflow
    dockerfile = (_root() / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^FROM [^\n]+@sha256:[0-9a-f]{64}$", dockerfile, re.MULTILINE)
    assert "USER 65532:65532" in dockerfile
    assert "/var/run/docker.sock" not in dockerfile
