"""Codex rollout reader: bounded, read-only tail scans of `rollout-*.jsonl`.

Line shape (codex-rs `RolloutLine`): {"timestamp", "type", "payload"} with
`type` one of session_meta, response_item, event_msg, turn_context, ...
Shapes here come from the codex-rs source (rollout policy, protocol), not yet
from a real recording; see docs/adapters/codex.md.

Nothing read here is stored except an exit code, a limit reason, usage
percentages, and a compaction summary Codex wrote itself.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

TAIL_BYTES = 256 * 1024
ZST_MAX_BYTES = 8 * 1024 * 1024
ZSTD_TIMEOUT = 0.15  # seconds for the zstd binary inside a hook's 300 ms budget
EXIT_TEXT = re.compile(r"^(?:Process exited with code|Exit code:)\s*(-?\d+)\s*$", re.M)
LIMIT_INFOS = {"usage_limit_exceeded": "rate_limit", "rate_limit_exceeded": "rate_limit",
               "server_overloaded": "overloaded", "unauthorized": "auth",
               "internal_server_error": "server_error"}


def _name_ok(path):
    name = path.name
    return name.startswith("rollout-") and (name.endswith(".jsonl") or name.endswith(".jsonl.zst"))


def _zst_bytes(path, timeout):
    """Python 3.14+ stdlib `compression.zstd`, else the `zstd` binary (at most
    `timeout` seconds), else None."""
    try:
        from compression import zstd  # type: ignore  # Python 3.14+
        with zstd.open(str(path), "rb") as stream:
            return stream.read(ZST_MAX_BYTES)
    except ImportError:
        pass
    binary = shutil.which("zstd")
    if not binary:
        return None
    result = subprocess.run([binary, "-dc", "--", str(path)], capture_output=True, timeout=timeout)
    return result.stdout[:ZST_MAX_BYTES] if result.returncode == 0 else None


def tail_lines(path: str | Path | None, limit: int = TAIL_BYTES, *,
               zstd_timeout: float = ZSTD_TIMEOUT) -> list[dict[str, Any]]:
    """Parsed JSON objects from the last `limit` bytes; [] when unreadable.
    Hooks keep the 150 ms zstd limit; tests pass a long one so a loaded
    machine cannot make them flaky."""
    if not path:
        return []
    path = Path(path).expanduser()
    if not _name_ok(path):
        return []
    try:
        if path.name.endswith(".zst"):
            # The live rollout is plain; Codex compresses only cold files and
            # rewrites them as plain on append, so this path is rarely used.
            data = _zst_bytes(path, zstd_timeout)
            if data is None:
                return []
            data = data[-limit:]
        else:
            with open(str(path), "rb") as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - limit))
                data = stream.read(limit)
    except (OSError, subprocess.SubprocessError):
        return []
    lines = data.split(b"\n")
    if len(data) == limit:
        lines = lines[1:]  # first line may be cut
    rows = []
    for line in lines:
        try:
            row = json.loads(line.decode("utf-8"))
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("payload"), dict):
            rows.append(row)
    return rows


def exit_code(rows: list[dict[str, Any]], call_id: str) -> int | None:
    """Exit code of the shell call `call_id`, or None if not in the tail yet."""
    for row in reversed(rows):
        payload = row["payload"]
        if row.get("type") == "event_msg" and payload.get("type") == "item_completed":
            item = payload.get("item") or {}
            if (isinstance(item, dict) and item.get("type") == "CommandExecution"
                    and item.get("id") == call_id and type(item.get("exit_code")) is int):
                return item["exit_code"]
        if (row.get("type") == "response_item" and payload.get("type") == "function_call_output"
                and payload.get("call_id") == call_id):
            output = payload.get("output")
            if isinstance(output, list):  # content items
                output = "\n".join(str(i.get("text", "")) for i in output if isinstance(i, dict))
            elif isinstance(output, dict):
                output = output.get("content") if isinstance(output.get("content"), str) else ""
            match = EXIT_TEXT.search(str(output)[:4000])
            if match:
                return int(match[1])
    return None


# codex-rs tui `TokenUsage::percent_of_context_window_remaining`: the first
# 12000 tokens (system prompt, tools) are not counted against the window.
CONTEXT_BASELINE = 12000


def usage(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Newest `token_count` in the tail as percentages, or None.

    Shape (recorded in tests/fixtures/codex/rollouts/rollout-real-session.jsonl):
    rate_limits.primary/secondary {used_percent, window_minutes, resets_at
    (epoch seconds)}; info.last_token_usage.total_tokens and
    info.model_context_window. context_pct is the share of the window in use,
    computed as codex-rs computes what it shows (last turn, baseline removed).
    """
    limits, context = None, None
    for row in reversed(rows):
        payload = row["payload"]
        if row.get("type") != "event_msg" or payload.get("type") != "token_count":
            continue
        if context is None:
            context = _context_pct(payload.get("info"))
        snapshot = payload.get("rate_limits")
        # codex-rs RateLimitSnapshot.limit_id names the bucket; the tui's /status
        # (tui/src/status/rate_limits.rs) shows the "codex" bucket first and
        # model-specific quotas after it. Only the plan's own bucket (or a
        # snapshot without an id, older Codex) is read, so a newer reading for
        # another bucket never replaces it with that bucket's 0%.
        if limits is None and isinstance(snapshot, dict) and \
                str(snapshot.get("limit_id") or "codex").lower() == "codex":
            limits = snapshot
        if limits is not None and context is not None:
            break
    limits = limits or {}
    primary = limits.get("primary") if isinstance(limits.get("primary"), dict) else {}
    secondary = limits.get("secondary") if isinstance(limits.get("secondary"), dict) else {}
    result = {"limit_pct": _number(primary.get("used_percent")),
              "limit_window_minutes": _integer(primary.get("window_minutes")),
              "limit_resets_at": _epoch(primary.get("resets_at")),
              "weekly_pct": _number(secondary.get("used_percent")),
              "context_pct": context}
    return result if any(value is not None for value in result.values()) else None


