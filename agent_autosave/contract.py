"""Public CLI envelopes, stream selection, and stable exit codes."""
from __future__ import annotations

import json
import sys
from enum import IntEnum
from typing import Any, Dict, Optional, TextIO, Tuple

from . import SAVE_VERSION, __version__


class ExitCode(IntEnum):
    SUCCESS = 0
    FAILED = 1  # not_found, conflict, permission
    INVALID_ARGS = 2
    INTERNAL = 5  # io, internal


# error.code (the specific, stable id) -> error.type (the category callers branch on).
ERROR_TYPES = {
    "invalid_args": "invalid_args",
    "invalid_config": "invalid_args",
    "not_supported": "invalid_args",
    "not_initialized": "not_found",
    "no_save": "not_found",
    "nothing_to_resume": "not_found",
    "nothing_to_finish": "not_found",
    "unknown_task": "not_found",
    "unknown_entry": "not_found",
    "no_vault": "not_found",
    "no_handoff": "not_found",
    "not_installed": "not_found",
    "agent_not_installed": "not_found",
    "edited_config": "conflict",
    "secrets_found": "conflict",
    "user_level_needed": "conflict",
    "codex_untrusted": "permission",
    "adapter_problem": "io",
    "command_failed": "internal",
}
TYPE_EXITS = {
    "invalid_args": ExitCode.INVALID_ARGS,
    "not_found": ExitCode.FAILED,
    "conflict": ExitCode.FAILED,
    "permission": ExitCode.FAILED,
    "io": ExitCode.INTERNAL,
    "internal": ExitCode.INTERNAL,
}

# What every command returns: the envelope, the exit code, and the human text
# (None prints the default for success and only message and hint for failure).
Outcome = Tuple[Dict[str, Any], "ExitCode", Optional[str]]


def metadata(elapsed_ms: int = 0) -> Dict[str, Any]:
    return {
        "version": __version__,
        "save_v": SAVE_VERSION,
        "elapsed_ms": max(0, elapsed_ms),
    }


def success(data: Dict[str, Any], *, elapsed_ms: int = 0) -> Dict[str, Any]:
    return {"ok": True, "data": data, "meta": metadata(elapsed_ms)}


def failure(
    code: str, message: str, hint: str, *, detail: Optional[Dict[str, Any]] = None, elapsed_ms: int = 0
) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": {"type": ERROR_TYPES[code], "code": code, "message": message, "hint": hint,
                  "detail": detail or {}},
        "meta": metadata(elapsed_ms),
    }


def fail(code: str, message: str, hint: str, *, detail: Optional[Dict[str, Any]] = None,
         text: Optional[str] = None) -> Outcome:
    """A failed Outcome; the exit code follows from the error type."""
    return failure(code, message, hint, detail=detail), TYPE_EXITS[ERROR_TYPES[code]], text


def emit(
    result: Dict[str, Any],
    *,
    json_output: bool = False,
    quiet: bool = False,
    text: Optional[str] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> None:
    """Emit one JSON object when piped; --quiet makes success silent unless --json.

    Failures use the plan's ``error`` member instead of ``data``. Hook traffic
    bypasses this renderer because stdout belongs to the agent's hook protocol.
    """
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    if result["ok"] and quiet and not json_output:
        return  # --quiet: silent success, even when piped (agents' tool output)
    if json_output or not out.isatty():
        print(json.dumps(result, ensure_ascii=True), file=out)
    elif not result["ok"]:
        from .ui import Style, color_wanted, unicode_ok
        style = Style(color_wanted(err), unicode_ok(err))
        print(style.paint("error:", "bold", "red") + " " + result["error"]["message"], file=err)
        print(style.paint("hint:", "yellow") + " " + result["error"]["hint"], file=err)
        if text:
            print(text, file=err)
    elif not quiet:
        print(text if text is not None else json.dumps(result["data"]), file=out)
