"""Hook entrypoint: stdin payload -> normalized event -> ledger. Always fails open.

Only the standard library is used, and the compiler, git and rendering modules
are imported lazily so routine tool hooks stay well inside the 300 ms budget.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

TYPE_CHECKING = False  # the status line path never imports typing (see tests/test_statusline.py)
if TYPE_CHECKING:
    from typing import Any, BinaryIO, Mapping, TextIO

MAX_PAYLOAD = 2 * 1024 * 1024
AGENTS = ("claude-code", "codex", "cursor", "gemini", "antigravity", "copilot", "opencode")
END_EVENTS = ("SessionEnd", "sessionEnd")
PROMPT_EVENTS = ("UserPromptSubmit", "beforeSubmitPrompt")
# Payloads without hook_event_name: the event comes from our command line
# (antigravity.google/docs/hooks; our OpenCode plugin builds its own).
NAMELESS = ("antigravity", "opencode")
# Each agent's events under the Claude Code names the rest of this module uses.
CANONICAL = {"gemini": {"BeforeAgent": "UserPromptSubmit", "AfterAgent": "Stop", "AfterTool": "PostToolUse",
                        "PreCompress": "PreCompact"},
             "opencode": {"session.created": "SessionStart", "tool.execute.after": "PostToolUse",
                          "session.idle": "Stop", "session.compacted": "PreCompact"}}
CURSOR_CONTINUE = '{"continue":true}'
STATE_FILE = "adapter-claude-code.json"  # per agent: adapter-<agent>.json
KEEP_SESSIONS = 20


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def find_project(start: str | Path) -> Path | None:
    """Nearest directory at or above `start` that holds `.autosave/`."""
    from pathlib import Path
    current = Path(start).resolve()
    for root in (current, *current.parents):
        if (root / ".autosave").is_dir():
            return root
    return None


def record(agent: str, payload: Any, *, base: Path, env: Mapping[str, str], event: str = "") -> Path | None:
    """AGENT_AUTOSAVE_RECORD=1 or `record on` in .autosave/config.json: keep the
    raw payload, redacted, as a fixture candidate."""
    settings = {}
    if env.get("AGENT_AUTOSAVE_RECORD") != "1":
        settings = _record_settings(base)
        if not settings.get("enabled"):
            return None
    else:
        settings = {"dir": env.get("AGENT_AUTOSAVE_RECORD_DIR", ""),
                    "agent_version": env.get("AGENT_AUTOSAVE_RECORD_VERSION", "")}
    from pathlib import Path
    import uuid
    from .config import resolve
    from .redact import Redactor, shorten_home
    from .storage import atomic_write, private_dir
    target = settings.get("dir")
    if target:
        directory = Path(target).expanduser() / agent
    elif base is not None:
        directory = Path(base) / ".autosave" / "recordings" / agent
    else:
        return None
    redactor = Redactor.from_project(base or Path.cwd(), resolve())
    # The home prefix is personal: "~" replaces it (also its realpath), so
    # fixtures keep the vendor shape without the user name.
    payload = shorten_home(payload)
    private_dir(directory)
    name = str(payload.get("hook_event_name") or event or "unknown")
    name = "".join(c for c in name if c.isalnum()) or "unknown"
    envelope = {"v": 1, "recorded_at": _now(), "agent": agent,
                "agent_version": settings.get("agent_version") or "unknown",
                "provenance": "Real hook stdin captured by agent-autosave record mode; secrets redacted, home directory shown as ~",
                "payload": payload}
    path = directory / "{}-{}.json".format(name, uuid.uuid4().hex[:12])
    atomic_write(path, envelope, redactor=redactor)
    return path


def _record_settings(base):
    """The `record` block of .autosave/config.json, {} if absent or unreadable."""
    if base is None:
        return {}
    try:
        with open(os.path.join(str(base), ".autosave", "config.json"), encoding="utf-8") as stream:
            value = json.load(stream).get("record")
    except (OSError, ValueError, AttributeError):
        return {}
    if not isinstance(value, dict) or value.get("enabled") is not True:
        return {}
    return {"enabled": True, "dir": str(value.get("dir") or ""), "agent_version": str(value.get("agent_version") or "")}


def _session_state(directory, session, name=STATE_FILE):
    from .storage import read_json
    try:
        state = read_json(directory / name)
    except Exception:
        state = None
    if not isinstance(state, dict) or not isinstance(state.get("sessions"), dict):
        state = {"v": 1, "sessions": {}}
    entry = state["sessions"].get(session)
    if not isinstance(entry, dict):
        entry = {"turn": 0, "tasks": {}}
    return state, entry


def _save_state(directory, state, session, entry, redactor, name=STATE_FILE):
    from .storage import atomic_write
    sessions = state["sessions"]
    sessions.pop(session, None)
    sessions[session] = entry  # insertion order = recency
    while len(sessions) > KEEP_SESSIONS:
        sessions.pop(next(iter(sessions)))
    atomic_write(directory / name, state, redactor=redactor)


def _codex_facts(payload, event_name):
    """Exit code / limit reason from the Codex rollout tail (never stored raw)."""
    import time
    from .transcripts import codex as rollout
    path = payload.get("transcript_path")
    if not isinstance(path, str):
        return {}
    if event_name == "PostToolUse" and payload.get("tool_name") == "Bash":
        call_id = payload.get("tool_use_id")
        # The async hook may start before Codex writes the call's output line:
        # retry briefly, well inside the 300 ms budget.
        deadline = time.monotonic() + 0.12
        while True:
            code = rollout.exit_code(rollout.tail_lines(path), call_id)
            if code is not None or time.monotonic() >= deadline:
                return {"exit": code}
            time.sleep(0.03)
    if event_name in ("Stop", "SessionEnd"):
        rows = rollout.tail_lines(path)  # one bounded read for every fact
        reason, turn_id = rollout.limit_hit(rows)
        facts = {"limit": reason, "limit_turn": turn_id} if reason else {}
        if event_name == "Stop":
            usage = rollout.usage(rows)
            if usage:
                facts["usage"] = usage
            compacted = rollout.compacted(rows)
            if compacted:
                facts["compacted"] = compacted
        return facts
    return {}


def _session_start_output(text, agent="claude-code"):
    """The session-start context in each agent's documented output shape:
    Copilot reads a top-level additionalContext, Antigravity injectSteps,
    Gemini CLI hookSpecificOutput.additionalContext like Claude Code and
    Codex. OpenCode's plugin ignores stdout."""
    if not text or agent == "opencode":
        return ""
    if agent == "copilot":
        return json.dumps({"additionalContext": text}, ensure_ascii=True)
    if agent == "antigravity":
        return json.dumps({"injectSteps": [{"systemMessage": {"systemMessage": text}}]}, ensure_ascii=True)
    return json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                              "additionalContext": text}}, ensure_ascii=True)


