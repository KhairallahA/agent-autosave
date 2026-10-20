"""GitHub Copilot CLI hook payloads -> events, without I/O.

From https://docs.github.com/en/copilot/reference/hooks-reference (checked
2026-10-19). Our hooks file uses the PascalCase event names, so payloads
come in the "VS Code compatible" format: hook_event_name, session_id,
timestamp (ISO 8601), cwd, snake_case fields. SessionStart.source is
startup/resume/new; UserPromptSubmit.prompt; PostToolUse.tool_name,
tool_input, tool_result; PostToolUseFailure.error; Stop.stop_reason;
PreCompact.trigger; SessionEnd.reason. Runtime tool names: bash,
powershell, create, edit, view, grep, glob, web_fetch (Claude names are
accepted too: the docs map them for PreToolUse). Not yet recorded.
"""
from __future__ import annotations

from typing import Any

from . import mapped

EVENTS = {"SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "Stop", "PreCompact",
          "SessionEnd"}
TOOLS = {"bash": "Bash", "powershell": "Bash", "create": "Write", "edit": "Edit", "str_replace_editor": "Edit",
         "apply_patch": "Edit", "view": "Read", "grep": "Grep", "rg": "Grep", "glob": "Glob",
         "web_fetch": "WebFetch", "web_search": "WebSearch"}
RENAMES = {"path": "file_path", "filePath": "file_path"}


def normalize(payload: dict[str, Any], *, ts: str, turn: int, tasks: dict[str, Any] | None = None,
              **_: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    name = payload.get("hook_event_name")
    if name not in EVENTS or not isinstance(payload.get("session_id"), str):
        raise ValueError("Unsupported Copilot CLI hook payload.")
    base = {"hook_event_name": name, "session_id": payload["session_id"], "cwd": payload.get("cwd") or ""}
    if name == "SessionStart":
        source = payload.get("source")
        shaped = dict(base, source=source if source in ("startup", "resume") else "startup")
    elif name == "UserPromptSubmit":
        shaped = dict(base, prompt=str(payload.get("prompt") or ""))
    elif name == "Stop":
        shaped = dict(base, last_assistant_message="")
    elif name == "PreCompact":
        shaped = dict(base, trigger=payload.get("trigger") or "auto")
    elif name == "SessionEnd":
        shaped = dict(base, reason=str(payload.get("reason") or "other"))
    else:
        tool = str(payload.get("tool_name") or "tool")
        error = payload.get("error") if name == "PostToolUseFailure" else None
        shaped = mapped.tool_payload(base, TOOLS.get(tool, tool), mapped.tool_input(payload.get("tool_input"), RENAMES),
                                     str(error or "failed") if name == "PostToolUseFailure" else None)
    return mapped.run("copilot", shaped, ts=ts, turn=turn, tasks=tasks)
