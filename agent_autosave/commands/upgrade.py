"""`agent-autosave upgrade`: refresh installed adapters to this version."""
from __future__ import annotations

import argparse

from ..contract import ExitCode, Outcome, fail, success
from . import codex_layers, common


def run(args: argparse.Namespace) -> Outcome:
    from ..installer import detect, upgrade
    project = common.find_project()
    command = detect.hook_command()
    plan = upgrade.plan(project, command)
    if plan is None:
        return fail("not_initialized", "agent-autosave is not set up in this folder (no .autosave/install.json).",
                    "Run agent-autosave init")
    if not args.yes:
        lines = ["agent-autosave upgrade (dry run)"] + ["  - " + r for r in plan["reasons"]]
        if not plan["reasons"]:
            lines.append("  up to date")
        else:
            lines.append("Run agent-autosave upgrade --yes to apply.")
        return success(dict(plan, dry_run=True)), ExitCode.SUCCESS, "\n".join(lines)
    if plan["codex_layer"] == "user" and not args.user_level:
        return fail("user_level_needed", "This repo's Codex hooks live in the user-level hooks.json; upgrading "
                    "rewrites that file.", "Run agent-autosave upgrade --yes --user-level.")
    try:
        outcome = upgrade.apply(project, command, codex_existing=lambda p: codex_layers.choose(p)[2])
    except (ValueError, OSError) as error:
        return fail("adapter_problem", "Could not upgrade: {}.".format(type(error).__name__),
                    "Run agent-autosave doctor.")
    common.sync_memory(project)  # journal pages for tasks finished before this version
    return success(dict(outcome, dry_run=False)), ExitCode.SUCCESS, "Upgraded: " + ", ".join(outcome["agents"])