def _handoff_ask(store, save, agent, state_name):
    """Low battery: once per crossing, ask the agent whose usage limit passed
    handoff.warn_at_percent to record a handoff note, unless one was already
    recorded since. Returns the ask's text, or ''.

    UserPromptSubmit accepts hookSpecificOutput.additionalContext in Claude
    Code and in Codex (developers.openai.com/codex/hooks)."""
    item = (save.get("usage") or {}).get(agent) or {}
    crossed = item.get("crossed_at")
    if not store.config["handoff"]["ask"] or not crossed or item.get("handoff_at"):
        return ""
    from .locking import exclusive
    from .loader import AGENT_NAMES, window_name
    from .storage import atomic_write, read_json
    with exclusive(store.directory / ".capture.lock", timeout=0.1):
        state = read_json(store.directory / state_name) or {"v": 1, "sessions": {}}
        if crossed in (state.get("asked") or []):
            return ""
        state["asked"] = (list(state.get("asked") or []) + [crossed])[-10:]
        atomic_write(store.directory / state_name, state, redactor=store.redactor)
    return ("agent-autosave: {} has used {}% of its {} limit. Before continuing, record a short handoff "
            "(what is done, what is next, anything risky) with: agent-autosave note --quiet --type handoff \"...\"").format(
                AGENT_NAMES.get(agent, agent), int(round(item["limit_pct"])), window_name(item.get("limit_window_minutes")))


def _true_exit(event: dict) -> dict:
    """A command's exit as far as it can be known: unknown behind a pipe into
    a filter, failing when a test runner printed its failure summary."""
    if event.get("kind") != "tool" or "exit" not in event:
        return event
    from .shellexit import judge
    code, kept = judge(str(event.get("summary") or ""), event["exit"], event.get("output_tail"))
    event = dict(event, exit=code)
    if kept is not None:
        event["output_tail"] = kept
    elif not (type(code) is int and code != 0):
        event.pop("output_tail", None)
    return event


