"""Git-aware, bounded repository inventory that never follows symlinks."""

from __future__ import annotations

import hashlib
import os
import selectors
import stat
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from restore_rehearsal.errors import EnvironmentError, SafetyError

MAX_FILES = 20_000
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_READ_BYTES = 32 * 1024 * 1024
MAX_GIT_PATH_BYTES = 16 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 30

TEXT_SUFFIXES = {
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".md",
    ".mjs",
    ".py",
    ".rb",
    ".rs",
    ".scss",
    ".sh",
    ".sql",
    ".svelte",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True)
class ScanFile:
    path: str
    absolute_path: Path
    size: int
    sha256: str | None
    text: str | None
    tracked: bool
    sensitive: bool
    skipped_reason: str | None
    is_symlink: bool = False


@dataclass(frozen=True)
class Inventory:
    root: Path
    files: tuple[ScanFile, ...]
    commit: str | None
    skipped_unsafe_names: int
    read_bytes: int


def inventory_repository(root: Path) -> Inventory:
    repository = root.expanduser().resolve(strict=True)
    if not repository.is_dir():
        raise EnvironmentError("brain project root must be a directory")
    git_root = _git(repository, ["rev-parse", "--show-toplevel"])
    try:
        canonical_git_root = Path(os.fsdecode(git_root).strip()).resolve(strict=True)
    except OSError as exc:
        raise EnvironmentError("could not resolve the Git repository root") from exc
    if canonical_git_root != repository:
        raise SafetyError("brain build must run at the top level of a Git repository")

    tracked_items = _git_paths(repository, ["ls-files", "--cached", "-z"])
    all_items = _git_paths(
        repository,
        ["ls-files", "--cached", "--others", "--exclude-standard", "-z"],
    )
    tracked = {_decode_path(item) for item in tracked_items}
    candidates = sorted({_decode_path(item) for item in all_items})
    if len(candidates) > MAX_FILES:
        raise SafetyError(f"repository exceeds the {MAX_FILES}-file brain scan limit")

    scanned: list[ScanFile] = []
    unsafe_names = 0
    read_bytes = 0
    for relative in candidates:
        if not _safe_relative_name(relative):
            unsafe_names += 1
            continue
        path = repository / PurePosixPath(relative)
        name = PurePosixPath(relative).name.lower()
        if (
            ".rrbrain" in name
            or (name.startswith("project-brain") and name.endswith(".html"))
            or ".git" in PurePosixPath(relative).parts
        ):
            continue
        try:
            info = path.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode):
            scanned.append(
                ScanFile(
                    path=relative,
                    absolute_path=path,
                    size=0,
                    sha256=None,
                    text=None,
                    tracked=relative in tracked,
                    sensitive=False,
                    skipped_reason="symlink",
                    is_symlink=True,
                )
            )
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        sensitive = _sensitive_path(relative)
        if sensitive:
            scanned.append(
                ScanFile(
                    path=relative,
                    absolute_path=path,
                    size=info.st_size,
                    sha256=None,
                    text=None,
                    tracked=relative in tracked,
                    sensitive=True,
                    skipped_reason="sensitive-path",
                )
            )
            continue
        if info.st_size > MAX_FILE_BYTES:
            scanned.append(
                ScanFile(
                    path=relative,
                    absolute_path=path,
                    size=info.st_size,
                    sha256=None,
                    text=None,
                    tracked=relative in tracked,
                    sensitive=False,
                    skipped_reason="file-size-limit",
                )
            )
            continue
        if read_bytes + info.st_size > MAX_TOTAL_READ_BYTES:
            scanned.append(
                ScanFile(
                    path=relative,
                    absolute_path=path,
                    size=info.st_size,
                    sha256=None,
                    text=None,
                    tracked=relative in tracked,
                    sensitive=False,
                    skipped_reason="total-read-limit",
                )
            )
            continue
        content = _safe_read(repository, path, info)
        read_bytes += len(content)
        digest = hashlib.sha256(content).hexdigest()
        text = None
        if _may_parse_text(relative) and b"\0" not in content[:8192]:
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                text = None
        scanned.append(
            ScanFile(
                path=relative,
                absolute_path=path,
                size=info.st_size,
                sha256=digest,
                text=text,
                tracked=relative in tracked,
                sensitive=False,
                skipped_reason=None if text is not None else "binary-or-unsupported-text",
            )
        )

    commit_raw = _git(repository, ["rev-parse", "HEAD"], allow_failure=True)
    commit = os.fsdecode(commit_raw).strip() if commit_raw else None
    if commit and not __import__("re").fullmatch(r"[0-9a-f]{40,64}", commit):
        commit = None
    return Inventory(
        root=repository,
        files=tuple(scanned),
        commit=commit,
        skipped_unsafe_names=unsafe_names,
        read_bytes=read_bytes,
    )


