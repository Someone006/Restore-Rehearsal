from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from restore_rehearsal.errors import EnvironmentError, SafetyError
from restore_rehearsal.importers import (
    import_bytes,
    importer_catalog,
    select_importer,
)
from restore_rehearsal.importers.common import xml_document, yaml_documents
from restore_rehearsal.plugins import ImportBundle, plugin_catalog, plugin_doctor
from restore_rehearsal.plugins.registry import PluginRegistry
from restore_rehearsal.project_init import detect_project, initialize_project

PRIVATE_CANARY = "SYNTHETIC_PRIVATE_IMPORT_CANARY"


def test_all_eight_builtin_importers_normalize_without_private_bodies() -> None:
    artifacts: tuple[tuple[str, bytes, str], ...] = (
        (
            "results.sarif",
            json.dumps(
                {
                    "version": "2.1.0",
                    "runs": [
                        {
                            "tool": {
                                "driver": {
                                    "name": "demo-analyzer",
                                    "rules": [{"id": "DEMO001"}],
                                }
                            },
                            "results": [
                                {
                                    "ruleId": "DEMO001",
                                    "level": "error",
                                    "message": {"text": "Unsafe input"},
                                    "locations": [
                                        {
                                            "physicalLocation": {
                                                "artifactLocation": {"uri": "src/app.py"}
                                            }
                                        }
                                    ],
                                    "fixes": [{"description": {"text": PRIVATE_CANARY}}],
                                }
                            ],
                        }
                    ],
                }
            ).encode(),
            "sarif",
        ),
        (
            "bom.json",
            json.dumps(
                {
                    "bomFormat": "CycloneDX",
                    "specVersion": "1.6",
                    "components": [
                        {
                            "bom-ref": "pkg:pypi/demo@1",
                            "name": "demo",
                            "version": "1",
                            "type": "library",
                            "externalReferences": [{"url": PRIVATE_CANARY}],
                        }
                    ],
                }
            ).encode(),
            "sbom",
        ),
        (
            "junit.xml",
            (
                '<testsuite name="unit" tests="1" failures="1">'
                '<testcase name="fails" classname="demo" file="tests/test_demo.py">'
                f"<failure>{PRIVATE_CANARY}</failure><system-out>{PRIVATE_CANARY}</system-out>"
                "</testcase></testsuite>"
            ).encode(),
            "junit",
        ),
        (
            "coverage.info",
            (
                "TN:demo\nSF:src/app.py\nDA:1,1," + PRIVATE_CANARY + "\nLF:2\nLH:1\nend_of_record\n"
            ).encode(),
            "coverage",
        ),
        (
            "openapi.yaml",
            (
                "openapi: 3.1.0\ninfo:\n  title: Demo\n  version: 1\npaths:\n"
                "  /items:\n    get:\n      operationId: listItems\n"
                f"      example: {PRIVATE_CANARY}\n"
                "      responses:\n        '200':\n          description: ok\n"
                "          content:\n            application/json:\n              schema:\n"
                "                $ref: '#/components/schemas/Item'\n"
                "components:\n  schemas:\n    Item:\n      type: object\n"
                "      properties:\n        id:\n          type: string\n"
            ).encode(),
            "openapi",
        ),
        (
            "tfplan.json",
            json.dumps(
                {
                    "format_version": "1.2",
                    "terraform_version": "1.10",
                    "resource_changes": [
                        {
                            "address": "aws_db_instance.demo",
                            "mode": "managed",
                            "type": "aws_db_instance",
                            "provider_name": "registry.terraform.io/hashicorp/aws",
                            "change": {
                                "actions": ["delete", "create"],
                                "before": {"password": PRIVATE_CANARY},
                                "after": {"password": PRIVATE_CANARY},
                                "after_sensitive": {"password": True},
                            },
                        }
                    ],
                }
            ).encode(),
            "terraform",
        ),
        (
            "k8s.yaml",
            (
                "apiVersion: v1\nkind: Secret\nmetadata:\n  name: demo-secret\n"
                f"data:\n  token: {PRIVATE_CANARY}\n---\n"
                "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\n"
                f"  annotations:\n    private: {PRIVATE_CANARY}\nspec:\n  selector:\n"
                "    matchLabels:\n      app: web\n  template:\n    metadata:\n"
                "      labels:\n        app: web\n    spec:\n      containers:\n"
                "        - name: web\n          image: example.invalid/web:1\n"
                "          env:\n            - name: TOKEN\n"
                f"              value: {PRIVATE_CANARY}\n"
            ).encode(),
            "kubernetes",
        ),
        (
            "ci-result.json",
            json.dumps(
                {
                    "schema": "rehearse.dev/ci-result/v1",
                    "name": "CI",
                    "jobs": [
                        {
                            "name": "tests",
                            "status": "failed",
                            "duration_seconds": 12,
                            "log": PRIVATE_CANARY,
                            "environment": {"TOKEN": PRIVATE_CANARY},
                        }
                    ],
                }
            ).encode(),
            "ci",
        ),
    )

    assert {item["kind"] for item in importer_catalog()} == {
        "ci",
        "coverage",
        "junit",
        "kubernetes",
        "openapi",
        "sarif",
        "sbom",
        "terraform",
    }
    for name, raw, expected_kind in artifacts:
        bundle = import_bytes(name, raw)
        encoded = bundle.to_json()

        assert bundle.kind == expected_kind
        assert bundle.source_name == name
        assert PRIVATE_CANARY not in encoded
        assert ImportBundle.from_payload(json.loads(encoded)) == bundle


