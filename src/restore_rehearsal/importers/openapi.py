"""OpenAPI/Swagger structure importer with local-reference-only resolution."""

from __future__ import annotations

from typing import Any

from restore_rehearsal.errors import SafetyError
from restore_rehearsal.importers.common import (
    FragmentBuilder,
    json_document,
    mapping,
    safe_text,
    sequence,
    yaml_documents,
)
from restore_rehearsal.plugins.model import ImportBundle, ImportContext

HTTP_METHODS = {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
MAX_ROUTES = 5_000
MAX_OPERATIONS = 10_000
MAX_SCHEMAS = 10_000
MAX_REFERENCE_VISITS = 20_000


class OpenApiImporter:
    api_version = "1"
    id = "builtin.openapi"
    version = "1"
    description = "Import OpenAPI routes, operations, schemas, and local references"
    kind = "openapi"

    def detect(self, source_name: str, data: bytes) -> int:
        lowered = source_name.lower()
        if any(token in lowered for token in ("openapi", "swagger")) and lowered.endswith(
            (".json", ".yaml", ".yml")
        ):
            return 100
        head = data[:8192].lower()
        if b'"openapi"' in head or b'"swagger"' in head:
            return 90
        if b"openapi:" in head or b"swagger:" in head:
            return 85
        return 0

    def parse(self, context: ImportContext, data: bytes) -> ImportBundle:
        document = self._document(data)
        version = safe_text(document.get("openapi") or document.get("swagger"), 50)
        if not (version.startswith("3.") or version.startswith("2.")):
            raise SafetyError("artifact is not a supported OpenAPI 3.x or Swagger 2.x document")
        builder = FragmentBuilder(context, self.kind, self.id, self.version)
        title = safe_text(mapping(document.get("info")).get("title"), 300, fallback="API")
        api_key = builder.add_node(
            f"api:{title}:{version}",
            kind="api-contract",
            name=title,
            properties={"specification_version": version},
        )

        schema_source = (
            mapping(mapping(document.get("components")).get("schemas"))
            if version.startswith("3.")
            else mapping(document.get("definitions"))
        )
        schema_keys: dict[str, str] = {}
        for index, (schema_name, raw_schema) in enumerate(sorted(schema_source.items())):
            if index >= MAX_SCHEMAS:
                break
            schema = mapping(raw_schema)
            key = builder.add_node(
                f"schema:{schema_name}",
                kind="api-schema",
                name=safe_text(schema_name, 300, fallback=f"schema-{index}"),
                properties={
                    "type": safe_text(schema.get("type"), 50, fallback="object"),
                    "required_count": len(sequence(schema.get("required"), maximum=1_000)),
                    "property_count": len(mapping(schema.get("properties"))),
                },
            )
            schema_keys[schema_name] = key
            builder.add_edge(api_key, key, kind="declares")

        route_count = operation_count = missing_ids = reference_count = 0
        for raw_route, raw_path_item in sorted(mapping(document.get("paths")).items()):
            if route_count >= MAX_ROUTES:
                break
            route = safe_text(raw_route, 500, fallback=f"route-{route_count}")
            path_item = mapping(raw_path_item)
            route_key = builder.add_node(
                f"route:{route}",
                kind="api-route",
                name=route,
                properties={
                    "operation_count": sum(1 for key in path_item if key.lower() in HTTP_METHODS)
                },
            )
            builder.add_edge(api_key, route_key, kind="contains")
            route_count += 1
            for method, raw_operation in sorted(path_item.items()):
                method_lower = method.lower()
                if method_lower not in HTTP_METHODS or operation_count >= MAX_OPERATIONS:
                    continue
                operation = mapping(raw_operation)
                operation_id_raw = operation.get("operationId")
                operation_id = safe_text(
                    operation_id_raw,
                    300,
                    fallback=f"{method_lower.upper()} {route}",
                )
                operation_key = builder.add_node(
                    f"operation:{method_lower}:{route}",
                    kind="api-operation",
                    name=operation_id,
                    properties={
                        "method": method_lower.upper(),
                        "route": route,
                        "deprecated": bool(operation.get("deprecated", False)),
                        "tag_count": len(sequence(operation.get("tags"), maximum=100)),
                    },
                )
                builder.add_edge(route_key, operation_key, kind="exposes")
                if not isinstance(operation_id_raw, str) or not operation_id_raw.strip():
                    builder.add_finding(
                        lens="architecture",
                        rule="imported-api-operation-id-missing",
                        severity="low",
                        title=f"API operation has no operationId: {method_lower.upper()} {route}",
                        description=(
                            "The imported contract does not provide a stable operation identifier."
                        ),
                        remediation=(
                            "Add a stable operationId if generated clients, tests, or change "
                            "tracking depend on it."
                        ),
                        node_key=operation_key,
                    )
                    missing_ids += 1
                for reference in _local_schema_refs(operation):
                    schema_name = _schema_name(reference)
                    target = schema_keys.get(schema_name) if schema_name else None
                    if target is not None:
                        builder.add_edge(operation_key, target, kind="uses-schema")
                        reference_count += 1
                operation_count += 1

        for schema_name, raw_schema in sorted(schema_source.items()):
            source = schema_keys.get(schema_name)
            if source is None:
                continue
            for reference in _local_schema_refs(raw_schema):
                target_name = _schema_name(reference)
                target = schema_keys.get(target_name) if target_name else None
                if target is not None and target != source:
                    builder.add_edge(source, target, kind="references")
                    reference_count += 1

        return builder.bundle(
            {
                "format": "OpenAPI" if version.startswith("3.") else "Swagger",
                "version": version,
                "routes": route_count,
                "operations": operation_count,
                "schemas": len(schema_keys),
                "local_schema_references": reference_count,
                "operations_without_id": missing_ids,
            }
        )

    @staticmethod
    def _document(data: bytes) -> dict[str, Any]:
        stripped = data.lstrip()
        if stripped.startswith((b"{", b"[")):
            return mapping(json_document(data))
        documents = yaml_documents(data)
        if len(documents) != 1:
            raise SafetyError("OpenAPI YAML must contain exactly one document")
        return mapping(documents[0])


def _local_schema_refs(value: Any) -> tuple[str, ...]:
    result: set[str] = set()
    stack: list[tuple[Any, int]] = [(value, 0)]
    visits = 0
    while stack:
        item, depth = stack.pop()
        visits += 1
        if visits > MAX_REFERENCE_VISITS or depth > 30:
            break
        if isinstance(item, dict):
            reference = item.get("$ref")
            if isinstance(reference, str) and (
                reference.startswith("#/components/schemas/")
                or reference.startswith("#/definitions/")
            ):
                result.add(reference[:1_000])
            for key, child in item.items():
                if key in {"example", "examples", "default", "security", "securitySchemes"}:
                    continue
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item[:1_000])
    return tuple(sorted(result))


def _schema_name(reference: str) -> str | None:
    prefixes = ("#/components/schemas/", "#/definitions/")
    for prefix in prefixes:
        if reference.startswith(prefix):
            name = reference[len(prefix) :]
            if name and "/" not in name and "~" not in name:
                return name
    return None
