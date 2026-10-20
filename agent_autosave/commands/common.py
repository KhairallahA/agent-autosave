"""Helpers the commands share: finding the project and its receipt, waiting
for a late SessionEnd, loading a fresh save, staleness, the context block.

Nothing here runs in a hook; these run in the user's terminal, so they may
call git and wait briefly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any, Callable

from ..contract import Outcome, fail

if TYPE_CHECKING:
    from ..state import TaskStore

AGENTS = ("claude-code", "codex", "cursor", "gemini", "antigravity", "copilot", "opencode", "generic")
# Agent names in install messages (init, add, remove).
INSTALL_NAMES = {"claude-code": "Claude Code", "codex": "Codex", "cursor": "Cursor", "gemini": "Gemini CLI",
                 "antigravity": "Antigravity", "copilot": "Copilot CLI", "opencode": "OpenCode",
                 "generic": "Generic (AGENTS.md)"}
# Agent names in task output (status, resume, handoffs), where the CLI itself
# can be the source of an event.
NAMES = {"claude-code": "Claude Code", "codex": "Codex", "cursor": "Cursor", "gemini": "Gemini CLI",
         "antigravity": "Antigravity", "copilot": "Copilot CLI", "opencode": "OpenCode",
         "generic": "your agent", "cli": "the CLI"}
SETTLE_SECONDS = 1.0
SETTLE_POLL = 0.05
# File mtimes come from the kernel's coarse clock (a few ms behind the
# wall clock the hook stamped session_end with).
MTIME_SLACK = timedelta(milliseconds=50)
TAIL_BYTES = 64 * 1024


def find_project() -> Path:
    """The folder holding .autosave/: the current directory or a parent."""
    from ..capture import find_project as find
    return find(Path.cwd())


def receipt(project: Path) -> dict[str, Any] | None:
    """install.json, or None when nothing is installed here."""
    from ..storage import read_json
    return read_json(Path(project) / ".autosave" / "install.json")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(ts: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def last_session_end(directory: Path) -> datetime | None:
    """Timestamp (aware datetime) of the newest session_end in the ledger tail."""
    path = Path(directory) / "events.jsonl"
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - TAIL_BYTES))
            tail = handle.read().decode("utf-8", "replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        if '"session_end"' not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("kind") == "session_end":
            return _parse(row.get("ts"))
    return None


def _views_mtime(directory: Path) -> datetime | None:
    times = []
    for name in ("save.json", "SAVE.md"):
        try:
            times.append(os.stat(Path(directory) / name).st_mtime)
        except OSError:
            return None
    return datetime.fromtimestamp(min(times), timezone.utc)


def wait_for_settle(directory: Path, *, timeout: float = SETTLE_SECONDS, poll: float = SETTLE_POLL,
                    sleep: Callable[[float], Any] = time.sleep, clock: Callable[[], float] = time.monotonic) -> bool:
    """Codex returns the terminal before its SessionEnd hook finishes: if the
    ledger has a session_end newer than save.json/SAVE.md, poll up to
    `timeout` seconds for the hook to write them. Returns True if it waited."""
    end = last_session_end(directory)
    if end is None:
        return False
    deadline = clock() + timeout
    waited = False
    while True:
        written = _views_mtime(directory)
        if written is not None and written >= end - MTIME_SLACK:
            return waited
        if clock() >= deadline:
            return waited
        waited = True
        sleep(poll)


def ready(project: Path) -> bool:
    """A task exists: init (or clear) wrote task.json."""
    return (Path(project) / ".autosave/task.json").is_file()


def store(project: Path) -> TaskStore:
    from ..state import TaskStore
    return TaskStore(project, lock_timeout=2)


def not_ready() -> Outcome:
    return fail("no_save", "There is no save yet in .autosave/.",
                "Run agent-autosave init, or start an agent session.")


def find_save(project: Path, task_id: str) -> dict[str, Any] | None:
    """The save of task `task_id`: the current one, a branch slot's parked
    save, or an archived save in history/. None when there is no such task."""
    import re
    from ..branches import slug
    from ..storage import read_view
    if not re.fullmatch(r"t_[0-9]+_[0-9]+(?:_[0-9]+)?", str(task_id)):
        return None
    current = load_fresh(project)
    if isinstance(current, dict) and current.get("task_id") == task_id:
        return current
    directory = Path(project) / ".autosave"
    for branch, task in ((store(project).slots() or {}).get("slots") or {}).items():
        if isinstance(task, dict) and task.get("task_id") == task_id:
            return read_view(directory / "slots" / (slug(branch) + ".json"))
    return read_view(directory / "history" / (task_id + ".json"))


def unknown_task() -> Outcome:
    return fail("unknown_task", "No task with that id in this folder.",
                "Run agent-autosave status --all or agent-autosave history to see the task ids.")


def nothing_to_finish() -> Outcome:
    return fail("nothing_to_finish", "There is no task to finish yet.",
                "Start an agent session first; run agent-autosave done yourself when its task is finished.")


def load_fresh(project: Path) -> dict[str, Any] | None:
    """The save as status/show/resume should see it: settled after a pending
    SessionEnd, the ledger recompiled, git facts kept from the last hook
    rebuild (so a HEAD that moved since is still detected)."""
    from ..storage import read_view
    directory = Path(project) / ".autosave"
    wait_for_settle(directory)
    previous = read_view(directory / "save.json")
    if not isinstance(previous, dict) or previous.get("v") != 1 or "status" not in previous:
        previous = None
    if not (directory / "task.json").is_file():
        return previous
    try:
        return store(project).rebuild(git_state=(previous or {}).get("git"))
    except Exception:
        return previous  # rebuild logged to errors.log; show what is on disk


def staleness(project: Path, save: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    from ..gitstate import commits_between, read as read_git
    from ..loader import stale_reason
    old_head = (save.get("git") or {}).get("head")
    current = read_git(project, timeout_ms=config["budgets_ms"]["git_call"]) if old_head else None
    current_head = (current or {}).get("head")
    commits = commits_between(project, old_head, current_head) if current_head and current_head != old_head else None
    reason = stale_reason(save, config, current_head=current_head, commits_since=commits)
    return {"stale": bool(reason), "reason": reason, "current_head": current_head, "commits_since": commits}


def context_block(project: Path, save: dict[str, Any], config: dict[str, Any], stale: dict[str, Any]) -> str:
    return context_explained(save, config, stale)["context"]


def context_explained(save: dict[str, Any], config: dict[str, Any], stale: dict[str, Any]) -> dict[str, Any]:
    """The resume block (inject.resume_max_chars) and how its sections were packed."""
    from ..loader import explain
    return explain(save, config, force=True, current_head=stale["current_head"],
                   commits_since=stale["commits_since"], budget=config["inject"]["resume_max_chars"])


def handoff_text(handoffs: list[dict[str, Any]]) -> str:
    if not handoffs:
        return "Handoffs: none"
    count = len(handoffs)
    lines = ["Handoffs: handed off {} time{}".format(count, "" if count == 1 else "s")]
    for item in handoffs[-5:]:
        lines.append("  {} -> {} at {} ({})".format(NAMES.get(item["from"], item["from"]),
                                                   NAMES.get(item["to"], item["to"]), item["at"], item["reason"]))
    return "\n".join(lines)


def cli_note(task_store: TaskStore, text: str, kind: str) -> None:
    from ..origin import note_source
    task_store.append({"v": 1, "ts": now(), "agent": "cli", "session": "cli", "turn": 0,
                       "kind": "note", "text": text, "type": kind, "by": note_source()})


def current_source(project: Path) -> tuple[str, str | None]:
    """(agent, task id) to credit a note or candidate made from the CLI: the
    agent only when its own tool-call marker is set (see origin.py), else
    "you", whatever session is open."""
    from ..origin import note_source
    from ..storage import read_view
    save = read_view(Path(project) / ".autosave" / "save.json") or {}
    return note_source(), save.get("task_id")


def sync_memory(project: Path) -> None:
    """The memory sync from a command; a failure is logged, never fatal."""
    try:
        from ..memory_sync import sync
        sync(project)
    except (OSError, ValueError) as error:  # the command's own result still stands
        from ..storage import log_error
        log_error(Path(project) / ".autosave", "memory.sync", error)
