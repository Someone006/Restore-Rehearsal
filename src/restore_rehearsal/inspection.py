"""Bounded, local-only inspection plans and evidence reports.

This module deliberately does not launch applications, execute project code, or contact URLs.
Agents and people may use their available browser, desktop, terminal, or test tools to perform the
plan, then record content-minimizing evidence in the validated report format.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from restore_rehearsal.errors import EnvironmentError, SafetyError

PLAN_SCHEMA = "rehearse.dev/inspection-plan/v1"
REPORT_SCHEMA = "rehearse.dev/inspection-report/v1"
SURFACES = ("web", "native", "cli", "api", "code", "docs", "data", "mixed")
CHECK_STATUSES = {"passed", "failed", "warning", "skipped", "not-run"}
REPORT_STATUSES = {"passed", "failed", "warning", "incomplete"}
EVIDENCE_KINDS = {
    "accessibility",
    "artifact",
    "console-summary",
    "network-summary",
    "screenshot",
    "test-result",
    "text-observation",
    "video",
}
SEVERITIES = {"low", "medium", "high", "critical"}
MAX_INSPECTION_BYTES = 1024 * 1024
MAX_EVIDENCE_FILE_BYTES = 64 * 1024 * 1024
MAX_CHECKS = 100
MAX_EVIDENCE = 500
MAX_ISSUES = 500
MAX_TEXT = 2_000

_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
)

_CHECKS: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "web": (
        ("web-responsive", "Check desktop, tablet, and mobile layout states.", True),
        (
            "web-interactions",
            "Check primary interactions, focus, loading, empty, and error states.",
            True,
        ),
        (
            "web-keyboard",
            "Check keyboard order, focus visibility, dialogs, and escape behavior.",
            True,
        ),
        ("web-accessibility", "Check names, roles, contrast, zoom, and reduced motion.", True),
        ("web-console", "Record bounded console-error and failed-request summaries.", False),
        (
            "web-content",
            "Check labels, validation, destructive actions, and recovery guidance.",
            True,
        ),
    ),
    "native": (
        ("native-launch", "Check launch, first-run, loading, empty, and failure states.", True),
        (
            "native-layout",
            "Check window resizing, density, clipping, and platform conventions.",
            True,
        ),
        ("native-input", "Check pointer, keyboard, shortcuts, focus, and cancellation.", True),
        ("native-accessibility", "Check accessible names, roles, order, contrast, and zoom.", True),
        (
            "native-recovery",
            "Check offline, permission, crash-recovery, and unsaved-work states.",
            True,
        ),
    ),
    "cli": (
        ("cli-help", "Check help, usage, version, examples, and discoverability.", False),
        ("cli-success", "Check the primary non-interactive success path and stable output.", False),
        ("cli-errors", "Check invalid input, exit codes, stderr, and remediation guidance.", False),
        (
            "cli-automation",
            "Check JSON output, pipes, cancellation, timeouts, and idempotency.",
            False,
        ),
        (
            "cli-tui",
            "When applicable, check terminal sizes, focus, keyboard use, and redraw.",
            True,
        ),
    ),
    "api": (
        (
            "api-contract",
            "Check request, response, schema, status, and compatibility contracts.",
            False,
        ),
        ("api-validation", "Check malformed, missing, oversized, and conflicting inputs.", False),
        ("api-auth", "Check authorization boundaries without recording credentials.", False),
        ("api-failures", "Check bounded errors, retries, rate limits, and idempotency.", False),
        (
            "api-observability",
            "Check correlation and diagnostic evidence without sensitive data.",
            False,
        ),
    ),
    "code": (
        ("code-structure", "Check ownership, boundaries, dependencies, and change impact.", False),
        ("code-quality", "Check formatting, lint, types, complexity, and dead paths.", False),
        ("code-tests", "Check representative tests, failure modes, and coverage gaps.", False),
        (
            "code-security",
            "Check trust boundaries, secret handling, dependencies, and unsafe APIs.",
            False,
        ),
        (
            "code-operability",
            "Check configuration, logs, metrics, performance, and recovery.",
            False,
        ),
    ),
    "docs": (
        ("docs-render", "Check rendered structure, navigation, overflow, and code blocks.", True),
        (
            "docs-accuracy",
            "Check commands, examples, versions, prerequisites, and limitations.",
            False,
        ),
        (
            "docs-links",
            "Check internal references and explicitly authorized external links.",
            False,
        ),
        ("docs-accessibility", "Check headings, landmarks, link names, contrast, and zoom.", True),
        (
            "docs-safety",
            "Check privacy, destructive-action, support, and recovery guidance.",
            False,
        ),
    ),
    "data": (
        ("data-schema", "Check schema, types, constraints, keys, and compatibility.", False),
        (
            "data-quality",
            "Check nulls, ranges, duplicates, consistency, and representative samples.",
            False,
        ),
        (
            "data-privacy",
            "Check classification, minimization, retention, and redaction evidence.",
            False,
        ),
        ("data-lineage", "Check origin, transformations, ownership, and reproducibility.", False),
        ("data-scale", "Check bounded performance, limits, and failure behavior.", False),
    ),
}


def build_inspection_plan(target: str | Path, *, surface: str = "auto") -> dict[str, Any]:
    """Create a content-minimizing plan without opening the target or making network calls."""

    descriptor, detected = _target_descriptor(target)
    selected = detected if surface == "auto" else surface
    if selected not in SURFACES:
        raise SafetyError(f"inspection surface must be auto or one of: {', '.join(SURFACES)}")
    selected_surfaces = tuple(_CHECKS) if selected == "mixed" else (selected,)
    checks = [
        {
            "id": check_id,
            "surface": item_surface,
            "instruction": instruction,
            "visual": visual,
        }
        for item_surface in selected_surfaces
        for check_id, instruction, visual in _CHECKS[item_surface]
    ]
    plan = {
        "schema": PLAN_SCHEMA,
        "created_at": _now(),
        "target": descriptor,
        "surface": selected,
        "checks": checks,
        "guardrails": {
            "automatic_execution": False,
            "automatic_network": False,
            "credentials_online": False,
            "private_evidence": True,
            "outbound_consent_required": True,
        },
    }
    validate_inspection_plan(plan)
    return plan


def build_inspection_report(
    plan: dict[str, Any],
    *,
    checks: list[dict[str, Any]],
    evidence: list[dict[str, Any]] | None = None,
    issues: list[dict[str, Any]] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Build a validated report from evidence produced by an explicitly chosen inspection tool."""

    validate_inspection_plan(plan)
    evidence_items = evidence or []
    issue_items = issues or []
    derived = _derive_report_status(checks, issue_items)
    report = {
        "schema": REPORT_SCHEMA,
        "created_at": _now(),
        "plan_sha256": hashlib.sha256(_canonical(plan)).hexdigest(),
        "target": plan["target"],
        "surface": plan["surface"],
        "status": status or derived,
        "checks": checks,
        "evidence": evidence_items,
        "issues": issue_items,
        "limitations": [
            "Only listed evidence was inspected.",
            (
                "A passing report is not a security, accessibility, legal, "
                "or reliability certification."
            ),
        ],
    }
    validate_inspection_report(report, expected_plan=plan)
    return report


