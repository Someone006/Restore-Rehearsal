"""Validation and rewriting of the fully resolved Docker Compose model."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from restore_rehearsal.errors import SafetyError
from restore_rehearsal.recipe import Recipe

RUN_LABEL = "dev.rehearse.run"
OWNED_LABEL = "dev.rehearse.owned"
PROJECT_RE = re.compile(r"^rr-[a-z0-9][a-z0-9-]{0,38}-[0-9a-f]{16}$")
DOCKER_SOCKET_PATHS = {
    "/run/docker.sock",
    "/var/run/docker.sock",
    "/run/podman/podman.sock",
}


@dataclass(frozen=True)
class IsolationPlan:
    baseline: dict[str, Any]
    candidate: dict[str, Any]
    transformations: tuple[str, ...]
    warnings: tuple[str, ...]
    secret_values: tuple[str, ...]


def build_isolation_plan(
    source: dict[str, Any], recipe: Recipe, *, project: str, run_id: str
) -> IsolationPlan:
    """Return isolated baseline/candidate models or fail before Docker mutation."""

    if not PROJECT_RE.fullmatch(project):
        raise SafetyError("generated project identifier failed its safety check")
    if not re.fullmatch(r"[0-9a-f]{16}", run_id):
        raise SafetyError("generated run identifier failed its safety check")

    model = copy.deepcopy(source)
    services = model.get("services")
    if not isinstance(services, dict) or not services:
        raise SafetyError("resolved Compose model has no services")
    if not all(
        isinstance(name, str) and isinstance(value, dict) for name, value in services.items()
    ):
        raise SafetyError("resolved Compose services are malformed")

    _validate_recipe_services(recipe, services)
    _reject_top_level_data_sources(model)

    transformations: list[str] = []
    warnings: list[str] = []
    model["name"] = project
    allowed_binds = {path.resolve() for path in recipe.safety.allow_read_only_binds}
    backup_targets = {
        (mount.service, mount.target) for backup in recipe.backups for mount in backup.mounts
    }

    volumes = model.setdefault("volumes", {})
    if not isinstance(volumes, dict):
        raise SafetyError("resolved Compose volumes are malformed")
    _isolate_volumes(volumes, project, run_id, transformations)

    networks = model.setdefault("networks", {})
    if not isinstance(networks, dict):
        raise SafetyError("resolved Compose networks are malformed")
    _isolate_networks(
        networks,
        project,
        run_id,
        allow_egress=recipe.safety.allow_egress,
        transformations=transformations,
    )

    for service_name, service in services.items():
        _reject_unsafe_service(service_name, service)
        _label_service(service, run_id)

        if service.pop("container_name", None) is not None:
            transformations.append(f"removed fixed container name from {service_name}")
        published_ports = service.pop("ports", None)
        if published_ports:
            transformations.append(f"removed published host ports from {service_name}")

        mounts = service.get("volumes", [])
        if not isinstance(mounts, list):
            raise SafetyError(f"service {service_name!r} has malformed volumes")
        rewritten_mounts: list[dict[str, Any]] = []
        for index, mount in enumerate(mounts):
            if not isinstance(mount, dict):
                raise SafetyError(f"service {service_name!r} has a malformed volume mount")
            target = mount.get("target")
            if not isinstance(target, str) or not target.startswith("/"):
                raise SafetyError(f"service {service_name!r} has a volume without a safe target")
            mount_type = mount.get("type")
            if mount_type == "bind":
                source_path = _resolve_bind_source(service_name, mount)
                if str(source_path) in DOCKER_SOCKET_PATHS or source_path.name in {
                    "docker.sock",
                    "podman.sock",
                }:
                    raise SafetyError(
                        f"service {service_name!r} attempts to mount a container socket"
                    )
                if (service_name, target) in backup_targets:
                    transformations.append(
                        f"replaced original bind at {service_name}:{target} with a backup input"
                    )
                    continue
                if bool(mount.get("read_only")) and source_path in allowed_binds:
                    safe_mount = copy.deepcopy(mount)
                    safe_mount["source"] = str(source_path)
                    safe_mount["read_only"] = True
                    safe_mount["bind"] = {"create_host_path": False}
                    rewritten_mounts.append(safe_mount)
                    warnings.append(f"allowed explicit read-only bind for {service_name}:{target}")
                    continue
                if bool(mount.get("read_only")):
                    raise SafetyError(
                        f"service {service_name!r} uses an unapproved read-only bind at "
                        f"{target!r}; explicitly allow it or declare it as a backup mount"
                    )
                if source_path.is_file():
                    raise SafetyError(
                        f"service {service_name!r} bind-mounts a file at {target!r}; declare it as "
                        "a backup mount or explicitly allow that read-only source"
                    )
                volume_key = _scratch_volume_key(service_name, index)
                volumes[volume_key] = _owned_volume(project, volume_key, run_id)
                rewritten_mounts.append(
                    {
                        "type": "volume",
                        "source": volume_key,
                        "target": target,
                        "read_only": False,
                        "volume": {},
                    }
                )
                transformations.append(f"isolated writable bind at {service_name}:{target}")
            elif mount_type == "volume":
                safe_mount = copy.deepcopy(mount)
                source_name = safe_mount.get("source")
                if not source_name:
                    source_name = _scratch_volume_key(service_name, index)
                    safe_mount["source"] = source_name
                    safe_mount["volume"] = {}
                    volumes[source_name] = _owned_volume(project, source_name, run_id)
                    transformations.append(
                        "made anonymous volume persistent within rehearsal for "
                        f"{service_name}:{target}"
                    )
                elif source_name not in volumes:
                    raise SafetyError(
                        f"service {service_name!r} references undeclared volume {source_name!r}"
                    )
                rewritten_mounts.append(safe_mount)
            elif mount_type == "tmpfs":
                rewritten_mounts.append(copy.deepcopy(mount))
            else:
                raise SafetyError(
                    f"service {service_name!r} uses unsupported mount type {mount_type!r}"
                )
        service["volumes"] = rewritten_mounts

    for backup in recipe.backups:
        for mount in backup.mounts:
            service = services[mount.service]
            service_mounts = service.setdefault("volumes", [])
            service_mounts[:] = [
                item for item in service_mounts if item.get("target") != mount.target
            ]
            service_mounts.append(
                {
                    "type": "bind",
                    "source": str(backup.path),
                    "target": mount.target,
                    "read_only": True,
                    "bind": {"create_host_path": False},
                }
            )
            transformations.append(f"mounted backup {backup.name!r} read-only in {mount.service}")

    if not recipe.safety.allow_egress:
        transformations.append("made all rehearsal networks internal")

    candidate = copy.deepcopy(model)
    for service_name, image in recipe.candidate.images.items():
        candidate["services"][service_name]["image"] = image
        candidate["services"][service_name].pop("pull_policy", None)
        if "@sha256:" not in image:
            warnings.append(f"candidate image for {service_name} is not digest-pinned")

    return IsolationPlan(
        baseline=model,
        candidate=candidate,
        transformations=tuple(dict.fromkeys(transformations)),
        warnings=tuple(dict.fromkeys(warnings)),
        secret_values=_collect_environment_values(source),
    )


def _validate_recipe_services(recipe: Recipe, services: dict[str, Any]) -> None:
    references = {step.service for step in recipe.restore}
    references.update(check.service for check in recipe.checks)
    references.update(recipe.candidate.images)
    references.update(mount.service for backup in recipe.backups for mount in backup.mounts)
    missing = sorted(references - services.keys())
    if missing:
        raise SafetyError(f"recipe references unknown Compose service(s): {', '.join(missing)}")


def _reject_top_level_data_sources(model: dict[str, Any]) -> None:
    if model.get("secrets"):
        raise SafetyError("Compose secrets are not supported in the v1 safety model")
    if model.get("configs"):
        raise SafetyError("Compose configs are not supported in the v1 safety model")


def _isolate_volumes(
    volumes: dict[str, Any], project: str, run_id: str, transformations: list[str]
) -> None:
    for name, raw in list(volumes.items()):
        if raw is None:
            raw = {}
            volumes[name] = raw
        if not isinstance(raw, dict):
            raise SafetyError(f"volume {name!r} is malformed")
        if raw.get("external"):
            raise SafetyError(
                f"external volume {name!r} is refused; use an isolated restore target"
            )
        driver = raw.get("driver")
        if driver not in {None, "", "local"} or raw.get("driver_opts"):
            raise SafetyError(f"volume {name!r} uses a driver or options that may reach host data")
        old_name = raw.get("name")
        raw.clear()
        raw.update(_owned_volume(project, name, run_id))
        if old_name and old_name != raw["name"]:
            transformations.append(f"renamed volume {name!r} into the rehearsal namespace")


def _isolate_networks(
    networks: dict[str, Any],
    project: str,
    run_id: str,
    *,
    allow_egress: bool,
    transformations: list[str],
) -> None:
    for name, raw in list(networks.items()):
        if raw is None:
            raw = {}
            networks[name] = raw
        if not isinstance(raw, dict):
            raise SafetyError(f"network {name!r} is malformed")
        if raw.get("external"):
            raise SafetyError(f"external network {name!r} is refused")
        driver = raw.get("driver")
        if driver not in {None, "", "bridge"} or raw.get("driver_opts"):
            raise SafetyError(f"network {name!r} uses an unsupported driver or driver options")
        ipam = raw.get("ipam") or {}
        if not isinstance(ipam, dict):
            raise SafetyError(f"network {name!r} has malformed IPAM settings")
        if ipam.get("config"):
            raise SafetyError(
                f"network {name!r} pins IP ranges, which is unsafe for a disposable run"
            )
        old_name = raw.get("name")
        raw.clear()
        raw.update(
            {
                "name": _resource_name(project, name),
                "internal": not allow_egress,
                "labels": {RUN_LABEL: run_id, OWNED_LABEL: "true"},
            }
        )
        if old_name and old_name != raw["name"]:
            transformations.append(f"renamed network {name!r} into the rehearsal namespace")


def _reject_unsafe_service(name: str, service: dict[str, Any]) -> None:
    if service.get("build"):
        raise SafetyError(f"service {name!r} uses build; only prebuilt images are supported")
    if not service.get("image"):
        raise SafetyError(f"service {name!r} has no image")
    if service.get("privileged"):
        raise SafetyError(f"service {name!r} requests privileged mode")
    for field in (
        "devices",
        "device_cgroup_rules",
        "volumes_from",
        "gpus",
        "use_api_socket",
        "provider",
        "post_start",
        "pre_stop",
        "credential_spec",
        "cgroup",
        "cgroup_parent",
        "security_opt",
        "sysctls",
        "storage_opt",
        "mac_address",
    ):
        if service.get(field):
            raise SafetyError(f"service {name!r} uses unsafe field {field!r}")
    if service.get("cap_add"):
        raise SafetyError(f"service {name!r} adds Linux capabilities")
    for field in ("network_mode", "pid", "ipc", "uts", "userns_mode"):
        value = service.get(field)
        if value:
            raise SafetyError(f"service {name!r} uses host/container namespace field {field!r}")
    if service.get("extra_hosts"):
        raise SafetyError(f"service {name!r} uses extra_hosts, which can bypass network isolation")
    if service.get("runtime"):
        raise SafetyError(f"service {name!r} requests a custom container runtime")
    deploy = service.get("deploy") or {}
    if not isinstance(deploy, dict):
        raise SafetyError(f"service {name!r} has malformed deploy settings")
    resources = deploy.get("resources") or {}
    reservations = resources.get("reservations") or {} if isinstance(resources, dict) else {}
    if isinstance(reservations, dict) and reservations.get("devices"):
        raise SafetyError(f"service {name!r} reserves host devices")
    attachments = service.get("networks") or {}
    if not isinstance(attachments, dict):
        raise SafetyError(f"service {name!r} has malformed network attachments")
    for attachment in attachments.values():
        if isinstance(attachment, dict) and any(
            key in attachment
            for key in ("ipv4_address", "ipv6_address", "link_local_ips", "mac_address")
        ):
            raise SafetyError(f"service {name!r} pins a network address")


def _label_service(service: dict[str, Any], run_id: str) -> None:
    labels = service.setdefault("labels", {})
    if not isinstance(labels, dict):
        raise SafetyError("resolved service labels are malformed")
    labels[RUN_LABEL] = run_id
    labels[OWNED_LABEL] = "true"


def _resolve_bind_source(service_name: str, mount: dict[str, Any]) -> Path:
    source = mount.get("source")
    if not isinstance(source, str) or not source:
        raise SafetyError(f"service {service_name!r} has a bind mount without a source")
    try:
        path = Path(source).resolve(strict=True)
    except OSError as exc:
        raise SafetyError(
            f"service {service_name!r} bind source does not exist; refusing Docker auto-creation"
        ) from exc
    if not (path.is_file() or path.is_dir()):
        raise SafetyError(
            f"service {service_name!r} bind source is not a regular file or directory"
        )
    return path


def _owned_volume(project: str, key: str, run_id: str) -> dict[str, Any]:
    return {
        "name": _resource_name(project, key),
        "labels": {RUN_LABEL: run_id, OWNED_LABEL: "true"},
    }


def _resource_name(project: str, key: str) -> str:
    safe_key = re.sub(r"[^a-zA-Z0-9_.-]+", "-", key).strip("-.") or "resource"
    return f"{project}_{safe_key}"[:180]


def _scratch_volume_key(service: str, index: int) -> str:
    safe_service = re.sub(r"[^a-zA-Z0-9_.-]+", "-", service)
    return f"rr-bind-{safe_service}-{index}"


def _collect_environment_values(model: dict[str, Any]) -> tuple[str, ...]:
    values: set[str] = set()
    services = model.get("services", {})
    if not isinstance(services, dict):
        return ()
    for service in services.values():
        if not isinstance(service, dict):
            continue
        environment = service.get("environment", {})
        if isinstance(environment, dict):
            for value in environment.values():
                if isinstance(value, (str, int, float)) and len(str(value)) >= 4:
                    values.add(str(value))
    return tuple(sorted(values, key=len, reverse=True))
