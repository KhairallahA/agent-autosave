"""`agent-autosave add`: wire one more agent, or repair an installed one."""
from __future__ import annotations

import argparse

from ..contract import ExitCode, Outcome, fail, success
from . import codex_layers, common


def run(args: argparse.Namespace) -> Outcome:
    from ..installer import detect
    from ..installer.install import add
    project = common.find_project()
    receipt = common.receipt(project)
    if receipt is None:
        return fail("not_initialized", "agent-autosave is not set up in this folder (no .autosave/install.json).",
                    "Run agent-autosave init")
    agent = args.agent
    found = detect.all_agents()
    if agent != "generic" and not found[agent]["installed"]:
        return fail("agent_not_installed", "{} was not found on this machine.".format(common.INSTALL_NAMES[agent]),
                    "Install {}, then run agent-autosave add {}.".format(common.INSTALL_NAMES[agent], agent))
    kwargs = {}
    if agent == "codex":
        layer = (receipt.get("hooks") or {}).get("codex") or {}
        user_level = getattr(args, "user_level", False)
        if layer.get("added") and layer.get("layer") == "user" and not user_level:
            return fail("user_level_needed", "This repo's Codex hooks live in the user-level {}; repairing them "
                        "writes that file.".format(layer.get("path")),
                        "Run agent-autosave add codex --user-level.")
        if not user_level and not detect.codex_trusted(project):
            help_ = codex_layers.trust_help(project)
            return fail("codex_untrusted", help_["message"], help_["hint"], text=help_["text"])
        user_dir, write, found_layer = codex_layers.choose(project, user_level)
        if layer.get("added"):
            # Repair in the recorded layer only (never a second layer).
            user_dir = detect.codex_home() if layer.get("layer") == "user" else None
            write, found_layer = True, None
        kwargs = {"codex_user_dir": user_dir, "codex_hooks": write, "codex_found": found_layer}
    if getattr(args, "mcp", False):
        kwargs["mcp"] = True
    try:
        outcome = add(project, agent, **kwargs)
    except (ValueError, OSError) as error:
        return fail("adapter_problem", "Could not merge configuration: {}.".format(type(error).__name__),
                    "Run agent-autosave doctor.")
    verb = "Repaired" if outcome["repaired"] else "Added"
    changed = outcome["changed"] or []
    text = "{} {}: {}".format(verb, common.INSTALL_NAMES[agent], ", ".join(changed) if changed else "nothing missing")
    if agent == "cursor" and not outcome["repaired"]:
        text += "\nTrust this folder in Cursor (project hooks run only in trusted workspaces)."
    if agent == "codex" and changed and not outcome["repaired"]:
        text += "\nIn Codex, open /hooks and trust the agent-autosave hooks."
    return success(outcome), ExitCode.SUCCESS, text
