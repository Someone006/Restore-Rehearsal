from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from restore_rehearsal.adapters import (
    adapter_catalog,
    detect_adapters,
    propose_adapter,
)
from restore_rehearsal.adapters import registry as adapter_registry
from restore_rehearsal.brain.api import build_brain
from restore_rehearsal.errors import SafetyError
from restore_rehearsal.mcp_server import MAX_REQUEST_BYTES, serve_mcp


def _brain(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("def ready():\n    return True\n", encoding="utf-8")
    brain = tmp_path / "project.rrbrain"
    build_brain(project, brain)
    return brain


def _serve(brain: Path, messages: list[dict[str, object]], *, allow_write: bool = False):
    source = io.StringIO("".join(f"{json.dumps(item)}\n" for item in messages))
    destination = io.StringIO()
    serve_mcp(brain, allow_write=allow_write, input=source, output=destination)
    return [json.loads(line) for line in destination.getvalue().splitlines()]


def test_mcp_transcript_is_bounded_local_and_read_only_by_default(tmp_path: Path) -> None:
    brain = _brain(tmp_path)
    responses = _serve(
        brain,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "synthetic", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "brain_summary", "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "recovery_adapters", "arguments": {}},
            },
        ],
    )

    assert [item["id"] for item in responses] == [1, 2, 3, 4]
    listed = {item["name"] for item in responses[1]["result"]["tools"]}
    assert "brain_summary" in listed
    assert "brain_remember" not in listed
    assert "recovery_adapters" in listed
    assert responses[2]["result"]["isError"] is False
    assert responses[2]["result"]["structuredContent"]["counts"]["nodes"] >= 2
    assert len(responses[3]["result"]["structuredContent"]) == 6
    assert str(tmp_path) not in json.dumps(responses)


def test_mcp_write_tool_requires_explicit_opt_in(tmp_path: Path) -> None:
    brain = _brain(tmp_path)
    listed = _serve(
        brain,
        [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}],
        allow_write=True,
    )[0]
    names = {item["name"] for item in listed["result"]["tools"]}
    assert "brain_remember" in names

    response = _serve(
        brain,
        [
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "brain_remember",
                    "arguments": {
                        "kind": "decision",
                        "title": "Keep the local boundary",
                        "body": "No network service is required.",
                    },
                },
            }
        ],
        allow_write=True,
    )[0]
    assert response["result"]["isError"] is False
    assert response["result"]["structuredContent"]["recorded"] is True


def test_mcp_exposes_bounded_snapshot_diff_impact_and_import_queries(
    tmp_path: Path,
) -> None:
    brain = _brain(tmp_path)
    project = tmp_path / "project"
    (project / "app.py").write_text(
        "def ready():\n    return True\n\ndef changed():\n    return True\n",
        encoding="utf-8",
    )
    build_brain(project, brain)

    responses = _serve(
        brain,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "brain_snapshots", "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "brain_diff", "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "brain_impact", "arguments": {"depth": 1}},
            },
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "brain_imports", "arguments": {}},
            },
        ],
    )

    descriptors = {item["name"]: item for item in responses[0]["result"]["tools"]}
    assert {"brain_snapshots", "brain_diff", "brain_impact", "brain_imports"} <= set(descriptors)
    assert descriptors["brain_diff"]["inputSchema"]["additionalProperties"] is False
    snapshots = responses[1]["result"]["structuredContent"]
    assert snapshots["truncated"] is False
    assert len(snapshots["items"]) == 2
    diff = responses[2]["result"]["structuredContent"]
    assert diff["from"]["sequence"] == 1
    assert diff["to"]["sequence"] == 2
    impact = responses[3]["result"]["structuredContent"]
    assert impact["depth"] == 1
    assert impact["changed_node_count"] >= 1
    imports = responses[4]["result"]["structuredContent"]
    assert imports == {"items": [], "truncated": False}


