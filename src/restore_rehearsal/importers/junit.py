"""JUnit-style XML result importer that intentionally discards logs and failure bodies."""

from __future__ import annotations

from restore_rehearsal.importers.common import (
    FragmentBuilder,
    local_name,
    safe_relative_path,
    safe_text,
    xml_document,
)
from restore_rehearsal.plugins.model import ImportBundle, ImportContext


class JunitImporter:
    api_version = "1"
    id = "builtin.junit"
    version = "1"
    description = "Import JUnit XML suite and test status summaries without logs"
    kind = "junit"

    def detect(self, source_name: str, data: bytes) -> int:
        lowered = source_name.lower()
        if lowered.endswith(".xml") and any(
            token in lowered for token in ("junit", "test-results", "test_result")
        ):
            return 95
        head = data[:4096].lower()
        return 75 if b"<testsuite" in head or b"<testsuites" in head else 0

    def parse(self, context: ImportContext, data: bytes) -> ImportBundle:
        root = xml_document(data)
        builder = FragmentBuilder(context, self.kind, self.id, self.version)
        suites = [element for element in root.iter() if local_name(element.tag) == "testsuite"]
        case_count = failure_count = skipped_count = 0
        for suite_index, suite in enumerate(suites[:1_000]):
            suite_name = safe_text(suite.attrib.get("name"), 300, fallback=f"suite-{suite_index}")
            suite_key = builder.add_node(
                f"suite:{suite_index}:{suite_name}",
                kind="test-suite",
                name=suite_name,
                properties={
                    "tests": _integer(suite.attrib.get("tests")),
                    "failures": _integer(suite.attrib.get("failures")),
                    "errors": _integer(suite.attrib.get("errors")),
                    "skipped": _integer(suite.attrib.get("skipped")),
                    "time_seconds": safe_text(suite.attrib.get("time"), 50, fallback="0"),
                },
            )
            for case_index, case in enumerate(
                child for child in suite if local_name(child.tag) == "testcase"
            ):
                if case_index >= 20_000:
                    break
                name = safe_text(case.attrib.get("name"), 300, fallback=f"case-{case_index}")
                class_name = safe_text(case.attrib.get("classname"), 300, fallback="unknown")
                path = safe_relative_path(case.attrib.get("file"))
                child_kinds = {local_name(child.tag) for child in case}
                status = "passed"
                if "failure" in child_kinds or "error" in child_kinds:
                    status = "failed"
                    failure_count += 1
                elif "skipped" in child_kinds:
                    status = "skipped"
                    skipped_count += 1
                case_key = builder.add_node(
                    f"case:{suite_index}:{case_index}:{class_name}:{name}",
                    kind="test-case",
                    name=name,
                    path=path,
                    properties={
                        "class": class_name,
                        "status": status,
                        "time_seconds": safe_text(case.attrib.get("time"), 50, fallback="0"),
                    },
                )
                builder.add_edge(suite_key, case_key, kind="contains", evidence_path=path)
                if status == "failed":
                    builder.add_finding(
                        lens="test-strategy",
                        rule="imported-test-failure",
                        severity="high",
                        title=f"Imported test failed: {name}",
                        description=(
                            "The imported JUnit summary marks this test as failed or errored. "
                            "Failure output was intentionally not stored."
                        ),
                        remediation=(
                            "Inspect the original local test report and reproduce the failure "
                            "with the appropriate test command."
                        ),
                        node_key=case_key,
                        evidence_path=path,
                    )
                case_count += 1
        return builder.bundle(
            {
                "suites": len(suites[:1_000]),
                "cases": case_count,
                "failures": failure_count,
                "skipped": skipped_count,
            }
        )


def _integer(value: str | None) -> int:
    try:
        return max(0, min(int(value or 0), 10_000_000))
    except ValueError:
        return 0
