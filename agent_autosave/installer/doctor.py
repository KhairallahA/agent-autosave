"""Read-only audit of installed adapters: green / yellow / red, each with a hint.

Red means autosave is broken for that agent (exit 1); yellow means it works
but something deserves attention; green is fine. Nothing is written.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Callable

from ..storage import read_json
from .install import AGENTS, _resources, _tree, markers, owners_of
from .userhooks import is_ours

GREEN, YELLOW, RED = "green", "yellow", "red"
BLOCK_FILES = {"claude-code": "CLAUDE.md", "codex": "AGENTS.md", "generic": "AGENTS.md", "copilot": "AGENTS.md",
               "opencode": "AGENTS.md",
               "gemini": "GEMINI.md", "antigravity": "ANTIGRAVITY.md"}
BINARIES = {"claude-code": ("claude",), "codex": ("codex",), "cursor": ("cursor", "cursor-agent"),
            "gemini": ("gemini",), "antigravity": ("agy",), "copilot": ("copilot",), "opencode": ("opencode",)}
NAMES = {"claude-code": "Claude Code", "codex": "Codex", "cursor": "Cursor", "gemini": "Gemini CLI",
         "antigravity": "Antigravity", "copilot": "Copilot CLI", "opencode": "OpenCode", "generic": "Generic", "core": "agent-autosave", "memory": "memory",
         "vault": "vault", "agentic-stack": "agentic-stack"}


def _check(agent, level, message, hint=None):
    return {"agent": agent, "level": level, "message": message, "hint": hint}


def _event_map(data: Any, agent: str) -> Any:
    """The event -> entries map: Antigravity's sits under our hook name."""
    if not isinstance(data, dict):
        return None
    return data.get("agent-autosave") if agent == "antigravity" else data.get("hooks")


def expected_events(agent: str) -> list[str]:
    path = _resources(agent) / "hooks.json"
    if not path.exists():
        return []
    return list(_event_map(json.loads(path.read_text(encoding="utf-8")), agent))


def _ours(entry, agent):
    hooks = entry.get("hooks") if isinstance(entry.get("hooks"), list) else [entry]
    return any(is_ours(h) and agent in " ".join([str(h.get("command", ""))] + [str(a) for a in h.get("args", []) or []])
               for h in hooks if isinstance(h, dict))


def hook_events(path: str | Path, agent: str) -> tuple[set[str], str | None]:
    """(events with an agent-autosave hook for `agent`, error or None)."""
    try:
        data = _tree(Path(path).read_bytes().decode("utf-8"))["value"]
    except FileNotFoundError:
        return set(), "missing"
    except (OSError, UnicodeDecodeError, ValueError):
        return set(), "invalid"
    hooks = _event_map(data, agent)
    found = set()
    for event, entries in (hooks.items() if isinstance(hooks, dict) else []):
        if any(isinstance(e, dict) and _ours(e, agent) for e in (entries if isinstance(entries, list) else [])):
            found.add(event)
    return found, None


def _command_ok(command):
    if not command:
        return False
    if os.path.isabs(command):
        return os.path.isfile(command) and os.access(command, os.X_OK)
    return shutil.which(command) is not None


