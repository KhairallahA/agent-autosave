"""`agent-autosave resume`: launch an agent with the save as its first prompt."""
from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Any

from ..contract import ExitCode, Outcome, fail, success
from . import common


LAUNCHERS = {"claude-code": "claude", "codex": "codex", "gemini": "gemini", "antigravity": "agy",
             "copilot": "copilot", "opencode": "opencode"}
# Flags before the first prompt: run it, then stay interactive. Copilot CLI:
# `-i PROMPT` (--interactive=PROMPT) (docs.github.com/en/copilot/reference/cli-command-reference);
# OpenCode: `--prompt` (opencode.ai/docs/cli/).
LAUNCH_FLAGS = {"gemini": ["--prompt-interactive"], "antigravity": ["--prompt-interactive"],
                "copilot": ["-i"], "opencode": ["--prompt"]}


def next_agent(project: Path, save: dict[str, Any]) -> str | None:
    """The agent after the one that last worked on the task, per switch.order."""
    from .go import order_of
    chain = order_of(project)
    if not chain:
        return None
    last = ((save.get("interrupted") or {}).get("agent")
            or (save.get("last_session") or {}).get("agent")
            or ((save.get("agents") or [{}])[-1] or {}).get("agent"))
    if last in chain:
        return chain[(chain.index(last) + 1) % len(chain)] if len(chain) > 1 else None
    return chain[0]


def launch(project: Path, agent: str, *, dry_run: bool = False,
           save: dict[str, Any] | None = None, named: bool = False) -> Outcome:
    """Start `agent` with the save as its first prompt (shared by resume and
    go). A `named` save (resume --task) is resumed finished or not."""
    from ..config import load
    from ..loader import resumable
    if save is None and not named:
        save = common.load_fresh(project)
    if save is None or not (named or resumable(save)):
        return fail("nothing_to_resume", "There is no unfinished save to resume.",
                    "Run agent-autosave status to see the current save.")
    config = load(project)
    prompt = common.context_block(project, save, config, common.staleness(project, save, config))
    if agent not in LAUNCHERS:
        return fail("not_supported", "{} cannot be started with a first prompt.".format(common.NAMES[agent]),
                    "Paste the output of `agent-autosave show --context` as your first message, or ask "
                    "the agent to read .autosave/SAVE.md.")
    executable = shutil.which(LAUNCHERS[agent])
    command = [executable or LAUNCHERS[agent]] + LAUNCH_FLAGS.get(agent, []) + [prompt]
    printable = " ".join(shlex.quote(c) for c in command)
    data = {"agent": agent, "command": command, "dry_run": dry_run, "status": save["status"]}
    if dry_run:
        return success(data), ExitCode.SUCCESS, printable
    if executable is None:
        return fail("agent_not_installed", "{} was not found on this machine.".format(common.NAMES[agent]),
                    "Install it, or run agent-autosave resume {} --dry-run.".format(agent))
    code = subprocess.call(command, cwd=str(project))
    data["exit"] = code
    return success(data), ExitCode.SUCCESS, "{} exited ({}).".format(common.NAMES[agent], code)


def start(project: Path, agent: str, *, dry_run: bool = False) -> Outcome:
    """Start `agent` with no first prompt: `go` at the start of a task, when
    there is nothing to hand over yet."""
    executable = shutil.which(LAUNCHERS[agent])
    command = [executable or LAUNCHERS[agent]]
    data = {"agent": agent, "command": command, "dry_run": dry_run, "status": None}
    if dry_run:
        return success(data), ExitCode.SUCCESS, shlex.quote(command[0])
    if executable is None:
        return fail("agent_not_installed", "{} was not found on this machine.".format(common.NAMES[agent]),
                    "Install it, or run agent-autosave go --dry-run.")
    data["exit"] = subprocess.call(command, cwd=str(project))
    return success(data), ExitCode.SUCCESS, "{} exited ({}).".format(common.NAMES[agent], data["exit"])


def run(args: argparse.Namespace) -> Outcome:
    project = common.find_project()
    if getattr(args, "task", None):
        save = common.find_save(project, args.task)
        if save is None:
            return common.unknown_task()
    else:
        save = common.load_fresh(project)
    agent = next_agent(project, save or {}) if args.next else args.agent
    if agent is None:
        return fail("nothing_to_resume", "No next agent in switch.order.",
                    "Pass an agent name, or set switch.order in .autosave/config.json.")
    return launch(project, agent, dry_run=bool(args.dry_run), save=save, named=bool(args.task))
