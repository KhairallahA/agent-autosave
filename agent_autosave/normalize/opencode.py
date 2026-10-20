"""OpenCode plugin payloads -> events, without I/O.

OpenCode has no command hooks; its plugins are JavaScript modules in
.opencode/plugins/ (https://opencode.ai/docs/plugins/, checked
2026-10-19). Our plugin (adapters/opencode/agent-autosave.js) builds this
small payload itself and pipes it to `agent-autosave hook opencode <event>`:
{session_id, cwd, tool?, args?, error?}. Event names are the documented
ones: session.created, session.idle, session.compacted, tool.execute.after.
Tool names and args from the docs' examples: bash (command), read and
edit (filePath). Not yet recorded.
"""
from __future__ import annotations

from typing import Any

from . import mapped

EVENTS = {"session.created": "SessionStart", "tool.execute.after": "PostToolUse", "session.idle": "Stop",
          "session.compacted": "PreCompact"}
TOOLS = {"bash": "Bash", "edit": "Edit", "write": "Write", "read": "Read", "grep": "Grep", "glob": "Glob",
         "list": "LS", "webfetch": "WebFetch"}
RENAMES = {"filePath": "file_path"}


def normalize(payload: dict[str, Any], *, ts: str, turn: int, tasks: dict[str, Any] | None = None,
              **_: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    name = EVENTS.get(payload.get("hook_event_name"))
    if name is None or not isinstance(payload.get("session_id"), str):
        raise ValueError("Unsupported OpenCode plugin payload.")
    base = {"hook_event_name": name, "session_id": payload["session_id"], "cwd": payload.get("cwd") or ""}
    if name == "SessionStart":
        shaped = dict(base, source="startup")
    elif name == "Stop":
        shaped = dict(base, last_assistant_message="")
    elif name == "PreCompact":
        shaped = dict(base, trigger="auto")
    else:
        tool = str(payload.get("tool") or "tool")
        error = payload.get("error") or None
        shaped = mapped.tool_payload(base, TOOLS.get(tool, tool), mapped.tool_input(payload.get("args"), RENAMES),
                                     str(error) if error else None)
    return mapped.run("opencode", shaped, ts=ts, turn=turn, tasks=tasks)
