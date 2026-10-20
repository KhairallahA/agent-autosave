"""`agent-autosave save`: rebuild the save now."""
from __future__ import annotations

import argparse

from ..contract import ExitCode, Outcome, success
from . import common


def run(args: argparse.Namespace) -> Outcome:
    project = common.find_project()
    store = common.store(project)
    common.wait_for_settle(store.directory)
    if args.note:
        text = " ".join(args.note.split())
        if text:
            common.cli_note(store, text, "decision")
    save = store.rebuild()
    return success({"status": save["status"], "task_id": save["task_id"], "updated": save["updated"]}), \
        ExitCode.SUCCESS, "Saved: {} ({}).".format(save["status"], save["task_id"])
