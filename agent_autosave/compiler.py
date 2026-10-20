"""Pure, ordered fold of one task's normalized events into a version-one save."""
from __future__ import annotations

from copy import deepcopy
import fnmatch
import functools
import re
from typing import Any, Iterable

from . import display
from .commandline import chain, is_check, is_readonly
from .config import resolve
from .events import prepare, timestamp
from .origin import SOURCES
from .redact import Redactor
from .storage import json_bytes

CONTINUATIONS = {"continue", "go on", "yes", "ok", "keep going"}


def is_resume_block(text: Any) -> bool:
    from .loader import MARKER
    return isinstance(text, str) and MARKER in text[:4000]


def _memory_note(save: dict[str, Any], event: dict[str, Any], agent: str) -> None:
    """Decision, tried and warning notes, with who wrote them, for memory
    candidates at archive. A note from the CLI carries who ran it ("by",
    decided when it was written, see origin.py); one without it is "you".
    Added only when there is one, so older saves are unchanged."""
    if event["type"] in ("decision", "tried", "warning"):
        save.setdefault("memory_notes", []).append({"type": event["type"], "text": event["text"][:500],
                                                    "agent": agent, "at": event["ts"]})


def task_id_at(started: str) -> str:
    return "t_" + timestamp(started).strftime("%Y%m%d_%H%M%S%f")


def substantive(text: str) -> bool:
    return len(text.strip()) >= 20 and text.strip().casefold() not in CONTINUATIONS


def _one_line(text):
    return " ".join(text.splitlines()).strip()


def _ignored(path, patterns):
    path = path.replace("\\", "/")
    for pattern in patterns:
        pattern = pattern.replace("\\", "/")
        if pattern.endswith("/"):
            if path.startswith(pattern) or "/" + pattern in "/" + path:
                return True
        elif fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], pattern):
            # Name patterns (*.pyc, .DS_Store) match at any depth.
            return True
    return False


_OWN = re.compile(r"(?:\S*/)?agent-autosave(?:\s|$)|(['\"])[^'\"]*/agent-autosave(?:\.exe)?\1(?:\s|$)|python[0-9.]*\s+-m\s+agent_autosave(?:\s|$)")
_OWN_JSON = re.compile(r'^\s*\{"ok": (?:true|false), .*"meta": \{.*\}\}\s*$')


@functools.lru_cache(maxsize=4096)
def strip_own_commands(command: str) -> str:
    """Drop agent-autosave's own commands from a (possibly chained) command."""
    parts, separators = chain(command)
    own = [bool(_OWN.match(part.strip())) for part in parts]
    if not any(own):
        return command  # nothing of ours: keep the command exactly as run
    start, end = 0, len(parts)
    while start < end and (own[start] or not parts[start].strip()):
        start += 1
    while end > start and (own[end - 1] or not parts[end - 1].strip()):
        end -= 1
    if any(own[start:end]):
        return command  # ours sits mid-chain: keep the original text verbatim
    text = parts[start].strip() if start < end else ""
    for index in range(start + 1, end):
        separator = separators[index - 1]
        text += (separator + " " if separator in ";\n" else " " + separator + " ") + parts[index].strip()
    return text


USAGE = ("limit_pct", "limit_window_minutes", "limit_resets_at", "weekly_pct", "context_pct")


def _reading(previous, event, threshold):
    """The newest usage reading of one agent. `crossed_at` is when its limit
    first reached `threshold` in this window: kept while it stays above with
    the same reset time, new after a reset, None once below again."""
    fields = {key: event.get(key) for key in USAGE}
    crossed = None
    if fields["limit_pct"] is not None and fields["limit_pct"] >= threshold:
        same_window = previous and previous.get("limit_resets_at") == fields["limit_resets_at"]
        crossed = previous["crossed_at"] if same_window and previous.get("crossed_at") else event["ts"]
    return dict(fields, session=event["session"], at=event["ts"], crossed_at=crossed)


