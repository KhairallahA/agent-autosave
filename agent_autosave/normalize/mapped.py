"""Shared mapping for the agents whose payloads are documented but not yet
recorded (Gemini CLI, Antigravity, Copilot CLI, OpenCode): each module
turns its payload into the Claude Code shape, and the Claude Code
normalizer, which is verified against recordings, makes the events.

Only documented fields are read; anything else is ignored. Tool names are
mapped to the Claude names the normalizer knows (Bash, Edit, Write, Read,
...); others become a plain tool event with the tool's name as summary."""
from __future__ import annotations

from typing import Any

from . import claude_code


def tool_input(args: Any, renames: dict[str, str]) -> dict[str, Any]:
    """The vendor's arguments with the keys the Claude normalizer reads
    (command, file_path) filled in from the vendor's own key names."""
    inputs = dict(args) if isinstance(args, dict) else {}
    for vendor, claude in renames.items():
        if isinstance(inputs.get(vendor), str) and claude not in inputs:
            inputs[claude] = inputs[vendor]
    return inputs


def run(agent: str, shaped: dict[str, Any], *, ts: str, turn: int,
        tasks: dict[str, Any] | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalize a Claude-shaped payload and label the events as `agent`."""
    events, tasks = claude_code.normalize(shaped, ts=ts, turn=turn, tasks=tasks)
    return [dict(event, agent=agent) for event in events], tasks


def tool_payload(base: dict[str, Any], tool: str, inputs: dict[str, Any], error: str | None) -> dict[str, Any]:
    """PostToolUse, or PostToolUseFailure with the error text."""
    shaped = dict(base, tool_name=tool, tool_input=inputs, tool_response={})
    if error:
        shaped.update(hook_event_name="PostToolUseFailure", error=error)
    else:
        shaped["hook_event_name"] = "PostToolUse"
    return shaped
