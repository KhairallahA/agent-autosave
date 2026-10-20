"""`agent-autosave init`: set up .autosave/ and wire the detected agents.

In a terminal, `init` runs the setup wizard (`agent_autosave.wizard`). With
--yes or --agents, or when stdin or stdout is not a terminal, it asks
nothing and uses the defaults.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any, Sequence

from ..contract import ExitCode, Outcome, fail, success
from . import codex_layers, common

if TYPE_CHECKING:
    from ..ui import Style

HOOK_COUNTS = {"claude-code": 8, "codex": 6, "cursor": 7, "gemini": 6, "antigravity": 3, "copilot": 7}
# Agents whose hooks live in one project file, and the file with their block.
HOOK_FILES = {"gemini": (".gemini/settings.json", "GEMINI.md"), "antigravity": (".agents/hooks.json", "ANTIGRAVITY.md"),
              "copilot": (".github/hooks/agent-autosave.json", "AGENTS.md")}


def interactive(args: argparse.Namespace) -> bool:
    """Ask questions only when a person is at a terminal and no flag answers them."""
    if getattr(args, "yes", False) or args.agents is not None or getattr(args, "json", False):
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def run(args: argparse.Namespace) -> Outcome:
    """Set up .autosave/ and wire the agents: the wizard in a terminal, else the defaults."""
    if interactive(args):
        from ..wizard import flow
        return flow.run(args)
    if getattr(args, "reconfigure", False) and common.receipt(Path.cwd()) is not None:
        return fail("invalid_args", "--reconfigure asks questions, so it needs a terminal.",
                    "Run agent-autosave init --reconfigure in a terminal, or edit .autosave/config.json.")
    return execute(args)


def requested_agents(args: argparse.Namespace) -> list[str] | None:
    return [a.strip() for a in args.agents.split(",")] if args.agents else None


def execute(args: argparse.Namespace, *, agents: Sequence[str] | None = None, gitignore: bool = True,
            style: Style | None = None, heading: bool = True, footer: bool = True, mcp: bool | None = None,
            statusline: str = "auto") -> Outcome:
    """Install for `agents` (default: --agents, else every detected agent).
    The MCP server is registered unless --no-mcp (or the wizard said no)."""
    if mcp is None:
        mcp = not getattr(args, "no_mcp", False)
    from ..installer import detect
    from ..installer.install import install
    from ..ui import style as current_style
    style = style or current_style()
    requested = list(agents) if agents is not None else requested_agents(args)
    found = detect.all_agents()
    for agent in requested or []:
        if agent != "generic" and not found[agent]["installed"]:
            return fail("agent_not_installed", "{} was not found on this machine.".format(common.INSTALL_NAMES[agent]),
                        "Install {}, then run agent-autosave init again.".format(common.INSTALL_NAMES[agent]))
    chosen = tuple(a for a in common.AGENTS if (a in requested if requested is not None else
                                                (found[a]["installed"] and a != "generic")))
    if args.user_level and "codex" not in chosen:
        return fail("not_supported", "--user-level applies to Codex only, and Codex is not selected.",
                    "Run agent-autosave init --agents codex --user-level.")
    codex_dir = detect.codex_home()
    user_file, project_file = codex_dir / "hooks.json", Path.cwd() / ".codex/hooks.json"
    codex_wanted = "codex" in chosen
    trusted = codex_wanted and detect.codex_trusted(Path.cwd())
    codex_skipped = None
    if codex_wanted and not trusted and not args.user_level:
        # Never fall back to user-level hooks: they run in every Codex session.
        codex_skipped = codex_layers.trust_help(Path.cwd())
        chosen = tuple(a for a in chosen if a != "codex")
        if not chosen:
            return fail("codex_untrusted", codex_skipped["message"], codex_skipped["hint"], text=codex_skipped["text"])
    user_level = "codex" in chosen and bool(args.user_level)
    # Codex runs hooks from every layer: write ours to exactly one, and none if
    # either layer already has agent-autosave hooks.
    existing = {"user": codex_layers.count_hooks(user_file), "project": codex_layers.count_hooks(project_file)}
    write_codex = "codex" in chosen and not existing["user"] and not existing["project"]
    found_layer = None
    if "codex" in chosen and not write_codex:
        found_layer = ({"layer": "project", "path": ".codex/hooks.json", "hooks": existing["project"]}
                       if existing["project"] else {"layer": "user", "path": str(user_file), "hooks": existing["user"]})
    try:
        outcome = install(Path.cwd(), chosen, codex_user_dir=codex_dir if user_level else None,
                          codex_hooks=write_codex, codex_found=found_layer, gitignore=gitignore, mcp=mcp,
                          statusline=statusline)
    except (ValueError, OSError) as error:
        return fail("adapter_problem", "Could not merge configuration: " + type(error).__name__ + ".",
                    "Check .claude/settings.json and .codex/hooks.json are valid JSON and see .autosave/errors.log.")
    from .. import vault
    vault.start(Path.cwd())  # a configured vault is complete from the start
    already = bool(outcome.get("already_installed"))
    items: list[tuple[str, str, str]] = []  # (level, what, detail)
    notes: list[tuple[str, str, str]] = []  # (level, message, hint) printed after the list
    loud: list[tuple[str, str]] = []  # (message, hint) printed before the list
    warnings = []
    if codex_skipped:
        warnings.append({"code": "codex_untrusted_skipped", "message": codex_skipped["message"],
                         "hint": codex_skipped["hint"], "config_toml": codex_skipped["config_toml"],
                         "entry": codex_skipped["entry"]})
    codex_hooks = str(user_file) if user_level else ".codex/hooks.json"
    if "codex" in chosen and not write_codex:
        codex_hooks = str(user_file) if existing["user"] else ".codex/hooks.json"

    def version(agent):
        return " " + found[agent]["version"] if found[agent].get("version") else ""

    if not already:
        for agent in common.AGENTS:
            name = common.INSTALL_NAMES[agent]
            if agent == "generic":
                if agent in chosen:
                    items.append(("add", "AGENTS.md", "agent-autosave block added (instructions only)"))
                continue
            if agent not in chosen:
                if agent == "codex" and codex_skipped:
                    items.append(("warn", "Codex" + version("codex"),
                                  "skipped: this folder is not trusted in Codex (no hooks written anywhere)"))
                else:
                    items.append(("skip", name, "not installed, skipped"))
                continue
            if agent == "claude-code":
                items.append(("add", name + version(agent), "{} hooks in .claude/settings.json".format(HOOK_COUNTS[agent])))
                items.append(("add", "CLAUDE.md", "agent-autosave block added"))
                items.extend(statusline_items(outcome.get("statusline") or {}))
            elif agent == "codex" and not write_codex:
                items.append(("same", name + version(agent), "hooks not added: {} already has {} agent-autosave "
                                                              "hook(s)".format(found_layer["path"], found_layer["hooks"])))
                items.append(("add", "AGENTS.md", "agent-autosave block added"))
            elif agent == "codex":
                items.append(("add", name + version(agent), "{} hooks in {}".format(HOOK_COUNTS[agent], codex_hooks)))
                items.append(("add", "AGENTS.md", "agent-autosave block added"))
                notes.append(("warn", "In Codex, open /hooks and trust the 6 agent-autosave hooks.",
                              "Codex skips new hooks until you do."))
            elif agent == "cursor":
                items.append(("add", name, "{} hooks in .cursor/hooks.json".format(HOOK_COUNTS[agent])))
                items.append(("add", ".cursor/rules/agent-autosave.mdc", "always-apply rule added"))
                notes.append(("warn", "Cursor runs project hooks only in a trusted workspace.",
                              "Trust this folder in Cursor."))
            elif agent in HOOK_FILES:
                hooks, block = HOOK_FILES[agent]
                items.append(("add", name + version(agent), "{} hooks in {}".format(HOOK_COUNTS[agent], hooks)))
                items.append(("add", block, "agent-autosave block added"))
            else:
                items.append(("add", name + version(agent), "plugin in .opencode/plugins/agent-autosave.js"))
                items.append(("add", "AGENTS.md", "agent-autosave block added"))
            items.extend(mcp_items(agent, outcome, mcp))
        items.append(("add", ".autosave/", "created" + (" and added to .gitignore" if gitignore else
                                                       "; .gitignore left alone, as you chose")))
    if codex_skipped and not already:
        notes.append(("warn", "Codex was skipped: trust this folder in Codex first.", codex_skipped["text"]))
    if "codex" in chosen and not write_codex and not already:
        # Loud, not buried: Codex will run the hooks from the other layer,
        # which may point at another checkout or venv of agent-autosave.
        layer = found_layer["layer"]
        message = ("Codex hooks were NOT written to this repo: {} ({}-level) already has {} agent-autosave hook(s), "
                   "and Codex runs every layer, so a second set would record every event twice. Codex in this repo "
                   "will run the hooks from that file.").format(found_layer["path"], layer, found_layer["hooks"])
        hint = ("Keep it, or for project-level hooks: {0} -m agent_autosave.installer.userhooks --codex --dry-run, then "
               "{0} -m agent_autosave.installer.userhooks --codex, then agent-autosave remove codex and agent-autosave add codex.".format(
                   sys.executable) if layer == "user" else "Keep it; this repo's .codex/hooks.json is in use.")
        warnings.append({"code": "codex_hooks_in_other_layer", "layer": layer, "path": str(found_layer["path"]),
                         "hooks": found_layer["hooks"], "message": message, "hint": hint})
        loud.append((message, hint))
    data = dict(outcome, detected=found, warnings=warnings)
    if "cursor" in outcome["agents"] and "claude-code" in outcome["agents"]:
        # Cursor also runs .claude/settings.json hooks (Third-Party Imports).
        message = ("Cursor also runs the Claude Code hooks in .claude/settings.json; agent-autosave drops those "
                   "Cursor events (they carry cursor_version) and records Cursor from .cursor/hooks.json only.")
        hint = "Nothing to do. To stop Cursor running them, turn off Third-Party Imports in Cursor's hooks settings."
        warnings.append({"code": "cursor_runs_claude_hooks", "message": message, "hint": hint})
        notes.append(("info", message, hint))
    if "codex" in chosen:
        data["codex"] = {"trusted": bool(trusted), "hooks_file": codex_hooks, "user_level": codex_hooks == str(user_file),
                         "hooks_written": bool(write_codex and not already),
                         "review_hooks": "In Codex, open /hooks and trust the agent-autosave hooks."}
    # Read-only check of the user-level settings (never modified here): hooks
    # there as well as in the project make Claude Code record every event twice.
    from ..installer.userhooks import detect as user_detect, settings_path
    user_settings = settings_path()
    if "claude-code" in outcome["agents"] and user_settings.parent.parent.resolve() != Path.cwd().resolve():
        leftovers = user_detect()
        if leftovers:
            hint = "{0} -m agent_autosave.installer.userhooks --dry-run, then {0} -m agent_autosave.installer.userhooks".format(
                sys.executable)
            warnings.append({"code": "duplicate_user_hooks", "path": str(user_settings),
                             "hooks": len(leftovers), "hint": hint})
            notes.append(("warn", "{} also has {} agent-autosave hook(s); Claude Code would run both and record every "
                                  "event twice.".format(user_settings, len(leftovers)),
                          "Fix (backs the file up, removes only agent-autosave hooks): " + hint))
    if codex_wanted:
        # Checked on every run (also "already initialized"): both layers with
        # our hooks make Codex record every event twice.
        both = {"user": codex_layers.count_hooks(user_file), "project": codex_layers.count_hooks(project_file)}
        if both["user"] and both["project"]:
            module = "{} -m agent_autosave.installer.userhooks".format(sys.executable)
            # Keep the layer that runs here: the project file when trusted.
            target = "--codex" if trusted else "--file " + str(project_file)
            remove_from = user_file if trusted else project_file
            hint = "{0} {1} --dry-run, then {0} {1}".format(module, target)
            warnings.append({"code": "duplicate_codex_hooks", "path": str(remove_from),
                             "hooks": both["user"] + both["project"], "hint": hint})
            notes.append(("warn", "agent-autosave Codex hooks are in both {} and {}; Codex runs both and records every "
                                  "event twice.".format(user_file, project_file),
                          "Fix (backs the file up, removes only agent-autosave hooks from {}): {}".format(remove_from, hint)))
    text = render(items, notes, loud, already=already, style=style, heading=heading, footer=footer)
    return success(data), ExitCode.SUCCESS, text


MCP_NOTES = {"claude-code": "MCP server registered (Claude Code asks you to approve it once)",
             "codex": "MCP server registered (read in trusted projects)",
             "cursor": "MCP server registered (enable it in Cursor's MCP settings)",
             "gemini": "MCP server registered as \"autosave\""}


def statusline_items(line: dict[str, Any]) -> list[tuple[str, str, str]]:
    """The init line for Claude Code's status line."""
    mode = line.get("mode")
    if mode == "added":
        return [("add", "Status line", "agent-autosave statusline in .claude/settings.json")]
    if mode == "wrapped":
        return [("add", "Status line", "yours from {} now ends with the save".format(line["path"]))]
    if mode == "kept":
        return [("same", "Status line", "you have one ({}); left alone. To add ours after yours: "
                                         "agent-autosave init --reconfigure".format(line["path"]))]
    return []


