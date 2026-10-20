"""Claude Code 2.1.291 payloads, verified against recorded hook stdin."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any

from . import vendor_id

EVENTS = {"SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure",
          "Stop", "StopFailure", "PreCompact", "SessionEnd"}
TOOL_PLAN = "__tool_plan__"  # reserved key: this session used a to-do tool
CHECKBOX = re.compile(r"^- \[([ x])\] (?:\d+[.)] )?(.+?)\s*$")
CHECKLIST_MAX = 20
STATES = {"pending": "todo", "in_progress": "doing", "completed": "done"}
# Tools that only read. Read (file_path), Glob/Grep (pattern, path?), WebFetch
# (url) and WebSearch (query) are in @anthropic-ai/claude-code 2.1.87
# sdk-tools.d.ts (FileReadInput, GlobInput, GrepInput, WebFetchInput,
# WebSearchInput). LS, NotebookRead and TodoRead are older tool names not in
# that file: matched by name only, their inputs read only when the shape is there.
READONLY = {"Read", "Glob", "Grep", "LS", "NotebookRead", "WebFetch", "WebSearch", "TodoRead"}
# Edits: Edit/Write (file_path), NotebookEdit (notebook_path, sdk-tools.d.ts
# NotebookEditInput). MultiEdit (file_path plus edits[]) is an older tool not
# in sdk-tools.d.ts: its path is used only when present as a string.
EDITS = {"Edit": ("Edited ", "file_path"), "MultiEdit": ("Edited ", "file_path"),
         "Write": ("Wrote ", "file_path"), "NotebookEdit": ("Edited ", "notebook_path")}


def summary_event(base: dict[str, Any], found: dict[str, Any]) -> dict[str, Any]:
    """A `summary` event from a compact_summary() result: the boundary's
    timestamp as `at`, its trigger and token counts when known."""
    fields = {"text": found["text"]}
    for key in ("at", "trigger", "pre_tokens", "post_tokens"):
        if found.get(key) is not None:
            fields[key] = found[key]
    return dict(base, kind="summary", **fields)


def normalize(payload: dict[str, Any], *, ts: str, turn: int, tasks: dict[str, Any] | None = None,
              summary: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return normalized events and incremental task state, without I/O.
    `summary` is the compaction the hook read from the transcript
    (transcripts.claude.compact_summary)."""
    name = payload["hook_event_name"]
    if name not in EVENTS or not isinstance(payload.get("session_id"), str):
        raise ValueError("Unsupported Claude hook payload.")
    base = dict(v=1, ts=ts, agent="claude-code", session=payload["session_id"], turn=turn)
    tasks = deepcopy(tasks or {})
    def row(kind, **fields):
        return dict(base, kind=kind, **fields)
    if name == "SessionStart":
        source = payload["source"]
        if source not in {"startup", "resume", "clear", "compact"}:
            raise ValueError("Unrecorded Claude session source.")
        fields = {"source": source}
        for key in ("model", "transcript_path"):
            if key in payload:
                value = payload[key]
                if key == "transcript_path":
                    from ..redact import shorten_home
                    value = shorten_home(value)
                fields["transcript" if key == "transcript_path" else key] = value
        extra = [summary_event(base, summary)] if summary and source == "compact" else []
        return [row("session_start", **fields)] + extra, tasks
    if name == "UserPromptSubmit":
        return [row("prompt", text=payload["prompt"], **vendor_id(payload, "prompt_id"))], tasks
    if name == "Stop":
        message = payload.get("last_assistant_message", "")
        events = []
        if not tasks.get(TOOL_PLAN):
            # Claude sometimes keeps its plan as a Markdown checklist in the
            # final message instead of calling a to-do tool (seen in 2.1.291).
            # Only used when no to-do tool ran in this session.
            items = checklist(message)
            if items:
                events.append(row("plan", items=items, origin="message"))
        return events + [row("turn_end", last_message=message)], tasks
    if name == "StopFailure":
        reason = {"authentication_failed": "auth", "oauth_org_not_allowed": "auth",
                  "account_on_hold": "auth", "cloud_credential_error": "auth",
                  "rate_limit": "rate_limit", "overloaded": "overloaded",
                  "server_error": "server_error"}.get(payload["error"], "unknown")
        return [row("interrupted", reason=reason)], tasks
    if name == "PreCompact":
        return [row("compact", trigger=payload["trigger"])], tasks
    if name == "SessionEnd":
        return [row("session_end", reason=payload["reason"])], tasks
    tool, inputs = payload["tool_name"], payload["tool_input"]
    response = payload.get("tool_response", {})
    if not isinstance(inputs, dict) or not isinstance(response, dict):
        raise ValueError("Invalid Claude tool fields.")
    if name == "PostToolUse" and tool in {"TodoWrite", "TaskCreate", "TaskUpdate"}:
        tasks[TOOL_PLAN] = True
    if name == "PostToolUse" and tool == "TodoWrite":
        items = [{"step": item["content"], "state": STATES[item["status"]]} for item in inputs["todos"]]
        return [row("plan", items=items)], tasks
    if name == "PostToolUse" and tool in {"TaskCreate", "TaskUpdate"}:
        if tool == "TaskCreate":
            identifier = response["task"]["id"]
            tasks[identifier] = {"step": inputs["subject"], "state": "todo"}
        else:
            identifier = inputs["taskId"]
            if not response.get("success"):
                return [], tasks
            if inputs.get("status") == "deleted":
                tasks.pop(identifier, None)
            else:
                if identifier not in tasks:
                    raise ValueError("TaskUpdate has no captured TaskCreate.")
                if "subject" in inputs:
                    tasks[identifier]["step"] = inputs["subject"]
                if "status" in inputs:
                    tasks[identifier]["state"] = STATES[inputs["status"]]
        return [row("plan", items=[v for k, v in tasks.items() if k != TOOL_PLAN])], tasks
    fields = dict({"tool": tool, "summary": tool, "files": []}, **vendor_id(payload, "tool_use_id"))
    if tool == "Bash":
        fields["summary"] = inputs["command"]
        if name == "PostToolUseFailure":
            error = payload["error"]
            match = re.match(r"^Exit code (\d+)\b", error)
            fields.update(exit=int(match[1]) if match else 1, output_tail=error)
        else:
            fields["exit"] = 0
            # Read only by shellexit.judge (a test runner's failure summary);
            # a passing command's output is dropped before it is stored.
            output = "\n".join(str(response.get(k) or "") for k in ("stdout", "stderr") if response.get(k))
            if output:
                fields["output_tail"] = output
    elif tool in EDITS and isinstance(inputs.get(EDITS[tool][1]), str):
        path = _relative(inputs[EDITS[tool][1]], payload["cwd"]) or inputs[EDITS[tool][1]]
        fields.update(summary=EDITS[tool][0] + path, files=[path])
    elif tool in READONLY and name == "PostToolUse":
        fields["readonly"] = True
        source = inputs.get("notebook_path" if tool == "NotebookRead" else "file_path")
        relative = _relative(source, payload["cwd"]) if tool in ("Read", "NotebookRead") and isinstance(source, str) else None
        if relative:
            fields["files"] = [relative]  # project files only: never a path outside the repo
    # Never copy file contents, patches, arbitrary tool inputs, or successful output.
    return [row("tool", **fields)], tasks


def _relative(path, cwd):
    """`path` relative to the project folder, or None when it lies outside."""
    try:
        return str(Path(path).relative_to(Path(cwd)))
    except ValueError:
        return None


def checklist(message: Any) -> list[dict[str, str]]:
    """Lines starting '- [ ] ' or '- [x] ' -> plan items (first 20); else []."""
    items = []
    for line in str(message).splitlines():
        match = CHECKBOX.match(line)
        if match:
            step = match[2].replace("**", "").strip()[:200]
            if step and len(items) < CHECKLIST_MAX:
                items.append({"step": step, "state": "done" if match[1] in "xX" else "todo"})
    return items
