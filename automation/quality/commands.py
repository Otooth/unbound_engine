"""Small command runner abstraction used by ffmpeg and ffprobe adapters."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    def __post_init__(self) -> None:
        if type(self.returncode) is not int:
            raise ValueError("returncode must be an integer")


type CommandRunner = Callable[[Sequence[str]], object]


class ExternalCommandError(RuntimeError):
    """Raised when an injected or default external command runner fails."""

    def __init__(self, command: Sequence[str], result: CommandResult) -> None:
        self.command = tuple(command)
        self.result = result
        detail = (result.stderr or result.stdout).strip()
        suffix = f": {detail}" if detail else ""
        super().__init__(
            f"{shlex.join(self.command)} exited with {result.returncode}{suffix}"
        )


def default_command_runner(command: Sequence[str]) -> CommandResult:
    completed = subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    raise ValueError("runner stdout and stderr must be text or bytes")


def normalize_command_result(value: object) -> CommandResult:
    """Normalize common fake-runner and subprocess result shapes."""

    if isinstance(value, CommandResult):
        return value
    if isinstance(value, Mapping):
        if "returncode" not in value:
            raise ValueError("runner result is missing returncode")
        return CommandResult(
            returncode=value["returncode"],
            stdout=_text(value.get("stdout")),
            stderr=_text(value.get("stderr")),
        )
    if hasattr(value, "returncode"):
        return CommandResult(
            returncode=getattr(value, "returncode"),
            stdout=_text(getattr(value, "stdout", "")),
            stderr=_text(getattr(value, "stderr", "")),
        )
    raise ValueError(
        "runner must return CommandResult, a mapping, or a CompletedProcess-like "
        "object"
    )


def execute_command(
    command: Sequence[str],
    *,
    runner: CommandRunner | None = None,
) -> CommandResult:
    """Execute a command through an injectable callback and require success."""

    normalized_command = tuple(str(part) for part in command)
    if not normalized_command:
        raise ValueError("command must not be empty")
    result = normalize_command_result(
        (runner or default_command_runner)(normalized_command)
    )
    if result.returncode != 0:
        raise ExternalCommandError(normalized_command, result)
    return result
