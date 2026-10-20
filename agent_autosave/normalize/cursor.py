"""Cursor hook payloads -> normalized events, without I/O.

Shapes follow Cursor's hooks docs (cursor.com/docs/hooks, checked 2026-10-15).
Fixtures in tests/fixtures/cursor/ are doc-derived until real recordings
arrive (see docs/adapters/cursor.md).

- The conversation id is the session (sessionStart's `session_id` equals it).
- Shell exit codes come from postToolUse's `tool_output` JSON (`exitCode`);
  afterShellExecution carries no exit code, so it is not wired.
- File edits come from afterFileEdit (`file_path`, absolute).
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from . import vendor_id

EVENTS = {"sessionStart", "beforeSubmitPrompt", "postToolUse", "postToolUseFailure",
          "afterFileEdit", "preCompact", "stop", "sessionEnd"}
TAIL = 20


def session_of(payload: dict[str, Any]) -> str | None:
    for key in ("conversation_id", "session_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def root_of(payload: dict[str, Any]) -> str | None:
    roots = payload.get("workspace_roots")
    if isinstance(roots, list) and roots and isinstance(roots[0], str):
        return roots[0]
    return payload.get("cwd") if isinstance(payload.get("cwd"), str) else None


def _relative(path, root):
    candidate = Path(str(path))
    if root and candidate.is_absolute():
        try:
            return str(candidate.relative_to(Path(root)))
        except ValueError:
            pass
    return str(candidate)


def _shell_result(output):
    """(exit, tail) from postToolUse's JSON-stringified tool output."""
    try:
        data = json.loads(output) if isinstance(output, str) else output
    except ValueError:
        return None, ""
    if not isinstance(data, dict):
        return None, ""
    code = data.get("exitCode", data.get("exit_code"))
    code = code if type(code) is int else None
    text = "\n".join(str(data.get(k) or "") for k in ("stdout", "stderr") if data.get(k))
    return code, "\n".join(text.splitlines()[-TAIL:])


def normalize(payload: dict[str, Any], *, ts: str, turn: int,
              tasks: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    name = payload.get("hook_event_name")
    session = session_of(payload)
    if name not in EVENTS or session is None:
        raise ValueError("Unsupported Cursor hook payload.")
    tasks = deepcopy(tasks or {})
    base = dict(v=1, ts=ts, agent="cursor", session=session, turn=turn)

    def row(kind, **fields):
        return dict(base, kind=kind, **fields)

    if name == "sessionStart":
        fields = {"source": "startup"}
        if isinstance(payload.get("model"), str):
            fields["model"] = payload["model"]
        return [row("session_start", **fields)], tasks
    if name == "beforeSubmitPrompt":
        return [row("prompt", text=str(payload.get("prompt") or ""), **vendor_id(payload, "generation_id"))], tasks
    if name in ("postToolUse", "postToolUseFailure"):
        if payload.get("tool_name") != "Shell":
            return [], tasks
        tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        command = str(tool_input.get("command") or "").strip()
        if not command:
            return [], tasks
        if name == "postToolUse":
            code, tail = _shell_result(payload.get("tool_output"))
        else:
            code, tail = None, ""  # failed/timed out/denied: no exit code reported
        fields = dict({"tool": "Shell", "summary": command, "exit": code, "files": []}, **vendor_id(payload, "tool_use_id"))
        if tail:
            fields["output_tail"] = tail  # kept only for a failure (events.prepare, shellexit.judge)
        return [row("tool", **fields)], tasks
    if name == "afterFileEdit":
        path = payload.get("file_path")
        if not isinstance(path, str) or not path:
            return [], tasks
        relative = _relative(path, root_of(payload))
        return [row("tool", tool="Write", summary="Edited " + relative, files=[relative])], tasks
    if name == "preCompact":
        trigger = payload.get("trigger") if payload.get("trigger") in ("manual", "auto") else "auto"
        return [row("compact", trigger=trigger)], tasks
    if name == "stop":
        if payload.get("status") == "error":
            return [row("interrupted", reason="unknown")], tasks
        return [row("turn_end")], tasks
    # sessionEnd
    return [row("session_end", reason=str(payload.get("reason") or "other"))], tasks

