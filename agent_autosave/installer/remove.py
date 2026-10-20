"""Undo only recorded insertions; preserve later edits or report conflicts.

`remove(project)` takes every agent out; `remove(project, agent)` drops one
owner and reverts only entries no other agent still owns (AGENTS.md can be
shared by codex and generic). .gitignore goes with the last agent, and so
does install.json; the .autosave/ data itself is kept.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ..locking import exclusive
from ..storage import atomic_write, read_json, regular_path
from .install import CORE, _tree, digest, markers, owners_of, replace_user_file


PROJECT_FILES = {".claude/settings.json", "CLAUDE.md", ".codex/hooks.json", "AGENTS.md", ".gitignore",
                 ".cursor/hooks.json", ".cursor/rules/agent-autosave.mdc", "GEMINI.md", "ANTIGRAVITY.md",
                 ".mcp.json", ".codex/config.toml", ".cursor/mcp.json", ".gemini/settings.json",
                 ".claude/settings.local.json", ".agents/hooks.json", ".github/hooks/agent-autosave.json",
                 ".opencode/plugins/agent-autosave.js", ".gitattributes"}
# Antigravity's hooks.json maps hook names to events; ours is this name
# (antigravity.google/docs/hooks).
NAMED_HOOKS = "agent-autosave"


class RemoveConflict(ValueError):
    """Our insertion was edited in a way that makes removal ambiguous."""


def _checked(project, item):
    relative = Path(item["path"])
    if item.get("scope") == "user":
        # Only the Codex user-level hooks file may live outside the repo.
        from .detect import codex_home
        if not relative.is_absolute() or relative != codex_home().resolve() / "hooks.json":
            raise ValueError("Unsafe installation receipt path.")
    elif relative.is_absolute() or ".." in relative.parts or relative.as_posix() not in PROJECT_FILES:
        raise ValueError("Unsafe installation receipt path.")
    return regular_path(project / relative)


def revert(text: str, item: dict[str, Any], *, positional: bool) -> tuple[str, int]:
    """Text without item's spans. Spans already gone are tolerated (reported
    in `missing`); a span found more than once is refused."""
    restored, missing = text, 0
    for span in reversed(item["spans"]):
        if positional:
            position = span["at"]
        else:
            count = restored.count(span["text"])
            if count > 1:
                raise RemoveConflict("Managed configuration was edited; refusing ambiguous removal.")
            position = restored.find(span["text"])
        if position < 0 or restored[position:position + len(span["text"])] != span["text"]:
            if item["policy"] == "managed_block" and any(m in restored for m in markers(item["path"])):
                raise RemoveConflict("Managed block was edited; refusing ambiguous removal.")
            missing += 1
            continue
        restored = restored[:position] + span.get("old", "") + restored[position + len(span["text"]):]
    if item["policy"] in ("merge_json", "replace_json"):
        _tree(restored)
    return restored, missing


LEGACY_DIRS = (".claude", ".codex", ".cursor", ".cursor/rules")


def _hook_ours(hook, agents):
    from .userhooks import is_ours
    if not is_ours(hook):
        return False
    import shlex
    text = " ".join([str(hook.get("command", ""))] + [str(a) for a in hook.get("args", []) or []])
    try:
        words = shlex.split(text)
    except ValueError:
        words = text.split()
    return "hook" in words and any(a in words for a in agents)


# Names our MCP server is registered under: "autosave" in Gemini CLI, whose
# tool names are mcp_<server>_<tool>; "agent-autosave" everywhere else.
SERVER_NAMES = ("agent-autosave", "autosave")


def _servers_ours(data: Any) -> list[str]:
    """Our MCP server entries in a settings object: named like ours and
    running the agent-autosave command."""
    from .userhooks import is_ours
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return []
    return [name for name in SERVER_NAMES if is_ours(servers.get(name)) and
            "mcp" in [str(a) for a in (servers[name].get("args") or [])]]


def has_ours(data: Any, agents: Iterable[str]) -> bool:
    if _servers_ours(data):
        return True
    maps = [data.get(key) for key in ("hooks", NAMED_HOOKS)] if isinstance(data, dict) else []
    for entries in (e for hooks in maps if isinstance(hooks, dict) for e in hooks.values()):
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            inner = entry.get("hooks") if isinstance(entry.get("hooks"), list) else [entry]
            if any(isinstance(h, dict) and _hook_ours(h, agents) for h in inner):
                return True
    return False


def strip_by_content(text: str, agents: Iterable[str], items: list[dict[str, Any]]) -> str | None:
    """Remove every hook that is ours (agent-autosave command for `agents`)
    from JSON `text`, keeping all other content; used when our insertion can
    no longer be found byte for byte (the file was reformatted or edited).
    Returns new text, or None when nothing is left that the user wrote."""
    import json
    try:
        data = json.loads(text)
    except ValueError:
        raise RemoveConflict("Hooks file is not plain JSON; remove agent-autosave hooks by hand.")
    if not isinstance(data, dict):
        raise RemoveConflict("Hooks file is not a JSON object.")
    inserted = "".join(span["text"] for item in items for span in item.get("spans", []))
    for name in _servers_ours(data):
        del data["mcpServers"][name]
    from .statusline import is_ours as status_line_ours
    if status_line_ours(data.get("statusLine")):
        # Theirs comes back if we had replaced it; ours alone just goes.
        old = [span["old"] for item in items for span in item.get("spans", []) if "old" in span]
        if old:
            data["statusLine"] = json.loads(old[0])
        else:
            del data["statusLine"]
    if data.get("mcpServers") == {} and '"mcpServers":' in inserted:
        del data["mcpServers"]
    if isinstance(data.get(NAMED_HOOKS), dict) and has_ours({"hooks": data[NAMED_HOOKS]}, agents):
        del data[NAMED_HOOKS]
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        for event in list(hooks):
            entries = hooks[event]
            if not isinstance(entries, list):
                continue
            kept, changed = [], False
            for entry in entries:
                if isinstance(entry, dict) and isinstance(entry.get("hooks"), list):
                    inner = [h for h in entry["hooks"] if not (isinstance(h, dict) and _hook_ours(h, agents))]
                    if len(inner) != len(entry["hooks"]):
                        changed = True
                        if not inner:
                            continue
                        entry = dict(entry, hooks=inner)
                elif isinstance(entry, dict) and _hook_ours(entry, agents):
                    changed = True
                    continue
                kept.append(entry)
            if changed and not kept:
                del hooks[event]
            elif changed:
                hooks[event] = kept
        if not hooks and '"hooks":' in inserted:
            del data["hooks"]
    if data.get("version") == 1 and set(data) == {"version"} and '"version":1' in inserted:
        del data["version"]
    if not data and items and items[0].get("created"):
        return None
    from .userhooks import _indent
    return json.dumps(data, indent=_indent(text), ensure_ascii=False) + ("\n" if text.endswith("\n") else "")


def remove(project: str | Path, agent: str | None = None) -> dict[str, Any]:
    project = Path(project).resolve()
    directory = project / ".autosave"
    with exclusive(directory / ".install.lock"):
        receipt_path = directory / "install.json"
        receipt = read_json(receipt_path)
        if receipt is None:
            return {"removed": []}
        agents = list(receipt.get("agents") or [])
        if agent is not None and agent not in agents:
            return {"removed": [], "agents": agents, "not_installed": True}
        remaining = [a for a in agents if a != agent] if agent is not None else []
        keep, drop, kept_shared, left_user = [], [], [], []
        for item in receipt["files"]:
            owners = [] if agent is None else [o for o in owners_of(item) if o != agent]
            if owners == [CORE] and not remaining:
                owners = []
            item = dict(item, agents=owners)
            (keep if owners else drop).append(item)
        # A codex user-level hooks file may serve other repos as well.
        codex_gone = "codex" in agents and "codex" not in remaining
        hooks = receipt.get("hooks") or {}
        if codex_gone and (hooks.get("codex") or {}).get("layer") == "user":
            user_entries = [i for i in drop if i.get("scope") == "user"]
            for item in user_entries:
                _checked(project, item)
            from . import registry
            others, change = registry.unregister(hooks["codex"]["path"], project)
            if others:
                drop = [i for i in drop if i.get("scope") != "user"]
                kept_shared.append(hooks["codex"]["path"])
            elif not user_entries and change is not None:
                # Last user of hooks another repo wrote: revert its change.
                drop.append(dict(change, path=hooks["codex"]["path"], scope="user", agents=[]))
            elif not user_entries:
                left_user.append(hooks["codex"]["path"])  # never recorded: doctor shows the strip fix
        # Revert per file, later entries first, on the in-memory text.
        by_path = {}
        for item in receipt["files"]:
            by_path.setdefault(item["path"], []).append(item)
        drop_keys = {(i["path"], i["after"]) for i in drop}
        prepared, missing, by_content = [], 0, []
        gone_agents = [agent] if agent is not None else (agents or ["claude-code", "codex", "cursor"])
        for relative in dict.fromkeys(i["path"] for i in drop):
            entries = by_path.get(relative) or [i for i in drop if i["path"] == relative]
            path = _checked(project, entries[0])
            if not path.exists():
                continue
            current = path.read_bytes().decode("utf-8")
            text = current
            first_before = entries[0]["before"]
            all_reverted = True
            dropped = [i for i in entries if (i["path"], i["after"]) in drop_keys]
            all_reverted = len(dropped) == len(entries)
            json_file = entries[0]["policy"] in ("merge_json", "replace_json")
            exact, gone_total = True, 0
            if not (json_file and any(i.get("by_content") for i in dropped)):
                for item in reversed(dropped):
                    if digest(text) == item["before"]:
                        continue  # write-ahead journal entry not yet applied
                    text, gone = revert(text, item, positional=digest(text) == item["after"])
                    gone_total += gone
            else:
                exact = False
            if json_file:
                import json as _json
                try:
                    leftover = has_ours(_json.loads(text), gone_agents)
                except ValueError:
                    leftover = gone_total > 0
                if gone_total or leftover or not exact:
                    # Reformatted or edited: find our hooks by content instead.
                    text = strip_by_content(current, gone_agents, entries if all_reverted else dropped)
                    by_content.append(relative)
                    if text is None:
                        prepared.append((path, "", True, entries))
                        continue
            else:
                missing += gone_total
            if text == current:
                continue
            created = entries[0].get("created")
            delete = bool(created and all_reverted and (digest(text) == first_before or text in ("", "{}")))
            prepared.append((path, text, delete, entries))
        folders = set()
        for path, text, delete, entries in prepared:
            if delete:
                path.unlink()
                if entries[0].get("scope") != "user":
                    dirs = entries[0].get("dirs")
                    if dirs is None:  # older receipt: our usual folders
                        parent = Path(entries[0]["path"]).parent.as_posix()
                        dirs = [d for d in LEGACY_DIRS if parent == d or parent.startswith(d + "/")]
                    folders.update(dirs)
            else:
                replace_user_file(path, text)
        # Folders init created, deepest first, only once empty.
        for folder in sorted(folders, key=lambda d: d.count("/"), reverse=True):
            target = project / folder
            try:
                if target.is_dir() and not target.is_symlink() and not any(target.iterdir()):
                    target.rmdir()
            except OSError:
                pass
        if remaining:
            receipt["files"] = keep
            receipt["agents"] = remaining
            receipt["hooks"] = {k: v for k, v in hooks.items() if k in remaining}
            atomic_write(receipt_path, receipt)
        else:
            receipt_path.unlink()
        def shown(path):
            try:
                return str(path.relative_to(project))
            except ValueError:
                return str(path)
        return {"removed": [shown(p) for p, _, _, _ in prepared], "agents": remaining,
                "kept_shared": kept_shared, "left_user_hooks": left_user, "missing_spans": missing, "by_content": by_content}
