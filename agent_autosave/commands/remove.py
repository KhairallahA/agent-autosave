"""`agent-autosave remove`: undo an agent's changes, byte for byte."""
from __future__ import annotations

import argparse
import copy
import sys
from typing import TYPE_CHECKING, Any

from ..contract import ExitCode, Outcome, fail, success
from . import common

if TYPE_CHECKING:
    from ..ui import Style


def run(args: argparse.Namespace) -> Outcome:
    from ..installer.remove import RemoveConflict, remove
    if bool(args.agent) == bool(args.all):
        return fail("invalid_args", "Name one agent, or pass --all.",
                    "Run agent-autosave remove <agent> or agent-autosave remove --all.")
    project = common.find_project()
    if common.receipt(project) is None:
        return success({"removed": [], "agents": []}), ExitCode.SUCCESS, "Nothing is installed here, so there is nothing to restore."
    try:
        outcome = remove(project, None if args.all else args.agent)
    except RemoveConflict:
        return fail("edited_config", "An agent-autosave addition was edited, so removing it is ambiguous.",
                    "Delete the agent-autosave lines by hand, then run agent-autosave doctor.")
    except (ValueError, OSError) as error:
        return fail("adapter_problem", "Could not remove: {}.".format(type(error).__name__),
                    "Run agent-autosave doctor.")
    if outcome.get("not_installed"):
        return fail("not_installed", "{} is not installed here.".format(common.INSTALL_NAMES[args.agent]),
                    "Run agent-autosave doctor to see what is installed.")
    if not outcome.get("agents"):
        outcome.update(_memory_leftovers(project))
    from ..ui import style
    return success(outcome), ExitCode.SUCCESS, render(outcome, style())


def _memory_leftovers(project: Any) -> dict[str, Any]:
    """With the last agent gone: take our block out of agentic-stack's
    WORKSPACE.md, and name the vault folder, which remove never deletes
    (the journal and MEMORY.md are the user's notes)."""
    from .. import agentic, config as config_module, places, vault
    try:
        config = config_module.load(project)
    except config_module.ConfigError:
        config = copy.deepcopy(config_module.DEFAULTS)
    folder = vault.project_dir(project, config) if vault.configured(config) else None
    return {"workspace_block_removed": agentic.remove_block(project),
            "vault_kept": places.shown(project, folder) if folder is not None and folder.is_dir() else None}



def render(outcome: dict[str, Any], style: Style | None = None) -> str:
    """What remove restored and what it left in place."""
    from ..ui import Style
    style = style or Style()
    lines = [style.heading("agent-autosave remove"), ""]
    for path in outcome["removed"]:
        lines.append(style.item("ok", "restored " + path))
    if not outcome["removed"]:
        lines.append(style.item("same", "nothing to restore"))
    for path in outcome.get("kept_shared") or []:
        lines.append(style.item("info", "kept {}: other repos still use these hooks".format(path)))
    for path in outcome.get("left_user_hooks") or []:
        lines.append(style.item("warn", "left {}: this repo did not write those hooks".format(path)))
        lines.append(style.hint("to strip them: {} -m agent_autosave.installer.userhooks --codex".format(sys.executable)))
    for path in outcome.get("by_content") or []:
        lines.append(style.item("warn", "{}: reformatted or edited, so agent-autosave hooks were found by content "
                                        "and removed".format(path)))
    if outcome.get("missing_spans"):
        lines.append(style.item("info", "{} addition(s) were already gone".format(outcome["missing_spans"])))
    if outcome.get("workspace_block_removed"):
        lines.append(style.item("ok", "removed the agent-autosave block from .agent/memory/working/WORKSPACE.md"))
    lines.append("")
    if outcome.get("agents"):
        lines.append("  Still installed: " + ", ".join(common.INSTALL_NAMES.get(a, a) for a in outcome["agents"]))
    else:
        lines.append("  All agents removed. Your saves in .autosave/ are kept; delete the folder if you want them gone.")
        if outcome.get("vault_kept"):
            lines.append("  Your journal and MEMORY.md stay in the vault at {}.".format(outcome["vault_kept"]))
    return "\n".join(lines)
