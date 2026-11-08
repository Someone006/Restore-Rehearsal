from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from restore_rehearsal.errors import SafetyError
from restore_rehearsal.isolation import RUN_LABEL, build_isolation_plan

PROJECT = "rr-test-app-0123456789abcdef"
RUN_ID = "0123456789abcdef"


def make_plan(model: dict, recipe):
    return build_isolation_plan(model, recipe, project=PROJECT, run_id=RUN_ID)


def test_rewrites_production_resources_and_injects_read_only_backup(
    compose_model: dict, recipe_factory
) -> None:
    recipe = recipe_factory()

    plan = make_plan(compose_model, recipe)
    service = plan.baseline["services"]["app"]

    assert plan.baseline["name"] == PROJECT
    assert "container_name" not in service
    assert "ports" not in service
    assert service["labels"][RUN_LABEL] == RUN_ID
    assert plan.baseline["volumes"]["data"]["name"].startswith(PROJECT)
    assert plan.baseline["networks"]["default"]["internal"] is True
    backup_mount = next(item for item in service["volumes"] if item["type"] == "bind")
    assert backup_mount["read_only"] is True
    assert backup_mount["bind"]["create_host_path"] is False
    assert plan.candidate["services"]["app"]["image"].endswith("a" * 64)
    assert "SYNTHETIC_CANARY_123" in plan.secret_values


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("privileged", True),
        ("devices", ["/dev/sda:/dev/sda"]),
        ("cap_add", ["SYS_ADMIN"]),
        ("network_mode", "host"),
        ("pid", "host"),
        ("ipc", "host"),
        ("extra_hosts", ["host.docker.internal:host-gateway"]),
        ("build", {"context": "."}),
        ("use_api_socket", True),
        ("post_start", [{"command": ["whoami"], "privileged": True}]),
        ("security_opt", ["seccomp=unconfined"]),
        ("cgroup", "host"),
    ],
)
def test_refuses_unsafe_service_features(
    compose_model: dict, recipe_factory, field: str, value: object
) -> None:
    model = deepcopy(compose_model)
    model["services"]["app"][field] = value

    with pytest.raises(SafetyError):
        make_plan(model, recipe_factory())


def test_refuses_external_volume(compose_model: dict, recipe_factory) -> None:
    model = deepcopy(compose_model)
    model["volumes"]["data"]["external"] = True

    with pytest.raises(SafetyError, match="external volume"):
        make_plan(model, recipe_factory())


def test_refuses_external_network(compose_model: dict, recipe_factory) -> None:
    model = deepcopy(compose_model)
    model["networks"]["default"]["external"] = True

    with pytest.raises(SafetyError, match="external network"):
        make_plan(model, recipe_factory())


def test_converts_writable_directory_bind_to_owned_volume(
    compose_model: dict, recipe_factory, tmp_path: Path
) -> None:
    source = tmp_path / "state"
    source.mkdir()
    model = deepcopy(compose_model)
    model["services"]["app"]["volumes"].append(
        {"type": "bind", "source": str(source), "target": "/state", "bind": {}}
    )

    plan = make_plan(model, recipe_factory())
    mount = next(
        item for item in plan.baseline["services"]["app"]["volumes"] if item["target"] == "/state"
    )

    assert mount["type"] == "volume"
    assert mount["source"] in plan.baseline["volumes"]
    assert plan.baseline["volumes"][mount["source"]]["labels"][RUN_LABEL] == RUN_ID


def test_refuses_original_file_bind(compose_model: dict, recipe_factory, tmp_path: Path) -> None:
    source = tmp_path / "config.ini"
    source.write_text("synthetic=true\n", encoding="utf-8")
    model = deepcopy(compose_model)
    model["services"]["app"]["volumes"].append(
        {"type": "bind", "source": str(source), "target": "/config.ini", "read_only": False}
    )

    with pytest.raises(SafetyError, match="bind-mounts a file"):
        make_plan(model, recipe_factory())


def test_refuses_container_socket_by_name(
    compose_model: dict, recipe_factory, tmp_path: Path
) -> None:
    socket_canary = tmp_path / "docker.sock"
    socket_canary.write_text("not-a-real-socket\n", encoding="utf-8")
    model = deepcopy(compose_model)
    model["services"]["app"]["volumes"].append(
        {
            "type": "bind",
            "source": str(socket_canary),
            "target": "/var/run/docker.sock",
            "read_only": True,
        }
    )

    with pytest.raises(SafetyError, match="container socket"):
        make_plan(model, recipe_factory())


def test_egress_requires_explicit_recipe_setting(
    compose_model: dict, recipe_factory, tmp_path: Path
) -> None:
    recipe_factory()
    recipe_path = tmp_path / "rehearse.yaml"
    content = recipe_path.read_text(encoding="utf-8").replace(
        "allow_egress: false", "allow_egress: true"
    )
    recipe_path.write_text(content, encoding="utf-8")
    from restore_rehearsal.recipe import load_recipe

    plan = make_plan(compose_model, load_recipe(recipe_path))

    assert plan.baseline["networks"]["default"]["internal"] is False
