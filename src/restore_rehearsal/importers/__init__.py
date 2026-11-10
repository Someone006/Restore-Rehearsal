"""Bounded built-in evidence importer registry and dispatch helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from restore_rehearsal.errors import SafetyError
from restore_rehearsal.importers.ci import CiImporter
from restore_rehearsal.importers.common import MAX_ARTIFACT_BYTES, read_artifact
from restore_rehearsal.importers.coverage import CoverageImporter
from restore_rehearsal.importers.junit import JunitImporter
from restore_rehearsal.importers.kubernetes import KubernetesImporter
from restore_rehearsal.importers.openapi import OpenApiImporter
from restore_rehearsal.importers.sarif import SarifImporter
from restore_rehearsal.importers.sbom import SbomImporter
from restore_rehearsal.importers.terraform import TerraformImporter
from restore_rehearsal.plugins.model import ArtifactImporter, ImportBundle, ImportContext

BUILTIN_IMPORTERS: tuple[ArtifactImporter, ...] = (
    CiImporter(),
    CoverageImporter(),
    JunitImporter(),
    KubernetesImporter(),
    OpenApiImporter(),
    SarifImporter(),
    SbomImporter(),
    TerraformImporter(),
)


def importer_catalog() -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "id": importer.id,
            "kind": importer.kind,
            "version": importer.version,
            "description": importer.description,
        }
        for importer in sorted(BUILTIN_IMPORTERS, key=lambda item: item.id)
    )


def select_importer(source_name: str, data: bytes, *, kind: str = "auto") -> ArtifactImporter:
    if not data:
        raise SafetyError("artifact is empty")
    if len(data) > MAX_ARTIFACT_BYTES:
        raise SafetyError("artifact exceeds the import size limit")
    if kind != "auto":
        matches = [
            item
            for item in BUILTIN_IMPORTERS
            if kind in {item.kind, item.id, item.id.removeprefix("builtin.")}
        ]
        if not matches:
            raise SafetyError("unknown artifact importer type")
        return matches[0]
    scored = sorted(
        (
            (max(0, min(int(importer.detect(source_name, data)), 100)), importer.id, importer)
            for importer in BUILTIN_IMPORTERS
        ),
        key=lambda item: (-item[0], item[1]),
    )
    if not scored or scored[0][0] <= 0:
        raise SafetyError("artifact type could not be detected; select an importer explicitly")
    return scored[0][2]


def import_bytes(source_name: str, data: bytes, *, kind: str = "auto") -> ImportBundle:
    importer = select_importer(source_name, data, kind=kind)
    safe_name = Path(source_name).name or "artifact"
    context = ImportContext.from_bytes(safe_name, importer.kind, data)
    try:
        bundle = importer.parse(context, data)
    except SafetyError:
        raise
    except (TypeError, ValueError, KeyError, OverflowError, RecursionError) as exc:
        raise SafetyError("artifact could not be normalized safely") from exc
    bundle.validate()
    return bundle


def import_file(path: Path, *, kind: str = "auto") -> ImportBundle:
    raw = read_artifact(path)
    return import_bytes(path.name, raw, kind=kind)


__all__ = [
    "BUILTIN_IMPORTERS",
    "import_bytes",
    "import_file",
    "importer_catalog",
    "select_importer",
]
