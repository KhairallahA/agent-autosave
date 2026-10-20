"""Antigravity hook payloads -> events, without I/O.

From https://antigravity.google/docs/hooks (checked 2026-10-19): payloads
are camelCase with no event name (it comes from our command line). Common
fields: conversationId, workspacePaths, transcriptPath, modelName.
PostToolUse has toolCall {name, args} and error ("" on success); Stop has
terminationReason and error. Tools: run_command (CommandLine),
write_to_file / replace_file_content / multi_replace_file_content
(TargetFile), view_file (AbsolutePath), grep_search, find_by_name, list_dir.
There is no prompt event with the prompt text.

Recorded with antigravity-cli 1.3.5 and 1.4.2 (tests/fixtures/antigravity/
recorded/): PostToolUse carries no exit code and no output. `error` was ""
for `python3 -c "raise SystemExit(3)"`, so an empty error says nothing about
the exit, and a command's exit is stored as unknown (null), never 0. A
non-empty error ("exit status N" in the docs) gives N, or 1. No payload
carries the prompt or the agent's last message (Stop has terminationReason,
executionNum, fullyIdle and error only).
"""
from __future__ import annotations

import re
from typing import Any

from . import mapped

EVENTS = {"SessionStart", "PostToolUse", "Stop"}
TOOLS = {"run_command": "Bash", "write_to_file": "Write", "replace_file_content": "Edit",
         "multi_replace_file_content": "Edit", "view_file": "Read", "grep_search": "Grep",
         "find_by_name": "Glob", "list_dir": "LS", "search_web": "WebSearch", "read_url_content": "WebFetch"}
RENAMES = {"CommandLine": "command", "TargetFile": "file_path", "AbsolutePath": "file_path"}


def root_of(payload: dict[str, Any]) -> str | None:
    paths = payload.get("workspacePaths")
    return paths[0] if isinstance(paths, list) and paths and isinstance(paths[0], str) else None


def normalize(payload: dict[str, Any], *, ts: str, turn: int, tasks: dict[str, Any] | None = None,
              **_: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    name = payload.get("hook_event_name")
    if name not in EVENTS or not isinstance(payload.get("conversationId"), str):
        raise ValueError("Unsupported Antigravity hook payload.")
    base = {"hook_event_name": name, "session_id": payload["conversationId"], "cwd": root_of(payload) or ""}
    if name == "SessionStart":
        shaped = dict(base, source="startup")
    elif name == "Stop":
        shaped = dict(base, last_assistant_message="")
    else:
        call = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
        tool = str(call.get("name") or "tool")
        error = payload.get("error") or None
        shaped = mapped.tool_payload(base, TOOLS.get(tool, tool), mapped.tool_input(call.get("args"), RENAMES),
                                     str(error) if error else None)
    events, tasks = mapped.run("antigravity", shaped, ts=ts, turn=turn, tasks=tasks)
    if name == "PostToolUse":
        events = [_exit(event, shaped.get("error")) for event in events]
    return events, tasks


def _exit(event: dict[str, Any], error: Any) -> dict[str, Any]:
    """The exit Antigravity's error text gives: N for "exit status N", 1 for
    any other error, unknown (None) without one; Antigravity sends no code."""
    if event.get("kind") != "tool" or event.get("tool") != "Bash":
        return event
    if not error:
        return dict(event, exit=None)
    match = re.search(r"exit (?:status|code) (\d+)", str(error))
    return dict(event, exit=int(match[1]) if match else 1)