def _git(root: Path, arguments: list[str], *, allow_failure: bool = False) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(root), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EnvironmentError("could not inventory the Git repository") from exc
    if result.returncode != 0:
        if allow_failure:
            return b""
        raise EnvironmentError("brain build requires a readable Git repository")
    return result.stdout


def _git_paths(root: Path, arguments: list[str]) -> tuple[bytes, ...]:
    command = ["git", "-c", "core.fsmonitor=false", "-C", str(root), *arguments]
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
    except OSError as exc:
        raise EnvironmentError("could not inventory the Git repository") from exc
    if process.stdout is None:
        process.kill()
        raise EnvironmentError("could not inventory the Git repository")

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
    output = bytearray()
    path_count = 0
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, GIT_TIMEOUT_SECONDS)
            events = selector.select(timeout=remaining)
            if not events:
                if process.poll() is not None:
                    break
                raise subprocess.TimeoutExpired(command, GIT_TIMEOUT_SECONDS)
            chunk = os.read(process.stdout.fileno(), 64 * 1024)
            if not chunk:
                break
            output.extend(chunk)
            path_count += chunk.count(b"\0")
            if len(output) > MAX_GIT_PATH_BYTES or path_count > MAX_FILES:
                raise SafetyError(
                    f"repository exceeds the {MAX_FILES}-file or Git-path-byte brain limit"
                )
        return_code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        if return_code != 0 or (output and not output.endswith(b"\0")):
            raise EnvironmentError("brain build requires a readable Git repository")
        return tuple(bytes(item) for item in output.split(b"\0") if item)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EnvironmentError("could not inventory the Git repository") from exc
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
        process.wait()


def _decode_path(value: bytes) -> str:
    return os.fsdecode(value).replace(os.sep, "/")


def _safe_relative_name(value: str) -> bool:
    if not value or any(unicodedata.category(character).startswith("C") for character in value):
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts


def _sensitive_path(value: str) -> bool:
    path = PurePosixPath(value)
    lowered = [part.lower() for part in path.parts]
    name = lowered[-1]
    if name == ".env" or name.startswith(".env."):
        return True
    if name in {"id_rsa", "id_ed25519", "credentials", "credentials.json"}:
        return True
    if Path(name).suffix in {".key", ".pem", ".p12", ".pfx", ".jks", ".kdbx"}:
        return True
    return any(part in {"secrets", ".secrets", "credentials", "backups"} for part in lowered)


def _may_parse_text(value: str) -> bool:
    name = PurePosixPath(value).name.lower()
    if name in {
        "dockerfile",
        "license",
        "notice",
        "makefile",
        "procfile",
        "requirements.txt",
    }:
        return True
    return Path(name).suffix in TEXT_SUFFIXES


def _safe_read(root: Path, path: Path, expected: os.stat_result) -> bytes:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SafetyError("repository file changed during brain inventory") from exc
    if not resolved.is_relative_to(root):
        raise SafetyError("repository path escapes the selected brain root")
    for parent in path.parents:
        if parent == root:
            break
        try:
            if parent.is_symlink():
                raise SafetyError("brain scanner refuses files below a symlinked directory")
        except OSError as exc:
            raise SafetyError("repository path changed during brain inventory") from exc
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SafetyError("repository file changed during brain inventory") from exc
    try:
        actual = os.fstat(descriptor)
        if (
            not stat.S_ISREG(actual.st_mode)
            or actual.st_dev != expected.st_dev
            or actual.st_ino != expected.st_ino
            or actual.st_size != expected.st_size
        ):
            raise SafetyError("repository file changed during brain inventory")
        chunks: list[bytes] = []
        remaining = actual.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise SafetyError("repository file changed during brain inventory")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)
