"""Small, local-only MCP server for bounded Project Brain queries.

The transport is newline-delimited JSON-RPC over caller-provided streams.  It
opens no sockets and deliberately exposes a much smaller surface than the CLI.
"""

from __future__ import annotations

import json
import stat
import sys
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from restore_rehearsal import __version__
from restore_rehearsal.adapters import adapter_catalog
from restore_rehearsal.brain.api import (
    brain_context,
    brain_diff,
    brain_doctor,
    brain_findings,
    brain_impact,
    brain_imports,
    brain_memories,
    brain_query,
    brain_remember,
    brain_snapshots,
    brain_summary,
    lens_catalog,
)
from restore_rehearsal.errors import RehearsalError, SafetyError

MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
MAX_LIST_RESULTS = 100
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")


@dataclass(frozen=True)
class _Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[Path, dict[str, Any]], Any]
    writes: bool = False

    def descriptor(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


_EMPTY_OBJECT: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_SNAPSHOT_REFERENCE_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "integer", "minimum": 1, "maximum": 9_223_372_036_854_775_807},
        {
            "type": "string",
            "minLength": 1,
            "maxLength": 24,
            "pattern": "^(latest|[0-9]+|[0-9a-f]{24})$",
        },
    ]
}


def _object_schema(properties: dict[str, Any], *, required: tuple[str, ...] = ()) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


_TOOLS = (
    _Tool(
        "brain_summary",
        "Return bounded Project Brain metadata and graph counts.",
        _EMPTY_OBJECT,
        lambda brain, _arguments: brain_summary(brain),
    ),
    _Tool(
        "brain_query",
        "Search bounded project artifacts, findings, and durable memories.",
        _object_schema(
            {
                "query": {"type": "string", "minLength": 1, "maxLength": 500},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            required=("query",),
        ),
        lambda brain, arguments: brain_query(
            brain,
            _string(arguments, "query", maximum=500),
            limit=_integer(arguments, "limit", default=20, minimum=1, maximum=50),
        ),
    ),
    _Tool(
        "brain_context",
        "Return a bounded relationship neighborhood for one node or relative path.",
        _object_schema(
            {
                "target": {"type": "string", "minLength": 1, "maxLength": 500},
                "depth": {"type": "integer", "minimum": 0, "maximum": 3},
            },
            required=("target",),
        ),
        lambda brain, arguments: brain_context(
            brain,
            _string(arguments, "target", maximum=500),
            depth=_integer(arguments, "depth", default=2, minimum=0, maximum=3),
        ),
    ),
    _Tool(
        "brain_findings",
        "List up to 100 open evidence-backed review findings.",
        _object_schema(
            {
                "lens": {"type": "string", "minLength": 1, "maxLength": 64},
                "severity": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                },
            }
        ),
        lambda brain, arguments: _bounded_list(
            brain_findings(
                brain,
                lens=_optional_string(arguments, "lens", maximum=64),
                severity=_optional_string(arguments, "severity", maximum=16),
            )
        ),
    ),
    _Tool(
        "brain_memories",
        "List up to 100 durable memory records without their private bodies.",
        _object_schema(
            {
                "kind": {
                    "type": "string",
                    "enum": [
                        "decision",
                        "attempt",
                        "failure",
                        "fix",
                        "note",
                        "risk-acceptance",
                    ],
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "superseded", "deleted"],
                },
            }
        ),
        lambda brain, arguments: _bounded_list(
            brain_memories(
                brain,
                kind=_optional_string(arguments, "kind", maximum=32),
                status=_optional_string(arguments, "status", maximum=16),
            )
        ),
    ),
    _Tool(
        "brain_snapshots",
        "List bounded structural snapshots in newest-first order.",
        _EMPTY_OBJECT,
        lambda brain, _arguments: _bounded_list(brain_snapshots(brain)),
    ),
    _Tool(
        "brain_diff",
        "Compare two structural snapshots without reading source files.",
        _object_schema(
            {
                "from_snapshot": _SNAPSHOT_REFERENCE_SCHEMA,
                "to_snapshot": _SNAPSHOT_REFERENCE_SCHEMA,
            }
        ),
        lambda brain, arguments: brain_diff(
            brain,
            from_snapshot=_optional_snapshot_reference(arguments, "from_snapshot"),
            to_snapshot=_optional_snapshot_reference(arguments, "to_snapshot"),
        ),
    ),
    _Tool(
        "brain_impact",
        "Trace a bounded relationship neighborhood from structural snapshot changes.",
        _object_schema(
            {
                "from_snapshot": _SNAPSHOT_REFERENCE_SCHEMA,
                "to_snapshot": _SNAPSHOT_REFERENCE_SCHEMA,
                "depth": {"type": "integer", "minimum": 0, "maximum": 3},
            }
        ),
        lambda brain, arguments: brain_impact(
            brain,
            from_snapshot=_optional_snapshot_reference(arguments, "from_snapshot"),
            to_snapshot=_optional_snapshot_reference(arguments, "to_snapshot"),
            depth=_integer(arguments, "depth", default=2, minimum=0, maximum=3),
        ),
    ),
    _Tool(
        "brain_imports",
        "List up to 100 normalized evidence-import summaries without raw artifact bodies.",
        _EMPTY_OBJECT,
        lambda brain, _arguments: _bounded_list(brain_imports(brain)),
    ),
    _Tool(
        "brain_lenses",
        "List the bundled evidence-review lenses.",
        _EMPTY_OBJECT,
        lambda _brain, _arguments: list(lens_catalog()),
    ),
    _Tool(
        "recovery_adapters",
        "List built-in offline recovery adapters and their required configuration.",
        _EMPTY_OBJECT,
        lambda _brain, _arguments: list(adapter_catalog()),
    ),
    _Tool(
        "brain_remember",
        "Append a non-secret decision, attempt, failure, fix, note, or risk acceptance.",
        _object_schema(
            {
                "kind": {
                    "type": "string",
                    "enum": [
                        "decision",
                        "attempt",
                        "failure",
                        "fix",
                        "note",
                        "risk-acceptance",
                    ],
                },
                "title": {"type": "string", "minLength": 1, "maxLength": 200},
                "body": {"type": "string", "minLength": 1, "maxLength": 16384},
                "related_path": {"type": "string", "maxLength": 500},
                "supersedes": {
                    "type": "string",
                    "pattern": "^[0-9a-f]{24}$",
                },
            },
            required=("kind", "title", "body"),
        ),
        lambda brain, arguments: {
            "recorded": True,
            "memory_id": brain_remember(
                brain,
                kind=_string(arguments, "kind", maximum=32),
                title=_string(arguments, "title", maximum=200),
                body=_string(arguments, "body", maximum=16384),
                related_path=_optional_string(arguments, "related_path", maximum=500),
                supersedes=_optional_string(arguments, "supersedes", maximum=24),
            ),
        },
        writes=True,
    ),
)