def handle(agent: str, event_name: str, raw: bytes, *, cwd: str | Path | None = None,
           env: Mapping[str, str] | None = None) -> str:
    """Process one hook call; return the text for stdout (usually empty)."""
    env = os.environ if env is None else env
    if agent not in AGENTS or len(raw) > MAX_PAYLOAD:
        return ""
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Hook payload must be a JSON object.")
    if agent == "claude-code" and ("cursor_version" in payload or "timestamp" in payload):
        # Cursor and Copilot CLI also run .claude/settings.json hooks; their
        # own entries record those events. Claude Code sends no `timestamp`
        # (tests/fixtures/claude-code); Copilot's VS Code format always does.
        return ""
    if agent == "cursor":
        from .normalize.cursor import root_of
        start = root_of(payload) or cwd or os.getcwd()
    elif agent == "antigravity":
        from .normalize.antigravity import root_of as antigravity_root
        start = antigravity_root(payload) or cwd or os.getcwd()
    else:
        start = payload.get("cwd") if isinstance(payload.get("cwd"), str) else (cwd or os.getcwd())
    try:
        project = find_project(start)
    except OSError:
        project = None
    try:
        record(agent, payload, base=project, env=env, event=event_name)
    except Exception as error:
        if project is not None:
            from .storage import log_error
            log_error(project / ".autosave", "hook.record", error)
    if project is None:
        return ""  # not initialized: stay silent and write nothing
    if agent in NAMELESS:
        payload = dict(payload, hook_event_name=event_name)  # recorded above as sent
    if payload.get("hook_event_name") != event_name:
        raise ValueError("Hook event name does not match the configured hook.")
    raw_name = event_name

    from importlib import import_module
    from .locking import exclusive
    normalize = import_module(".normalize." + agent.replace("-", "_"), __package__).normalize
    event_name = CANONICAL.get(agent, {}).get(event_name, event_name)
    from .state import TaskStore
    # 200 ms lock wait: a detached SessionEnd rebuild may briefly hold the lock
    # when the next session starts; still inside the 300 ms hook budget.
    store = TaskStore(project, lock_timeout=0.2)
    directory = store.directory
    if agent == "cursor":
        from .normalize.cursor import session_of
        session = session_of(payload)
    else:
        session = payload.get("conversationId" if agent == "antigravity" else "session_id")
    if not isinstance(session, str) or not session:
        raise ValueError("Hook payload has no session id.")
    extra = {}
    if agent == "codex":
        # Read the rollout before taking the capture lock (bounded tail read).
        extra["facts"] = _codex_facts(payload, event_name)
    elif agent == "claude-code" and event_name == "SessionStart" and payload.get("source") == "compact":
        from .transcripts.claude import compact_summary
        extra["summary"] = compact_summary(payload.get("transcript_path"))
    state_name = "adapter-{}.json".format(agent)
    with exclusive(directory / ".capture.lock", timeout=0.1):
        state, entry = _session_state(directory, session, state_name)
        turn = entry["turn"] + (1 if event_name in PROMPT_EVENTS else 0)
        events, tasks = normalize(payload, ts=_now(), turn=turn, tasks=entry.get("tasks"), **extra)
        events = [_true_exit(event) for event in events]
        inject_cursor = False
        if agent == "cursor" and event_name == "beforeSubmitPrompt" and not (tasks or {}).get("__injected__"):
            # Cursor's sessionStart context misses the first message in the
            # Agents Window: inject on the conversation's first prompt instead.
            inject_cursor = True
            tasks = dict(tasks or {}, __injected__=True)
        updated = {"turn": turn, "tasks": tasks}
        for key in ("pending_summary", "summary_boundary", "recalled"):
            if key in entry:
                updated[key] = entry[key]
        if extra.get("summary"):
            updated["summary_boundary"] = extra["summary"].get("boundary")
            updated.pop("pending_summary", None)
        elif "summary" in extra and isinstance(payload.get("transcript_path"), str):
            # Claude Code can run SessionStart(compact) before it writes the
            # boundary and summary lines: read again at the next Stop or
            # SessionEnd, in the background process (retry_summaries).
            updated["pending_summary"] = payload["transcript_path"]
        retry_summary = agent == "claude-code" and event_name in ("Stop", "SessionEnd") and \
            bool(updated.get("pending_summary"))
        if updated != entry or session not in state["sessions"]:
            _save_state(directory, state, session, updated, store.redactor, state_name)

    output = ""
    recall_cursor = False
    if inject_cursor:
        from .storage import read_view
        from .loader import context
        previous = read_view(directory / "save.json") or {}
        old_head = (previous.get("git") or {}).get("head")
        save = store.rebuild()
        current_head = (save.get("git") or {}).get("head")
        if old_head and save.get("git") is not None:
            save = dict(save, git=dict(save["git"], head=old_head))
        text = _start_text(project, store, save, current_head, agent, session)
        recalled = _recall(project, store, events, agent, session, state_name)
        text = "\n\n".join(part for part in (text, recalled) if part)
        output = json.dumps(dict({"continue": True}, **({"additional_context": text} if text else {})),
                            ensure_ascii=True)
    elif agent == "cursor" and event_name == "beforeSubmitPrompt":
        output = CURSOR_CONTINUE
        recall_cursor = True
    if event_name == "SessionStart":
        # Finish the previous session's save (SessionEnd only appends), then
        # build the context before this start is recorded.
        from .storage import read_view
        from .loader import context
        previous = read_view(directory / "save.json") or {}
        old_head = (previous.get("git") or {}).get("head")
        save = store.rebuild()
        if any(item.get("source") == "clear" for item in events) and store.clear_ends_task(save):
            # The finished task is archived; this session starts a new one
            # and is handed nothing.
            save = store.start(_now(), reason="clear", git_state=save.get("git"))
        current_head = (save.get("git") or {}).get("head")
        if old_head and save.get("git") is not None:
            save = dict(save, git=dict(save["git"], head=old_head))
        output = _session_start_output(_start_text(project, store, save, current_head, agent, session), agent)
    if event_name in END_EVENTS and any(item["kind"] == "session_end" for item in events):
        # The agent is exiting: append and settle the status in-process,
        # without git (the last save's git state is reused). Codex kills the
        # hook's process group once it stops waiting (seen in a real run:
        # session_end landed in the ledger, SAVE.md stayed active), so the
        # writes are made back to back after all slow work (see end_session).
        from .storage import read_view
        previous = read_view(directory / "save.json") or {}
        store.end_session(events, git_state=previous.get("git"))
        if env.get("AGENT_AUTOSAVE_SESSION_END") != "sync":
            # Best effort: refresh the git facts (and read a pending
            # compaction summary) in a detached process.
            from .background import spawn
            spawn(project)
        else:
            if retry_summary:
                retry_summaries(project)
            _memory_sync(project, env, now=True)
        return output
    rebuild = False
    for item in events:
        store.append(item)
        kind = item["kind"]
        if kind in ("prompt", "turn_end", "interrupted", "compact", "session_start"):
            rebuild = True
        elif kind == "tool" and item.get("exit", 0) != 0:
            rebuild = True
    if rebuild:
        save = store.rebuild()
        if event_name == "UserPromptSubmit":
            ask = _handoff_ask(store, save, agent, state_name)
            recalled = _recall(project, store, events, agent, session, state_name)
            text = "\n\n".join(part for part in (ask, recalled) if part)
            if text and agent in ("claude-code", "codex", "gemini"):
                # Gemini CLI's BeforeAgent takes the same field
                # (geminicli.com/docs/hooks/reference/); Copilot drops it.
                output = json.dumps({"hookSpecificOutput": {"hookEventName": raw_name,
                                                            "additionalContext": text}}, ensure_ascii=True)
    if recall_cursor:
        recalled = _recall(project, store, events, agent, session, state_name)
        if recalled:
            output = json.dumps({"continue": True, "additional_context": recalled}, ensure_ascii=True)
    if store.archived or (any(item["kind"] == "turn_end" for item in events) and _sync_each_turn(project, store.config)):
        _memory_sync(project, env)
    if retry_summary:
        if env.get("AGENT_AUTOSAVE_SESSION_END") == "sync":
            retry_summaries(project)  # tests and synchronous runs
        else:
            from .background import spawn
            spawn(project)
    return output


