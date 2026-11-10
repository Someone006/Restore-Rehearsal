"""Generate a pinned Homebrew formula from an immutable source distribution."""

from __future__ import annotations

import argparse
import hashlib
import re
import tomllib
from pathlib import Path
from urllib.parse import urlparse

FORMULA = """class RestoreRehearsal < Formula
  include Language::Python::Virtualenv

  desc "Local project evidence, inspection, and isolated restore rehearsals"
  homepage "https://github.com/Someone006/Restore-Rehearsal"
  url "{source_url}"
  sha256 "{source_sha256}"
  license "Apache-2.0"

{resources}
  depends_on "python@3.12"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "rehearse {version}", shell_output("#{{bin}}/rehearse --version")
    assert_match "project-brain", shell_output("#{{bin}}/rehearse skill list")
  end
end
"""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _locked_resources(project_root: Path) -> list[dict[str, str]]:
    with (project_root / "uv.lock").open("rb") as source:
        lock = tomllib.load(source)
    packages = {str(item["name"]): item for item in lock["package"]}
    project = packages["restore-rehearsal"]
    direct = sorted(str(item["name"]) for item in project.get("dependencies", []))
    resources: list[dict[str, str]] = []
    for name in direct:
        package = packages[name]
        sdist = package.get("sdist")
        if not isinstance(sdist, dict):
            raise RuntimeError(f"locked dependency {name} has no source distribution")
        url = str(sdist.get("url", ""))
        digest = str(sdist.get("hash", ""))
        if not url.startswith("https://") or not digest.startswith("sha256:"):
            raise RuntimeError(f"locked dependency {name} lacks a pinned HTTPS source")
        resources.append(
            {
                "name": name,
                "url": url,
                "sha256": digest.removeprefix("sha256:"),
            }
        )
    return resources


def _resource_blocks(project_root: Path) -> str:
    blocks = []
    for item in _locked_resources(project_root):
        blocks.append(
            "\n".join(
                (
                    f'  resource "{item["name"]}" do',
                    f'    url "{item["url"]}"',
                    f'    sha256 "{item["sha256"]}"',
                    "  end",
                )
            )
        )
    return "\n\n".join(blocks) + "\n"


def render_formula(
    project_root: Path,
    source_distribution: Path,
    source_url: str,
    version: str,
) -> str:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9.-]*)?", version):
        raise ValueError("formula version is invalid")
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("formula source URL must be an HTTPS URL without credentials")
    source = source_distribution.resolve(strict=True)
    if not source.is_file() or not source.name.endswith(".tar.gz"):
        raise ValueError("formula source distribution must be a .tar.gz file")
    if version not in source.name:
        raise ValueError("formula version does not match the source distribution name")
    return FORMULA.format(
        source_url=source_url,
        source_sha256=sha256_file(source),
        resources=_resource_blocks(project_root.resolve(strict=True)),
        version=version,
    )


def write_formula(
    project_root: Path,
    source_distribution: Path,
    source_url: str,
    version: str,
    output: Path,
) -> Path:
    rendered = render_formula(project_root, source_distribution, source_url, version)
    destination = output.expanduser().resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8", newline="\n") as target:
            target.write(rendered)
    except FileExistsError as exc:
        raise RuntimeError("formula output exists; refusing to overwrite it") from exc
    return destination


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--sdist", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    print(
        write_formula(
            arguments.project_root,
            arguments.sdist,
            arguments.url,
            arguments.version,
            arguments.output,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
