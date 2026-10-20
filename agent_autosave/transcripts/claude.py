"""Claude Code transcript reader: the newest compaction summary, nothing else.

Claude Code writes each session as JSON lines under ~/.claude/projects/. After
a compaction the transcript holds an entry with `isCompactSummary: true`
whose `message.content` (a string or a list of text blocks) is the summary
Claude wrote, right after a `compact_boundary` system line. The shape is
recorded from Claude Code 2.1.302 in
tests/fixtures/claude-code/transcripts/compact-2.1.302.jsonl.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

TAIL_BYTES = 512 * 1024
# Claude Code opens every compaction summary with this fixed text (seen in the
# real 2.1.302 transcripts). It carries no information, so it is
# dropped before the 1200 stored characters are counted.
PREAMBLE = re.compile(r"\AThis session is being continued from a previous conversation that ran out of context\. "
                      r"The summary below covers the earlier portion of the conversation\.\s*(?:Summary:\s*)?")


def _allowed(path: Path) -> bool:
    """Only a .jsonl file under ~/.claude/projects: never an arbitrary path
    from a payload."""
    try:
        root = (Path.home() / ".claude" / "projects").resolve()
        resolved = path.expanduser().resolve()
        return resolved.suffix == ".jsonl" and root in resolved.parents and resolved.is_file()
    except (OSError, RuntimeError, ValueError):  # ValueError: a null byte in the path
        return False


def compact_summary(path: str | Path | None) -> dict[str, Any] | None:
    """The newest compaction in the last 512 KB, or None:
    {text, at, trigger, pre_tokens, post_tokens, boundary}.

    Shape recorded from Claude Code 2.1.302
    (tests/fixtures/claude-code/transcripts/compact-2.1.302.jsonl): a
    `type: system, subtype: compact_boundary` line with `compactMetadata`
    {trigger, preTokens, postTokens}, then a user line with
    `isCompactSummary: true` whose `parentUuid` is the boundary's `uuid`. The
    summary's timestamp is EARLIER than the boundary's even though it comes
    after it in the file, so lines are matched by file order and parentUuid,
    never by timestamp. `at` is the boundary's timestamp. Never raises."""
    rows = _tail_rows(path)
    if rows is None:
        return None
    boundary_index = next((i for i in range(len(rows) - 1, -1, -1)
                           if rows[i].get("type") == "system" and rows[i].get("subtype") == "compact_boundary"), None)
    if boundary_index is None:
        return None
    boundary = rows[boundary_index]
    after = [row for row in rows[boundary_index + 1:] if row.get("isCompactSummary") is True]
    uuid = boundary.get("uuid")
    summary = next((row for row in after if uuid and row.get("parentUuid") == uuid), after[0] if after else None)
    if summary is None:
        return None  # not written yet: the caller reads again later
    message = summary.get("message")
    text = PREAMBLE.sub("", _text(message.get("content") if isinstance(message, dict) else None)).strip()
    if not text:
        return None
    meta = boundary.get("compactMetadata") if isinstance(boundary.get("compactMetadata"), dict) else {}
    def count(value):
        return value if type(value) is int and value >= 0 else None
    at = boundary.get("timestamp") if isinstance(boundary.get("timestamp"), str) else None
    return {"text": text, "at": at,
            "trigger": meta.get("trigger") if isinstance(meta.get("trigger"), str) else None,
            "pre_tokens": count(meta.get("preTokens")), "post_tokens": count(meta.get("postTokens")),
            "boundary": uuid if isinstance(uuid, str) else None}


def _tail_rows(path: Any) -> list[dict[str, Any]] | None:
    """JSON objects from the last 512 KB of an allowed transcript, in file order."""
    try:
        if not isinstance(path, (str, Path)) or not path or not _allowed(Path(path)):
            return None
        with open(str(Path(path).expanduser().resolve()), "rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - TAIL_BYTES))
            data = stream.read(TAIL_BYTES)
    except (OSError, ValueError, RuntimeError):
        return None
    lines = data.split(b"\n")
    if size > TAIL_BYTES:
        lines = lines[1:]  # the first line may be cut
    rows = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line.decode("utf-8"))
        except (ValueError, RecursionError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(block["text"] for block in content
                         if isinstance(block, dict) and isinstance(block.get("text"), str)).strip()
    return ""