def serve_mcp(
    brain: Path,
    allow_write: bool = False,
    input: TextIO | None = None,
    output: TextIO | None = None,
) -> None:
    """Serve a fixed MCP tool set over local stdio until EOF.

    `input` and `output` are injectable so callers can embed the server and tests
    can exercise it without opening a process.  stdout must be reserved for the
    protocol when the default streams are used.
    """

    source = input if input is not None else sys.stdin
    destination = output if output is not None else sys.stdout
    unresolved = brain.expanduser()
    try:
        brain_info = unresolved.lstat()
    except OSError as exc:
        raise SafetyError("MCP Project Brain could not be inspected") from exc
    if stat.S_ISLNK(brain_info.st_mode):
        raise SafetyError("MCP Project Brain must not be a symlink")
    brain_path = unresolved.resolve(strict=True)
    if not brain_path.is_file():
        raise SafetyError("MCP Project Brain must be a regular file")
    brain_doctor(brain_path)
    tools = {tool.name: tool for tool in _TOOLS if allow_write or not tool.writes}

    while True:
        line = source.readline(MAX_REQUEST_BYTES + 1)
        if line == "":
            return
        if len(line.encode("utf-8", errors="replace")) > MAX_REQUEST_BYTES:
            _drain_line(source, line)
            _write_response(destination, _error(None, -32600, "request exceeds the size limit"))
            continue
        response = _handle_line(line, brain_path, tools)
        if response is not None:
            _write_response(destination, response)