def _number(value):
    return float(value) if type(value) in (int, float) and 0 <= value <= 1000 else None


def _integer(value):
    return value if type(value) is int and value > 0 else None


def _epoch(value):
    from datetime import datetime, timezone
    if type(value) not in (int, float) or not 0 < value < 32503680000:
        return None
    return datetime.fromtimestamp(int(value), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _context_pct(info):
    if not isinstance(info, dict) or not isinstance(info.get("last_token_usage"), dict):
        return None
    window, used = info.get("model_context_window"), info["last_token_usage"].get("total_tokens")
    if type(window) is not int or type(used) is not int or window <= CONTEXT_BASELINE:
        return None
    effective = window - CONTEXT_BASELINE
    remaining = effective - max(0, used - CONTEXT_BASELINE)
    return round(100 - max(0.0, min(100.0, remaining / effective * 100)), 1)


def compacted(rows: list[dict[str, Any]]) -> tuple[str, str] | None:
    """(timestamp, message) of the newest `compacted` item with a non-empty
    message, or None. Codex writes `message` "" when the summary went into
    replacement_history instead (seen in rollout-real-session.jsonl); that
    history is never rebuilt into a summary here."""
    for row in reversed(rows):
        if row.get("type") == "compacted":
            message = row["payload"].get("message")
            if isinstance(message, str) and message.strip() and isinstance(row.get("timestamp"), str):
                return row["timestamp"], message
            return None
    return None


def _snapshot_limited(snapshot):
    if not isinstance(snapshot, dict):
        return False
    if snapshot.get("rate_limit_reached_type"):
        return True
    for key in ("primary", "secondary"):
        window = snapshot.get(key)
        if isinstance(window, dict) and isinstance(window.get("used_percent"), (int, float)) \
                and window["used_percent"] >= 100:
            return True
    return False


def limit_hit(rows: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """(reason, turn_id) if the latest turn in the tail ended on a limit, else (None, None).

    Signals, newest turn only (Codex persists `turn_started`, `turn_complete`,
    `turn_aborted` and `token_count` event_msg lines; transient `error` events
    are not persisted):
    - `turn_complete.error.codex_error_info` usage/rate limit (or overload, auth);
    - the last `token_count.rate_limits` with `rate_limit_reached_type` set or a
      window at 100 % used.
    """
    start, turn_id = 0, None
    for index in range(len(rows) - 1, -1, -1):
        payload = rows[index]["payload"]
        if rows[index].get("type") == "event_msg" and payload.get("type") in ("turn_started", "task_started"):
            start, turn_id = index, payload.get("turn_id")
            break
    reason = None
    for row in rows[start:]:
        payload = row["payload"]
        if row.get("type") != "event_msg":
            continue
        kind = payload.get("type")
        if kind == "token_count":
            reason = "rate_limit" if _snapshot_limited(payload.get("rate_limits")) else None
        elif kind in ("turn_complete", "task_complete"):
            error = payload.get("error")
            info = error.get("codex_error_info") if isinstance(error, dict) else None
            if isinstance(info, str) and info in LIMIT_INFOS:
                reason = LIMIT_INFOS[info]
            elif isinstance(info, dict) and len(info) == 1 and next(iter(info)) in LIMIT_INFOS:
                reason = LIMIT_INFOS[next(iter(info))]
    return (reason, turn_id if isinstance(turn_id, str) else None) if reason else (None, None)