def mcp_items(agent: str, outcome: dict[str, Any], mcp: bool) -> list[tuple[str, str, str]]:
    """The init line for an agent's MCP registration, if it has one."""
    from ..installer.install import manifest
    if not mcp or agent == "generic":
        return []
    for item in manifest(agent)["files"]:
        if item.get("feature") == "mcp":
            if item["dst"] in (outcome.get("changed") or []):
                return [("add", item["dst"], MCP_NOTES[agent])]
            return [("same", item["dst"], "MCP server already registered there; left as it is")]
    return []


def render(items: Sequence[tuple[str, str, str]], notes: Sequence[tuple[str, str, str]],
           loud: Sequence[tuple[str, str]] = (), *, already: bool = False, style: Style | None = None,
           heading: bool = True, footer: bool = True) -> str:
    """What init changed, then what needs doing, then the next step."""
    from ..ui import Style
    style = style or Style()
    lines = [style.heading("agent-autosave init")] if heading else []
    for message, hint in loud:
        # Not wrapped: it holds paths and a command to copy, and the terminal wraps anyway.
        lines += ["", "  " + style.paint("!!! WARNING: ", "bold", "red") + style.paint(message, "red"),
                  "      " + hint]
    lines.append("")
    if already:
        lines.append(style.item("same", "Already set up here; nothing changed."))
        lines.append(style.hint("To change agents or preferences: agent-autosave init --reconfigure", 4))
    width = min(20, max((len(what) for _, what, _ in items), default=0))
    for level, what, detail in items:
        lines.append(style.item(level, style.bold(what.ljust(width)) + "  " +
                                (style.dim(detail) if level in ("skip", "same") else detail)))
    for level, message, hint in notes:
        lines.append("")
        lines.append(style.item(level, message))
        lines.extend("    " + line if line else "" for line in hint.splitlines())
    if footer and not already:
        lines += ["", "  " + style.paint("Done.", "bold", "green") + " Your agents now autosave. Next:",
                  "    " + style.command("agent-autosave status") + style.dim("    the save on one screen"),
                  "    " + style.command("agent-autosave doctor") + style.dim("    check every hook is in place")]
    return "\n".join(lines)