def _handle_line(line: str, brain: Path, tools: dict[str, _Tool]) -> dict[str, Any] | None:
    try:
        request = json.loads(line)
    except (json.JSONDecodeError, UnicodeError):
        return _error(None, -32700, "invalid JSON")
    if not isinstance(request, dict):
        return _error(None, -32600, "request must be a JSON object")
    request_id = request.get("id")
    if not _valid_request_id(request_id):
        return _error(None, -32600, "request id is invalid")
    if request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
        return _error(request_id, -32600, "invalid JSON-RPC request")
    if set(request) - {"jsonrpc", "id", "method", "params"}:
        return _error(request_id, -32600, "request contains unknown fields")
    method = request["method"]
    params = request.get("params", {})
    if not isinstance(params, dict):
        return _error(request_id, -32602, "params must be an object")
    is_notification = "id" not in request

    if method == "notifications/initialized":
        return None if is_notification else _success(request_id, {})
    if method == "ping":
        return None if is_notification else _success(request_id, {})
    if method == "initialize":
        if is_notification:
            return None
        if set(params) - {"protocolVersion", "capabilities", "clientInfo"}:
            return _error(request_id, -32602, "initialize contains unknown fields")
        requested = params.get("protocolVersion")
        protocol = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        return _success(
            request_id,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "rehearse", "version": __version__},
                "instructions": (
                    "Local Project Brain metadata can still be sensitive. Never send credentials "
                    "or backup contents online; obtain approval before an online agent receives "
                    "other project or personal data."
                ),
            },
        )
    if method == "tools/list":
        if is_notification:
            return None
        if params:
            return _error(request_id, -32602, "tools/list takes no parameters")
        return _success(
            request_id,
            {"tools": [tool.descriptor() for tool in tools.values()]},
        )
    if method == "tools/call":
        if is_notification:
            return None
        if set(params) - {"name", "arguments"}:
            return _error(request_id, -32602, "tools/call contains unknown fields")
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or name not in tools:
            return _tool_response(request_id, error="unknown or unavailable tool")
        if not isinstance(arguments, dict):
            return _tool_response(request_id, error="tool arguments must be an object")
        try:
            _reject_unknown_arguments(arguments, tools[name].input_schema)
            value = tools[name].handler(brain, arguments)
            return _tool_response(request_id, value=value)
        except RehearsalError as exc:
            return _tool_response(request_id, error=_safe_error(exc))
        except (TypeError, ValueError, UnicodeError):
            return _tool_response(request_id, error="tool arguments are invalid")
        except Exception:
            return _tool_response(request_id, error="tool failed safely")
    if is_notification:
        return None
    return _error(request_id, -32601, "method not found")


def _tool_response(
    request_id: str | int | None, *, value: Any = None, error: str | None = None
) -> dict[str, Any]:
    if error is not None:
        result = {
            "content": [{"type": "text", "text": error}],
            "isError": True,
        }
    else:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"))
        if len(text.encode("utf-8")) > MAX_RESPONSE_BYTES:
            return _tool_response(request_id, error="tool result exceeds the response limit")
        result = {
            "content": [{"type": "text", "text": text}],
            "structuredContent": value,
            "isError": False,
        }
    return _success(request_id, result)


def _success(request_id: str | int | None, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: str | int | None, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _write_response(destination: TextIO, response: dict[str, Any]) -> None:
    encoded = json.dumps(response, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_RESPONSE_BYTES:
        encoded = json.dumps(
            _error(response.get("id"), -32603, "response exceeds the size limit"),
            sort_keys=True,
            separators=(",", ":"),
        )
    destination.write(encoded + "\n")
    destination.flush()


def _drain_line(source: TextIO, first: str) -> None:
    if first.endswith("\n"):
        return
    while True:
        remainder = source.readline(MAX_REQUEST_BYTES + 1)
        if not remainder or remainder.endswith("\n"):
            return


def _reject_unknown_arguments(arguments: dict[str, Any], schema: dict[str, Any]) -> None:
    allowed = set(schema.get("properties", {}))
    if set(arguments) - allowed:
        raise SafetyError("tool arguments contain unknown fields")
    missing = set(schema.get("required", [])) - arguments.keys()
    if missing:
        raise SafetyError("tool arguments are missing required fields")


def _string(arguments: dict[str, Any], name: str, *, maximum: int) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise SafetyError(f"{name} must contain 1 to {maximum} characters")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise SafetyError(f"{name} contains unsupported control characters")
    return value


def _optional_string(arguments: dict[str, Any], name: str, *, maximum: int) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    return _string(arguments, name, maximum=maximum)


def _optional_snapshot_reference(arguments: dict[str, Any], name: str) -> str | int | None:
    value = arguments.get(name)
    if value is None:
        return None
    if isinstance(value, bool):
        raise SafetyError(f"{name} is not a valid snapshot reference")
    if isinstance(value, int):
        if not 1 <= value <= 9_223_372_036_854_775_807:
            raise SafetyError(f"{name} is not a valid snapshot reference")
        return value
    if not isinstance(value, str):
        raise SafetyError(f"{name} is not a valid snapshot reference")
    if value == "latest":
        return value
    if value.isascii() and value.isdigit() and 1 <= len(value) <= 19 and int(value) >= 1:
        return value
    if len(value) == 24 and all(character in "0123456789abcdef" for character in value):
        return value
    raise SafetyError(f"{name} is not a valid snapshot reference")


def _integer(
    arguments: dict[str, Any],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise SafetyError(f"{name} must be from {minimum} to {maximum}")
    return value


def _bounded_list(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "items": items[:MAX_LIST_RESULTS],
        "truncated": len(items) > MAX_LIST_RESULTS,
    }


def _valid_request_id(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return False
    return not isinstance(value, str) or len(value) <= 100


def _safe_error(error: BaseException) -> str:
    value = str(error)[:500]
    return "".join(
        character
        if character in "\n\t" or not unicodedata.category(character).startswith("C")
        else "?"
        for character in value
    )