def _streak(streaks, command, event, edits):
    """Track the same command failing again and again with no pass between:
    `failures` in a row since the first, and the edits tried in between.
    A pass ends the streak; the newest failing command is kept last."""
    streak = streaks.pop(command, None)
    if event["exit"] == 0:
        return
    if streak is None:
        streak = {"command": command, "failures": 0, "since": event["ts"], "edits_between": 0, "edits_at": edits}
    streak["failures"] += 1
    streak["edits_between"] = edits - streak["edits_at"]
    streaks[command] = streak


def _collapse(actions):
    """Consecutive identical actions become one: "Ran pytest -q (exit 1) x3"."""
    runs = []
    for action in actions:
        if runs and runs[-1][0] == action:
            runs[-1][1] += 1
        else:
            runs.append([action, 1])
    return [action if count == 1 else "{} x{}".format(action, count) for action, count in runs]


FINISHED = "Nothing: the task is finished."


ASK_WHAT_IS_LEFT = "Read the agent's last message, check git, and ask the user what is left."


def _next_action(save, unknown=()):
    if save.get("status") == "done":
        return FINISHED
    if save["failing"]:
        return "Make `" + save["failing"]["command"] + "` pass."
    for state in ("doing", "todo"):
        for item in save.get("plan", []):
            if item["state"] == state:
                return item["step"]
    if unknown:
        # Never claim a pass we did not see.
        from .shellexit import pipe_hides_exit
        if pipe_hides_exit(unknown[-1]):
            return "Re-run `" + unknown[-1] + "` with `set -o pipefail` and check its result (exit hidden by a pipe)."
        return "Re-run `" + unknown[-1] + "` and check its result (exit unknown)."
    if save.get("last_report") and save["last_actions"]:
        # Work was done and the agent reported: the goal may already be met.
        return ASK_WHAT_IS_LEFT
    if save["steering"]:
        return save["steering"][-1]
    return save["goal"]


