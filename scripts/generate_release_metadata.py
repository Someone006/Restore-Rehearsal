"""Generate deterministic checksums and SPDX/CycloneDX release SBOMs."""

from __future__ import annotations

import argparse
import json
import os
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.reproducible_build import project_version, sha256_file

LICENSES = {
    "pathspec": "MPL-2.0",
    "pyyaml": "MIT",
    "restore-rehearsal": "Apache-2.0",
}


def locked_packages(project_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with (project_root / "uv.lock").open("rb") as source:
        lock = tomllib.load(source)
    packages = {str(item["name"]): item for item in lock["package"]}
    project = packages["restore-rehearsal"]
    direct_names = sorted(str(item["name"]) for item in project.get("dependencies", []))
    return project, [packages[name] for name in direct_names]


def _created_at() -> str:
    raw_epoch = os.environ.get("SOURCE_DATE_EPOCH", "0")
    if not raw_epoch.isdigit():
        raise ValueError("SOURCE_DATE_EPOCH must contain only decimal digits")
    timestamp = datetime.fromtimestamp(int(raw_epoch), tz=UTC).replace(microsecond=0)
    return timestamp.isoformat().replace("+00:00", "Z")


def _purl(name: str, version: str) -> str:
    return f"pkg:pypi/{name.replace('_', '-').lower()}@{version}"


def _package_record(item: dict[str, Any]) -> dict[str, str]:
    name = str(item["name"])
    version = str(item["version"])
    sdist = item.get("sdist", {})
    return {
        "name": name,
        "version": version,
        "license": LICENSES.get(name, "NOASSERTION"),
        "purl": _purl(name, version),
        "download": str(sdist.get("url", "NOASSERTION")),
    }


def _artifact_properties(artifacts: Path) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for path in sorted((*artifacts.glob("*.whl"), *artifacts.glob("*.tar.gz"))):
        values.append(
            {
                "name": f"rehearse.dev/release-sha256/{path.name}",
                "value": sha256_file(path),
            }
        )
    if not values:
        raise RuntimeError("release metadata requires a wheel or source distribution")
    return values


def cyclonedx_document(project_root: Path, artifacts: Path) -> dict[str, Any]:
    project, dependencies = locked_packages(project_root)
    project_record = _package_record(project)
    dependency_records = [_package_record(item) for item in dependencies]
    serial_seed = f"{project_record['name']}:{project_record['version']}"
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, serial_seed)}",
        "version": 1,
        "metadata": {
            "timestamp": _created_at(),
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": "rehearse-release-metadata",
                        "version": "1",
                    }
                ]
            },
            "component": {
                "type": "application",
                "bom-ref": project_record["purl"],
                "name": project_record["name"],
                "version": project_record["version"],
                "purl": project_record["purl"],
                "licenses": [{"license": {"id": project_record["license"]}}],
                "properties": _artifact_properties(artifacts),
            },
        },
        "components": [
            {
                "type": "library",
                "bom-ref": item["purl"],
                "name": item["name"],
                "version": item["version"],
                "purl": item["purl"],
                "licenses": [{"license": {"id": item["license"]}}],
                "externalReferences": [{"type": "distribution", "url": item["download"]}],
            }
            for item in dependency_records
        ],
        "dependencies": [
            {
                "ref": project_record["purl"],
                "dependsOn": [item["purl"] for item in dependency_records],
            },
            *({"ref": item["purl"], "dependsOn": []} for item in dependency_records),
        ],
    }


def _spdx_id(name: str) -> str:
    safe = "".join(character if character.isalnum() else "-" for character in name)
    return f"SPDXRef-Package-{safe}"


def spdx_document(project_root: Path) -> dict[str, Any]:
    project, dependencies = locked_packages(project_root)
    records = [_package_record(project), *(_package_record(item) for item in dependencies)]
    project_record = records[0]
    namespace_seed = f"{project_record['name']}:{project_record['version']}"
    packages = []
    for item in records:
        packages.append(
            {
                "SPDXID": _spdx_id(item["name"]),
                "name": item["name"],
                "versionInfo": item["version"],
                "downloadLocation": item["download"],
                "filesAnalyzed": False,
                "licenseConcluded": item["license"],
                "licenseDeclared": item["license"],
                "copyrightText": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": item["purl"],
                    }
                ],
            }
        )
    relationships = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": _spdx_id(project_record["name"]),
        }
    ]
    relationships.extend(
        {
            "spdxElementId": _spdx_id(project_record["name"]),
            "relationshipType": "DEPENDS_ON",
            "relatedSpdxElement": _spdx_id(item["name"]),
        }
        for item in records[1:]
    )
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{project_record['name']}-{project_record['version']}",
        "documentNamespace": (
            f"https://rehearse.dev/spdx/{uuid.uuid5(uuid.NAMESPACE_URL, namespace_seed)}"
        ),
        "creationInfo": {
            "created": _created_at(),
            "creators": ["Tool: rehearse-release-metadata-1"],
        },
        "packages": packages,
        "relationships": relationships,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def write_release_metadata(project_root: Path, artifacts: Path) -> tuple[Path, Path, Path]:
    root = project_root.resolve(strict=True)
    destination = artifacts.resolve(strict=True)
    version = project_version(root)
    cyclonedx_path = destination / f"restore-rehearsal-{version}.cyclonedx.json"
    spdx_path = destination / f"restore-rehearsal-{version}.spdx.json"
    _write_json(cyclonedx_path, cyclonedx_document(root, destination))
    _write_json(spdx_path, spdx_document(root))

    manifest = destination / "SHA256SUMS"
    files = sorted(path for path in destination.iterdir() if path.is_file() and path != manifest)
    manifest.write_text(
        "".join(f"{sha256_file(path)}  {path.name}\n" for path in files),
        encoding="utf-8",
    )
    return cyclonedx_path, spdx_path, manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--artifacts", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    for path in write_release_metadata(arguments.project_root, arguments.artifacts):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
