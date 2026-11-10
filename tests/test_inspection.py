from __future__ import annotations

import hashlib
import stat
from pathlib import Path

import pytest

from restore_rehearsal.errors import EnvironmentError, SafetyError
from restore_rehearsal.inspection import (
    PLAN_SCHEMA,
    REPORT_SCHEMA,
    SURFACES,
    build_inspection_plan,
    build_inspection_report,
    load_inspection_json,
    validate_inspection_report,
    verify_inspection_evidence,
    write_inspection_json,
)


def test_universal_plan_covers_every_surface_without_execution_flags(tmp_path: Path) -> None:
    plan = build_inspection_plan(tmp_path, surface="mixed")

    assert plan["schema"] == PLAN_SCHEMA
    assert {item["surface"] for item in plan["checks"]} == set(SURFACES) - {"mixed"}
    assert any(item["visual"] for item in plan["checks"])
    assert any(not item["visual"] for item in plan["checks"])
    assert plan["guardrails"] == {
        "automatic_execution": False,
        "automatic_network": False,
        "credentials_online": False,
        "private_evidence": True,
        "outbound_consent_required": True,
    }
    assert str(tmp_path.parent) not in str(plan)


@pytest.mark.parametrize(
    ("target", "surface"),
    [
        ("guide.md", "docs"),
        ("results.parquet", "data"),
        ("desktop.app", "native"),
        ("service.py", "code"),
    ],
)
def test_plan_detects_local_surface_without_opening_target(target: str, surface: str) -> None:
    plan = build_inspection_plan(target)

    assert plan["surface"] == surface
    assert plan["target"]["location"] == target


def test_web_plan_removes_query_fragment_and_refuses_embedded_credentials() -> None:
    plan = build_inspection_plan("https://example.invalid/app?token=SYNTHETIC#private")

    assert plan["surface"] == "web"
    assert plan["target"]["location"] == "https://example.invalid/app"
    assert "SYNTHETIC" not in str(plan)
    with pytest.raises(SafetyError, match="without credentials"):
        build_inspection_plan("https://user:password@example.invalid/app")


def test_windows_drive_target_is_local_and_privacy_minimized() -> None:
    plan = build_inspection_plan(r"D:\private-parent\project\app.py", surface="code")

    assert plan["target"] == {"kind": "local", "label": "app.py", "location": "app.py"}
    assert "private-parent" not in str(plan)


def test_explicit_non_http_url_is_refused_but_colon_filename_is_local() -> None:
    with pytest.raises(SafetyError, match=r"HTTP\(S\)"):
        build_inspection_plan("ftp://example.invalid/project")

    plan = build_inspection_plan("design:notes.md", surface="docs")
    assert plan["target"]["kind"] == "local"


def test_private_report_round_trip_refuses_overwrite(tmp_path: Path) -> None:
    plan = build_inspection_plan("guide.md")
    captured_at = plan["created_at"]
    report = build_inspection_report(
        plan,
        checks=[
            {
                "id": "docs-render",
                "status": "warning",
                "summary": "Mobile rendering needs review.",
                "evidence": ["mobile-shot"],
            }
        ],
        evidence=[
            {
                "id": "mobile-shot",
                "kind": "screenshot",
                "label": "Mobile documentation view",
                "path": "evidence/mobile.png",
                "sha256": "a" * 64,
                "captured_at": captured_at,
            }
        ],
        issues=[
            {
                "id": "mobile-overflow",
                "severity": "medium",
                "title": "Code sample overflows",
                "description": "The example is wider than the inspected mobile viewport.",
                "remediation": "Wrap or scroll long examples without hiding content.",
                "check_id": "docs-render",
                "evidence": ["mobile-shot"],
            }
        ],
    )
    output = tmp_path / "inspection.json"

    write_inspection_json(output, report)

    assert report["schema"] == REPORT_SCHEMA
    assert report["status"] == "warning"
    assert load_inspection_json(output) == report
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    with pytest.raises(EnvironmentError, match="refusing to overwrite"):
        write_inspection_json(output, report)


def test_report_rejects_raw_fields_secrets_and_evidence_path_escape() -> None:
    plan = build_inspection_plan("tool.py", surface="code")
    report = build_inspection_report(
        plan,
        checks=[
            {
                "id": "code-tests",
                "status": "passed",
                "summary": "Selected tests passed.",
                "evidence": [],
            }
        ],
    )
    report["raw_output"] = "must not be stored"
    with pytest.raises(SafetyError, match="unexpected fields"):
        validate_inspection_report(report)

    with pytest.raises(SafetyError, match="credential"):
        build_inspection_report(
            plan,
            checks=[
                {
                    "id": "code-tests",
                    "status": "failed",
                    "summary": "github_pat_abcdefghijklmnopqrstuvwxyz0123456789",
                    "evidence": [],
                }
            ],
        )

    with pytest.raises(SafetyError, match="must be relative"):
        build_inspection_report(
            plan,
            checks=[
                {
                    "id": "code-tests",
                    "status": "passed",
                    "summary": "Selected tests passed.",
                    "evidence": ["escape"],
                }
            ],
            evidence=[
                {
                    "id": "escape",
                    "kind": "artifact",
                    "label": "Escaping evidence",
                    "path": "../outside.txt",
                    "sha256": "b" * 64,
                    "captured_at": plan["created_at"],
                }
            ],
        )


def test_report_plan_digest_must_match_selected_plan() -> None:
    first = build_inspection_plan("first.py", surface="code")
    second = build_inspection_plan("second.py", surface="code")
    report = build_inspection_report(
        first,
        checks=[
            {
                "id": "code-structure",
                "status": "passed",
                "summary": "Boundaries were inspected.",
                "evidence": [],
            }
        ],
    )

    with pytest.raises(SafetyError, match="does not match"):
        validate_inspection_report(report, expected_plan=second)


def test_evidence_files_are_digest_verified_without_following_links(tmp_path: Path) -> None:
    plan = build_inspection_plan("guide.md", surface="docs")
    evidence = tmp_path / "mobile.png"
    evidence.write_bytes(b"synthetic screenshot bytes")
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    report = build_inspection_report(
        plan,
        checks=[
            {
                "id": "docs-render",
                "status": "passed",
                "summary": "Rendered evidence was captured.",
                "evidence": ["mobile-shot"],
            }
        ],
        evidence=[
            {
                "id": "mobile-shot",
                "kind": "screenshot",
                "label": "Mobile view",
                "path": "mobile.png",
                "sha256": digest,
                "captured_at": plan["created_at"],
            }
        ],
    )
    report_path = tmp_path / "inspection.json"
    write_inspection_json(report_path, report)

    assert verify_inspection_evidence(report_path)["verified_files"] == 1
    evidence.write_bytes(b"changed")
    with pytest.raises(SafetyError, match="digest does not match"):
        verify_inspection_evidence(report_path)
