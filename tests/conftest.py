from __future__ import annotations

from pathlib import Path

import pytest

from restore_rehearsal.recipe import Recipe, load_recipe


@pytest.fixture
def recipe_factory(tmp_path: Path):
    def make(*, extra: str = "", backup_content: str = "synthetic-backup\n") -> Recipe:
        (tmp_path / "compose.yaml").write_text(
            "services:\n  app:\n    image: example/app:1\n",
            encoding="utf-8",
        )
        (tmp_path / "backup.txt").write_text(backup_content, encoding="utf-8")
        recipe = tmp_path / "rehearse.yaml"
        recipe.write_text(
            """version: 1
name: test-app
compose:
  files: [compose.yaml]
  pull: never
  startup_timeout: 30
backups:
  - name: fixture
    path: backup.txt
    mounts:
      - service: app
        target: /rehearsal-input/backup.txt
restore:
  - name: restore
    service: app
    command: [cp, /rehearsal-input/backup.txt, /data/restored.txt]
    timeout: 10
candidate:
  images:
    app: example/app@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
checks:
  - name: content
    type: exec
    service: app
    command: [test, -f, /data/restored.txt]
    timeout: 5
    retries: 1
safety:
  allow_egress: false
"""
            + extra,
            encoding="utf-8",
        )
        return load_recipe(recipe)

    return make


@pytest.fixture
def compose_model() -> dict:
    return {
        "name": "production",
        "services": {
            "app": {
                "image": "example/app:1",
                "container_name": "production-app",
                "environment": {"APP_TOKEN": "SYNTHETIC_CANARY_123"},
                "ports": [{"target": 8080, "published": "8080", "protocol": "tcp"}],
                "networks": {"default": None},
                "volumes": [{"type": "volume", "source": "data", "target": "/data", "volume": {}}],
            }
        },
        "volumes": {"data": {"name": "production_data"}},
        "networks": {"default": {"name": "production_default", "ipam": {}}},
    }
