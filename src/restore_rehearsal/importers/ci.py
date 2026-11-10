"""Allowlisted CI/check summary importer that discards logs and environment data."""

from __future__ import annotations

from typing import Any

from restore_rehearsal.errors import SafetyError
from restore_rehearsal.importers.common import (
    FragmentBuilder,
    json_document,
    mapping,
    safe_relative_path,
    safe_text,
    sequence,
)
from restore_rehearsal.plugins.model import ImportBundle, ImportContext

MAX_JOBS = 20_000


class CiImporter:
    api_version = "1"
    id = "builtin.ci"
    version = "1"
    description = "Import bounded CI job/check status without logs, environment, or credentials"
    kind = "ci"

    def detect(self, source_name: str, data: bytes) -> int:
        lowered = source_name.lower()
        if any(token in lowered for token in ("ci-result", "check-result", "workflow-result")):
            return 95
        head = data[:8192]
        if b'"rehearse.dev/ci-result/v1"' in head:
            return 100
        if b'"jobs"' in head and any(token in head for token in (b'"status"', b'"conclusion"')):
            return 65
        return 0

    def parse(self, context: ImportContext, data: bytes) -> ImportBundle:
        document = mapping(json_document(data))
        jobs = _job_records(document)
        if not jobs and not any(key in document for key in ("status", "conclusion")):
            raise SafetyError("CI artifact contains no supported job or check summaries")
        builder = FragmentBuilder(context, self.kind, self.id, self.version)
        pipeline_name = safe_text(
            document.get("name") or document.get("workflow") or document.get("pipeline"),
            300,
            fallback="CI pipeline",
        )
        pipeline_status = _status(document)
        pipeline_key = builder.add_node(
            "pipeline",
            kind="ci-pipeline",
            name=pipeline_name,
            path=safe_relative_path(document.get("path")),
            properties={
                "status": pipeline_status,
                "provider": safe_text(document.get("provider"), 100, fallback="unknown"),
                "logs_discarded": True,
                "environment_discarded": True,
            },
        )
        counts: dict[str, int] = {}
        for index, job in enumerate(jobs[:MAX_JOBS]):
            name = safe_text(
                job.get("name") or job.get("job") or job.get("id"),
                300,
                fallback=f"job-{index}",
            )
            status = _status(job)
            counts[status] = counts.get(status, 0) + 1
            path = safe_relative_path(
                job.get("artifact") or job.get("artifact_path") or job.get("path")
            )
            job_key = builder.add_node(
                f"job:{index}:{name}",
                kind="ci-job",
                name=name,
                path=path,
                properties={
                    "status": status,
                    "duration_seconds": _duration(job),
                    "started_at": _timestamp(job.get("started_at") or job.get("startedAt")),
                    "finished_at": _timestamp(
                        job.get("finished_at") or job.get("completed_at") or job.get("completedAt")
                    ),
                    "logs_discarded": True,
                    "environment_discarded": True,
                },
            )
            builder.add_edge(pipeline_key, job_key, kind="contains", evidence_path=path)
            if status in {"failed", "failure", "error", "timed-out", "timed_out"}:
                builder.add_finding(
                    lens="test-strategy",
                    rule="imported-ci-failure",
                    severity="high",
                    title=f"Imported CI job failed: {name}",
                    description=(
                        "The imported CI summary marks this job as unsuccessful. Logs and "
                        "environment values were intentionally not stored."
                    ),
                    remediation=(
                        "Inspect the original local CI evidence, reproduce the failure, and add a "
                        "bounded regression check."
                    ),
                    node_key=job_key,
                    evidence_path=path,
                )
            elif status in {"cancelled", "canceled", "skipped"}:
                builder.add_finding(
                    lens="test-strategy",
                    rule="imported-ci-incomplete",
                    severity="medium",
                    title=f"Imported CI job did not complete: {name}",
                    description=f"The imported CI summary reports status {status}.",
                    remediation=(
                        "Run or replace the missing verification before relying on the result."
                    ),
                    node_key=job_key,
                    evidence_path=path,
                )
        return builder.bundle(
            {
                "schema": safe_text(document.get("schema"), 100, fallback="provider-export"),
                "pipeline_status": pipeline_status,
                "jobs": len(jobs[:MAX_JOBS]),
                "status_counts": dict(sorted(counts.items())),
                "logs_environment_discarded": True,
            }
        )


def _job_records(document: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for key in ("jobs", "checks", "steps"):
        for raw in sequence(document.get(key), maximum=MAX_JOBS):
            result.append(mapping(raw))
            if len(result) >= MAX_JOBS:
                return result
    for workflow in sequence(document.get("workflows"), maximum=1_000):
        workflow_data = mapping(workflow)
        for key in ("jobs", "checks"):
            for raw in sequence(workflow_data.get(key), maximum=MAX_JOBS - len(result)):
                result.append(mapping(raw))
                if len(result) >= MAX_JOBS:
                    return result
    return result


def _status(value: dict[str, Any]) -> str:
    raw = (
        value.get("conclusion") or value.get("result") or value.get("status") or value.get("state")
    )
    return safe_text(raw, 30, fallback="unknown").lower().replace(" ", "-")


def _duration(value: dict[str, Any]) -> int:
    raw = value.get("duration_seconds") or value.get("duration") or value.get("time") or 0
    try:
        return max(0, min(int(float(raw)), 31_536_000))
    except (TypeError, ValueError, OverflowError):
        return 0


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return safe_text(value, 64)
