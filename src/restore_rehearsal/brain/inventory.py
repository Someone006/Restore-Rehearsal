"""Bounded local-project inventory with optional Git awareness and no symlink following."""

from __future__ import annotations

import hashlib
import os
import re
import selectors
import stat
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pathspec import GitIgnoreSpec

from restore_rehearsal.errors import EnvironmentError, SafetyError

MAX_FILES = 20_000
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_READ_BYTES = 32 * 1024 * 1024
MAX_GIT_PATH_BYTES = 16 * 1024 * 1024
MAX_DIRECTORIES = 50_000
MAX_DEPTH = 100
MAX_IGNORE_BYTES = 2 * 1024 * 1024
MAX_IGNORE_FILES = 1_000
MAX_IGNORE_PATTERNS = 20_000
GIT_TIMEOUT_SECONDS = 30
VCS_DIRECTORIES = {".git", ".hg", ".svn"}

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
    mode: str
    skipped_unsafe_names: int
    ignored_entries: int
    read_bytes: int


@dataclass(frozen=True)
class _IgnoreRuleSet:
    base: PurePosixPath
    spec: GitIgnoreSpec


def inventory_repository(root: Path) -> Inventory:
    repository = root.expanduser().resolve(strict=True)
    if not repository.is_dir():
        raise EnvironmentError("brain project root must be a directory")
    if repository == Path(repository.anchor) or repository == Path.home().resolve():
        raise SafetyError("brain project root must be a dedicated project directory")

    candidates, tracked, commit, mode, unsafe_names, ignored_entries = _candidate_paths(repository)

    scanned: list[ScanFile] = []
    read_bytes = 0
    for relative in candidates:
        if not _safe_relative_name(relative):
            unsafe_names += 1
            continue
        path = repository / PurePosixPath(relative)
        if _is_rehearse_output(relative) or any(
            part in VCS_DIRECTORIES for part in PurePosixPath(relative).parts
        ):
            continue
        try:
            info = path.lstat()
        except OSError as exc:
            raise SafetyError("project entry changed during brain inventory") from exc
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

    return Inventory(
        root=repository,
        files=tuple(scanned),
        commit=commit,
        mode=mode,
        skipped_unsafe_names=unsafe_names,
        ignored_entries=ignored_entries,
        read_bytes=read_bytes,
    )


def _candidate_paths(
    root: Path,
) -> tuple[list[str], set[str], str | None, str, int, int]:
    git_root_raw = _git(root, ["rev-parse", "--show-toplevel"], allow_failure=True)
    if git_root_raw:
        try:
            git_root = Path(os.fsdecode(git_root_raw).strip()).resolve(strict=True)
        except OSError:
            git_root = None
        if git_root is not None and root.is_relative_to(git_root):
            try:
                tracked_items = _git_paths(root, ["ls-files", "--cached", "-z", "--", "."])
                deleted_items = _git_paths(root, ["ls-files", "--deleted", "-z", "--", "."])
                all_items = _git_paths(
                    root,
                    [
                        "ls-files",
                        "--cached",
                        "--others",
                        "--exclude-standard",
                        "-z",
                        "--",
                        ".",
                    ],
                )
            except EnvironmentError:
                pass
            else:
                tracked = {_decode_path(item) for item in tracked_items}
                deleted = {_decode_path(item) for item in deleted_items}
                candidates = sorted({_decode_path(item) for item in all_items} - deleted)
                if len(candidates) > MAX_FILES:
                    raise SafetyError(f"project exceeds the {MAX_FILES}-file brain scan limit")
                candidates, ignored_entries = _filter_rehearse_ignored(root, candidates)
                ignored_entries += len(deleted)
                commit_raw = _git(root, ["rev-parse", "HEAD"], allow_failure=True)
                commit = os.fsdecode(commit_raw).strip() if commit_raw else None
                if commit and not re.fullmatch(r"[0-9a-f]{40,64}", commit):
                    commit = None
                return candidates, tracked, commit, "git", 0, ignored_entries

    candidates, unsafe_names, ignored_entries = _filesystem_paths(root)
    return candidates, set(), None, "filesystem", unsafe_names, ignored_entries


