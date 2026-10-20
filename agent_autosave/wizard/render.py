"""Answers -> the files the wizard will create or change, before anything is written."""
from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple, Sequence


class Answers(NamedTuple):
    agents: tuple            # agent ids to wire
    codex_user_dir: Any      # Path of ~/.codex for --user-level Codex hooks, else None
    codex_hooks: bool        # False: Codex hooks already live in another layer
    gitignore: bool          # add .autosave/ to .gitignore
    record: bool             # record raw hook payloads (fixtures for bug reports)
    vault: Any = None        # vault folder for the journal and MEMORY.md; None keeps them in .autosave/
    agentic: Any = None      # "auto" or "off" when agentic-stack was found; None leaves the setting alone
    mcp: bool = True         # register the agent-autosave MCP server with each agent
    statusline: str = "off"  # Claude Code status line: "auto", "wrap" or "off"


class Change(NamedTuple):
    path: str     # relative to the project, or absolute outside it
    action: str   # "create" or "change"
    why: str


def config(current: dict[str, Any] | None, answers: Answers) -> dict[str, Any]:
    """.autosave/config.json with the wizard's preferences applied (other fields kept)."""
    from ..config import DEFAULTS
    value = dict(current) if current else {k: v for k, v in DEFAULTS.items()}
    record = dict(value.get("record") or DEFAULTS["record"], enabled=bool(answers.record))
    value["record"] = record
    # "" keeps journal and memory in .autosave/; never null, which the config rejects.
    value["vault"] = dict(value.get("vault") or DEFAULTS["vault"], path=answers.vault or "")
    if answers.agentic is not None:
        value["integrations"] = dict(value.get("integrations") or DEFAULTS["integrations"],
                                     agentic_stack=answers.agentic)
    return value


def changes(project: Path, answers: Answers, *, installed: Sequence[str] = ()) -> list[Change]:
    """Every file the install will create or change, in the order it touches them."""
    from ..installer.install import jobs_for
    project = Path(project)
    out: list[Change] = []
    seen = set()
    names = {"claude-code": "Claude Code", "codex": "Codex", "cursor": "Cursor", "gemini": "Gemini CLI",
             "antigravity": "Antigravity", "copilot": "Copilot CLI", "opencode": "OpenCode",
             "generic": "agents that read AGENTS.md"}
    for agent in answers.agents:
        if agent in installed:
            continue
        for destination, policy, addition in jobs_for(agent, "agent-autosave", codex_user_dir=answers.codex_user_dir,
                                                      codex_hooks=answers.codex_hooks, mcp=answers.mcp):
            if destination in seen:
                continue
            seen.add(destination)
            path = Path(destination) if Path(destination).is_absolute() else project / destination
            server = "mcpServers" in addition if isinstance(addition, dict) else "[mcp_servers." in str(addition)
            what = ("MCP server for " if server else "hooks for " if policy == "merge_json" else
                    "plugin for " if str(destination).endswith(".js") else "instructions for ") + names[agent]
            if policy == "merge_json":
                what += " (merged; your entries kept)"
            elif path.exists():
                what += " (a marked block; the rest untouched)"
            out.append(Change(str(destination), "change" if path.exists() else "create", what))
    if answers.statusline != "off" and "claude-code" in answers.agents:
        from ..installer.statusline import existing
        found = existing(project)
        if found is None or (answers.statusline == "wrap" and not found["ours"]):
            target = found["path"] if found and found["scope"] == "project" else ".claude/settings.json"
            out.append(Change(target, "change" if (project / target).exists() or target in seen else "create",
                              "status line: " + ("yours, then the save" if found else "the save")))
    if not installed:
        if answers.gitignore:
            out.append(Change(".gitignore", "change" if (project / ".gitignore").exists() else "create",
                              "one line: .autosave/"))
        out.append(Change(".autosave/", "create", "the save, the event ledger, install.json (for remove)"))
    out.append(Change(".autosave/config.json", "change" if installed else "create",
                      "recording " + ("on" if answers.record else "off") + "; memory " +
                      ("in the vault " + str(answers.vault) if answers.vault else "in .autosave/") +
                      ("" if answers.agentic is None else "; agentic-stack " +
                       ("shared" if answers.agentic == "auto" else "off"))))
    return out