def compile_events(events: Iterable[dict[str, Any]], git_state: dict[str, Any] | None = None,
                   config: dict[str, Any] | None = None, *, started: str | None = None,
                   task_id: str | None = None, redactor: Redactor | None = None,
                   root: str | None = None) -> dict[str, Any]:
    """Same events, git facts, config, boundary and redactor => same result.

    Events are consumed in ledger order, not sorted by timestamp. The optional
    explicit boundary supports init/clear; tagged events from other tasks and
    events before the boundary are excluded. No clock, filesystem, or git calls.
    """
    config = resolve(config)
    redactor = redactor or Redactor()
    # Mask complete secrets before prompt/tail limits can cut them in half.
    events = [prepare(event, config, redactor) for event in events]
    if started is None:
        if not events:
            raise ValueError("An empty task needs an explicit start timestamp.")
        started = events[0]["ts"]
    boundary = timestamp(started)
    task_id = task_id or task_id_at(started)
    save = {
        "v": 1, "task_id": task_id, "started": started, "updated": started,
        "status": "active", "interrupted": None, "goal": "", "steering": [],
        "last_actions": [], "failing": None, "files_touched": [], "files_edited": [],
        "git": deepcopy(git_state), "decisions": [], "next_action": "",
        "agents": [], "handoffs": [], "last_session": None,
        "last_report": "", "last_report_agent": None, "last_report_at": None, "usage": {}, "summary": None,
    }
    handoff_notes = []  # timestamps of handoff notes, any source
    sessions = {}
    failures = {}
    unknown = {}  # commands whose newest run had an unknown exit, oldest first
    actions, files, steering, looked = [], [], [], []
    last_prompt = None
    last_agent = None
    compact_restore = None
    explicit_plan = False  # a to-do tool or `agent-autosave plan` has spoken
    previous = {}  # (agent, session) -> that session's previous event, minus ts
    seen = set()  # (agent, session, kind, vendor id): one hook delivered twice is one event
    checks = {}  # command -> its newest known run, for recognised checks
    streaks = {}  # command -> its current run of failures with no pass between
    edits = 0  # edit/write/patch tool events so far
    for event in events:
        if timestamp(event["ts"]) < boundary or event.get("task_id", task_id) != task_id:
            continue
        kind = event["kind"]
        if "id" in event:
            mark = (event["agent"], event["session"], kind, event["id"])
            if mark in seen:
                continue
            seen.add(mark)
        if kind == "tool" and event["tool"] in config["capture"]["ignore_tools"]:
            continue
        if kind not in ("plan_set", "plan_done"):
            # Lifecycle idempotency, applied here so existing ledgers are
            # fixed too: a session_end while that session has already ended
            # (nothing since), or a turn_end identical except ts to the
            # session's previous event, is a repeat and is skipped. A resume
            # reuses the session id, so its later end is kept.
            key = (event["agent"], event["session"])
            bare = {k: v for k, v in event.items() if k != "ts"}
            last = previous.get(key)
            previous[key] = bare
            if last is not None and kind == last["kind"] and (
                    kind == "session_end" or (kind == "turn_end" and bare == last)):
                continue
        save["updated"] = event["ts"]
        if kind == "plan_set":
            # `agent-autosave plan set`: folded in ledger order, newest wins
            # against to-do tool plans; never an agent session or handoff.
            save["plan"], explicit_plan = [{"step": step, "state": "todo"} for step in event["steps"]], True
            continue
        if kind == "plan_done":
            # Applies to whatever plan is current at this point in the ledger;
            # an index past the end is ignored.
            plan = save.get("plan") or []
            if event["index"] <= len(plan):
                plan[event["index"] - 1]["state"] = "done"
            continue
        if event["agent"] == "cli":
            # `agent-autosave note` / `save --note`: bookkeeping from the user's
            # terminal, never an agent session or a handoff.
            if kind == "task_done":
                # `agent-autosave done`: the user says the task is finished, with or without a plan.
                # The next substantive prompt then starts a new task.
                save["status"], save["interrupted"], compact_restore = "done", None, None
                if event.get("note"):
                    save["decisions"].append(event["note"])
            elif kind == "note":
                if event["type"] == "handoff":
                    handoff_notes.append(event["ts"])
                _memory_note(save, event, event.get("by") if event.get("by") in SOURCES else "you")
                if event["type"] == "decision":
                    save["decisions"].append(event["text"])
                else:
                    save.setdefault("notes", []).append({"type": event["type"], "text": event["text"]})
            continue
        key = (event["agent"], event["session"])
        if key not in sessions:
            sessions[key] = {"agent": key[0], "session": key[1], "turns": set(), "transcript": None}
        session = sessions[key]
        if event["turn"]:
            session["turns"].add(event["turn"])
        if event.get("transcript"):
            session["transcript"] = event["transcript"]
        # The newest agent session and whether its newest event is its end
        # (a Codex resume reuses the session id, so a later event reopens it).
        save["last_session"] = {"agent": key[0], "session": key[1], "ended": kind == "session_end"}
        if last_agent is not None and last_agent != event["agent"]:
            reason = (save["interrupted"] or {}).get("reason") or save["status"]
            save["handoffs"].append({"from": last_agent, "to": event["agent"], "at": event["ts"], "reason": reason})
        last_agent = event["agent"]
        if kind == "prompt" and is_resume_block(event["text"]):
            # Our own resume block (agent-autosave resume): the agent picks up
            # this task; it is never the goal, steering or a new task.
            save["status"], save["interrupted"], compact_restore = "active", None, None
        elif kind == "prompt":
            text = event["text"].strip()[:config["capture"]["prompt_max_chars"]]
            # An identical consecutive prompt (a resend) is one instruction.
            # Slash commands (/compact ...) stay in the ledger but are
            # instructions to the agent's UI, not task steering.
            if substantive(text) and text != last_prompt and not text.startswith("/"):
                if not save["goal"]:
                    save["goal"] = text
                else:
                    steering.append(text[:300])
            if substantive(text):
                last_prompt = text
            save["status"], save["interrupted"], compact_restore = "active", None, None
        elif kind == "session_start" and compact_restore is not None and event.get("source") == "compact":
            # The compaction finished and the session resumed: not interrupted.
            save["status"], save["interrupted"], compact_restore = compact_restore, None, None
        elif kind == "session_start":
            # Paused/interrupted/done have specific clearing events; a session
            # notification alone must not erase the context needed by its loader.
            if save["status"] not in {"paused", "interrupted", "done"}:
                save["status"] = "active"
        elif kind == "plan":
            # Newest wins between to-do tool and CLI plans; a checklist parsed
            # from a message only fills in while neither has been recorded.
            if event.get("origin", "tool") != "message":
                save["plan"], explicit_plan = deepcopy(event["items"]), True
            elif not explicit_plan:
                save["plan"] = deepcopy(event["items"])
        elif kind == "tool":
            command = event["summary"].strip()
            if "exit" in event:
                # Our own CLI (plan set/done, status, ...) is bookkeeping, not
                # task work: drop it from chains, and skip calls made only of it.
                command = strip_own_commands(command)
                if not command:
                    continue
            action = _one_line(command)
            if "exit" in event and event["exit"] is None:
                # Unknown exit: neither a pass nor a failure. It never resolves
                # an earlier failure of the same command and never adds one.
                from .shellexit import pipe_hides_exit
                action = "Ran " + action + (" (exit hidden by a pipe)" if pipe_hides_exit(command) else " (exit unknown)")
                unknown.pop(command, None)
                unknown[command] = True
            elif "exit" in event:
                action = "Ran " + action + " (exit " + str(event["exit"]) + ")"
                if is_check(command):
                    checks.pop(command, None)
                    checks[command] = {"command": command, "exit": event["exit"], "at": event["ts"]}
                failures.pop(command, None)
                unknown.pop(command, None)
                _streak(streaks, command, event, edits)
                if event["exit"] != 0:
                    failures[command] = {
                        "command": command, "exit": event["exit"],
                        "tail": "\n".join([line for line in event.get("output_tail", "").splitlines()
                                            if not _OWN_JSON.match(line)][-config["capture"]["output_tail_lines"]:]),
                    }
            if event.get("readonly") or ("exit" in event and is_readonly(command)):
                # Reading (Read, Grep, cat, git diff, ...) is not progress:
                # a successful read never enters last_actions; its files are
                # "looked at". A failing read still counts as a failure above.
                if event.get("exit", 0) == 0:
                    looked.extend(event["files"])
                    continue
            actions.append(action)
            if re.search(r"edit|write|patch", event["tool"], re.IGNORECASE):
                files.extend(event["files"])
                edits += 1
        elif kind == "summary":
            # Written by the agent itself when it compacted; never rebuilt here.
            # Claude Code: `at` is the compact_boundary line's timestamp and the
            # token counts its compactMetadata (recorded shape:
            # tests/fixtures/claude-code/transcripts/compact-2.1.302.jsonl).
            save["summary"] = {"text": display.shorten(event["text"].strip(), 1200), "agent": event["agent"],
                               "at": event.get("at") or event["ts"], "trigger": event.get("trigger"),
                               "pre_tokens": event.get("pre_tokens"), "post_tokens": event.get("post_tokens")}
        elif kind == "usage":
            save["usage"][event["agent"]] = _reading(save["usage"].get(event["agent"]), event,
                                                     config["handoff"]["warn_at_percent"])
        elif kind == "note" and event["type"] == "decision":
            _memory_note(save, event, event["agent"])
            save["decisions"].append(event["text"])
        elif kind == "note":
            _memory_note(save, event, event["agent"])
            if event["type"] == "handoff":
                handoff_notes.append(event["ts"])
            save.setdefault("notes", []).append({"type": event["type"], "text": event["text"]})
        elif kind == "interrupted":
            save["status"] = "interrupted"
            save["interrupted"] = {"reason": event["reason"], "agent": event["agent"], "at": event["ts"]}
            compact_restore = None
        elif kind == "compact":
            if save["interrupted"] is None:
                if compact_restore is None:
                    compact_restore = save["status"]
                save["status"] = "interrupted"
                save["interrupted"] = {"reason": "compact", "agent": event["agent"], "at": event["ts"]}
        elif kind == "session_end":
            if compact_restore is not None:
                # A clean exit after compaction is a pause, not an interruption.
                save["status"] = "done" if compact_restore == "done" else "paused"
                save["interrupted"], compact_restore = None, None
            elif save["status"] != "interrupted" and save["status"] != "done":
                save["status"] = "paused"
        elif kind == "turn_end":
            report = display.report(event.get("last_message", ""), 600)
            if report:
                # The agent's own closing words: what remains and why, which
                # no tool event records.
                save["last_report"], save["last_report_agent"], save["last_report_at"] = report, event["agent"], event["ts"]
            if compact_restore is not None:
                save["status"], save["interrupted"] = compact_restore, None
                compact_restore = None
            plan = save.get("plan", [])
            if (plan and all(item["state"] == "done" for item in plan) and not failures
                    and not unknown and save["status"] != "interrupted"):
                save["status"] = "done"
    save["files_edited"] = list(dict.fromkeys(
        path for path in files if not _ignored(path, config["capture"]["ignore_paths"])
    ))[:config["compile"]["files"]]
    if git_state:
        files.extend(git_state.get("dirty", []))
    save["files_touched"] = list(dict.fromkeys(
        path for path in files if not _ignored(path, config["capture"]["ignore_paths"])
    ))[:config["compile"]["files"]]
    save["steering"] = steering[-config["compile"]["steering"]:]
    # The newest steering prompt: what the user asked for last, often not the goal.
    save["latest_request"] = steering[-1] if steering else ""
    save["last_actions"] = _collapse(actions)[-config["compile"]["last_actions"]:][::-1]
    # For display only (SAVE.md, context, status); root is a compile input.
    save["last_actions_shown"] = [display.action(item, root) for item in save["last_actions"]]
    ignore = config["capture"]["ignore_paths"]
    save["files_read"] = list(dict.fromkeys(p for p in reversed(looked) if not _ignored(p, ignore)))[:10]
    save["failing"] = next(reversed(failures.values())) if failures else None
    for item in save["usage"].values():
        # The handoff the low-battery ask wants: a note at or after the crossing.
        item["handoff_at"] = next((ts for ts in reversed(handoff_notes) if item["crossed_at"]
                                   and timestamp(ts) >= timestamp(item["crossed_at"])), None)
    stuck = [s for s in streaks.values() if s["failures"] >= 3]
    save["stuck"] = {k: v for k, v in stuck[-1].items() if k != "edits_at"} if stuck else None
    newest = list(checks.values())[::-1]
    save["checks"] = ([c for c in newest if c["exit"] != 0] + [c for c in newest if c["exit"] == 0])[:6]
    # Newest run had no exit code: shown as "exit unknown", never as a pass.
    save["unknown_exit"] = [c for c in unknown if c not in failures][-5:][::-1]
    for session in sessions.values():
        save["agents"].append({**session, "turns": len(session["turns"])})
    save["next_action"] = _next_action(save, list(unknown))
    return redactor.value(save)


def serialize(save: dict[str, Any]) -> bytes:
    """Canonical UTF-8 JSON with a trailing newline."""
    return json_bytes(save)
