"""Gemini CLI hook payloads -> events, without I/O.

From https://geminicli.com/docs/hooks/reference/ (checked 2026-10-19):
every payload has session_id, transcript_path, cwd, hook_event_name and
timestamp. SessionStart.source is startup/resume/clear; BeforeAgent.prompt;
AfterAgent.prompt_response; AfterTool.tool_name, tool_input and
tool_response {llmContent, returnDisplay, error?}; PreCompress.trigger;
SessionEnd.reason. Tool names in that page: run_shell_command, read_file,
write_file, replace. Not yet recorded: see docs/testing/recording-gemini.md.
"""
from __future__ import annotations

from typing import Any

from . import mapped

EVENTS = {"SessionStart": "SessionStart", "BeforeAgent": "UserPromptSubmit", "AfterAgent": "Stop",
          "AfterTool": "PostToolUse", "PreCompress": "PreCompact", "SessionEnd": "SessionEnd"}
TOOLS = {"run_shell_command": "Bash", "write_file": "Write", "replace": "Edit", "read_file": "Read"}
RENAMES = {"absolute_path": "file_path", "path": "file_path"}


def normalize(payload: dict[str, Any], *, ts: str, turn: int, tasks: dict[str, Any] | None = None,
              **_: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    name = EVENTS.get(payload.get("hook_event_name"))
    if name is None or not isinstance(payload.get("session_id"), str):
        raise ValueError("Unsupported Gemini CLI hook payload.")
    base = {"hook_event_name": name, "session_id": payload["session_id"], "cwd": payload.get("cwd") or ""}
    if name == "SessionStart":
        shaped = dict(base, source=payload.get("source") if payload.get("source") in ("startup", "resume", "clear")
                      else "startup")
    elif name == "UserPromptSubmit":
        shaped = dict(base, prompt=str(payload.get("prompt") or ""))
    elif name == "Stop":
        shaped = dict(base, last_assistant_message=str(payload.get("prompt_response") or ""))
    elif name == "PreCompact":
        shaped = dict(base, trigger=payload.get("trigger") or "auto")
    elif name == "SessionEnd":
        shaped = dict(base, reason=str(payload.get("reason") or "other"))
    else:
        tool = str(payload.get("tool_name") or "tool")
        response = payload.get("tool_response") if isinstance(payload.get("tool_response"), dict) else {}
        error = response.get("error")
        error = (error.get("message") if isinstance(error, dict) else error) or None
        shaped = mapped.tool_payload(base, TOOLS.get(tool, tool), mapped.tool_input(payload.get("tool_input"), RENAMES),
                                     str(error) if error else None)
    return mapped.run("gemini", shaped, ts=ts, turn=turn, tasks=tasks)
