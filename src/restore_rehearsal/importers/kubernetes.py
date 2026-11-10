"""Kubernetes manifest importer with strict secret and value omission."""

from __future__ import annotations

from typing import Any

from restore_rehearsal.errors import SafetyError
from restore_rehearsal.importers.common import (
    FragmentBuilder,
    mapping,
    safe_text,
    sequence,
    yaml_documents,
)
from restore_rehearsal.plugins.model import ImportBundle, ImportContext

WORKLOAD_KINDS = {
    "CronJob",
    "DaemonSet",
    "Deployment",
    "Job",
    "Pod",
    "ReplicaSet",
    "StatefulSet",
}
MAX_OBJECTS = 10_000


class KubernetesImporter:
    api_version = "1"
    id = "builtin.kubernetes"
    version = "1"
    description = "Import Kubernetes object relationships without secret, config, or env values"
    kind = "kubernetes"

    def detect(self, source_name: str, data: bytes) -> int:
        lowered = source_name.lower()
        head = data[:16_384]
        if (b"apiVersion:" in head or b'"apiVersion"' in head) and (
            b"kind:" in head or b'"kind"' in head
        ):
            return 90
        if any(
            token in lowered for token in ("k8s", "kubernetes", "manifest")
        ) and lowered.endswith((".yaml", ".yml")):
            return 70
        return 0

    def parse(self, context: ImportContext, data: bytes) -> ImportBundle:
        documents = yaml_documents(data)
        objects = _objects(documents)
        if not objects:
            raise SafetyError("Kubernetes artifact contains no manifest objects")
        builder = FragmentBuilder(context, self.kind, self.id, self.version)
        root = builder.add_node(
            "manifest",
            kind="kubernetes-manifest",
            name=safe_text(context.source_name, 300),
            path=context.source_name,
        )
        object_keys: dict[tuple[str, str, str], str] = {}
        workload_labels: dict[str, dict[str, str]] = {}
        services: list[tuple[str, str, dict[str, str]]] = []
        secret_count = 0
        container_count = 0
        for index, obj in enumerate(objects):
            kind = safe_text(obj.get("kind"), 100, fallback="Unknown")
            api_version = safe_text(obj.get("apiVersion"), 100, fallback="unknown")
            metadata = mapping(obj.get("metadata"))
            name = safe_text(metadata.get("name"), 253, fallback=f"object-{index}")
            namespace = safe_text(metadata.get("namespace"), 253, fallback="default")
            key = builder.add_node(
                f"object:{namespace}:{kind}:{name}",
                kind="kubernetes-object",
                name=f"{kind}/{name}",
                path=context.source_name,
                properties={
                    "api_version": api_version,
                    "object_kind": kind,
                    "namespace": namespace,
                    "values_discarded": True,
                },
            )
            object_keys[(namespace, kind, name)] = key
            builder.add_edge(root, key, kind="contains", evidence_path=context.source_name)
            if kind in {"Secret", "ConfigMap"}:
                secret_count += kind == "Secret"
                if kind == "Secret":
                    builder.add_finding(
                        lens="security-supply-chain",
                        rule="kubernetes-secret-manifest",
                        severity="high",
                        title=f"Kubernetes Secret manifest present: {name}",
                        description=(
                            "The imported manifest declares a Secret. Its data, stringData, "
                            "annotations, and environment values were intentionally discarded."
                        ),
                        remediation=(
                            "Keep secret material out of source control and generated evidence; "
                            "use an approved secret delivery mechanism."
                        ),
                        node_key=key,
                        evidence_path=context.source_name,
                    )
                continue

            spec = mapping(obj.get("spec"))
            if kind == "Service":
                services.append((key, namespace, _string_map(spec.get("selector"))))
            if kind in WORKLOAD_KINDS:
                labels = _workload_labels(kind, obj)
                workload_labels[key] = labels
                pod_spec = _pod_spec(kind, spec)
                service_account = pod_spec.get("serviceAccountName") or pod_spec.get(
                    "serviceAccount"
                )
                if isinstance(service_account, str) and service_account:
                    account_name = safe_text(service_account, 253)
                    account_key = builder.add_node(
                        f"service-account:{namespace}:{account_name}",
                        kind="service-account",
                        name=account_name,
                        properties={"namespace": namespace},
                    )
                    builder.add_edge(key, account_key, kind="uses")
                for container_kind in ("containers", "initContainers"):
                    for container_index, raw_container in enumerate(
                        sequence(pod_spec.get(container_kind), maximum=1_000)
                    ):
                        container = mapping(raw_container)
                        container_name = safe_text(
                            container.get("name"), 253, fallback=f"container-{container_index}"
                        )
                        image = _safe_image(container.get("image"))
                        container_key = builder.add_node(
                            f"container:{namespace}:{kind}:{name}:{container_kind}:{container_name}",
                            kind="container-image",
                            name=container_name,
                            properties={
                                "image": image,
                                "container_kind": container_kind,
                                "environment_values_discarded": True,
                            },
                        )
                        builder.add_edge(key, container_key, kind="uses-image")
                        container_count += 1
                for volume_index, raw_volume in enumerate(
                    sequence(pod_spec.get("volumes"), maximum=1_000)
                ):
                    volume = mapping(raw_volume)
                    volume_name = safe_text(
                        volume.get("name"), 253, fallback=f"volume-{volume_index}"
                    )
                    source_type = next(
                        (
                            item
                            for item in (
                                "persistentVolumeClaim",
                                "configMap",
                                "secret",
                                "emptyDir",
                                "projected",
                                "hostPath",
                                "csi",
                            )
                            if item in volume
                        ),
                        "other",
                    )
                    volume_key = builder.add_node(
                        f"volume:{namespace}:{kind}:{name}:{volume_name}",
                        kind="storage-volume",
                        name=volume_name,
                        properties={"source_type": source_type, "values_discarded": True},
                    )
                    builder.add_edge(key, volume_key, kind="mounts")

        selected = 0
        for service_key, namespace, selector in services:
            if not selector:
                continue
            for workload_key, labels in workload_labels.items():
                workload_node_namespace = next(
                    (
                        ns
                        for (ns, _kind, _name), candidate_key in object_keys.items()
                        if candidate_key == workload_key
                    ),
                    None,
                )
                if workload_node_namespace == namespace and all(
                    labels.get(key) == value for key, value in selector.items()
                ):
                    builder.add_edge(service_key, workload_key, kind="routes-to")
                    selected += 1
        return builder.bundle(
            {
                "objects": len(objects),
                "containers": container_count,
                "service_routes": selected,
                "secret_objects": secret_count,
                "secret_config_env_values_discarded": True,
            }
        )


