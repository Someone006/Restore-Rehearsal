"""Coverage summary importer for Cobertura/coverage.py XML and LCOV records."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from restore_rehearsal.errors import SafetyError
from restore_rehearsal.importers.common import (
    FragmentBuilder,
    local_name,
    safe_relative_path,
    safe_text,
    xml_document,
)
from restore_rehearsal.plugins.model import ImportBundle, ImportContext

MAX_COVERAGE_FILES = 20_000


class CoverageImporter:
    api_version = "1"
    id = "builtin.coverage"
    version = "1"
    description = "Import bounded LCOV or Cobertura coverage summaries without source lines"
    kind = "coverage"

    def detect(self, source_name: str, data: bytes) -> int:
        lowered = source_name.lower()
        if lowered.endswith((".lcov", "lcov.info", "coverage.info")):
            return 100
        if lowered.endswith(".xml") and any(
            token in lowered for token in ("coverage", "cobertura")
        ):
            return 95
        head = data[:4096].lstrip().lower()
        if head.startswith(b"<coverage") or b"<coverage " in head:
            return 80
        if b"sf:" in head and (b"lf:" in head or b"da:" in head):
            return 75
        return 0

    def parse(self, context: ImportContext, data: bytes) -> ImportBundle:
        if data.lstrip().startswith(b"<"):
            return self._xml(context, data)
        return self._lcov(context, data)

    def _xml(self, context: ImportContext, data: bytes) -> ImportBundle:
        root = xml_document(data)
        if local_name(root.tag) != "coverage":
            raise SafetyError("coverage XML must have a coverage root element")
        builder = FragmentBuilder(context, self.kind, self.id, self.version)
        file_count = low_count = 0
        total_valid = total_covered = 0
        for element in root.iter():
            if local_name(element.tag) != "class" or file_count >= MAX_COVERAGE_FILES:
                continue
            raw_path = element.attrib.get("filename")
            path = safe_relative_path(raw_path)
            name = _display_name(raw_path, fallback=f"coverage-file-{file_count}")
            line_rate = _rate(element.attrib.get("line-rate"))
            branch_rate = _rate(element.attrib.get("branch-rate"))
            lines_valid, lines_covered = _xml_line_counts(element)
            total_valid += lines_valid
            total_covered += lines_covered
            key = builder.add_node(
                f"file:{path or name}:{file_count}",
                kind="coverage-file",
                name=name,
                path=path,
                properties={
                    "line_rate": line_rate,
                    "branch_rate": branch_rate,
                    "lines_valid": lines_valid,
                    "lines_covered": lines_covered,
                },
            )
            if line_rate < 0.5 and low_count < 5_000:
                builder.add_finding(
                    lens="test-strategy",
                    rule="imported-low-line-coverage",
                    severity="medium",
                    title=f"Imported line coverage is below 50%: {name}",
                    description=(
                        "The imported coverage summary reports less than half of executable "
                        "lines covered. Source lines were intentionally not stored."
                    ),
                    remediation=(
                        "Review the local coverage report and add focused tests for important "
                        "uncovered behavior."
                    ),
                    node_key=key,
                    evidence_path=path,
                )
                low_count += 1
            file_count += 1

        declared_valid = _integer(root.attrib.get("lines-valid"))
        declared_covered = _integer(root.attrib.get("lines-covered"))
        if declared_valid:
            total_valid, total_covered = declared_valid, min(declared_covered, declared_valid)
        return builder.bundle(
            {
                "format": "Cobertura XML",
                "files": file_count,
                "lines_valid": total_valid,
                "lines_covered": total_covered,
                "line_rate": _ratio(total_covered, total_valid),
                "low_coverage_files": low_count,
            }
        )

    def _lcov(self, context: ImportContext, data: bytes) -> ImportBundle:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SafetyError("LCOV artifact must be UTF-8") from exc
        builder = FragmentBuilder(context, self.kind, self.id, self.version)
        records: list[dict[str, Any]] = []
        current: dict[str, Any] = {}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line == "end_of_record":
                if current.get("source") is not None:
                    records.append(current)
                current = {}
                if len(records) >= MAX_COVERAGE_FILES:
                    break
                continue
            if line.startswith("SF:"):
                current["source"] = line[3:]
            elif line.startswith("LF:"):
                current["lines_valid"] = _integer(line[3:])
            elif line.startswith("LH:"):
                current["lines_covered"] = _integer(line[3:])
            elif line.startswith("BRF:"):
                current["branches_valid"] = _integer(line[4:])
            elif line.startswith("BRH:"):
                current["branches_covered"] = _integer(line[4:])
        if current.get("source") is not None and len(records) < MAX_COVERAGE_FILES:
            records.append(current)
        if not records:
            raise SafetyError("LCOV artifact contains no bounded source records")

        total_valid = total_covered = 0
        total_branches = covered_branches = 0
        low_count = 0
        for index, record in enumerate(records):
            raw_path = record.get("source")
            path = safe_relative_path(raw_path)
            name = _display_name(raw_path, fallback=f"coverage-file-{index}")
            valid = int(record.get("lines_valid", 0))
            covered = min(int(record.get("lines_covered", 0)), valid) if valid else 0
            branch_valid = int(record.get("branches_valid", 0))
            branch_covered = (
                min(int(record.get("branches_covered", 0)), branch_valid) if branch_valid else 0
            )
            total_valid += valid
            total_covered += covered
            total_branches += branch_valid
            covered_branches += branch_covered
            line_rate = _ratio(covered, valid)
            key = builder.add_node(
                f"file:{path or name}:{index}",
                kind="coverage-file",
                name=name,
                path=path,
                properties={
                    "line_rate": line_rate,
                    "branch_rate": _ratio(branch_covered, branch_valid),
                    "lines_valid": valid,
                    "lines_covered": covered,
                },
            )
            if valid and line_rate < 0.5 and low_count < 5_000:
                builder.add_finding(
                    lens="test-strategy",
                    rule="imported-low-line-coverage",
                    severity="medium",
                    title=f"Imported line coverage is below 50%: {name}",
                    description=(
                        "The imported LCOV summary reports less than half of executable lines "
                        "covered. Per-line records were intentionally discarded."
                    ),
                    remediation=(
                        "Review the original local report and add focused tests for important "
                        "uncovered behavior."
                    ),
                    node_key=key,
                    evidence_path=path,
                )
                low_count += 1
        return builder.bundle(
            {
                "format": "LCOV",
                "files": len(records),
                "lines_valid": total_valid,
                "lines_covered": total_covered,
                "line_rate": _ratio(total_covered, total_valid),
                "branches_valid": total_branches,
                "branches_covered": covered_branches,
                "branch_rate": _ratio(covered_branches, total_branches),
                "low_coverage_files": low_count,
            }
        )


def _xml_line_counts(element: Any) -> tuple[int, int]:
    valid = covered = 0
    for child in element.iter():
        if local_name(child.tag) != "line":
            continue
        valid += 1
        if _integer(child.attrib.get("hits")) > 0:
            covered += 1
    return valid, covered


def _integer(value: Any) -> int:
    try:
        return max(0, min(int(str(value or "0")), 1_000_000_000))
    except (TypeError, ValueError):
        return 0


def _rate(value: Any) -> float:
    try:
        return max(0.0, min(float(str(value or "0")), 1.0))
    except (TypeError, ValueError):
        return 0.0


def _ratio(covered: int, valid: int) -> float:
    return round(covered / valid, 6) if valid else 0.0


def _display_name(value: Any, *, fallback: str) -> str:
    if not isinstance(value, str) or not value:
        return fallback
    normalized = value.replace("\\", "/")
    return safe_text(PurePosixPath(normalized).name, 300, fallback=fallback)