def write_inspection_json(path: Path, payload: dict[str, Any]) -> None:
    """Write one validated plan or report privately and atomically, refusing overwrites."""

    schema = payload.get("schema")
    if schema == PLAN_SCHEMA:
        validate_inspection_plan(payload)
    elif schema == REPORT_SCHEMA:
        validate_inspection_report(payload)
    else:
        raise SafetyError("unsupported inspection document schema")
    encoded = (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True).encode("utf-8") + b"\n"
    )
    if len(encoded) > MAX_INSPECTION_BYTES:
        raise SafetyError("inspection document exceeds the private evidence size limit")
    target = _safe_destination(path)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError as exc:
            raise EnvironmentError("inspection output exists; refusing to overwrite it") from exc
        except OSError as exc:
            raise EnvironmentError("inspection output could not be created") from exc
        _fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def load_inspection_json(path: Path) -> dict[str, Any]:
    """Read and validate one bounded regular inspection plan or report."""

    source = path.expanduser()
    try:
        info = source.lstat()
    except OSError as exc:
        raise EnvironmentError("inspection document could not be inspected") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_INSPECTION_BYTES:
        raise SafetyError("inspection document must be a bounded, singly linked regular file")
    try:
        raw = source.read_bytes()
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SafetyError("inspection document is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise SafetyError("inspection document must be a JSON object")
    if payload.get("schema") == PLAN_SCHEMA:
        validate_inspection_plan(payload)
    elif payload.get("schema") == REPORT_SCHEMA:
        validate_inspection_report(payload)
    else:
        raise SafetyError("unsupported inspection document schema")
    return payload


def verify_inspection_evidence(path: Path) -> dict[str, Any]:
    """Verify file-backed evidence without opening links or escaping the report root."""

    source = path.expanduser().resolve(strict=True)
    report = load_inspection_json(source)
    if report.get("schema") != REPORT_SCHEMA:
        raise SafetyError("inspection evidence verification requires a report")
    root = source.parent
    verified = 0
    metadata_only = 0
    for item in report["evidence"]:
        relative = item.get("path")
        if relative is None:
            metadata_only += 1
            continue
        candidate = root / PurePosixPath(relative)
        try:
            info = candidate.lstat()
        except OSError as exc:
            raise SafetyError("inspection evidence file is missing or unreadable") from exc
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > MAX_EVIDENCE_FILE_BYTES
        ):
            raise SafetyError("inspection evidence must be a bounded, singly linked regular file")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise SafetyError("inspection evidence file could not be resolved") from exc
        if not resolved.is_relative_to(root):
            raise SafetyError("inspection evidence escapes the report directory")
        digest = hashlib.sha256()
        try:
            with candidate.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise SafetyError("inspection evidence file changed while being verified") from exc
        if digest.hexdigest() != item["sha256"]:
            raise SafetyError("inspection evidence digest does not match the report")
        verified += 1
    return {
        "status": "ok",
        "verified_files": verified,
        "metadata_only_evidence": metadata_only,
        "total_evidence": len(report["evidence"]),
    }


