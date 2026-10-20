"""Task boundaries and persistence around the pure compiler, for later adapters."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .config import load, resolve
from .events import prepare, timestamp
from .ledger import Ledger
from .redact import Redactor
from .storage import atomic_write, log_error, private_dir, read_json, read_view

_AUTO_GIT = object()


def _utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class TaskStore:
    """One active task per working tree; no hooks, installers, or agent launchers."""
    def __init__(self, project: str | Path, config: dict[str, Any] | None = None, *, lock_timeout: float = 0.05) -> None:
        self.project = Path(project)
        self.config = load(project) if config is None else resolve(config)
        self.redactor = Redactor.from_project(project, self.config)
        self.ledger = Ledger(project, self.config, redactor=self.redactor, lock_timeout=lock_timeout)
        self.directory = self.ledger.directory
        # The task id the last boundary archived (None until one does): the
        # hook then starts the memory sync to write its journal page.
        self.archived: str | None = None

    def _error(self, operation, error):
        log_error(self.directory, operation, error, redactor=self.redactor)

    def slots(self) -> dict[str, Any] | None:
        """task.json as {"v": 1, "current": branch, "slots": {branch: task}}.

        One task per branch: the current branch's task is the save; the
        others are parked in slots/<slug>.json. A task.json written before
        slots (a single task) reads as one slot for the branch checked out
        now ("" outside git). Read only: _task() writes the migration."""
        data = read_json(self.directory / "task.json")
        if data is None or "slots" in data:
            return data
        from .branches import head_branch
        branch = head_branch(self.project) or ""
        return {"v": 1, "current": branch, "slots": {branch: data}, "legacy": True}

    def _write_slots(self, data):
        data = {key: value for key, value in data.items() if key != "legacy"}
        atomic_write(self.directory / "task.json", data, redactor=self.redactor)

    def _write_task(self, task):
        """Store `task` as the current branch's slot."""
        data = self.slots()
        if data is None:
            from .branches import head_branch
            data = {"v": 1, "current": head_branch(self.project) or "", "slots": {}}
        elif data.get("legacy"):
            data = dict(data, slots={})
        data["slots"][data["current"]] = task
        self._write_slots(data)

    def _task(self, at=None):
        """The current branch's task. Under the ledger lock: migrates an old
        task.json, and on a branch change parks this branch's save and
        switches to (or starts) the new branch's task. `at` stamps a task
        started by the switch (the triggering event's time)."""
        data = self.slots()
        if data is None:
            raise ValueError("No active task; call start before appending or rebuilding.")
        if data.get("legacy"):
            data = {"v": 1, "current": data["current"], "slots": {data["current"]: data["slots"][data["current"]]}}
            self._write_slots(data)  # one atomic replace: the old file or the new, never half
        from .branches import head_branch
        branch = head_branch(self.project)
        if branch is not None and branch != data["current"]:
            data = self._switch_locked(data, branch, at)
        task = data["slots"].get(data["current"])
        if not isinstance(task, dict):
            raise ValueError("Invalid stored task slot.")
        if not isinstance(task.get("task_id"), str) or not re.fullmatch(r"t_[0-9]+_[0-9]+(?:_[0-9]+)?", task["task_id"]):
            raise ValueError("Invalid stored task identifier.")
        timestamp(task.get("started"))
        cursor = task.get("cursor")
        if (not isinstance(cursor, dict) or type(cursor.get("offset")) is not int or cursor["offset"] < 0
                or (cursor.get("identity") is not None and
                    (not isinstance(cursor["identity"], list) or len(cursor["identity"]) != 2
                     or not all(type(item) is int for item in cursor["identity"])) )):
            raise ValueError("Invalid stored ledger cursor.")
        return task

    def _switch_locked(self, data, branch, at):
        """Park the current branch's save, then make `branch` current: its
        parked save comes back, or it starts a new task. One ledger serves
        every branch (events carry their task id)."""
        from .branches import slug
        from .compiler import compile_events, task_id_at
        from .events import timestamp as parse
        current = read_view(self.directory / "save.json")
        private_dir(self.directory / "slots")
        if current is not None:
            atomic_write(self.directory / "slots" / (slug(data["current"]) + ".json"), current, redactor=self.redactor)
        data = dict(data, current=branch, slots=dict(data["slots"]))
        parked = None
        if branch in data["slots"]:
            parked = read_view(self.directory / "slots" / (slug(branch) + ".json"))
        else:
            started = at or _utc_now()
            parse(started)
            identifier, suffix, taken = task_id_at(started), 1, self._known_ids(data)
            base = identifier
            while identifier in taken:
                identifier, suffix = base + "_" + str(suffix), suffix + 1
            data["slots"][branch] = {"v": 1, "task_id": identifier, "started": started,
                                     "cursor": self.ledger.cursor(), "start_head": None}
            parked = compile_events([], None, self.config, started=started, task_id=identifier, redactor=self.redactor, root=str(self.project))
        # task.json first: a crash before the views are rewritten leaves a
        # stale save.json that the next rebuild replaces, never a misfiled one.
        self._write_slots(data)
        if parked is not None:
            self._write_save(parked)
        return data

    def _known_ids(self, data):
        ids = {task.get("task_id") for task in data["slots"].values() if isinstance(task, dict)}
        history = self.directory / "history"
        if history.is_dir():
            ids |= {path.stem for path in history.glob("t_*.json")}
        return ids

    def live_task_ids(self) -> set[str]:
        """Every slot's task id: ledger rotation keeps all of their events."""
        data = self.slots() or {"slots": {}}
        return {task["task_id"] for task in data["slots"].values() if isinstance(task, dict) and "task_id" in task}

    def start(self, started: str, *, reason: str = "init", git_state: dict[str, Any] | None = None) -> dict[str, Any]:
        """The init/clear callers supply time explicitly; archive before resetting."""
        if reason not in {"init", "clear"}:
            raise ValueError("Unsupported explicit task boundary.")
        try:
            timestamp(started)
            with self.ledger.lock():
                return self._start_locked(started, git_state)
        except Exception as error:
            self._error("task.start", error)
            raise

    def _start_locked(self, started, git_state):
        from .compiler import compile_events, task_id_at
        self.ledger._repair_tail()
        previous = read_view(self.directory / "save.json")
        previous_task = None
        if previous is not None:
            # Rebuild before archiving so successful tool events since the last
            # turn-end snapshot are not silently missing from the old save.
            previous_task = self._task()
            previous = self._compile_locked(previous_task, previous.get("git"))
            # Archiving ends the task: an open `active` becomes `paused` so
            # history never shows a still-running save after clear / new task.
            if previous.get("status") == "active":
                previous = dict(previous, status="paused")
            private_dir(self.directory / "history")
            atomic_write(self.directory / "history" / (previous["task_id"] + ".json"), previous, redactor=self.redactor)
            self.archived = previous["task_id"]
        base = task_id_at(started)
        identifier = base
        suffix = 1
        while ((self.directory / "history" / (identifier + ".json")).exists()
               or (previous is not None and previous["task_id"] == identifier)):
            identifier = base + "_" + str(suffix)
            suffix += 1
        task = {"v": 1, "task_id": identifier, "started": started,
                "cursor": self.ledger.cursor(), "start_head": (git_state or {}).get("head")}
        if previous is not None:
            # Sessions still open in the archived task: their late lifecycle
            # events (Codex's SessionEnd after clear) go to that task.
            task["previous"] = {key: previous_task[key] for key in ("task_id", "started", "cursor")
                                if key in previous_task}
            task["previous"]["git"] = previous.get("git")
            task["previous"]["sessions"] = [[a["agent"], a["session"]] for a in previous.get("agents") or []]
        self._write_task(task)
        save = compile_events([], git_state, self.config, started=started, task_id=identifier, redactor=self.redactor, root=str(self.project))
        self._write_save(save)
        self.ledger.prune_history()
        return save

    @staticmethod
    def clear_ends_task(save: dict[str, Any] | None) -> bool:
        """A clear (Claude's /clear: SessionStart source "clear") ends the task
        when it looks finished: it has a goal, nothing failing, no command
        with an unknown exit, and no unfinished plan step. Otherwise the clear
        only resets the agent's context and the task carries on."""
        if not isinstance(save, dict) or not save.get("goal"):
            return False
        if save.get("failing") or save.get("unknown_exit"):
            return False
        return all(item.get("state") == "done" for item in save.get("plan") or [])

    ROUTED_KINDS = frozenset({"session_end", "turn_end", "interrupted", "compact", "tool", "plan"})

    def _route_locked(self, clean, task):
        """Return the archived task this event belongs to, or None.

        An event from a session of the previous task that has not yet spoken
        in this task (no prompt or session_start since the clear) belongs to
        the previous task. A prompt or session_start adopts the session into
        the current task."""
        previous = task.get("previous") or {}
        key = [clean.get("agent"), clean.get("session")]
        if key not in (previous.get("sessions") or []):
            return None
        if clean["kind"] in self.ROUTED_KINDS:
            return previous
        previous["sessions"] = [s for s in previous["sessions"] if s != key]
        self._write_task(task)
        return None

    def _rearchive_locked(self, previous):
        """Recompile an archived task after a late event of its own."""
        path = self.directory / "history" / (previous["task_id"] + ".json")
        if not path.is_file():
            return
        save = self._compile_locked(dict(previous, cursor=previous.get("cursor") or {"offset": 0, "identity": None}),
                                    previous.get("git"))
        if save.get("status") == "active":
            save = dict(save, status="paused")
        atomic_write(path, save, redactor=self.redactor)
        self.archived = previous["task_id"]

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        """Append only; the adapter decides when to call rebuild."""
        try:
            clean = prepare(event, self.config, self.redactor)
            with self.ledger.lock():
                # Prompt boundary detection reads the ledger before appending;
                # recover an interrupted append before that read as well.
                self.ledger._repair_tail()
                task = self._task(at=clean["ts"])
                previous = read_view(self.directory / "save.json")
                from .compiler import is_resume_block
                if clean["kind"] == "prompt" and previous and not is_resume_block(clean.get("text")):
                    # Re-evaluate queued events before making the boundary decision.
                    current = self._compile_locked(task, previous.get("git"))
                    if current["status"] == "done":
                        self._start_locked(clean["ts"], previous.get("git"))
                        task = self._task()
                owner = self._route_locked(clean, task)
                if owner is not None:
                    clean["task_id"] = owner["task_id"]
                    self.ledger._append_locked(clean, active_task=self.live_task_ids())
                    self._rearchive_locked(owner)
                    clean["routed"] = True
                    return clean
                clean["task_id"] = task["task_id"]
                self.ledger._append_locked(clean, active_task=self.live_task_ids())
            return clean
        except Exception as error:
            self._error("task.append", error)
            raise

    def _own_paths(self):
        """Our own footprint (.autosave/ and files init wrote) is not task work."""
        import json
        own = [".autosave/"]
        try:
            receipt = json.loads((self.project / ".autosave/install.json").read_text(encoding="utf-8"))
            own += [item["path"] for item in receipt.get("files", []) if isinstance(item.get("path"), str)]
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        try:
            from . import agentic
            if agentic.enabled(self.project, self.config):
                own.append(".agent/")  # agentic-stack's own files are not task work
        except Exception:
            pass
        return own

    def _compile_config(self, own):
        config = dict(self.config, capture=dict(self.config["capture"]))
        config["capture"]["ignore_paths"] = list(config["capture"]["ignore_paths"]) + own
        return config

    def end_session(self, events: list[dict[str, Any]], *, git_state: dict[str, Any] | None) -> dict[str, Any] | None:
        """SessionEnd: append and settle the save in one lock hold.

        Codex kills the hook's process group when it stops waiting for it
        (shutdown after /quit), so everything slow (imports, ledger read,
        compile, render) happens before the first write, and the three writes
        (ledger line, save.json, SAVE.md) land back to back.
        """
        try:
            from .render import render
            cleans = [prepare(event, self.config, self.redactor) for event in events]
            with self.ledger.lock():
                self.ledger._repair_tail()
                task = self._task()
                owner = self._route_locked(cleans[0], task) if cleans else None
                if owner is not None:
                    # A session from before `clear` ending late: it ends the
                    # archived task; the current save is left untouched.
                    for clean in cleans:
                        clean["task_id"] = owner["task_id"]
                        self.ledger._append_locked(clean, active_task=self.live_task_ids())
                    self._rearchive_locked(owner)
                    return read_view(self.directory / "save.json")
                for clean in cleans:
                    clean["task_id"] = task["task_id"]
                save = self._compile_locked(task, git_state, extra=cleans)
                markdown = render(save, self.config, redactor=self.redactor)
                for clean in cleans:
                    self.ledger._append_locked(clean, active_task=self.live_task_ids())
                atomic_write(self.directory / "save.json", save, redactor=self.redactor, durable=False)
                atomic_write(self.directory / "SAVE.md", markdown, redactor=self.redactor, durable=False)
                return save
        except Exception as error:
            self._error("task.end_session", error)
            raise

    def _compile_locked(self, task, git_state, extra=()):
        from .compiler import compile_events  # lazy: append-only hooks never compile
        from .compiler import _ignored
        events = self.ledger._read_locked(task_id=task["task_id"], cursor=task["cursor"]) + list(extra)
        own = self._own_paths()
        config = self._compile_config(own)
        if git_state:
            # Same ignore list as files_touched (node_modules, __pycache__, ...).
            ignore = config["capture"]["ignore_paths"]
            git_state = dict(git_state, **{key: [p for p in git_state[key] if not _ignored(p, ignore)]
                                           for key in ("dirty", "untracked") if key in git_state})
        return compile_events(events, git_state, config, started=task["started"],
                              task_id=task["task_id"], redactor=self.redactor, root=str(self.project))

    def _write_save(self, save):
        # Each view is independently atomic. JSON is authoritative; rebuilding
        # repairs a Markdown view left stale by a crash between the two replaces.
        atomic_write(self.directory / "save.json", save, redactor=self.redactor, durable=False)
        from .render import render  # lazy: append-only hooks never render
        atomic_write(self.directory / "SAVE.md", render(save, self.config, redactor=self.redactor), redactor=self.redactor, durable=False)

    def rebuild(self, *, git_state: Any = _AUTO_GIT) -> dict[str, Any]:
        try:
            with self.ledger.lock():
                task = self._task()
                if git_state is _AUTO_GIT:
                    from .gitstate import read as read_git  # lazy: spawns subprocess
                    git_state = read_git(self.project, start_head=task.get("start_head"),
                                         timeout_ms=self.config["budgets_ms"]["git_call"],
                                         on_error=lambda error: self._error("git.read", error))
                save = self._compile_locked(task, git_state)
                self._write_save(save)
                return save
        except Exception as error:
            self._error("task.rebuild", error)
            raise
