"""Find (and on request remove) agent-autosave hooks in ~/.claude/settings.json.

init only reads that file and warns: hooks there plus the project's own make
Claude Code run every agent-autosave hook twice. Removal is a separate,
explicit command that backs the file up first:

    python3 -m agent_autosave.installer.userhooks --dry-run   # show only
    python3 -m agent_autosave.installer.userhooks             # back up, remove

Codex: `--codex` works on $CODEX_HOME/hooks.json (default ~/.codex), and
`--file PATH` on any hooks file (e.g. a project's .codex/hooks.json).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any, Sequence, TextIO

_OURS = re.compile(r"(?:^|[\s/\\\"'])agent-autosave(?:\.exe)?(?:[\s\"']|$)|-m\s+agent_autosave(?:\s|$)")


def settings_path(home: str | Path | None = None) -> Path:
    return Path(home if home is not None else os.path.expanduser("~")) / ".claude" / "settings.json"


def is_ours(hook: Any) -> bool:
    if not isinstance(hook, dict):
        return False
    text = " ".join([str(hook.get("command", ""))] + [str(a) for a in hook.get("args", []) or []])
    return bool(_OURS.search(text))


def find(data: Any) -> list[dict[str, Any]]:
    """[(event, command text)] for every agent-autosave hook in settings data."""
    found = []
    hooks = data.get("hooks") if isinstance(data, dict) else None
    for event, groups in (hooks or {}).items() if isinstance(hooks, dict) else []:
        for group in groups if isinstance(groups, list) else []:
            for hook in (group.get("hooks") or []) if isinstance(group, dict) else []:
                if is_ours(hook):
                    found.append((event, " ".join([str(hook.get("command", ""))] + [str(a) for a in hook.get("args", []) or []])))
    return found


def strip(data: Any) -> tuple[Any, list[dict[str, Any]]]:
    """Return (new data, removed) with our hooks and now-empty groups/events dropped."""
    data = json.loads(json.dumps(data))
    removed = find(data)
    hooks = data.get("hooks")
    if not removed or not isinstance(hooks, dict):
        return data, []
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if isinstance(group, dict) and isinstance(group.get("hooks"), list):
                kept = [h for h in group["hooks"] if not is_ours(h)]
                if not kept and len(kept) != len(group["hooks"]):
                    continue  # the group only held our hooks
                group["hooks"] = kept
            kept_groups.append(group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    if not hooks:
        del data["hooks"]
    return data, removed


def detect(home: str | Path | None = None) -> list[dict[str, Any]]:
    """Read-only: our hooks in the user-level settings, [] if none or unreadable."""
    path = settings_path(home)
    try:
        return find(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


def _indent(text):
    match = re.search(r"\n([ \t]+)\"", text)
    return match.group(1) if match else 2


def main(argv: Sequence[str] | None = None, home: str | Path | None = None, out: TextIO | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out = out or sys.stdout
    dry = "--dry-run" in argv
    path = settings_path(home)
    if "--codex" in argv:
        from .detect import codex_home
        path = codex_home(home) / "hooks.json"
    if "--file" in argv:
        index = argv.index("--file")
        if index + 1 >= len(argv):
            print("--file needs a path.", file=out)
            return 2
        path = Path(argv[index + 1]).expanduser()
    if not path.is_file():
        print("No {} - nothing to do.".format(path), file=out)
        return 0
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except ValueError:
        print("{} is not plain JSON (comments?); edit it by hand. Nothing changed.".format(path), file=out)
        return 1
    new, removed = strip(data)
    if not removed:
        print("No agent-autosave hooks in {} - nothing to do.".format(path), file=out)
        return 0
    for event, command in removed:
        print("{} {}: {}".format("would remove" if dry else "removed", event, command), file=out)
    if dry:
        print("Dry run: {} unchanged. Run again without --dry-run to apply.".format(path), file=out)
        return 0
    backup = path.with_name(path.name + ".agent-autosave-backup-" + time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(str(path), str(backup))
    body = json.dumps(new, indent=_indent(text), ensure_ascii=False) + ("\n" if text.endswith("\n") else "")
    tmp = path.with_name(path.name + ".agent-autosave-tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(str(tmp), str(path))
    print("Backup: {}".format(backup), file=out)
    print("Updated {}; everything else kept.".format(path), file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
