"""The wizard: banner, agents, Codex trust, preferences, summary, next steps.

Nothing is written until the summary is confirmed. User-level Codex hooks
are written only with --user-level.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, TextIO

from .. import __version__
from ..contract import ExitCode, Outcome, success
from ..ui import Style, style as current_style
from . import render, screen as screen_module, widgets, write

ORDER = ("claude-code", "codex", "cursor", "gemini", "antigravity", "copilot", "opencode")


def run(args: argparse.Namespace, *, stream: TextIO | None = None, prompter: widgets.Prompter | None = None) -> Outcome:
    stream = sys.stdout if stream is None else stream
    style = current_style(stream)
    screen = screen_module.Screen(style, stream)
    ask = prompter or widgets.Prompter(screen)
    try:
        return _run(args, screen, ask, style)
    except widgets.Cancelled:
        ask.close()
        screen.end(style.paint("Cancelled.", "yellow") + " Nothing was written.")
        return success({"cancelled": True}), ExitCode.SUCCESS, ""
    finally:
        ask.close()


def _run(args: argparse.Namespace, screen: screen_module.Screen, ask: widgets.Prompter, style: Style) -> Outcome:
    from ..commands import codex_layers, common, init
    from ..installer import detect
    project = Path.cwd()
    receipt = common.receipt(project)
    installed = tuple(receipt.get("agents") or []) if receipt else ()
    screen.write(screen_module.banner(style, __version__))
    if receipt is not None and not args.reconfigure:
        screen.section("Already set up here", ["Agents: " + (", ".join(common.INSTALL_NAMES.get(a, a) for a in installed)
                                                           or "none"),
                                               "To add agents or change preferences: " +
                                               style.command("agent-autosave init --reconfigure")])
        screen.end()
        return success(dict(receipt, already_installed=True)), ExitCode.SUCCESS, ""
    screen.section("Setup" if receipt is None else "Reconfigure", [
        "agent-autosave records what your coding agents do in this folder and",
        "hands the task to the next agent when one stops. About 20 seconds.",
        style.dim("Nothing is written until you confirm the summary."), ""])

    # 1. Agents
    found = detect.all_agents()
    options, missing = [], []
    for agent in ORDER:
        info = found[agent]
        name = common.INSTALL_NAMES[agent]
        if agent in installed:
            options.append(widgets.Option(agent, name, "already set up", checked=True))
        elif info["installed"]:
            options.append(widgets.Option(agent, name, info.get("version") or "found", checked=True))
        else:
            missing.append(name)
    options.append(widgets.Option("generic", "Other agents (AGENTS.md)", "instructions only, no hooks",
                                  checked="generic" in installed))
    if missing:
        screen.body([style.dim("Not found on this machine: " + ", ".join(missing))])
    chosen = ask.multiselect("Which agents should autosave here?", options)

    # 2. Codex trust: hooks go in the project only when Codex trusts it.
    codex_user_dir, codex_hooks = None, True
    if "codex" in chosen and "codex" not in installed:
        user_file = detect.codex_home() / "hooks.json"
        existing = {"user": codex_layers.count_hooks(user_file),
                    "project": codex_layers.count_hooks(project / ".codex/hooks.json")}
        codex_hooks = not existing["user"] and not existing["project"]
        if args.user_level:
            codex_user_dir = detect.codex_home()
            screen.note("warn", "--user-level: Codex hooks go to {} and run in every Codex session.".format(user_file))
        elif not detect.codex_trusted(project):
            if not _codex_trust(screen, ask, style, project, found["codex"].get("path")):
                chosen = [a for a in chosen if a != "codex"]
                codex_hooks = True

    # 3. Preferences
    gitignore = True
    if receipt is None:
        gitignore = ask.select("Keep .autosave/ out of git?", [
            widgets.Option("ignore", "Yes, add it to .gitignore", "recommended: saves hold your prompts"),
            widgets.Option("manual", "No, I manage .gitignore myself", "nothing is written to .gitignore")]) == "ignore"
    else:
        gitignore = bool(receipt.get("gitignore", True))
    current = _config(project)
    record = ask.confirm("Record raw hook payloads too? (for bug reports; secrets redacted)",
                         default=bool(((current or {}).get("record") or {}).get("enabled")))

    mcp = ask.confirm("Give agents the agent-autosave MCP tools too? (a local server; no network)",
                      default=bool(receipt.get("mcp", True)) if receipt else True)

    statusline = _statusline(ask, project, chosen)

    vault, agentic = _memory(ask, project, current)

    # 4. Summary, then write.
    answers = render.Answers(tuple(a for a in init_order(chosen)), codex_user_dir, codex_hooks, gitignore, record,
                             vault, agentic, mcp, statusline)
    planned = render.changes(project, answers, installed=installed)
    rows = [[style.paint(c.action, "green" if c.action == "create" else "yellow"), style.bold(c.path), style.dim(c.why)]
            for c in planned]
    removed = [a for a in installed if a not in chosen]
    screen.section("Summary: {} file{} to create or change".format(len(planned), "" if len(planned) == 1 else "s"),
                   [line.lstrip() for line in style.table(rows, indent=0)] +
                   ([style.dim("Not removed (run agent-autosave remove <agent>): " +
                               ", ".join(common.INSTALL_NAMES[a] for a in removed))] if removed else []))
    if not ask.confirm("Write these changes?", default=True):
        screen.end("Nothing was written.")
        return success({"cancelled": True}), ExitCode.SUCCESS, ""

    data: dict[str, Any]
    if receipt is None:
        outcome, code, text = init.execute(args, agents=answers.agents, gitignore=gitignore, style=style,
                                           heading=False, footer=False, mcp=mcp, statusline=statusline)
        if not outcome["ok"]:
            return outcome, code, text
        data = outcome["data"]
        skipped = style.mark("skip")  # not-found agents were listed at the start
        screen.section("Done", [line[2:] if line.startswith("  ") else line for line in text.split("\n")
                                if line and not line.lstrip().startswith(skipped)])
    else:
        data = {"agents": list(installed), "added": []}
        from ..commands import add
        lines = []
        for agent in answers.agents:
            if agent in installed:
                continue
            outcome, code, text = add.run(argparse.Namespace(agent=agent, user_level=args.user_level, json=False,
                                                             quiet=False, mcp=mcp))
            if outcome["ok"]:
                data["added"].append(agent)
                lines.append(style.item("add", text.split("\n")[0], 0))
            else:
                lines.append(style.item("fail", outcome["error"]["message"], 0))
                lines.append(style.hint(outcome["error"]["hint"], 2))
        if "claude-code" in installed and statusline != "off":
            from ..installer.install import set_statusline
            changed = set_statusline(project, statusline)
            if changed["changed"]:
                lines.append(style.item("add", "Claude Code status line: " + ", ".join(changed["changed"]), 0))
        screen.section("Done", lines or [style.item("same", "No agents to add.", 0)])
    backup = write.write_json(project / ".autosave/config.json", render.config(_config(project), answers))
    if answers.vault:
        from .. import vault
        vault.start(project)
    screen.body([style.item("ok", "Recording {}{}".format("on" if record else "off",
                                                          " (old config kept as {})".format(backup.name) if backup else ""), 0)])
    data["wizard"] = {"agents": list(answers.agents), "gitignore": gitignore, "record": record,
                      "vault": answers.vault, "agentic_stack": answers.agentic, "mcp": mcp}

    # 5. Next steps
    launch = [a for a in answers.agents if a in ("claude-code", "codex", "gemini")]
    commands = {"claude-code": "claude", "codex": "codex", "gemini": "gemini"}
    screen.section("Next steps", [
        "1. Start {} here as usual. Every session now autosaves.".format(
            " or ".join(style.command(commands[a]) for a in launch) if launch else "your agent"),
        "2. " + style.command("agent-autosave status") + style.dim("          the save on one screen"),
        "3. " + style.command("agent-autosave resume <agent>") + style.dim("  hand the task to another agent"),
        "4. " + style.command("agent-autosave doctor") + style.dim("          if something looks wrong")])
    screen.end(style.paint("Ready.", "bold", "green"))
    return success(data), ExitCode.SUCCESS, ""


def _memory(ask: widgets.Prompter, project: Path, current: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Where the journal and MEMORY.md live, and whether to share with agentic-stack when it is here."""
    from .. import agentic
    vault = ((current or {}).get("vault") or {}).get("path")
    where = ask.select("Where should the journal and project memory live?", [
        widgets.Option("repo", "In .autosave/ in this repo", "MEMORY.md and the journal stay with the project"),
        widgets.Option("vault", "In an Obsidian vault", "a folder you pick; one subfolder per project")],
        default=1 if vault else 0)
    path = ask.text("Vault folder", default=vault or "~/Documents/Obsidian") if where == "vault" else None
    found = agentic.detected(project)
    use = None
    if found:
        setting = ((current or {}).get("integrations") or {}).get("agentic_stack", "auto")
        use = "auto" if ask.confirm("agentic-stack is here (.agent/). Keep its WORKSPACE.md current and recall "
                                    "its lessons?", default=setting != "off") else "off"
    return path or None, use