def _objects(documents: tuple[Any, ...]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in documents:
        document = mapping(raw)
        if not document:
            continue
        if document.get("kind") == "List":
            result.extend(
                mapping(item) for item in sequence(document.get("items"), maximum=MAX_OBJECTS)
            )
        else:
            result.append(document)
        if len(result) > MAX_OBJECTS:
            raise SafetyError("Kubernetes artifact exceeds the object limit")
    return result[:MAX_OBJECTS]


def _pod_spec(kind: str, spec: dict[str, Any]) -> dict[str, Any]:
    if kind == "Pod":
        return spec
    template = mapping(spec.get("template"))
    if kind == "CronJob":
        job_template = mapping(spec.get("jobTemplate"))
        template = mapping(mapping(job_template.get("spec")).get("template"))
    return mapping(template.get("spec"))


def _workload_labels(kind: str, obj: dict[str, Any]) -> dict[str, str]:
    metadata = mapping(obj.get("metadata"))
    if kind == "Pod":
        return _string_map(metadata.get("labels"))
    spec = mapping(obj.get("spec"))
    if kind == "CronJob":
        job_template = mapping(spec.get("jobTemplate"))
        template = mapping(mapping(job_template.get("spec")).get("template"))
    else:
        template = mapping(spec.get("template"))
    return _string_map(mapping(template.get("metadata")).get("labels"))


def _string_map(value: Any) -> dict[str, str]:
    return {
        safe_text(key, 100): safe_text(item, 200)
        for key, item in list(mapping(value).items())[:200]
        if isinstance(item, (str, int, bool))
    }


def _safe_image(value: Any) -> str:
    image = safe_text(value, 500, fallback="unknown")
    if "://" in image or " " in image or "\t" in image:
        return "[invalid-image-reference]"
    # OCI digests legitimately contain @; redact likely embedded credentials before it.
    if "@" in image and not image.rsplit("@", 1)[1].startswith(("sha256:", "sha512:")):
        return "[redacted-image-reference]"
    return image
