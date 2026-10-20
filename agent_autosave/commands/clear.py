"""`agent-autosave clear`: archive the save and start a new task."""
from __future__ import annotations

import argparse

from ..contract import ExitCode, Outcome, success
from . import common


def run(args: argparse.Namespace) -> Outcome:
    project = common.find_project()
    from ..gitstate import read as read_git
    from ..storage import read_view
    store = common.store(project)
    previous = read_view(store.directory / "save.json") or {}
    git_state = read_git(project, timeout_ms=store.config["budgets_ms"]["git_call"])
    save = store.start(common.now(), reason="clear", git_state=git_state)
    archived = previous.get("task_id")
    common.sync_memory(project)  # the archived task's journal page and memory candidates
    return success({"archived": archived, "task_id": save["task_id"]}), ExitCode.SUCCESS, \
        "Archived {} to .autosave/history/; new task {}.".format(archived, save["task_id"]) if archived \
        else "New task {}.".format(save["task_id"])