def _filesystem_paths(root: Path) -> tuple[list[str], int, int]:
    root_info = root.stat()
    candidates: list[str] = []
    unsafe_names = 0
    ignored_entries = 0
    directory_count = 1
    path_bytes = 0
    root_rules, ignore_bytes, ignore_files, ignore_patterns = _load_ignore_rules(
        root, PurePosixPath()
    )
    _validate_ignore_totals(ignore_bytes, ignore_files, ignore_patterns)
    stack: list[tuple[Path, PurePosixPath, tuple[_IgnoreRuleSet, ...], int]] = [
        (root, PurePosixPath(), root_rules, 0)
    ]

    while stack:
        directory, relative_directory, rules, depth = stack.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: os.fsencode(entry.name))
        except OSError as exc:
            raise SafetyError("project directory could not be read during brain inventory") from exc
        child_directories: list[tuple[Path, PurePosixPath, tuple[_IgnoreRuleSet, ...], int]] = []
        for entry in entries:
            relative_path = relative_directory / entry.name
            relative = relative_path.as_posix()
            if not _safe_relative_name(relative):
                unsafe_names += 1
                continue
            path_bytes += len(os.fsencode(relative)) + 1
            if path_bytes > MAX_GIT_PATH_BYTES:
                raise SafetyError("project paths exceed the brain path-byte safety limit")
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise SafetyError("project entry changed during brain inventory") from exc
            is_directory = stat.S_ISDIR(info.st_mode)
            if is_directory and entry.name in VCS_DIRECTORIES:
                ignored_entries += 1
                continue
            if _ignored_by_rules(relative_path, is_directory=is_directory, rules=rules):
                ignored_entries += 1
                continue
            if is_directory:
                if info.st_dev != root_info.st_dev:
                    raise SafetyError("project contains a cross-device directory mount")
                if depth >= MAX_DEPTH:
                    raise SafetyError(
                        f"project exceeds the {MAX_DEPTH}-directory-depth brain limit"
                    )
                directory_count += 1
                if directory_count > MAX_DIRECTORIES:
                    raise SafetyError(
                        f"project exceeds the {MAX_DIRECTORIES}-directory brain limit"
                    )
                child_path = Path(entry.path)
                new_rules, added_bytes, added_files, added_patterns = _load_ignore_rules(
                    child_path, relative_path
                )
                ignore_bytes += added_bytes
                ignore_files += added_files
                ignore_patterns += added_patterns
                _validate_ignore_totals(ignore_bytes, ignore_files, ignore_patterns)
                child_rules = rules + new_rules
                child_directories.append((child_path, relative_path, child_rules, depth + 1))
                continue
            if _is_rehearse_output(relative):
                ignored_entries += 1
                continue
            if stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                candidates.append(relative)
                if len(candidates) > MAX_FILES:
                    raise SafetyError(f"project exceeds the {MAX_FILES}-file brain scan limit")
            else:
                ignored_entries += 1
        stack.extend(reversed(child_directories))
    return sorted(candidates), unsafe_names, ignored_entries


def _load_ignore_rules(
    directory: Path,
    relative_directory: PurePosixPath,
    *,
    names: tuple[str, ...] = (".gitignore", ".rehearseignore"),
) -> tuple[tuple[_IgnoreRuleSet, ...], int, int, int]:
    rules: list[_IgnoreRuleSet] = []
    total_bytes = 0
    total_files = 0
    total_patterns = 0
    for name in names:
        source = directory / name
        try:
            info = source.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SafetyError("project ignore file could not be inspected") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            continue
        if info.st_size > MAX_FILE_BYTES:
            raise SafetyError("project ignore file exceeds the brain file-size limit")
        content = _safe_read(directory, source, info)
        total_bytes += len(content)
        total_files += 1
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SafetyError("project ignore file must be UTF-8") from exc
        lines = text.splitlines()
        total_patterns += sum(
            1 for line in lines if line.strip() and not line.lstrip().startswith("#")
        )
        _validate_ignore_totals(total_bytes, total_files, total_patterns)
        rules.append(
            _IgnoreRuleSet(
                base=relative_directory,
                spec=GitIgnoreSpec.from_lines(lines),
            )
        )
    return tuple(rules), total_bytes, total_files, total_patterns


def _validate_ignore_totals(total_bytes: int, total_files: int, total_patterns: int) -> None:
    if (
        total_bytes > MAX_IGNORE_BYTES
        or total_files > MAX_IGNORE_FILES
        or total_patterns > MAX_IGNORE_PATTERNS
    ):
        raise SafetyError("project ignore rules exceed the brain safety limits")


def _filter_rehearse_ignored(root: Path, candidates: list[str]) -> tuple[list[str], int]:
    """Apply nested tool-specific exclusions even when Git supplied the inventory."""

    directories = {PurePosixPath()}
    for value in candidates:
        if not _safe_relative_name(value):
            continue
        parent = PurePosixPath(value).parent
        while parent != PurePosixPath("."):
            directories.add(parent)
            if len(directories) > MAX_DIRECTORIES:
                raise SafetyError(f"project exceeds the {MAX_DIRECTORIES}-directory brain limit")
            parent = parent.parent
    ordered_directories = sorted(
        directories, key=lambda value: (len(value.parts), value.as_posix())
    )
    rules: tuple[_IgnoreRuleSet, ...] = ()
    ignore_bytes = 0
    ignore_files = 0
    ignore_patterns = 0
    for base in ordered_directories:
        if base != PurePosixPath(".") and _path_or_parent_ignored(
            base, is_directory=True, rules=rules
        ):
            continue
        directory = root if base == PurePosixPath(".") else root / base
        new_rules, added_bytes, added_files, added_patterns = _load_ignore_rules(
            directory,
            PurePosixPath() if base == PurePosixPath(".") else base,
            names=(".rehearseignore",),
        )
        ignore_bytes += added_bytes
        ignore_files += added_files
        ignore_patterns += added_patterns
        _validate_ignore_totals(ignore_bytes, ignore_files, ignore_patterns)
        rules += new_rules

    if not rules:
        return candidates, 0
    kept = [
        value
        for value in candidates
        if not _safe_relative_name(value)
        or not _path_or_parent_ignored(PurePosixPath(value), is_directory=False, rules=rules)
    ]
    return kept, len(candidates) - len(kept)


