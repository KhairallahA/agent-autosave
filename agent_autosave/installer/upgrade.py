"""Refresh installed adapters: same agents and hook layers, current command
and resources. A dry run by default; `apply=True` removes and reinstalls.
The receipt format stays v1 (older receipts gain per-entry owners)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..storage import read_json
from .install import AGENTS, jobs_for

# init writes every default into .autosave/config.json, so a value that still
# equals an old default was never chosen by the user: upgrade moves it on.
OLD_DEFAULTS = {("inject", "max_chars"): 1500, ("compile", "save_md_max_chars"): 8000}


def config_changes(project: Path) -> list[tuple[str, str, int, int]]:
    """(section, field, old, new) for each old default still in config.json."""
    from ..config import DEFAULTS
    config = read_json(Path(project) / ".autosave" / "config.json")
    changes = []
    for (section, key), old in OLD_DEFAULTS.items():
        if isinstance(config, dict) and isinstance(config.get(section), dict) and config[section].get(key) == old:
            changes.append((section, key, old, DEFAULTS[section][key]))
    return changes


def migrate_config(project: Path) -> list[str]:
    """Rewrite old defaults in config.json, keeping the previous file as config.json.bak."""
    import shutil
    from ..storage import atomic_write
    changes = config_changes(project)
    if not changes:
        return []
    path = Path(project) / ".autosave" / "config.json"
    shutil.copyfile(str(path), str(path) + ".bak")
    config = read_json(path)
    for section, key, _, new in changes:
        config[section][key] = new
    atomic_write(path, config)
    return ["{}.{}".format(section, key) for section, key, _, _ in changes]


def plan(project: str | Path, command: str) -> dict[str, Any] | None:
    project = Path(project).resolve()
    receipt = read_json(project / ".autosave" / "install.json")
    if receipt is None:
        return None
    reasons = []
    if receipt.get("command") != command:
        reasons.append("hook command {} -> {}".format(receipt.get("command"), command))
    recorded = {}
    for item in receipt.get("files", []):
        recorded.setdefault(item["path"], "")
        recorded[item["path"]] += "".join(span["text"] for span in item.get("spans", []))
    for agent in receipt.get("agents") or []:
        if agent not in AGENTS:
            continue
        for relative, policy, addition in jobs_for(agent, command, codex_hooks=False):
            if policy in ("managed_block", "create_file") and addition.rstrip() not in recorded.get(relative, ""):
                reasons.append("{} text changed ({})".format(relative, agent))
    settings = recorded.get(".claude/settings.json", "")
    if "claude-code" in (receipt.get("agents") or []) and '"args"' in settings:
        reasons.append(".claude/settings.json hooks become one command string (claude-code)")
    for section, key, old, new in config_changes(project):
        reasons.append("config.json {}.{} {} -> {} (the old default)".format(section, key, old, new))
    if any("agents" not in item for item in receipt.get("files", [])):
        reasons.append("install.json entries gain owners")
    from .doctor import audit, RED
    for check in audit(project):
        if check["level"] == RED and check["agent"] != "core":
            reasons.append("repair: " + check["message"])
    layer = (receipt.get("hooks") or {}).get("codex") or {}
    return {"agents": list(receipt.get("agents") or []), "reasons": list(dict.fromkeys(reasons)),
            "codex_layer": layer.get("layer")}


def apply(project: str | Path, command: str, *, codex_existing: Callable[[Path], dict[str, Any] | None] | None = None) -> dict[str, Any] | None:
    """Remove everything, then install the same agents with `command`.
    `codex_existing(project)` -> {'layer','path','hooks'} or None: hooks another
    live repo still uses (kept by remove) are found in place, not duplicated."""
    from .detect import codex_home
    from .install import install
    from .remove import remove
    project = Path(project).resolve()
    before = plan(project, command)
    if before is None:
        return None
    remove(project)
    agents = [a for a in before["agents"] if a in AGENTS]
    found = codex_existing(project) if (codex_existing and "codex" in agents) else None
    outcome = install(project, agents, command=command,
                      codex_user_dir=codex_home() if before["codex_layer"] == "user" else None,
                      codex_hooks=found is None, codex_found=found)
    return dict(outcome, reasons=before["reasons"], config_migrated=migrate_config(project))