def test_mcp_snapshot_tools_reject_invalid_or_unknown_arguments(tmp_path: Path) -> None:
    brain = _brain(tmp_path)
    responses = _serve(
        brain,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "brain_diff",
                    "arguments": {"from_snapshot": "../private"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "brain_impact", "arguments": {"depth": 4}},
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "brain_imports", "arguments": {"path": "x"}},
            },
        ],
    )

    assert all(item["result"]["isError"] is True for item in responses)
    assert "snapshot reference" in responses[0]["result"]["content"][0]["text"]
    assert "depth must be from 0 to 3" in responses[1]["result"]["content"][0]["text"]
    assert "unknown fields" in responses[2]["result"]["content"][0]["text"]


def test_mcp_rejects_malformed_oversized_and_unknown_requests(tmp_path: Path) -> None:
    brain = _brain(tmp_path)
    source = io.StringIO(
        "not-json\n"
        + ("{" + "x" * (MAX_REQUEST_BYTES + 10) + "}\n")
        + json.dumps({"jsonrpc": "2.0", "id": 3, "method": "unknown"})
        + "\n"
    )
    destination = io.StringIO()
    serve_mcp(brain, input=source, output=destination)
    responses = [json.loads(line) for line in destination.getvalue().splitlines()]

    assert responses[0]["error"]["code"] == -32700
    assert responses[1]["error"]["code"] == -32600
    assert responses[2]["error"]["code"] == -32601


def test_mcp_refuses_a_symlinked_brain(tmp_path: Path) -> None:
    brain = _brain(tmp_path)
    link = tmp_path / "linked.rrbrain"
    link.symlink_to(brain)

    with pytest.raises(SafetyError, match="must not be a symlink"):
        serve_mcp(link, input=io.StringIO(), output=io.StringIO())


def test_adapter_catalog_and_detection_are_offline_and_content_minimizing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "adapter-project"
    root.mkdir()
    canary = "SYNTHETIC_PRIVATE_BACKUP_CONTENT"
    for name in (
        "postgres.dump",
        "mysql-export.sql",
        "snapshot.sqlite3",
        "mongo.archive",
        "rds-provider-export.parquet",
    ):
        (root / name).write_text(canary, encoding="utf-8")
    (root / "s3-export").mkdir()
    (root / "s3-export" / "manifest.json").write_text(canary, encoding="utf-8")

    catalog = adapter_catalog()
    proposals = detect_adapters(root)
    ids = {item.adapter_id for item in proposals}
    encoded = json.dumps([item.to_dict() for item in proposals])

    assert {item["id"] for item in catalog} == {
        "postgres",
        "mysql",
        "sqlite",
        "mongodb",
        "object-storage",
        "managed-export",
    }
    assert {"postgres", "mysql", "sqlite", "mongodb", "object-storage", "managed-export"} <= ids
    assert canary not in encoded
    assert str(root) not in encoded
    assert all(not item.network_calls and not item.generated_secrets for item in proposals)


def test_adapter_proposals_refuse_unsafe_inputs_and_bound_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "bounded-project"
    root.mkdir()
    (root / "one.sql").write_text("synthetic", encoding="utf-8")
    (root / "two.sql").write_text("synthetic", encoding="utf-8")
    link = tmp_path / "project-link"
    link.symlink_to(root, target_is_directory=True)

    with pytest.raises(SafetyError, match="must not be a symlink"):
        detect_adapters(link)
    with pytest.raises(SafetyError, match="format is invalid"):
        propose_adapter("postgres", detected_format="bad\nformat")

    monkeypatch.setattr(adapter_registry, "MAX_DETECTION_ENTRIES", 1)
    with pytest.raises(SafetyError, match="entry limit"):
        detect_adapters(root)


def test_managed_native_export_never_claims_an_online_restore(tmp_path: Path) -> None:
    export = tmp_path / "rds-provider-export.parquet"
    export.write_text("synthetic", encoding="utf-8")
    proposal = propose_adapter(
        "managed-export", backup=export, detected_format="provider-native-snapshot"
    )

    assert proposal.restorable_offline is False
    assert proposal.network_calls is False
    assert proposal.generated_secrets is False
    assert any("local logical export" in note for note in proposal.notes)