def _statusline(ask: widgets.Prompter, project: Path, chosen: list[str]) -> str:
    """"auto", "wrap" or "off" for Claude Code's status line."""
    if "claude-code" not in chosen:
        return "off"
    from ..installer.statusline import existing
    found = existing(project)
    if found is None:
        return "auto" if ask.confirm("Show the save in Claude Code's status line?", default=True) else "off"
    if found["ours"] or not found["command"]:
        return "off"
    return ask.select("Claude Code already has a status line ({}).".format(found["path"]), [
        widgets.Option("wrap", "Wrap it", "yours first, then the save; remove puts yours back"),
        widgets.Option("off", "Leave it alone", "no agent-autosave status line")])


def init_order(chosen: list[str]) -> list[str]:
    return [a for a in ORDER + ("generic",) if a in chosen]


def _config(project: Path) -> dict[str, Any] | None:
    from ..storage import read_json
    try:
        value = read_json(project / ".autosave/config.json")
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _codex_trust(screen: screen_module.Screen, ask: widgets.Prompter, style: Style, project: Path,
                 codex: str | None) -> bool:
    """Offer to open Codex to trust the folder; True if it is trusted afterwards."""
    from ..commands import codex_layers
    from ..installer import detect
    help_ = codex_layers.trust_help(project)
    screen.section("Codex does not trust this folder yet", [
        "Codex runs project hooks only in folders you trust. agent-autosave never",
        "falls back to user-level hooks (they would run in every Codex session)."])
    options = [widgets.Option("skip", "Skip Codex for now", "show me the exact steps")]
    if codex:
        options.insert(0, widgets.Option("open", "Open Codex here to trust it",
                                         "accept the trust prompt, then quit Codex"))
    choice = ask.select("How do you want to handle Codex?", options)
    if choice == "open":
        ask.close()  # give Codex the terminal
        screen.body([style.dim("Starting codex… accept “trust this folder”, then quit with /quit or Ctrl-C."), ""])
        try:
            subprocess.call([codex or shutil.which("codex") or "codex"], cwd=str(project))
        except OSError:
            pass
        if detect.codex_trusted(project):
            screen.note("ok", "Codex trusts this folder now; its hooks go in .codex/hooks.json.")
            return True
        screen.note("warn", "Codex still does not trust this folder, so Codex is skipped.")
    screen.body([line for line in help_["text"].split("\n")])
    return False