def validate_inspection_plan(plan: dict[str, Any]) -> None:
    _exact_keys(plan, {"schema", "created_at", "target", "surface", "checks", "guardrails"}, "plan")
    if plan.get("schema") != PLAN_SCHEMA:
        raise SafetyError("unsupported inspection plan schema")
    _timestamp(plan.get("created_at"), "plan timestamp")
    surface = plan.get("surface")
    if surface not in SURFACES:
        raise SafetyError("inspection plan has an invalid surface")
    _validate_target(plan.get("target"))
    checks = plan.get("checks")
    if not isinstance(checks, list) or not 1 <= len(checks) <= MAX_CHECKS:
        raise SafetyError("inspection plan has an invalid check count")
    seen: set[str] = set()
    for item in checks:
        _exact_keys(item, {"id", "surface", "instruction", "visual"}, "plan check")
        check_id = _identifier(item.get("id"), "check identifier")
        if check_id in seen:
            raise SafetyError("inspection plan repeats a check identifier")
        seen.add(check_id)
        if item.get("surface") not in _CHECKS:
            raise SafetyError("inspection plan check has an invalid surface")
        _text(item.get("instruction"), "check instruction", 500)
        if not isinstance(item.get("visual"), bool):
            raise SafetyError("inspection plan check visual flag must be boolean")
    guardrails = plan.get("guardrails")
    required = {
        "automatic_execution": False,
        "automatic_network": False,
        "credentials_online": False,
        "private_evidence": True,
        "outbound_consent_required": True,
    }
    if guardrails != required:
        raise SafetyError("inspection plan weakens required privacy or execution guardrails")


