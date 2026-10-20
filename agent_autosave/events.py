"""Version-one normalized event contract, independent of agent hook formats."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import re
from typing import Any

from .display import shorten
from .redact import Redactor, shorten_home

COMMON = {"v", "ts", "agent", "session", "turn", "kind"}
EXTRA = {
    "session_start": {"source", "model", "transcript"},
    "prompt": {"text"},
    "tool": {"tool", "summary", "exit", "files", "output_tail", "readonly"},
    "plan": {"items", "origin"},
    "plan_set": {"steps"},
    "plan_done": {"index"},
    "turn_end": {"last_message"},
    "compact": {"trigger"},
    "interrupted": {"reason"},
    "note": {"text", "type", "by"},
    "session_end": {"reason"},
    "task_done": {"note"},
    "usage": {"limit_pct", "limit_window_minutes", "limit_resets_at", "weekly_pct", "context_pct"},
    "summary": {"text", "at", "trigger", "pre_tokens", "post_tokens"},
}
REQUIRED = {
    "session_start": {"source"}, "prompt": {"text"},
    "tool": {"tool", "summary", "files"}, "plan": {"items"}, "plan_set": {"steps"}, "plan_done": {"index"},
    "turn_end": set(), "compact": {"trigger"},
    "interrupted": {"reason"}, "note": {"text", "type"}, "session_end": {"reason"},
    "task_done": set(), "usage": set(), "summary": {"text"},
}
# Free text people and agents write, stored with the home directory as "~".
FREE_TEXT = {"note": ("text",), "turn_end": ("last_message",), "summary": ("text",), "task_done": ("note",)}
USAGE_NUMBERS = {"limit_pct", "weekly_pct", "context_pct", "limit_window_minutes"}
REASONS = {"rate_limit", "overloaded", "auth", "server_error", "unknown"}
_STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z\Z")

from .origin import SOURCES  # noqa: E402  (who ran a note)


class EventError(ValueError):
    pass


def timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not _STAMP.fullmatch(value):
        raise EventError("Expected a UTC ISO 8601 timestamp ending in Z.")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise EventError("Invalid UTC timestamp.") from None


def validate(event: Any) -> None:
    if not isinstance(event, dict) or not COMMON <= event.keys():
        raise EventError("Missing common event fields.")
    if type(event["v"]) is not int or event["v"] != 1:
        raise EventError("Unsupported event version.")
    kind = event["kind"]
    if not isinstance(kind, str) or kind not in EXTRA:
        raise EventError("Unknown event kind.")
    if not REQUIRED[kind] <= event.keys() or event.keys() - COMMON - EXTRA[kind] - {"task_id", "id"}:
        raise EventError("Missing or unexpected event payload fields.")
    timestamp(event["ts"])
    if type(event["turn"]) is not int or event["turn"] < 0:
        raise EventError("Event turn must be a non-negative integer.")
    for key, value in event.items():
        if key in {"v", "turn", "items", "files", "steps", "index"}:
            continue
        if kind == "usage" and key in USAGE_NUMBERS:
            if value is not None and (type(value) not in (int, float) or value != value or value < 0):
                raise EventError("Usage values must be non-negative numbers or null.")
            continue
        if kind == "usage" and key == "limit_resets_at" and value is None:
            continue
        if kind == "summary" and key in ("pre_tokens", "post_tokens"):
            if type(value) is not int or value < 0:
                raise EventError("Summary token counts must be non-negative integers.")
            continue
        if key == "exit":
            if value is not None and type(value) is not int:
                raise EventError("Tool exit must be an integer or null (unknown).")
        elif key == "readonly":
            if type(value) is not bool:
                raise EventError("Tool readonly must be a boolean.")
        elif not isinstance(value, str):
            raise EventError("Event text fields must be strings.")
    if not event["agent"] or not event["session"]:
        raise EventError("Agent and session identifiers must not be empty.")
    if "id" in event and not 0 < len(event["id"]) <= 200:
        raise EventError("Event id must be 1 to 200 characters.")
    if "task_id" in event and not re.fullmatch(r"t_[0-9]+_[0-9]+(?:_[0-9]+)?", event["task_id"]):
        raise EventError("Invalid task identifier.")
    if kind == "tool" and (not isinstance(event["files"], list) or
                           not all(isinstance(item, str) for item in event["files"])):
        raise EventError("Tool files must be a list of paths.")
    if kind == "plan":
        if not isinstance(event["items"], list):
            raise EventError("Plan items must be a list.")
        for item in event["items"]:
            if (not isinstance(item, dict) or set(item) != {"step", "state"}
                    or not isinstance(item["step"], str) or not item["step"].strip()
                    or item["state"] not in ("todo", "doing", "done")):
                raise EventError("Invalid plan item.")
        if event.get("origin", "tool") not in ("tool", "message"):
            raise EventError("Invalid plan origin.")
    if kind == "plan_set" and (not isinstance(event["steps"], list) or not event["steps"] or not all(
            isinstance(step, str) and step.strip() for step in event["steps"])):
        raise EventError("Plan steps must be non-empty text.")
    if kind == "plan_done" and (type(event["index"]) is not int or event["index"] < 1):
        raise EventError("Plan step index must be a positive integer.")
    enums = {"source": {"startup", "resume", "clear", "compact"},
             "trigger": {"manual", "auto"}, "type": {"decision", "todo", "warning", "handoff", "tried"},
             "by": set(SOURCES)}
    for key, choices in enums.items():
        if key in event and event[key] not in choices:
            raise EventError("Invalid event enumeration.")
    if kind == "summary" and "at" in event:
        timestamp(event["at"])
    if kind == "usage" and event.get("limit_resets_at") is not None:
        timestamp(event["limit_resets_at"])
    if kind == "interrupted" and event["reason"] not in REASONS:
        raise EventError("Invalid interruption reason.")


def _failed(event):
    """A known non-zero exit. `exit: null` means unknown, never a failure."""
    return type(event.get("exit")) is int and event["exit"] != 0


def prepare(event: dict[str, Any], config: dict[str, Any], redactor: Redactor | None = None) -> dict[str, Any]:
    """Validate, redact BEFORE trimming, and retain only minimal capture data."""
    validate(event)
    result = deepcopy(event)
    # Successful output is never retained. Drop it wholesale, never truncate
    # retained text before redaction (which could split a secret).
    if result["kind"] == "tool" and not _failed(result):
        result.pop("output_tail", None)  # success or unknown exit: output may be anything
    for field in FREE_TEXT.get(result["kind"], ()):
        if isinstance(result.get(field), str):
            # The home directory is personal; journal and memory pages may be
            # shared or written to a vault.
            result[field] = shorten_home(result[field])
    result = (redactor or Redactor()).value(result)
    validate(result)
    capture = config["capture"]
    if result["kind"] == "prompt":
        result["text"] = result["text"][:capture["prompt_max_chars"]]
    if result["kind"] == "summary":
        result["text"] = shorten(result["text"].strip(), 1200)
    if result["kind"] == "plan_set":
        result["steps"] = [step[:300] for step in result["steps"]]
    if "last_message" in result:
        # Long enough for a closing summary ("Remaining: ...; I chose ...");
        # the save keeps at most 600 characters of the newest one.
        result["last_message"] = result["last_message"][:2000]
    if result["kind"] == "tool":
        result["summary"] = result["summary"].strip()
        if _failed(result):
            result["output_tail"] = "\n".join(result.get("output_tail", "").splitlines()[-capture["output_tail_lines"]:])
        else:
            result.pop("output_tail", None)
    return result
