"""Where Claude Code's status line comes from, and how ours goes in.

Claude Code reads `statusLine` from its settings layers; the most specific
wins: .claude/settings.local.json, then .claude/settings.json, then
~/.claude/settings.json (https://code.claude.com/docs/en/settings).
Ours goes in only when no layer has one. When one does, the wizard can
wrap it: ours runs theirs (copied to .autosave/statusline.json) and appends
our segment. Their original setting is kept byte for byte in the receipt.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

PROJECT_LAYERS = (".claude/settings.local.json", ".claude/settings.json")
WRAP_FILE = ".autosave/statusline.json"


def _status_line(path: Path) -> Any:
    from .install import _tree
    try:
        data = _tree(path.read_bytes().decode("utf-8"))["value"]
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return data.get("statusLine") if isinstance(data, dict) else None


def is_ours(value: Any) -> bool:
    from .userhooks import is_ours as command_is_ours
    return isinstance(value, dict) and command_is_ours(value) and "statusline" in str(value.get("command"))


def existing(project: str | Path, home: str | Path | None = None) -> dict[str, Any] | None:
    """The status line Claude Code would show here: {"path", "scope",
    "command", "ours"}, or None when no layer sets one."""
    from .userhooks import settings_path
    project = Path(project)
    layers = [(name, "project", project / name) for name in PROJECT_LAYERS]
    layers.append((str(settings_path(home)), "user", settings_path(home)))
    for name, scope, path in layers:
        value = _status_line(path)
        if value is not None:
            command = value.get("command") if isinstance(value, dict) else None
            return {"path": name, "scope": scope, "command": command if isinstance(command, str) else None,
                    "ours": is_ours(value)}
    return None


def replace_json(text: str, key: str, value: Any) -> tuple[str, list[dict[str, Any]]]:
    """Swap one top-level setting's value; the span keeps the old bytes so
    remove can put them back exactly."""
    import json
    from .install import _tree
    node = _tree(text)["children"][key]
    new = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    old = text[node["start"]:node["end"]]
    return text[:node["start"]] + new + text[node["end"]:], [{"at": node["start"], "text": new, "old": old}]