def test_importers_refuse_entities_aliases_unknown_types_and_traversal() -> None:
    with pytest.raises(SafetyError, match="entities"):
        xml_document(b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>')
    with pytest.raises(SafetyError, match="aliases"):
        yaml_documents(b"first: &value secret\nsecond: *value\n")
    deeply_nested = ("[" * 2_000 + "value" + "]" * 2_000).encode()
    with pytest.raises(SafetyError, match="bounded UTF-8 YAML"):
        yaml_documents(deeply_nested)
    with pytest.raises(SafetyError, match="could not be detected"):
        select_importer("unknown.bin", b"opaque bytes")

    bundle = import_bytes(
        "results.sarif",
        json.dumps(
            {
                "version": "2.1.0",
                "runs": [
                    {
                        "tool": {"driver": {"name": "demo"}},
                        "results": [
                            {
                                "ruleId": "DEMO",
                                "message": {"text": "finding"},
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {"uri": "../../private.txt"}
                                        }
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ).encode(),
    )
    assert all(node.path is None for node in bundle.nodes)


def test_project_init_detects_plain_project_and_refuses_overwrite(tmp_path: Path) -> None:
    root = tmp_path / "plain-project"
    root.mkdir()
    _write(root / "pyproject.toml", '[project]\nname="demo"\ndependencies=["fastapi"]\n')
    _write(
        root / "compose.yaml",
        "services:\n  db:\n    image: postgres:17\n  api:\n    image: example.invalid/api:1\n",
    )
    _write(root / ".github" / "workflows" / "ci.yml", "jobs: {}\n")
    _write(root / "tests" / "test_api.py", "def test_api(): pass\n")
    _write(root / "reports" / "results.sarif", "{}\n")
    _write(root / ".env", f"API_KEY={PRIVATE_CANARY}\n")

    profile = detect_project(root)
    preview = initialize_project(root, dry_run=True)
    encoded = json.dumps(preview, sort_keys=True)

    assert profile.inventory_mode == "filesystem"
    assert profile.ecosystems == ("python",)
    assert "fastapi" in profile.frameworks
    assert "postgresql" in profile.database_hints
    assert "github-actions" in profile.ci_systems
    assert "sarif" in profile.suggested_importers
    assert str(root) not in encoded
    assert PRIVATE_CANARY not in encoded
    assert not (root / "rehearse-project.yaml").exists()

    result = initialize_project(root)

    assert result["config"] == "rehearse-project.yaml"
    assert stat.S_IMODE((root / "rehearse-project.yaml").stat().st_mode) == 0o600
    assert stat.S_IMODE((root / ".rehearseignore").stat().st_mode) == 0o600
    assert PRIVATE_CANARY not in (root / "rehearse-project.yaml").read_text()
    with pytest.raises(EnvironmentError, match="refusing to overwrite"):
        initialize_project(root)
    with pytest.raises(SafetyError, match="inside"):
        initialize_project(root, output=Path("../escape.yaml"), create_ignore=False)


def test_plugin_metadata_is_safe_and_external_code_is_not_automatically_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded = False

    class FakeEntryPoint:
        name = "external-demo"
        dist = SimpleNamespace(name="external-package", version="1.0")

        def load(self) -> object:
            nonlocal loaded
            loaded = True
            raise AssertionError("must not load during discovery")

    class FakeEntryPoints:
        def select(self, *, group: str) -> list[FakeEntryPoint]:
            return [FakeEntryPoint()] if group == "rehearse.importers" else []

    monkeypatch.setattr(
        "restore_rehearsal.plugins.registry.metadata.entry_points",
        lambda: FakeEntryPoints(),
    )

    catalog = plugin_catalog(include_external=True)
    external = next(item for item in catalog if item["id"] == "external-demo")
    diagnosis = plugin_doctor("external-demo", kind="importer")

    assert len(PluginRegistry().importers()) == 0
    assert external["enabled"] is False
    assert diagnosis["executed"] is False
    assert loaded is False


def test_external_plugin_activation_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = False

    class ExternalImporter:
        api_version = "1"
        id = "external-demo"
        version = "1.0"
        description = "Synthetic explicit importer"
        kind = "synthetic"

        def detect(self, source_name: str, data: bytes) -> int:
            return 0

        def parse(self, context: object, data: bytes) -> object:
            raise NotImplementedError

    class FakeEntryPoint:
        name = "external-demo"
        dist = SimpleNamespace(name="external-package", version="1.0")

        def load(self) -> object:
            nonlocal loaded
            loaded = True
            return ExternalImporter

    class FakeEntryPoints:
        def select(self, *, group: str) -> list[FakeEntryPoint]:
            return [FakeEntryPoint()] if group == "rehearse.importers" else []

    monkeypatch.setattr(
        "restore_rehearsal.plugins.registry.metadata.entry_points",
        lambda: FakeEntryPoints(),
    )
    registry = PluginRegistry()

    descriptor = registry.activate_external("importer", "external-demo")

    assert loaded is True
    assert descriptor.enabled is True
    assert registry.importer("external-demo").kind == "synthetic"


def test_external_plugin_metadata_cannot_inject_terminal_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnsafeEntryPoint:
        name = "unsafe\nplugin"
        dist = SimpleNamespace(name="unsafe\x1b[31m", version="1\n2")

    class SafeEntryPoint:
        name = "safe-demo"
        dist = SimpleNamespace(name="unsafe\x1b[31m", version="1\n2")

    class FakeEntryPoints:
        def select(self, *, group: str) -> list[object]:
            return [UnsafeEntryPoint(), SafeEntryPoint()] if group == "rehearse.importers" else []

    monkeypatch.setattr(
        "restore_rehearsal.plugins.registry.metadata.entry_points",
        lambda: FakeEntryPoints(),
    )

    external = [item for item in plugin_catalog() if item["source"] != "builtin"]

    assert [item["id"] for item in external] == ["safe-demo"]
    assert external[0]["source"] == "external"
    assert external[0]["version"] == "unknown"

    unsafe_plugin = SimpleNamespace(
        id="unsafe-description",
        version="1",
        description="unsafe\x1b[31m",
        api_version="1",
    )
    with pytest.raises(SafetyError, match="description"):
        PluginRegistry().register_importer(unsafe_plugin)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
