"""Which Codex hooks layer to write: Codex runs every layer, so ours go to exactly one."""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any


def count_hooks(path: Path) -> int:
    """Count agent-autosave Codex hooks in a user-level hooks.json (read-only)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    hooks = data.get("hooks") if isinstance(data, dict) else None
    count = 0
    for entries in (hooks.values() if isinstance(hooks, dict) else []):
        for entry in (entries if isinstance(entries, list) else []):
            for hook in ((entry.get("hooks") or []) if isinstance(entry, dict) else []):
                command = str(hook.get("command", "")) if isinstance(hook, dict) else ""
                if "agent-autosave" in command and " hook codex " in command:
                    count += 1
    return count


def trust_help(project: Path) -> dict[str, str]:
    """How to trust `project` in Codex: the exact config.toml entry and a
    command that appends it; then `agent-autosave add codex`."""
    from ..installer import detect
    config = detect.codex_home() / "config.toml"
    key = str(Path(project).resolve()).replace("\\", "\\\\").replace('"', '\\"')
    entry = '[projects."{}"]\ntrust_level = "trusted"'.format(key)
    command = "printf '%s\\n' '' {} {} >> {}".format(
        shlex.quote('[projects."{}"]'.format(key)), shlex.quote('trust_level = "trusted"'), shlex.quote(str(config)))
    message = ("Codex was skipped: this folder is not trusted in Codex, and agent-autosave never writes "
               "user-level hooks unless you pass --user-level.")
    hint = ("Trust the folder (start codex here and accept the trust prompt, or add this to {}: {}), "
           "then run agent-autosave add codex.").format(config, entry.replace("\n", " "))
    text = "\n".join([
        "To trust this folder, either start `codex` here and accept the trust prompt, or add to {}:".format(config),
        "", "    " + entry.replace("\n", "\n    "), "",
        "  e.g.  " + command,
        "Then run: agent-autosave add codex",
        "(Or, to put the hooks in {} for every Codex session: agent-autosave add codex --user-level)".format(
            detect.codex_home() / "hooks.json")])
    return {"message": message, "hint": hint, "text": text, "config_toml": str(config), "entry": entry,
            "command": command}


def choose(project: Path, user_level: bool = False) -> tuple[Path | None, bool, dict[str, Any] | None]:
    """(codex_user_dir or None, write hooks?, found layer or None): Codex runs
    every layer, so hooks go to exactly one, and none if one already has ours."""
    from ..installer import detect
    codex_dir = detect.codex_home()
    user_file, project_file = codex_dir / "hooks.json", Path(project) / ".codex/hooks.json"
    existing = {"user": count_hooks(user_file), "project": count_hooks(project_file)}
    found = None
    if existing["project"]:
        found = {"layer": "project", "path": ".codex/hooks.json", "hooks": existing["project"]}
    elif existing["user"]:
        found = {"layer": "user", "path": str(user_file), "hooks": existing["user"]}
    return (codex_dir if user_level else None), found is None, found
