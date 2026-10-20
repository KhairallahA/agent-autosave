"""`agent-autosave plan set` / `plan done`: record the task plan."""
from __future__ import annotations

import argparse

from ..contract import ExitCode, Outcome, fail, success
from . import common


def run(args: argparse.Namespace) -> Outcome:
    from ..events import EventError
    from ..state import TaskStore
    from datetime import datetime, timezone
    store = TaskStore(common.find_project(), lock_timeout=2)
    # Append-only: one event per call under the ledger lock; the compiler
    # folds plan_set / plan_done in ledger order, so concurrent calls never
    # overwrite each other (no read-modify-write).
    if args.plan_action == "set":
        steps = [" ".join(step.split()) for step in args.steps]
        if not all(steps):
            return fail("invalid_args", "Plan steps must not be empty.", "Run agent-autosave plan set \"first step\" \"second step\".")
        fields = {"kind": "plan_set", "steps": steps}
    elif args.plan_action == "done":
        fields = {"kind": "plan_done", "index": args.number}
    else:
        return fail("invalid_args", "Use plan set or plan done.", "Run agent-autosave plan --help.")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    try:
        store.append(dict({"v": 1, "ts": ts, "agent": "cli", "session": "cli", "turn": 0}, **fields))
        save = store.rebuild()
    except (EventError, ValueError):
        return fail("command_failed", "Could not record the plan.", "See .autosave/errors.log.")
    if fields["kind"] == "plan_done" and args.number > len(save.get("plan") or []):
        return fail("invalid_args", "No plan step {}; the plan has {}.".format(args.number, len(save.get("plan") or [])),
                    "Run agent-autosave plan set first.")
    plan = save.get("plan") or []
    lines = ["- [" + ("x" if i["state"] == "done" else " ") + "] " + i["step"] for i in plan]
    return success({"plan": plan}), ExitCode.SUCCESS, "\n".join(lines)