def validate_inspection_report(
    report: dict[str, Any], *, expected_plan: dict[str, Any] | None = None
) -> None:
    _exact_keys(
        report,
        {
            "schema",
            "created_at",
            "plan_sha256",
            "target",
            "surface",
            "status",
            "checks",
            "evidence",
            "issues",
            "limitations",
        },
        "report",
    )
    if report.get("schema") != REPORT_SCHEMA:
        raise SafetyError("unsupported inspection report schema")
    _timestamp(report.get("created_at"), "report timestamp")
    if not _SHA256.fullmatch(str(report.get("plan_sha256", ""))):
        raise SafetyError("inspection report has an invalid plan digest")
    _validate_target(report.get("target"))
    if report.get("surface") not in SURFACES or report.get("status") not in REPORT_STATUSES:
        raise SafetyError("inspection report has an invalid surface or status")
    if expected_plan is not None:
        validate_inspection_plan(expected_plan)
        expected_digest = hashlib.sha256(_canonical(expected_plan)).hexdigest()
        if report["plan_sha256"] != expected_digest:
            raise SafetyError("inspection report does not match the selected plan")
        if (
            report["target"] != expected_plan["target"]
            or report["surface"] != expected_plan["surface"]
        ):
            raise SafetyError("inspection report target does not match the selected plan")
        valid_checks = {item["id"] for item in expected_plan["checks"]}
    else:
        valid_checks = None
    evidence = report.get("evidence")
    if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE:
        raise SafetyError("inspection report has too much evidence")
    evidence_ids: set[str] = set()
    for item in evidence:
        _exact_keys(item, {"id", "kind", "label", "path", "sha256", "captured_at"}, "evidence")
        evidence_id = _identifier(item.get("id"), "evidence identifier")
        if evidence_id in evidence_ids:
            raise SafetyError("inspection report repeats an evidence identifier")
        evidence_ids.add(evidence_id)
        if item.get("kind") not in EVIDENCE_KINDS:
            raise SafetyError("inspection evidence has an invalid kind")
        _text(item.get("label"), "evidence label", 200)
        path = item.get("path")
        if path is not None:
            _relative_path(path, "evidence path")
            if not _SHA256.fullmatch(str(item.get("sha256", ""))):
                raise SafetyError("file evidence must include a SHA-256 digest")
        elif item.get("sha256") is not None and not _SHA256.fullmatch(str(item["sha256"])):
            raise SafetyError("inspection evidence has an invalid digest")
        _timestamp(item.get("captured_at"), "evidence timestamp")
    checks = report.get("checks")
    if not isinstance(checks, list) or len(checks) > MAX_CHECKS:
        raise SafetyError("inspection report has an invalid check count")
    check_ids: set[str] = set()
    for item in checks:
        _exact_keys(item, {"id", "status", "summary", "evidence"}, "check result")
        check_id = _identifier(item.get("id"), "check result identifier")
        if check_id in check_ids or (valid_checks is not None and check_id not in valid_checks):
            raise SafetyError("inspection report has an unknown or repeated check")
        check_ids.add(check_id)
        if item.get("status") not in CHECK_STATUSES:
            raise SafetyError("inspection check result has an invalid status")
        _text(item.get("summary"), "check summary", 500)
        _evidence_references(item.get("evidence"), evidence_ids)
    issues = report.get("issues")
    if not isinstance(issues, list) or len(issues) > MAX_ISSUES:
        raise SafetyError("inspection report has too many issues")
    issue_ids: set[str] = set()
    for item in issues:
        _exact_keys(
            item,
            {"id", "severity", "title", "description", "remediation", "check_id", "evidence"},
            "issue",
        )
        issue_id = _identifier(item.get("id"), "issue identifier")
        if issue_id in issue_ids:
            raise SafetyError("inspection report repeats an issue identifier")
        issue_ids.add(issue_id)
        if item.get("severity") not in SEVERITIES:
            raise SafetyError("inspection issue has an invalid severity")
        _text(item.get("title"), "issue title", 300)
        _text(item.get("description"), "issue description", MAX_TEXT)
        _text(item.get("remediation"), "issue remediation", MAX_TEXT)
        check_id = item.get("check_id")
        if check_id is not None and check_id not in check_ids:
            raise SafetyError("inspection issue references an unknown check")
        _evidence_references(item.get("evidence"), evidence_ids)
    limitations = report.get("limitations")
    if not isinstance(limitations, list) or not 1 <= len(limitations) <= 20:
        raise SafetyError("inspection report must state bounded limitations")
    for item in limitations:
        _text(item, "inspection limitation", 500)