def _start_text(project, store, save, current_head, agent, session):
    """The SessionStart (or Cursor first-prompt) text: the handoff block with
    its Project memory section, else the project memory block alone
    (inject.memory_on_start), else ''."""
    from .loader import context
    memory = _session_memory(project, store, save)
    text = context(save, store.config, current_head=current_head, agent=agent, session=session, memory=memory)
    if text or not memory:
        return text
    from .recall import start_block
    return start_block(project, store.config, memory)


def _session_memory(project, store, save):
    """recall.session_memory, or None when it fails (logged): memory is an
    extra; the handoff must never depend on it."""
    try:
        from .recall import session_memory
        return session_memory(project, store.config, save)
    except Exception as error:
        store._error("recall.session", error)
        return None


def _recall(project, store, events, agent, session, state_name):
    """The recall block for this prompt ('' when nothing is relevant), each
    entry shown at most once per session (remembered in adapter-<agent>.json)."""
    prompt = next((item.get("text") for item in events if item["kind"] == "prompt"), None)
    if not prompt or not store.config["recall"]["enabled"]:
        return ""
    try:
        from .locking import exclusive
        from .recall import recall
        from .storage import atomic_write
        state, entry = _session_state(store.directory, session, state_name)
        text, ids = recall(project, store.config, prompt, seen=entry.get("recalled") or [], agent=agent)
        if ids:
            with exclusive(store.directory / ".capture.lock", timeout=0.1):
                state, entry = _session_state(store.directory, session, state_name)
                entry["recalled"] = (list(entry.get("recalled") or []) + ids)[-200:]
                state["sessions"][session] = entry
                atomic_write(store.directory / state_name, state, redactor=store.redactor)
        return text
    except Exception as error:
        store._error("recall.prompt", error)
        return ""


