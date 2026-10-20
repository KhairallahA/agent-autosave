"""Codex hook payloads -> normalized events, without I/O.

Shapes follow the current Codex hooks docs (developers.openai.com/codex/hooks)
and the codex-rs hook schemas. Until real recordings arrive the fixtures in
tests/fixtures/codex/ are doc-derived (see docs/adapters/codex.md).

Two facts are not in the payload and are read from the rollout by capture.py
before calling this pure function (`facts`):
- `exit`: the shell exit code (Codex sends only the output text);
- `limit`: an interruption reason if the latest turn ended on a limit.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any

from . import vendor_id
from .claude_code import checklist

EVENTS = {"SessionStart", "UserPromptSubmit", "PostToolUse", "PreCompact", "Stop", "SessionEnd"}
TOOL_PLAN = "__tool_plan__"
LIMIT_TURN = "__limit_turn__"  # turn_id whose limit was already reported
COMPACTED = "__compacted__"  # timestamp of the rollout `compacted` item already recorded
STATES = {"pending": "todo", "in_progress": "doing", "completed": "done"}
SOURCES = {"startup": "startup", "resume": "resume", "clear": "clear", "compact": "compact",
           "fork": "resume"}  # a fork continues an existing history
PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$|^\*\*\* Move to: (.+?)\s*$", re.M)


def _relative(path, cwd):
    candidate = Path(path)
    try:
        return str(candidate.relative_to(Path(cwd))) if candidate.is_absolute() else str(candidate)
    except ValueError:
        return str(candidate)


def patch_files(patch: str, cwd: str | None) -> list[str]:
    files = []
    for match in PATCH_FILE.finditer(str(patch)):
        name = _relative(match[1] or match[2], cwd)
        if name not in files:
            files.append(name)
    return files


def normalize(payload: dict[str, Any], *, ts: str, turn: int, tasks: dict[str, Any] | None = None,
              facts: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return normalized events and incremental adapter state."""
    name = payload["hook_event_name"]
    if name not in EVENTS or not isinstance(payload.get("session_id"), str):
        raise ValueError("Unsupported Codex hook payload.")
    facts = facts or {}
    base = dict(v=1, ts=ts, agent="codex", session=payload["session_id"], turn=turn)
    tasks = deepcopy(tasks or {})

    def row(kind, **fields):
        return dict(base, kind=kind, **fields)

    def limit():
        # One `interrupted` per limited turn, whether Stop or SessionEnd saw it.
        reason, turn_id = facts.get("limit"), facts.get("limit_turn") or payload.get("turn_id") or ""
        if not reason or tasks.get(LIMIT_TURN) == turn_id:
            return []
        tasks[LIMIT_TURN] = turn_id
        return [row("interrupted", reason=reason)]

    if name == "SessionStart":
        source = SOURCES.get(payload["source"])
        if source is None:
            raise ValueError("Unknown Codex session source.")
        fields = {"source": source}
        if isinstance(payload.get("model"), str):
            fields["model"] = payload["model"]
        if isinstance(payload.get("transcript_path"), str):
            from ..redact import shorten_home
            fields["transcript"] = shorten_home(payload["transcript_path"])
        return [row("session_start", **fields)], tasks
    if name == "UserPromptSubmit":
        return [row("prompt", text=payload["prompt"], **vendor_id(payload, "turn_id"))], tasks
    if name == "Stop":
        message = payload.get("last_assistant_message") or ""
        events = []
        if not tasks.get(TOOL_PLAN):
            items = checklist(message)
            if items:
                events.append(row("plan", items=items, origin="message"))
        extra = [row("usage", **facts["usage"])] if facts.get("usage") else []
        compacted = facts.get("compacted")
        if compacted and tasks.get(COMPACTED) != compacted[0]:
            # Once per `compacted` item: its timestamp is kept in adapter state.
            tasks[COMPACTED] = compacted[0]
            extra.append(row("summary", text=compacted[1]))
        return events + extra + [row("turn_end", last_message=message, **vendor_id(payload, "turn_id"))] + limit(), tasks
    if name == "PreCompact":
        return [row("compact", trigger=payload["trigger"])], tasks
    if name == "SessionEnd":
        return limit() + [row("session_end", reason=payload.get("reason") or "other")], tasks
    tool, inputs = payload["tool_name"], payload["tool_input"]
    response = payload.get("tool_response")
    if tool == "update_plan":
        if not isinstance(inputs, dict) or not isinstance(inputs.get("plan"), list):
            raise ValueError("Invalid update_plan input.")
        tasks[TOOL_PLAN] = True
        items = [{"step": item["step"], "state": STATES[item["status"]]}
                 for item in inputs["plan"] if str(item.get("step", "")).strip()]
        return [row("plan", items=items)], tasks
    fields = dict({"tool": tool, "summary": tool, "files": []}, **vendor_id(payload, "tool_use_id"))
    if tool == "Bash":
        if not isinstance(inputs, dict) or not isinstance(inputs.get("command"), str):
            raise ValueError("Invalid Codex Bash input.")
        fields["summary"] = inputs["command"]
        code = facts.get("exit")
        fields["exit"] = code if type(code) is int else None  # not in the rollout: unknown
        if isinstance(response, str) and response:
            fields["output_tail"] = response  # kept only for a failure (events.prepare, shellexit.judge)
    elif tool == "apply_patch":
        command = inputs.get("command") if isinstance(inputs, dict) else inputs
        files = patch_files(command or "", payload.get("cwd") or "")
        fields.update(summary="Edited " + ", ".join(files) if files else "apply_patch", files=files)
    # Never copy patches, arbitrary tool inputs, or successful output.
    return [row("tool", **fields)], tasks
