"""Fixed inputs for the styled and plain output goldens (tests/fixtures/ui).

Each case renders one command's human output from fixed data. Times are
shown in local time, so the goldens are rendered with TZ=UTC.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict

ROOT = Path(__file__).resolve().parents[1]
GOLDENS = ROOT / "tests/fixtures/ui"
EXAMPLE = ROOT / "examples/claude-to-codex/.autosave"


def _status(style):
    from agent_autosave.commands import status
    save = json.loads((EXAMPLE / "save.json").read_text(encoding="utf-8"))
    plan = save.get("plan") or []
    data = {"status": save["status"], "goal": save["goal"], "plan_done": sum(p["state"] == "done" for p in plan),
            "plan_total": len(plan), "next_action": save["next_action"], "task_id": save["task_id"],
            "updated": save["updated"], "interrupted": save.get("interrupted"),
            "failing": (save.get("failing") or {}).get("command"), "handoffs": save["handoffs"],
            "handoff_count": len(save["handoffs"]), "stale": False, "stale_reason": None}
    return status.render(data, doing="Add a failing check", stopped=save["last_actions"][0], style=style)


def _status_interrupted(style):
    from agent_autosave.commands import status
    data = {"status": "interrupted", "goal": "Migrate auth to OAuth2", "plan_done": 1, "plan_total": 3,
            "next_action": "Finish the token refresh in src/auth.ts", "task_id": "t_1", "updated": "2026-10-15T09:30:00Z",
            "interrupted": {"reason": "rate_limit", "agent": "claude-code", "at": "2026-10-15T09:29:58Z"},
            "failing": None, "handoffs": [], "handoff_count": 0, "stale": True,
            "stale_reason": "This save may be stale: 2 commits landed since."}
    return status.render(data, style=style)


def _doctor(style):
    from agent_autosave.installer import doctor
    checks = [{"agent": "agent-autosave", "level": "green", "message": "Hook command found: ~/.local/bin/agent-autosave",
               "hint": ""},
              {"agent": "claude-code", "level": "green", "message": "All 8 hooks present in .claude/settings.json.",
               "hint": ""},
              {"agent": "codex", "level": "yellow", "message": "Codex has not been run in this folder yet.",
               "hint": "Start codex here, open /hooks and trust the agent-autosave hooks."},
              {"agent": "cursor", "level": "red", "message": "The stop hook is missing from .cursor/hooks.json.",
               "hint": "agent-autosave add cursor"}]
    return doctor.render(checks, style=style)


def _doctor_green(style):
    from agent_autosave.installer import doctor
    checks = [{"agent": "claude-code", "level": "green", "message": "All 8 hooks present in .claude/settings.json.",
               "hint": ""}, {"agent": "claude-code", "level": "green", "message": "CLAUDE.md block present.", "hint": ""}]
    return doctor.render(checks, style=style)


def _history(style):
    from agent_autosave.commands import history
    rows = [{"task_id": "t_20261015_093000", "status": "done", "agents": ["claude-code", "codex"], "plan_done": 3,
             "plan_total": 3, "goal": "Migrate auth to OAuth2 and keep the session API unchanged"},
            {"task_id": "t_20261014_170512", "status": "paused", "agents": ["codex"], "plan_done": 0, "plan_total": 0,
             "goal": ""}]
    return history.render(rows, style=style)


def _history_empty(style):
    from agent_autosave.commands import history
    return history.render([], style=style)


def _init(style):
    from agent_autosave.commands import init
    items = [("add", "Claude Code 2.1.4", "8 hooks in .claude/settings.json"),
             ("add", "CLAUDE.md", "agent-autosave block added"),
             ("add", "Codex 0.130.0", "6 hooks in .codex/hooks.json"), ("add", "AGENTS.md", "agent-autosave block added"),
             ("skip", "Cursor", "not installed, skipped"), ("skip", "Gemini CLI", "not installed, skipped"),
             ("skip", "Antigravity", "not installed, skipped"),
             ("add", ".autosave/", "created and added to .gitignore")]
    notes = [("warn", "In Codex, open /hooks and trust the 6 agent-autosave hooks.", "Codex skips new hooks until you do.")]
    return init.render(items, notes, style=style)


def _init_again(style):
    from agent_autosave.commands import init
    return init.render([], [], already=True, style=style)


def _remove(style):
    from agent_autosave.commands import remove
    return remove.render({"removed": [".codex/hooks.json", "AGENTS.md"], "agents": ["claude-code"],
                          "kept_shared": [], "left_user_hooks": [], "by_content": [], "missing_spans": 0}, style)


def _show(style):
    from agent_autosave.commands import show
    return show.highlight((EXAMPLE / "SAVE.md").read_text(encoding="utf-8"), style)


CASES: Dict[str, Callable] = {
    "status": _status, "status-interrupted": _status_interrupted, "doctor": _doctor, "doctor-green": _doctor_green,
    "history": _history, "history-empty": _history_empty, "init": _init, "init-again": _init_again,
    "remove": _remove, "show": _show,
}


def render_all() -> Dict[str, str]:
    """{file name: text} for every case, styled and plain."""
    from agent_autosave.ui import Style
    out = {}
    for name, case in CASES.items():
        out[name + ".styled.txt"] = case(Style(color=True, width=100)) + "\n"
        out[name + ".plain.txt"] = case(Style(color=False, width=100)) + "\n"
    return out