def _sync_each_turn(project, config):
    """Vault and agentic-stack pages follow the task turn by turn; otherwise
    the sync waits for session end or an archive."""
    from . import agentic, vault
    return vault.configured(config) or agentic.enabled(project, config)


def _memory_sync(project, env, now=False):
    """Run the memory sync detached, or in-process for synchronous runs
    (AGENT_AUTOSAVE_SESSION_END=sync, used by tests) or when `now`."""
    if now or env.get("AGENT_AUTOSAVE_SESSION_END") == "sync":
        from .memory_sync import sync
        sync(project)
        return
    from .background import spawn
    spawn(project, "sync")


def retry_summaries(project: Path) -> bool:
    """Read the compaction summary a Claude Code SessionStart(compact) could
    not find yet; runs in the background process (bounded 512 KB tail read).
    Appends one `summary` event per compaction and rebuilds. True when one
    was added."""
    from .locking import exclusive
    from .normalize.claude_code import summary_event
    from .state import TaskStore
    from .transcripts.claude import compact_summary
    store = TaskStore(project, lock_timeout=10)
    directory = store.directory
    name = "adapter-claude-code.json"
    added = False
    with exclusive(directory / ".capture.lock", timeout=5):
        from .storage import read_json
        try:
            state = read_json(directory / name)
        except Exception:
            return False
        if not isinstance(state, dict) or not isinstance(state.get("sessions"), dict):
            return False
        for session, entry in list(state["sessions"].items()):
            if not isinstance(entry, dict) or not entry.get("pending_summary"):
                continue
            found = compact_summary(entry["pending_summary"])
            if not found:
                continue
            entry.pop("pending_summary", None)
            if found.get("boundary") and found["boundary"] == entry.get("summary_boundary"):
                continue
            entry["summary_boundary"] = found.get("boundary")
            base = dict(v=1, ts=_now(), agent="claude-code", session=session, turn=entry.get("turn", 0))
            store.append(summary_event(base, found))
            added = True
        from .storage import atomic_write
        atomic_write(directory / name, state, redactor=store.redactor)
    if added:
        store.rebuild()
    return added


def _shield_exit():
    """The agent is shutting down: a hangup or polite termination aimed at
    its process group must not cut the final save short (SIGKILL still can,
    which end_session's write ordering limits)."""
    try:
        import signal
        for name in ("SIGHUP", "SIGTERM", "SIGINT", "SIGPIPE"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), signal.SIG_IGN)
    except Exception:
        pass


def main(agent: str, event_name: str, stdin: BinaryIO | None = None, stdout: TextIO | None = None) -> int:
    """Never raises, never blocks on errors, always returns exit code 0."""
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout
    project = None
    if event_name in END_EVENTS:
        _shield_exit()
    try:
        raw = stdin.read(MAX_PAYLOAD + 1)
        try:
            payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("cwd"), str):
                project = find_project(payload["cwd"])
        except Exception:
            pass
        if project is None:
            try:
                project = find_project(os.getcwd())
            except Exception:
                project = None
        output = handle(agent, event_name, raw)
        if not output and agent == "cursor" and event_name == "beforeSubmitPrompt":
            output = CURSOR_CONTINUE  # never hold a Cursor prompt back
        if output:
            stdout.write(output)
            stdout.flush()
    except BaseException as error:  # noqa: B902 - a hook must never fail the agent
        try:
            if project is not None:
                from .storage import log_error
                log_error(project / ".autosave", "hook.{}.{}".format(agent, event_name)[:80], error)
        except BaseException:
            pass
        if agent == "cursor" and event_name == "beforeSubmitPrompt":
            try:
                stdout.write(CURSOR_CONTINUE)
                stdout.flush()
            except BaseException:
                pass
    return 0
