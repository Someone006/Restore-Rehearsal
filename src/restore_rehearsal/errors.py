"""Typed errors and stable CLI exit codes."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    REHEARSAL_FAILED = 1
    INVALID_RECIPE = 2
    UNSAFE_CONFIGURATION = 3
    ENVIRONMENT_ERROR = 4
    INTERNAL_ERROR = 5


class RehearsalError(Exception):
    """Base error with an associated process exit code."""

    exit_code = ExitCode.INTERNAL_ERROR


class RecipeError(RehearsalError):
    exit_code = ExitCode.INVALID_RECIPE


class SafetyError(RehearsalError):
    exit_code = ExitCode.UNSAFE_CONFIGURATION


class EnvironmentError(RehearsalError):
    exit_code = ExitCode.ENVIRONMENT_ERROR


class ExecutionError(RehearsalError):
    exit_code = ExitCode.REHEARSAL_FAILED


class CleanupError(RehearsalError):
    exit_code = ExitCode.ENVIRONMENT_ERROR