def audit(project: str | Path, *, which: Callable[[str], str | None] = shutil.which,
          home: str | Path | None = None) -> list[dict[str, Any]]:
    project = Path(project).resolve()
    directory = project / ".autosave"
    checks = []
    receipt = read_json(directory / "install.json")
    if receipt is None:
        checks.append(_check("core", RED, "No install.json: no agent is wired in this repo.", "agent-autosave init"))
        return checks
    agents = [a for a in receipt.get("agents") or [] if a in AGENTS]
    command = receipt.get("command")
    if _command_ok(command):
        checks.append(_check("core", GREEN, "Hook command found: {}".format(command)))
    else:
        checks.append(_check("core", RED, "Hook command not found: {} (moved or uninstalled?)".format(command),
                             "agent-autosave upgrade --yes"))
    hooks = receipt.get("hooks") or {}
    for agent in agents:
        hint = "agent-autosave add {}".format(agent)
        if agent in BINARIES and not any(which(b) for b in BINARIES[agent]):
            checks.append(_check(agent, YELLOW, "{} binary not found on PATH.".format(NAMES[agent]),
                                 "Install {} or ignore if it is launched another way.".format(NAMES[agent])))
        events = expected_events(agent)
        if events:
            info = hooks.get(agent) or {}
            path = info.get("path")
            if not path:
                checks.append(_check(agent, RED, "install.json does not say where the hooks live.", hint))
            else:
                target = Path(path) if Path(path).is_absolute() else project / path
                found, error = hook_events(target, agent)
                if error == "missing":
                    checks.append(_check(agent, RED, "{} is missing.".format(path), hint))
                elif error == "invalid":
                    checks.append(_check(agent, RED, "{} is not valid JSON.".format(path),
                                         "Fix the JSON in {}, then run {}.".format(path, hint)))
                else:
                    missing = [e for e in events if e not in found]
                    for event in missing:
                        checks.append(_check(agent, RED, "Hook {} is missing from {}.".format(event, path), hint))
                    if not missing:
                        checks.append(_check(agent, GREEN, "All {} hooks present in {}.".format(len(events), path)))
        name = BLOCK_FILES.get(agent)
        if name:
            try:
                text = (project / name).read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""
            start, end = markers(name)
            if start in text and end in text:
                checks.append(_check(agent, GREEN, "{} block present.".format(name)))
            else:
                checks.append(_check(agent, RED, "{} block is missing.".format(name), hint))
        if receipt.get("mcp"):
            checks.extend(mcp_checks(project, agent, hooks.get(agent) or {}))
        if agent == "opencode":
            plugin = ".opencode/plugins/agent-autosave.js"
            if (project / plugin).is_file():
                checks.append(_check(agent, GREEN, plugin + " present."))
            else:
                checks.append(_check(agent, RED, plugin + " is missing.", hint))
        if agent == "cursor":
            rule = project / ".cursor/rules/agent-autosave.mdc"
            if rule.is_file():
                checks.append(_check(agent, GREEN, ".cursor/rules/agent-autosave.mdc present."))
            else:
                checks.append(_check(agent, RED, ".cursor/rules/agent-autosave.mdc is missing.", hint))
            checks.append(_check(agent, YELLOW if "claude-code" in agents else GREEN,
                                 "Cursor runs project hooks only in a trusted workspace."
                                 + (" It also runs .claude/settings.json hooks; agent-autosave drops those Cursor "
                                    "events (cursor_version)." if "claude-code" in agents else ""),
                                 "Trust this folder in Cursor." if "claude-code" not in agents else
                                 "Nothing to do; or turn off Third-Party Imports in Cursor's hooks settings."))
    if "codex" in agents:
        from .detect import codex_home, codex_trusted
        user_file, project_file = codex_home(home) / "hooks.json", project / ".codex/hooks.json"
        both = [p for p in (user_file, project_file) if hook_events(p, "codex")[0]]
        if len(both) == 2:
            checks.append(_check("codex", YELLOW, "agent-autosave Codex hooks are in both {} and {}; Codex records "
                                 "every event twice.".format(user_file, project_file),
                                 "{} -m agent_autosave.installer.userhooks --codex".format(sys.executable)))
        if (hooks.get("codex") or {}).get("layer") == "project" and not codex_trusted(project, home=home):
            checks.append(_check("codex", YELLOW, "This folder is not trusted in Codex, which skips project hooks.",
                                 "Start codex here once and trust the folder."))
        if (hooks.get("codex") or {}).get("layer") == "user":
            from . import registry
            if not any(i.get("repo") == str(project) for i in registry.users(user_file)):
                checks.append(_check("codex", YELLOW, "This repo is not in the user-level hooks registry, so remove in "
                                     "another repo could take the shared hooks away.", "agent-autosave add codex"))
    if "claude-code" in agents:
        local = project / ".claude/settings.local.json"
        if hook_events(local, "claude-code")[0]:
            # Claude Code merges settings.json and settings.local.json hooks:
            # both layers run, so every event is recorded twice.
            checks.append(_check("claude-code", YELLOW, "{} also has agent-autosave hooks; Claude Code records every "
                                 "event twice.".format(local),
                                 "{} -m agent_autosave.installer.userhooks --file {}".format(sys.executable, local)))
        from .userhooks import detect as user_detect, settings_path
        if settings_path(home).parent.parent.resolve() != project and user_detect(home):
            checks.append(_check("claude-code", YELLOW, "{} also has agent-autosave hooks; Claude Code records every "
                                 "event twice.".format(settings_path(home)),
                                 "{} -m agent_autosave.installer.userhooks".format(sys.executable)))
    log = directory / "errors.log"
    try:
        count = sum(1 for line in log.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
    except OSError:
        count = 0
    if count:
        checks.append(_check("core", YELLOW, ".autosave/errors.log has {} entr{}.".format(count, "y" if count == 1 else "ies"),
                             "Read .autosave/errors.log; delete it once handled."))
    from . import registry
    for hooks_file, install in registry.stale():
        checks.append(_check("core", YELLOW, "Stale registry entry: {} ({}) no longer has .autosave/install.json "
                             "(uses {}).".format(install.get("repo"), install.get("agent"), hooks_file),
                             "agent-autosave doctor --prune"))
    for item in receipt.get("files", []):
        if not owners_of(item):
            checks.append(_check("core", YELLOW, "install.json entry {} has no owner.".format(item.get("path")),
                                 "agent-autosave upgrade --yes"))
    checks.extend(memory_checks(project))
    return checks


def mcp_registered(path: Path) -> bool | None:
    """True when `path` registers our MCP server, False when it does not,
    None when the file is missing or unreadable."""
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if path.suffix == ".toml":
        from .install import USER_SERVER
        return bool(USER_SERVER.search(text))
    from .remove import _servers_ours
    try:
        return bool(_servers_ours(_tree(text)["value"]))
    except ValueError:
        return None


def mcp_checks(project: Path, agent: str, layer: dict[str, Any]) -> list[dict[str, Any]]:
    """The MCP server registration of one agent, when it has one."""
    from .install import manifest
    entry = next((i for i in manifest(agent)["files"] if i.get("feature") == "mcp"), None)
    if entry is None or (agent == "codex" and layer.get("layer") != "project"):
        return []
    found = mcp_registered(project / entry["dst"])
    if found:
        return [_check(agent, GREEN, "MCP server registered in {}.".format(entry["dst"]))]
    return [_check(agent, YELLOW, "MCP server is not registered in {}; the agent runs the commands instead.".format(
        entry["dst"]), "agent-autosave add {} --mcp".format(agent))]


def memory_checks(project: str | Path) -> list[dict[str, Any]]:
    """MEMORY.md size, the vault, and agentic-stack: yellow when something works worse than configured."""
    from datetime import date
    from .. import agentic, config as config_module, memory, places, vault
    try:
        config, warnings = config_module.load_with_warnings(project)
    except config_module.ConfigError as error:
        return [_check("memory", YELLOW, "Config is invalid, so memory runs on defaults: {}".format(error),
                       "Fix .autosave/config.json (see docs/configuration.md)")]
    checks = [_check("config", YELLOW, warning, "agent-autosave init --reconfigure") for warning in warnings]
    if vault.configured(config):
        reason = vault.problem(config)
        if reason:
            checks.append(_check("vault", YELLOW, "Vault {} unusable ({}); the journal and MEMORY.md fall back to "
                                 ".autosave/.".format(config["vault"]["path"], reason),
                                 "Fix vault.path, or agent-autosave vault move <folder> --yes"))
        else:
            checks.append(_check("vault", GREEN, "Vault folder: " + places.shown(project, places.home(project, config))))
    path = places.memory_file(project, config)
    try:
        counted = memory.counts(memory.read(project, config), date.today())
        active = sum(counted[kind] for kind in memory.TYPES)
    except (OSError, UnicodeDecodeError) as error:
        checks.append(_check("memory", YELLOW, "{} cannot be read: {}".format(places.shown(project, path), error)))
        active = None
    limit = config["memory"]["max_entries"]
    if active is not None and active > limit:
        checks.append(_check("memory", YELLOW, "{} has {} entries (memory.max_entries is {}); recall gets less "
                             "precise.".format(places.shown(project, path), active, limit),
                             "agent-autosave memory list, then memory forget <id> for stale ones"))
    elif active is not None:
        checks.append(_check("memory", GREEN, "{}: {} entr{} (limit {}).".format(
            places.shown(project, path), active, "y" if active == 1 else "ies", limit)))
    if config["integrations"]["agentic_stack"] == "on" and not agentic.detected(project):
        checks.append(_check("agentic-stack", YELLOW, "integrations.agentic_stack is on, but there is no .agent/ "
                             "folder with AGENTS.md and memory/ here.", "Install agentic-stack in this repo, or set "
                             "integrations.agentic_stack to auto"))
    if agentic.detected(project):
        if not agentic.enabled(project, config):
            checks.append(_check("agentic-stack", GREEN, "Found in .agent/; integrations.agentic_stack is off."))
        elif not (Path(project) / ".agent/tools/track.py").is_file():
            checks.append(_check("agentic-stack", YELLOW, ".agent/tools/track.py is missing, so finished tasks are "
                                 "not added as cases.", "Reinstall agentic-stack in this repo"))
        else:
            checks.append(_check("agentic-stack", GREEN, "Found in .agent/: WORKSPACE.md block {}; cases and lessons "
                                 "shared.".format("present" if agentic.has_block(project) else "written at the next "
                                                  "task")))
    return checks


def render(checks: list[dict[str, Any]], color: bool | None = None, style: Any = None) -> str:
    """The doctor report: one line per check, a hint under every problem."""
    from ..ui import Style
    if style is None:
        style = Style(color=not os.environ.get("NO_COLOR") if color is None else color)
    labels = {GREEN: ("ok", "ok  ", "green"), YELLOW: ("warn", "warn", "yellow"), RED: ("fail", "FAIL", "red")}
    lines = [style.heading("agent-autosave doctor"), ""]
    for check in checks:
        level, label, colour = labels[check["level"]]
        name = NAMES.get(check["agent"], check["agent"])
        message = style.paint(check["message"], "red") if check["level"] == RED else check["message"]
        lines.append("  {} {} {} {}".format(style.mark(level), style.paint(label, colour), style.bold(name.ljust(14)),
                                            message))
        if check.get("hint") and check["level"] != GREEN:
            lines.append("         " + style.dim("hint: " + check["hint"]))
    reds = sum(1 for c in checks if c["level"] == RED)
    yellows = sum(1 for c in checks if c["level"] == YELLOW)
    def plural(count, word):
        return "{} {}{}".format(count, word, "" if count == 1 else "s")
    summary = ("{}, {}.".format(plural(reds, "problem"), plural(yellows, "warning")) if reds or yellows
               else "All green: {} passed.".format(plural(len(checks), "check")))
    lines.append("")
    lines.append("  " + (style.paint(summary, "red") if reds else style.paint(summary, "yellow") if yellows
                         else style.paint(summary, "green")))
    return "\n".join(lines)
