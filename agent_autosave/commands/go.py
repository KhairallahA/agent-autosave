"""`agent-autosave go [agent ...]`: hand the terminal to one agent, then the
next when a usage limit or overload stops it.

No stdin interception while the agent runs: the person talks to the agent
directly. With no unfinished task yet, the first agent starts with no
prompt. After it exits we read the save. A finished task ends the chain.
An interruption (rate_limit / overloaded) or a last usage reading at or
above 98% prints the switch prompt and continues with `resume`. Anything
else (they quit) stops with a "how to continue" line.

`switch.order` (or the launchable agents wired here, in install order) is
used when no agents are named. `--auto` continues after a 5 s countdown
that any key cancels."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import select
import sys
from typing import Any, Callable

from ..contract import ExitCode, Outcome, fail, success
from ..loader import resumable
from . import common
from .resume import LAUNCHERS, launch, start

LIMIT = 98  # percent: at or above this, offer the next agent
COUNTDOWN = 5


def order_of(project: Path, named: list[str] | None = None) -> list[str]:
    """The agents to try, filtered to ones resume can launch."""
    if named:
        return [a for a in named if a in LAUNCHERS]
    from ..config import load
    from ..storage import read_json
    configured = load(project)["switch"]["order"]
    if configured:
        return [a for a in configured if a in LAUNCHERS]
    receipt = read_json(Path(project) / ".autosave/install.json") or {}
    return [a for a in receipt.get("agents") or [] if a in LAUNCHERS]


def should_switch(save: dict[str, Any] | None) -> str | None:
    """Why to hand off ("rate_limit", "overloaded" or "usage"), or None.
    The usage check reads the agent that ran last: the status line records
    Claude Code's reading even when no interrupted event arrives."""
    if not save or save.get("status") == "done":
        return None
    info = save.get("interrupted") or {}
    if info.get("reason") in ("rate_limit", "overloaded"):
        return info["reason"]
    last = info.get("agent") or (save.get("last_session") or {}).get("agent") or ""
    item = (save.get("usage") or {}).get(last)
    if isinstance(item, dict) and item.get("limit_pct") is not None and item["limit_pct"] >= LIMIT:
        return "usage"
    return None


def prompt_text(from_agent: str, to_agent: str, at: str | None, reason: str) -> str:
    name = common.NAMES.get(from_agent, from_agent)
    other = common.NAMES.get(to_agent, to_agent)
    when = ""
    if at:
        try:
            when = " at " + datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone().strftime("%H:%M")
        except ValueError:
            when = ""
    verb = "was overloaded" if reason == "overloaded" else "hit its usage limit"
    return "{} {}{}. Press Enter to continue in {}, or q to stop.".format(name, verb, when, other)


def ask(text: str, *, auto: bool, read_key: Callable[[float], str] | None = None) -> bool:
    """True to continue. `--auto` counts down; any key cancels. Plain Enter
    continues; q (or EOF) stops. The prompt goes to stderr: stdout keeps
    the JSON contract when piped."""
    sys.stderr.write(text + ("\n" if auto else " "))
    sys.stderr.flush()
    if auto:
        read_key = read_key or _key_reader()
        for left in range(COUNTDOWN, 0, -1):
            sys.stderr.write("\rContinuing in {}s (any key cancels)... ".format(left))
            sys.stderr.flush()
            if read_key(1.0):
                sys.stderr.write("\ncancelled.\n")
                sys.stderr.flush()
                return False
        sys.stderr.write("\n")
        sys.stderr.flush()
        return True
    answer = sys.stdin.readline()
    return bool(answer) and not answer.strip().lower().startswith("q")


def run(args: argparse.Namespace) -> Outcome:
    project = common.find_project()
    named = list(getattr(args, "agents", None) or [])
    chain = order_of(project, named or None)
    if not chain:
        return fail("nothing_to_resume", "No launchable agent is wired here.",
                    "Run agent-autosave add claude-code (or codex, gemini).")
    dry = bool(getattr(args, "dry_run", False))
    auto = bool(getattr(args, "auto", False))
    steps: list[dict[str, Any]] = []
    index = 0
    while index < len(chain):
        agent = chain[index]
        current = common.load_fresh(project)
        before = (current or {}).get("task_id")
        if index == 0 and not (current and resumable(current)):
            outcome = start(project, agent, dry_run=dry)  # nothing to hand over yet
        else:
            outcome = launch(project, agent, dry_run=dry)
        code, message = outcome[1], outcome[2]
        data = outcome[0].get("data") or {}
        steps.append({"agent": agent, "exit": data.get("exit"), "dry_run": dry,
                      "command": data.get("command")})
        if dry:
            return success({"steps": steps, "order": chain}), code, message
        if outcome[0].get("ok") is False:
            return outcome
        save = common.load_fresh(project)
        # `done` archives the task and the next prompt starts another: a new
        # task id means this one finished.
        if save and (save.get("status") == "done" or save.get("task_id") != before):
            return success({"steps": steps, "status": "done"}), ExitCode.SUCCESS, message
        reason = should_switch(save)
        if reason is None or index + 1 >= len(chain):
            next_name = chain[index + 1] if index + 1 < len(chain) else None
            tip = ("Run agent-autosave resume {} to continue.".format(next_name)
                   if next_name else "Run agent-autosave status.")
            return success({"steps": steps, "status": (save or {}).get("status")}), ExitCode.SUCCESS, \
                (message or "") + ("\n" if message else "") + tip
        nxt = chain[index + 1]
        info = (save or {}).get("interrupted") or {}
        at = info.get("at") or ((save or {}).get("usage") or {}).get(agent, {}).get("at")
        if not ask(prompt_text(agent, nxt, at, reason), auto=auto):
            return success({"steps": steps, "stopped": True}), ExitCode.SUCCESS, "Stopped."
        index += 1
    return success({"steps": steps}), ExitCode.SUCCESS, None


def _key_reader() -> Callable[[float], str]:
    def read(timeout: float) -> str:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return ""
        return sys.stdin.read(1) if sys.stdin.isatty() else sys.stdin.readline()
    return read
