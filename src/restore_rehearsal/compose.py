"""Shell-free Docker and Docker Compose process adapter."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from restore_rehearsal.errors import CleanupError, EnvironmentError, SafetyError
from restore_rehearsal.isolation import RUN_LABEL
from restore_rehearsal.recipe import ComposeSpec


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandFailure(EnvironmentError):
    """A failed external command whose captured output must be redacted before use."""

    def __init__(self, operation: str, result: CommandResult) -> None:
        super().__init__(f"{operation} failed with exit code {result.returncode}")
        self.operation = operation
        self.result = result


class CommandRunner:
    """Injectable subprocess runner. It never invokes a host shell."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: int | float | None = None,
        stdin_path: Path | None = None,
    ) -> CommandResult:
        stdin: BinaryIO | None = None
        try:
            if stdin_path is not None:
                stdin = stdin_path.open("rb")
            completed = subprocess.run(
                list(argv),
                cwd=cwd,
                stdin=stdin,
                capture_output=True,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise EnvironmentError(f"required executable {argv[0]!r} was not found") from exc
        except PermissionError as exc:
            raise EnvironmentError(f"required executable {argv[0]!r} is not executable") from exc
        except subprocess.TimeoutExpired as exc:
            stdout = _decode(exc.stdout or b"")
            stderr = _decode(exc.stderr or b"")
            result = CommandResult(returncode=124, stdout=stdout, stderr=stderr)
            raise CommandFailure("external command timed out", result) from exc
        except OSError as exc:
            raise EnvironmentError("could not start required external command") from exc
        finally:
            if stdin is not None:
                stdin.close()
        return CommandResult(
            returncode=completed.returncode,
            stdout=_decode(completed.stdout),
            stderr=_decode(completed.stderr),
        )


class ComposeClient:
    def __init__(
        self,
        runner: CommandRunner,
        *,
        project: str,
        run_id: str,
        compose: ComposeSpec,
    ) -> None:
        self.runner = runner
        self.project = project
        self.run_id = run_id
        self.compose = compose

    def resolve(self) -> dict[str, Any]:
        argv = ["docker", "compose", "--project-name", self.project]
        for compose_file in self.compose.files:
            argv.extend(["--file", str(compose_file)])
        for profile in self.compose.profiles:
            argv.extend(["--profile", profile])
        argv.extend(["config", "--format", "json"])
        result = self.runner.run(argv, cwd=self.compose.files[0].parent, timeout=60)
        if result.returncode != 0:
            raise CommandFailure("docker compose config", result)
        try:
            model = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise EnvironmentError("docker compose returned an invalid rendered model") from exc
        if not isinstance(model, dict):
            raise EnvironmentError("docker compose returned an invalid rendered model")
        return model

    def assert_project_absent(self) -> None:
        for kind, argv in self._resource_queries().items():
            result = self.runner.run(argv, timeout=30)
            if result.returncode != 0:
                raise CommandFailure(f"checking existing Docker {kind}", result)
            if _lines(result.stdout):
                raise SafetyError(
                    "generated project name already belongs to existing Docker resources"
                )

    def start(
        self,
        config: Path,
        *,
        services: Sequence[str] = (),
        wait: bool,
    ) -> None:
        argv = self._compose_for(config)
        argv.extend(["up", "--detach", "--remove-orphans", "--pull", self.compose.pull])
        if wait:
            argv.extend(["--wait", "--wait-timeout", str(self.compose.startup_timeout)])
        argv.extend(services)
        result = self.runner.run(
            argv,
            cwd=self.compose.files[0].parent,
            timeout=self.compose.startup_timeout + 300,
        )
        if result.returncode != 0:
            stage = "waiting for rehearsal services" if wait else "starting restore services"
            raise CommandFailure(stage, result)

    def exec(
        self,
        config: Path,
        service: str,
        command: Sequence[str],
        *,
        timeout: int,
        stdin_path: Path | None = None,
    ) -> CommandResult:
        argv = self._compose_for(config)
        argv.extend(["exec", "--no-TTY", service, *command])
        try:
            return self.runner.run(
                argv,
                cwd=self.compose.files[0].parent,
                timeout=timeout,
                stdin_path=stdin_path,
            )
        except CommandFailure as exc:
            return exc.result

    def health(self, config: Path, service: str, *, timeout: int) -> CommandResult:
        ps_argv = self._compose_for(config)
        ps_argv.extend(["ps", "--quiet", service])
        ps_result = self.runner.run(ps_argv, cwd=self.compose.files[0].parent, timeout=timeout)
        if ps_result.returncode != 0 or not _lines(ps_result.stdout):
            return CommandResult(
                returncode=ps_result.returncode or 1,
                stdout="",
                stderr=ps_result.stderr or "service has no running container",
            )
        container_id = _lines(ps_result.stdout)[0]
        if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
            return CommandResult(returncode=1, stdout="", stderr="invalid container identifier")
        inspect = self.runner.run(
            ["docker", "inspect", "--format", "{{json .State}}", container_id],
            timeout=timeout,
        )
        if inspect.returncode != 0:
            return inspect
        try:
            state = json.loads(inspect.stdout)
        except json.JSONDecodeError:
            return CommandResult(returncode=1, stdout="", stderr="invalid container state")
        if not isinstance(state, dict):
            return CommandResult(returncode=1, stdout="", stderr="invalid container state")
        running = state.get("Running") is True
        health = state.get("Health")
        healthy = not health or (isinstance(health, dict) and health.get("Status") == "healthy")
        return CommandResult(
            returncode=0 if running and healthy else 1,
            stdout="",
            stderr="" if running and healthy else "service is not running and healthy",
        )

    def cleanup(self, config: Path) -> None:
        self._assert_owned_resources()
        argv = self._compose_for(config)
        argv.extend(["down", "--volumes", "--remove-orphans", "--timeout", "10"])
        result = self.runner.run(argv, cwd=self.compose.files[0].parent, timeout=180)
        if result.returncode != 0:
            raise CleanupError("could not remove the isolated rehearsal project")

    def _assert_owned_resources(self) -> None:
        for kind, base_argv in self._resource_queries().items():
            all_result = self.runner.run(base_argv, timeout=30)
            if all_result.returncode != 0:
                raise CommandFailure(f"listing rehearsal {kind}", all_result)
            owned_argv = [*base_argv, "--filter", f"label={RUN_LABEL}={self.run_id}"]
            owned_result = self.runner.run(owned_argv, timeout=30)
            if owned_result.returncode != 0:
                raise CommandFailure(f"verifying rehearsal {kind}", owned_result)
            if set(_lines(all_result.stdout)) != set(_lines(owned_result.stdout)):
                raise CleanupError(
                    f"refused cleanup because a {kind} resource lacks the exact rehearsal run label"
                )

    def _resource_queries(self) -> dict[str, list[str]]:
        project_filter = f"label=com.docker.compose.project={self.project}"
        return {
            "containers": ["docker", "ps", "--all", "--quiet", "--filter", project_filter],
            "volumes": ["docker", "volume", "ls", "--quiet", "--filter", project_filter],
            "networks": ["docker", "network", "ls", "--quiet", "--filter", project_filter],
        }

    def _compose_for(self, config: Path) -> list[str]:
        return [
            "docker",
            "compose",
            "--project-name",
            self.project,
            "--file",
            str(config),
        ]


def write_private_json(path: Path, value: dict[str, Any]) -> None:
    """Create, never overwrite, a private JSON file."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
    except BaseException:
        try:
            path.unlink(missing_ok=True)
        finally:
            raise


def _decode(value: bytes | str) -> str:
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def _lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]
