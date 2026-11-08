"""Conservative text redaction for console errors and bounded diagnostics."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path


class Redactor:
    def __init__(
        self,
        *,
        secret_values: Iterable[str] = (),
        private_paths: Iterable[Path | str] = (),
    ) -> None:
        self._secrets = tuple(
            sorted(
                {str(value) for value in secret_values if len(str(value)) >= 4},
                key=len,
                reverse=True,
            )
        )
        self._paths = tuple(
            sorted({str(value) for value in private_paths if str(value)}, key=len, reverse=True)
        )

    def redact(self, value: object) -> str:
        text = str(value)
        for secret in self._secrets:
            text = text.replace(secret, "[REDACTED]")
        for path in self._paths:
            text = text.replace(path, "[PRIVATE_PATH]")
        text = re.sub(
            r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization)"
            r"(\s*[:=]\s*)([^\s,;]+)",
            r"\1\2[REDACTED]",
            text,
        )
        text = re.sub(
            r"(?i)\b(https?://)([^/@\s:]+):([^/@\s]+)@",
            r"\1[REDACTED]@",
            text,
        )
        text = re.sub(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b", "[REDACTED]", text)
        text = re.sub(r"\bAKIA[0-9A-Z]{16}\b", "[REDACTED]", text)
        text = re.sub(
            r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
            "[REDACTED]",
            text,
        )
        return "".join(
            character if character in "\n\t" or ord(character) >= 32 else "?" for character in text
        )