def _path_or_parent_ignored(
    relative: PurePosixPath,
    *,
    is_directory: bool,
    rules: tuple[_IgnoreRuleSet, ...],
) -> bool:
    parts = relative.parts
    for length in range(1, len(parts)):
        if _ignored_by_rules(PurePosixPath(*parts[:length]), is_directory=True, rules=rules):
            return True
    return _ignored_by_rules(relative, is_directory=is_directory, rules=rules)


def _ignored_by_rules(
    relative: PurePosixPath,
    *,
    is_directory: bool,
    rules: tuple[_IgnoreRuleSet, ...],
) -> bool:
    ignored = False
    for rule_set in rules:
        base_parts = rule_set.base.parts
        if relative.parts[: len(base_parts)] != base_parts:
            continue
        local = "/".join(relative.parts[len(base_parts) :])
        if is_directory:
            local += "/"
        result = rule_set.spec.check_file(local)
        if result.include is not None:
            ignored = result.include
    return ignored


def _is_rehearse_output(value: str) -> bool:
    name = PurePosixPath(value).name.lower()
    return ".rrbrain" in name or (name.startswith("project-brain") and name.endswith(".html"))


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
        if allow_failure:
            return b""
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
    if name in {
        ".netrc",
        ".npmrc",
        ".pypirc",
        "auth.json",
        "credentials",
        "credentials.json",
        "id_ed25519",
        "id_rsa",
    }:
        return True
    if name == "config" and any(part in {".aws", ".kube"} for part in lowered[:-1]):
        return True
    if name == "config.json" and ".docker" in lowered[:-1]:
        return True
    if Path(name).suffix in {".key", ".pem", ".p12", ".pfx", ".jks", ".kdbx"}:
        return True
    sensitive_directories = {
        ".aws",
        ".azure",
        ".gnupg",
        ".kube",
        ".secrets",
        ".ssh",
        "backups",
        "credentials",
        "secrets",
    }
    return any(part in sensitive_directories for part in lowered)


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


def _stat_matches_open_file(
    expected: os.stat_result,
    actual: os.stat_result,
    *,
    require_identity: bool,
) -> bool:
    if not stat.S_ISREG(actual.st_mode) or actual.st_size != expected.st_size:
        return False
    if require_identity:
        return actual.st_dev == expected.st_dev and actual.st_ino == expected.st_ino
    # Windows does not guarantee comparable identity or timestamp fields between path stat and
    # open-handle fstat calls. The caller rechecks canonical containment, symlinks, type, and size
    # after reading; POSIX keeps the stronger device/inode identity check above.
    return True


def _safe_read(root: Path, path: Path, expected: os.stat_result) -> bytes:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SafetyError("project file changed during brain inventory") from exc
    if not resolved.is_relative_to(root):
        raise SafetyError("project path escapes the selected brain root")
    for parent in path.parents:
        if parent == root:
            break
        try:
            if parent.is_symlink():
                raise SafetyError("brain scanner refuses files below a symlinked directory")
        except OSError as exc:
            raise SafetyError("project path changed during brain inventory") from exc
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SafetyError("project file changed during brain inventory") from exc
    try:
        actual = os.fstat(descriptor)
        require_identity = os.name != "nt"
        if not _stat_matches_open_file(expected, actual, require_identity=require_identity):
            raise SafetyError("project file changed during brain inventory")
        chunks: list[bytes] = []
        remaining = actual.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise SafetyError("project file changed during brain inventory")
            chunks.append(chunk)
            remaining -= len(chunk)
        try:
            resolved_after = path.resolve(strict=True)
            current = path.lstat()
        except OSError as exc:
            raise SafetyError("project file changed during brain inventory") from exc
        if (
            not resolved_after.is_relative_to(root)
            or stat.S_ISLNK(current.st_mode)
            or not _stat_matches_open_file(current, actual, require_identity=require_identity)
        ):
            raise SafetyError("project file changed during brain inventory")
        return b"".join(chunks)
    finally:
        os.close(descriptor)
