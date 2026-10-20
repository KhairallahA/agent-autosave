"""`agent-autosave note`: record a decision, to-do or warning."""
from __future__ import annotations

import argparse

from ..contract import ExitCode, Outcome, fail, success
from . import common


def run(args: argparse.Namespace) -> Outcome:
    project = common.find_project()
    text = text_of(args)
    if not text:
        return fail("invalid_args", "The note must not be empty.", "Run agent-autosave note \"your note\".")
    kind = args.type or "decision"
    from ..memory import NOTE_TYPES
    if args.keep and kind not in NOTE_TYPES:
        return fail("invalid_args", "Only decision, tried and warning notes can be kept as memory.",
                    "Run agent-autosave note --keep --type decision \"...\".")
    from ..storage import read_view
    store = common.store(project)
    previous = read_view(store.directory / "save.json") or {}
    common.cli_note(store, text, kind)
    save = store.rebuild(git_state=previous.get("git"))  # keep HEAD so staleness still shows
    data = {"type": kind, "decisions": save["decisions"], "notes": save.get("notes", [])}
    text = "Noted ({}).".format(kind)
    if args.keep:
        from .memory import propose
        outcome, _, proposed = propose(project, store.config, NOTE_TYPES[kind], text_of(args))
        data["candidate"] = outcome["data"]["candidate"]
        text += " " + proposed
    return success(data), ExitCode.SUCCESS, text


def text_of(args: argparse.Namespace) -> str:
    return " ".join(str(args.text).split())