def _target_descriptor(target: str | Path) -> tuple[dict[str, str], str]:
    raw = str(target)
    if any(unicodedata.category(character).startswith("C") for character in raw):
        raise SafetyError("inspection target contains unsafe characters")
    parsed = urlsplit(raw)
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"} or "://" in raw:
        if (
            scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise SafetyError("web inspection target must be an HTTP(S) URL without credentials")
        port = f":{parsed.port}" if parsed.port else ""
        safe_url = urlunsplit(
            (parsed.scheme, f"{parsed.hostname}{port}", parsed.path or "/", "", "")
        )
        _text(safe_url, "inspection URL", 1_000)
        return {"kind": "url", "label": parsed.hostname, "location": safe_url}, "web"
    path = Path(raw).expanduser()
    windows_path = PureWindowsPath(raw)
    if os.name != "nt" and windows_path.is_absolute():
        relative = windows_path.name or "."
    else:
        try:
            relative = path.resolve(strict=False).relative_to(Path.cwd().resolve()).as_posix()
        except (OSError, ValueError):
            relative = path.name or "."
    _relative_path(relative, "inspection target path")
    suffix = windows_path.suffix.lower() if windows_path.is_absolute() else path.suffix.lower()
    detected = "mixed" if not suffix else "code"
    if suffix in {".md", ".mdx", ".rst", ".adoc", ".html", ".htm"}:
        detected = "docs"
    elif suffix in {".csv", ".db", ".jsonl", ".ndjson", ".parquet", ".sqlite", ".sqlite3"}:
        detected = "data"
    elif suffix in {".apk", ".app", ".desktop", ".exe", ".ipa", ".msi"}:
        detected = "native"
    label = windows_path.name if windows_path.is_absolute() else path.name
    return {"kind": "local", "label": label or "project", "location": relative}, detected


def _validate_target(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"kind", "label", "location"}:
        raise SafetyError("inspection target descriptor is invalid")
    if value.get("kind") not in {"local", "url"}:
        raise SafetyError("inspection target kind is invalid")
    _text(value.get("label"), "inspection target label", 300)
    location = _text(value.get("location"), "inspection target location", 1_000)
    if value["kind"] == "local":
        _relative_path(location, "inspection target path")
    else:
        parsed = urlsplit(location)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
        ):
            raise SafetyError("inspection URL descriptor is invalid")


def _derive_report_status(checks: list[dict[str, Any]], issues: list[dict[str, Any]]) -> str:
    statuses = {item.get("status") for item in checks if isinstance(item, dict)}
    severities = {item.get("severity") for item in issues if isinstance(item, dict)}
    if "failed" in statuses or severities.intersection({"critical", "high"}):
        return "failed"
    if "warning" in statuses or severities.intersection({"medium", "low"}):
        return "warning"
    if not checks or statuses.intersection({"skipped", "not-run"}):
        return "incomplete"
    return "passed"


def _safe_destination(path: Path) -> Path:
    target = path.expanduser()
    if not target.name or target.name in {".", ".."}:
        raise EnvironmentError("inspection output path is invalid")
    try:
        parent = target.parent.resolve(strict=True)
    except OSError as exc:
        raise EnvironmentError("inspection output parent directory does not exist") from exc
    if not parent.is_dir():
        raise EnvironmentError("inspection output parent is not a directory")
    return parent / target.name


def _exact_keys(value: Any, expected: set[str], what: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise SafetyError(f"inspection {what} has unexpected fields")


def _identifier(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise SafetyError(f"inspection {what} is invalid")
    return value


def _text(value: Any, what: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise SafetyError(f"inspection {what} is invalid")
    if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
        raise SafetyError(f"inspection {what} appears to contain a credential or private key")
    return value


def _relative_path(value: Any, what: str) -> str:
    text = _text(value, what, 1_000)
    path = PurePosixPath(text.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise SafetyError(f"inspection {what} must be relative")
    return path.as_posix()


def _timestamp(value: Any, what: str) -> str:
    text = _text(value, what, 64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SafetyError(f"inspection {what} is invalid") from exc
    if parsed.tzinfo is None:
        raise SafetyError(f"inspection {what} must include a timezone")
    return text


def _evidence_references(value: Any, known: set[str]) -> None:
    if not isinstance(value, list) or len(value) > 50:
        raise SafetyError("inspection evidence references are invalid")
    for item in value:
        if item not in known:
            raise SafetyError("inspection result references unknown evidence")


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
