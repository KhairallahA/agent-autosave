"""`agent-autosave done`: the task is finished, with or without a plan.

Plans are optional, so "every plan step is done" cannot be the only way a
task ends. This records a `task_done` event; the next substantive prompt
then starts a new task instead of steering the finished one.
"""
from __future__ import annotations

import argparse

from ..contract import ExitCode, Outcome, success
from . import common


def run(args: argparse.Namespace) -> Outcome:
    project = common.find_project()
    from ..loader import resumable
    from ..storage import read_view
    store = common.store(project)
    previous = read_view(store.directory / "save.json") or {}
    current = store.rebuild(git_state=previous.get("git"))
    if current.get("status") != "done" and not resumable(current):
        return common.nothing_to_finish()  # an empty task: nothing was asked yet
    note = " ".join(str(args.note or "").split())
    if current.get("status") != "done" or note:
        event = {"v": 1, "ts": common.now(), "agent": "cli", "session": "cli", "turn": 0, "kind": "task_done"}
        if note:
            event["note"] = note
        store.append(event)
        current = store.rebuild(git_state=previous.get("git"))
    common.sync_memory(project)  # the finished task's journal page
    return success({"status": current["status"], "task_id": current["task_id"], "goal": current["goal"]}), \
        ExitCode.SUCCESS, "Task finished. The next prompt starts a new task."
