"""Original, insertion-only config merging with exact ownership bookkeeping."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Sequence

from ..config import resolve
from ..locking import exclusive
from ..redact import Redactor
from ..storage import atomic_write, private_dir, read_json, regular_path

MARKER_START = "<!-- agent-autosave:start -->"
MARKER_END = "<!-- agent-autosave:end -->"


def _tree(text):
    # Preserve offsets and comments. Decode string tokens before interpreting
    # punctuation, so braces and comment markers inside strings stay literal.
    clean = re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/',
                   lambda m: m[0] if m[0].startswith('"') else re.sub(r"[^\n]", " ", m[0]), text)
    decoder = json.JSONDecoder()
    def parse(position):
        while position < len(clean) and clean[position].isspace():
            position += 1
        start = position
        if position >= len(clean):
            raise ValueError("Incomplete settings JSON.")
        if clean[position] in "{[":
            opening = clean[position]
            closing = "}" if opening == "{" else "]"
            children = {} if opening == "{" else []
            position += 1
            while True:
                while position < len(clean) and clean[position].isspace():
                    position += 1
                if position >= len(clean):
                    raise ValueError("Incomplete settings JSON.")
                if clean[position] == closing:
                    position += 1
                    break
                if opening == "{":
                    key, position = decoder.raw_decode(clean, position)
                    if not isinstance(key, str) or key in children:
                        raise ValueError("Ambiguous settings key.")
                    while clean[position].isspace():
                        position += 1
                    if clean[position] != ":":
                        raise ValueError("Expected settings colon.")
                    child, position = parse(position + 1)
                    children[key] = child
                else:
                    child, position = parse(position)
                    children.append(child)
                while position < len(clean) and clean[position].isspace():
                    position += 1
                if position < len(clean) and clean[position] == ",":
                    position += 1
                    continue
                if position >= len(clean) or clean[position] != closing:
                    raise ValueError("Expected settings delimiter.")
            value = {k: v["value"] for k, v in children.items()} if opening == "{" else [v["value"] for v in children]
            return dict(start=start, end=position, children=children, value=value), position
        value, end = decoder.raw_decode(clean, position)
        return dict(start=start, end=end, value=value), end
    node, end = parse(0)
    if clean[end:].strip():
        raise ValueError("Unexpected settings suffix.")
    return node


def merge_json(text: str, additions: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Return merged JSONC and exact inserted spans; never reformat old text."""
    tree = _tree(text)
    edits = []
    def merge(node, extra):
        if isinstance(extra, dict) and isinstance(node["value"], dict):
            missing = {}
            for key, value in extra.items():
                if key in node["children"]:
                    merge(node["children"][key], value)
                else:
                    missing[key] = value
            if missing:
                body = json.dumps(missing, ensure_ascii=False, separators=(",", ":"))[1:-1]
                edits.append((node["end"] - 1, ("," if node["value"] else "") + body))
        elif isinstance(extra, list) and isinstance(node["value"], list):
            missing = [value for value in extra if value not in node["value"]]
            if missing:
                body = json.dumps(missing, ensure_ascii=False, separators=(",", ":"))[1:-1]
                edits.append((node["end"] - 1, ("," if node["value"] else "") + body))
        elif node["value"] != extra:
            raise ValueError("Refusing to replace an existing setting.")
    merge(tree, additions)
    for position, body in sorted(edits, reverse=True):
        text = text[:position] + body + text[position:]
    # Save final offsets, not a copy of the user's config (which may be secret).
    recorded, shift = [], 0
    for position, body in sorted(edits):
        recorded.append({"at": position + shift, "text": body})
        shift += len(body)
    _tree(text)
    return text, recorded


# .gitignore is not Markdown: an HTML comment there is a literal ignore
# pattern. Markdown files keep the HTML markers.
HASH_START = "# agent-autosave:start"
HASH_END = "# agent-autosave:end"
GITIGNORE_LINES = ".autosave/"  # covers events.jsonl, errors.log, history/


HASH_FILES = (".gitignore", ".gitattributes")


def markers(path: str | Path) -> tuple[str, str]:
    """Hash-comment markers where an HTML comment is not a comment (git's
    files, TOML); HTML markers in Markdown."""
    path = Path(str(path))
    return (HASH_START, HASH_END) if path.name in HASH_FILES or path.suffix == ".toml" else (MARKER_START, MARKER_END)


def managed_block(text: str, content: str, path: str | Path = "") -> tuple[str, list[dict[str, Any]]]:
    start, end = markers(path)
    if any(marker in text for marker in (MARKER_START, MARKER_END, HASH_START, HASH_END)):
        raise ValueError("A managed block already exists without matching ownership.")
    body = ("\n" if text and not text.endswith("\n") else "") + start + "\n" + content.rstrip() + "\n" + end + "\n"
    return text + body, [{"at": len(text), "text": body}]


def migrate_gitignore(project: str | Path, receipt_path: Path, redactor: Redactor | None = None) -> bool:
    """Rewrite an old HTML-marker .gitignore block (and its receipt) to the
    '# agent-autosave:start' form with the single `.autosave/` line. Only an
    untouched recorded block is migrated; returns True when it changed."""
    receipt = read_json(receipt_path)
    if not receipt:
        return False
    for item in receipt.get("files", []):
        if item.get("path") != ".gitignore" or len(item.get("spans", [])) != 1:
            continue
        old = item["spans"][0]["text"]
        if MARKER_START not in old:
            return False
        path = regular_path(Path(project) / ".gitignore")
        if not path.exists():
            return False
        current = path.read_bytes().decode("utf-8")
        if current.count(old) != 1:
            return False  # edited by the user: leave it, remove still refuses safely
        lead = "\n" if old.startswith("\n") else ""
        new = lead + HASH_START + "\n" + GITIGNORE_LINES + "\n" + HASH_END + "\n"
        at = current.find(old)
        after = current[:at] + new + current[at + len(old):]
        item["spans"] = [{"at": at, "text": new}]
        item["after"] = digest(after)
        atomic_write(receipt_path, receipt, redactor=redactor)
        replace_user_file(path, after)
        return True
    return False


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def replace_user_file(path: Path, text: str) -> None:
    """Preserve user bytes and mode; sanitize only OUR additions before merging.

    Never duplicate user configuration into autosave backups or diagnostics.
    Unlike private state writes, this must not chmod the project directory or
    redact pre-existing credentials that belong to the user's configuration.
    """
    regular_path(path)
    if path.parent.is_symlink():
        raise OSError("Refusing symlink configuration directory.")
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix=".autosave-install-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(text.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


AGENTS = ("claude-code", "codex", "cursor", "gemini", "antigravity", "copilot", "opencode", "generic")
# Where each agent's hooks live (Codex: project or user, handled below).
HOOK_FILES = {".claude/settings.json": "claude-code", ".cursor/hooks.json": "cursor",
              ".gemini/settings.json": "gemini", ".agents/hooks.json": "antigravity",
              ".github/hooks/agent-autosave.json": "copilot", ".opencode/plugins/agent-autosave.js": "opencode"}
CORE = "_core"  # owner of .gitignore: removed with the last agent


def _resources(agent):
    # Resources ship inside the Python package as well as the repo adapter.
    return Path(__file__).resolve().parents[1] / "resources" / agent


def shell_command(command: str) -> str:
    """The executable as typed in a shell: quoted only when needed."""
    if os.name == "nt":
        return '"{}"'.format(command) if " " in command else command
    import shlex
    return shlex.quote(command)


def fill_command(value: Any, command: str) -> Any:
    """The adapter template with our executable filled in: "agent-autosave"
    alone (an MCP server's `command`) becomes the path itself, and
    "agent-autosave <args>" (a hook run through a shell) the quoted path
    plus the arguments. Agents launched from a GUI lack the shell's PATH."""
    if isinstance(value, dict):
        return {key: fill_command(item, command) for key, item in value.items()}
    if isinstance(value, list):
        return [fill_command(item, command) for item in value]
    if value == "agent-autosave":
        return command
    if isinstance(value, str) and value.startswith("agent-autosave "):
        return shell_command(command) + value[len("agent-autosave"):]
    return value


def source_text(path: Path, command: str) -> str:
    """A TOML or JavaScript template: its quoted "agent-autosave" becomes the
    quoted path (JSON string escapes are valid in both)."""
    return path.read_text(encoding="utf-8").replace('"agent-autosave"', json.dumps(command))


def block_text(path: str | Path, command: str) -> str:
    """Instruction block with the same resolved executable as the hooks, so an
    agent whose shell PATH lacks agent-autosave can still run the CLI."""
    text = Path(path).read_text(encoding="utf-8")
    for verb in ("plan", "save", "note"):
        text = text.replace("`agent-autosave {} ".format(verb), "`" + shell_command(command) + " {} ".format(verb))
    return text


def create_file(text: str, content: str, path: str | Path = "") -> tuple[str, list[dict[str, Any]]]:
    if text:
        raise ValueError("Refusing to overwrite an existing file.")
    return content, [{"at": 0, "text": content}]


def owners_of(item: dict[str, Any]) -> list[str]:
    """Owning agents of a receipt entry; inferred for older receipts without owners."""
    if isinstance(item.get("agents"), list):
        return list(item["agents"])
    path = str(item.get("path", "")).replace("\\", "/")
    if path == ".gitignore":
        return [CORE]
    if path in (".claude/settings.json", "CLAUDE.md"):
        return ["claude-code"]
    if path.endswith(".codex/hooks.json") or item.get("scope") == "user" or path == "AGENTS.md":
        return ["codex"]
    return []


def manifest(agent: str) -> dict[str, Any]:
    return json.loads((_resources(agent) / "adapter.json").read_text(encoding="utf-8"))


def jobs_for(agent: str, command: str, *, codex_user_dir: Path | None = None,
             codex_hooks: bool = True, mcp: bool = False, statusline: bool = False) -> list[tuple[Any, ...]]:
    """[(path, policy, addition)] for one agent, from its adapter manifest.
    Entries marked "feature": "mcp" (the MCP server registration) or
    "statusline" (Claude Code's status line) are added only when asked."""
    resources = _resources(agent)
    jobs = []
    for item in manifest(agent)["files"]:
        if (item.get("feature") == "mcp" and not mcp) or (item.get("feature") == "statusline" and not statusline):
            continue
        if agent == "codex" and item.get("feature") == "mcp" and (codex_user_dir is not None or not codex_hooks):
            continue  # Codex reads project config only where it runs project hooks: a trusted folder
        source = resources / item["src"]
        destination = item["dst"]
        if item["src"].endswith(".json"):
            addition = fill_command(json.loads(source.read_text(encoding="utf-8")), command)
            if agent == "codex" and item["src"] == "hooks.json":
                if not codex_hooks:
                    continue
                if codex_user_dir is not None:
                    destination = str(Path(codex_user_dir).expanduser().resolve() / item["user_dst"])
        elif item["src"].endswith(".md") or item["src"].endswith(".mdc"):
            addition = block_text(source, command)
        else:
            addition = source_text(source, command)
        jobs.append((destination, item["merge_policy"], addition))
    return jobs


# A server the user already registers under our name (outside our block)
# is theirs: a second [mcp_servers.agent-autosave] table is invalid TOML.
USER_SERVER = re.compile(r'^\s*\[\s*mcp_servers\s*\.\s*(?:agent-autosave|"agent-autosave")\s*\]', re.M)


def without_user_servers(project: Path, jobs: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    """Drop the Codex MCP block when config.toml defines the server itself."""
    kept = []
    for job in jobs:
        if str(job[0]).endswith("config.toml") and job[1] == "managed_block":
            path = Path(project) / job[0]
            text = path.read_bytes().decode("utf-8", "replace") if path.is_file() else ""
            start, end = markers(job[0])
            outside = re.sub(re.escape(start) + r"[\s\S]*?" + re.escape(end), "", text)
            if USER_SERVER.search(outside):
                continue
        kept.append(job)
    return kept


def statusline_jobs(project: Path, command: str, choice: str,
                    home: str | Path | None = None) -> tuple[list[tuple[Any, ...]], dict[str, Any]]:
    """(jobs, receipt info) for Claude Code's status line. `choice` is
    "auto" (ours only where no settings layer has one), "wrap" (also wrap
    theirs) or "off"."""
    if choice == "off":
        return [], {"mode": "off"}
    from .statusline import existing
    found = existing(project, home)
    addition = fill_command(json.loads((_resources("claude-code") / "statusline.json").read_text(encoding="utf-8")),
                            command)
    if found is None:
        return [(".claude/settings.json", "merge_json", addition)], {"mode": "added"}
    if found["ours"]:
        return [], {"mode": "present", "path": found["path"]}
    if choice != "wrap" or not found["command"]:
        return [], {"mode": "kept", "path": found["path"]}
    info = {"mode": "wrapped", "path": found["path"], "command": found["command"]}
    if found["scope"] == "user":
        # The project layer wins over the user's: ours goes there and runs theirs.
        return [(".claude/settings.json", "merge_json", addition)], info
    return [(found["path"], "replace_json", addition)], info


def write_wrap(directory: Path, info: dict[str, Any], redactor: Redactor) -> None:
    """The wrapped status line command, for `agent-autosave statusline`."""
    if info.get("mode") == "wrapped":
        atomic_write(directory / "statusline.json", {"v": 1, "command": info["command"], "source": info["path"]},
                     redactor=redactor)


def _read(path, policy):
    # Decode bytes directly: universal-newline translation would break
    # removal's byte-for-byte guarantee on CRLF user configurations.
    return path.read_bytes().decode("utf-8") if path.exists() else ("{}" if policy == "merge_json" else "")


def _plan_changes(project, jobs, receipt_files, redactor):
    """New receipt entries for (path, policy, addition, owner) jobs, plus the
    owner updates to existing entries. Texts are simulated in memory so two
    jobs on one file chain correctly. Returns (new_entries, touched_existing)."""
    texts, new, touched, planned_dirs = {}, [], [], set()
    for relative, policy, addition, owner in jobs:
        path = regular_path(project / relative)
        text = texts[relative] if relative in texts else _read(path, policy)
        clean = redactor.value(addition)
        if clean != addition:
            raise ValueError("Installation additions contain a redacted value; refusing an unusable hook.")
        if policy == "managed_block":
            # One block per file, shared by its owners (AGENTS.md: codex + generic).
            shared = [e for e in list(receipt_files) + new if e["path"] == relative and e["policy"] == "managed_block"]
            start, _ = markers(relative)
            if shared and start in text:
                for entry in shared:
                    if owner not in owners_of(entry):
                        entry["agents"] = owners_of(entry) + [owner]
                        touched.append(entry)
                continue
            owners = [owner]
            if shared:
                # Our block was deleted by hand: re-add it, owned by everyone
                # who owned the lost one (whose entries are then dropped).
                owners = sorted(set(sum((owners_of(e) for e in shared), [])) | {owner})
                for entry in shared:
                    entry["agents"] = []
                    touched.append(entry)
            after, spans = managed_block(text, clean, relative)
        elif policy == "create_file":
            shared = [e for e in list(receipt_files) + new if e["path"] == relative]
            if shared and text == addition:
                for entry in shared:
                    if owner not in owners_of(entry):
                        entry["agents"] = owners_of(entry) + [owner]
                        touched.append(entry)
                continue
            owners = [owner]
            if shared and text and text != addition:
                raise ValueError("Refusing to overwrite an edited file.")
            after, spans = create_file(text, clean, relative)
        elif policy == "replace_json":
            from .statusline import replace_json
            owners = [owner]
            key = next(iter(clean))
            after, spans = replace_json(text, key, clean[key])
        else:
            owners = [owner]
            after, spans = merge_json(text, clean)
            if not spans:
                for entry in [e for e in receipt_files if e["path"] == relative]:
                    if owner not in owners_of(entry):
                        entry["agents"] = owners_of(entry) + [owner]
                        touched.append(entry)
                continue
        change = {"path": relative, "created": not path.exists() and relative not in texts, "policy": policy,
                  "before": digest(text), "after": digest(after), "spans": spans, "agents": owners}
        if change["created"] and not Path(relative).is_absolute():
            # Folders this file needs that do not exist yet: remove deletes
            # them again once empty.
            dirs, parent = [], Path(relative).parent
            while str(parent) not in (".", "") and not (project / parent).exists() and parent.as_posix() not in planned_dirs:
                dirs.append(parent.as_posix())
                planned_dirs.add(parent.as_posix())
                parent = parent.parent
            change["dirs"] = dirs
        if Path(relative).is_absolute():
            change["scope"] = "user"  # Codex user-level hooks.json, outside the repo
        texts[relative] = after
        new.append(change)
    return new, touched


def _apply(project, changes):
    texts = {}
    for change in changes:
        path = project / change["path"]
        before = texts.get(change["path"])
        if before is None:
            before = _read(path, change["policy"])
        if digest(before) != change["before"]:
            raise ValueError("Configuration changed during installation.")
        after = before
        for span in change["spans"]:
            after = after[:span["at"]] + span["text"] + after[span["at"] + len(span.get("old", "")):]
        texts[change["path"]] = after
    for relative, text in texts.items():
        replace_user_file(project / relative, text)


def _layers(changes, agents, codex_found=None, layers=None):
    layers = dict(layers or {})
    for change in changes:
        path = change["path"].replace("\\", "/")
        if path in HOOK_FILES and HOOK_FILES[path] in set(agents) | set(layers):
            layers[HOOK_FILES[path]] = {"layer": "project", "path": change["path"], "added": True}
        elif path.endswith(".codex/hooks.json") or change.get("scope") == "user":
            layers["codex"] = {"layer": "user" if change.get("scope") == "user" else "project",
                               "path": change["path"], "added": True}
    if "codex" in agents and "codex" not in layers and codex_found:
        layers["codex"] = {"layer": codex_found["layer"], "path": str(codex_found["path"]), "added": False,
                           "existing_hooks": int(codex_found.get("hooks") or 0)}
    return layers


def _register(project, layers, changes):
    """Record user-level hook files in the reference-counted registry."""
    info = layers.get("codex")
    if not info or info.get("layer") != "user":
        return
    from . import registry
    change = next((c for c in changes if c.get("scope") == "user"), None)
    registry.register(info["path"], project, "codex", change)


def install(project: str | Path, agents: Sequence[str] = ("claude-code",), *, command: str | None = None,
            codex_user_dir: Path | None = None, codex_hooks: bool = True,
            codex_found: dict[str, Any] | None = None, gitignore: bool = True, mcp: bool = False,
            statusline: str = "auto") -> dict[str, Any]:
    """Create .autosave/ and wire the given agents.

    `command` is the hook executable; default is the absolute path of the
    `agent-autosave` console script, falling back to the plain name.
    `codex_user_dir` (the Codex home, e.g. ~/.codex) puts the Codex hooks in
    its user-level hooks.json instead of the project's .codex/hooks.json; the
    caller chooses it when the folder is not trusted or --user-level is given.
    `codex_hooks=False` writes no Codex hooks file at all (hooks already live in
    another layer: Codex runs every layer, so a second set records twice).
    `codex_found` ({'layer', 'path', 'hooks'}) names that layer; it is recorded
    in install.json so the receipt says where each agent's hooks live.
    `gitignore=False` leaves .gitignore alone (the user manages it); the
    receipt remembers that, so `add` does not add the line later.
    `mcp=True` also registers the MCP server with each agent that has one;
    the receipt remembers it for `add`. `statusline` ("auto", "wrap" or
    "off") puts agent-autosave in Claude Code's status line (see
    statusline_jobs).
    """
    project = Path(project).resolve()
    agents = tuple(agents)
    if set(agents) - set(AGENTS):
        raise ValueError("Unknown agent.")
    if command is None:
        from .detect import hook_command
        command = hook_command()
    directory = private_dir(project / ".autosave")
    with exclusive(directory / ".install.lock"):
        receipt = directory / "install.json"
        existing = read_json(receipt)
        if existing is not None:
            migrate_gitignore(project, receipt)
            return {"agents": existing.get("agents", [existing.get("agent")]), "changed": [],
                    "created": [], "already_installed": True}
        config = resolve()
        redactor = Redactor.from_project(project, config)
        jobs = []
        for agent in agents:
            jobs += [job + (agent,) for job in jobs_for(agent, command, codex_user_dir=codex_user_dir,
                                                         codex_hooks=codex_hooks, mcp=mcp)]
        jobs = without_user_servers(project, jobs)
        line = {"mode": "off"}
        if "claude-code" in agents:
            extra, line = statusline_jobs(project, command, statusline)
            jobs += [job + ("claude-code",) for job in extra]
        if gitignore:
            jobs.append((".gitignore", "managed_block", GITIGNORE_LINES, CORE))
        changes, _ = _plan_changes(project, jobs, [], redactor)
        layers = _layers(changes, agents, codex_found)
        # Write-ahead ownership journal makes partial installation removable.
        journal = {"v": 1, "agents": list(agents), "command": command, "hooks": layers, "files": changes, "mcp": mcp,
                   "statusline": line}
        if not gitignore:
            journal["gitignore"] = False
        atomic_write(receipt, journal, redactor=redactor)
        write_wrap(directory, line, redactor)
        _apply(project, changes)
        try:
            _register(project, layers, changes)
        except Exception:
            pass  # the registry is bookkeeping; doctor reports a missing entry
        created = []
        from ..state import TaskStore
        if not (directory / "task.json").exists():
            TaskStore(project, config).start(datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
            created.append(".autosave/task.json")
        if not (directory / "config.json").exists():
            atomic_write(directory / "config.json", config, redactor=redactor)
            created.append(".autosave/config.json")
        return {"agents": list(agents), "changed": list(dict.fromkeys(entry["path"] for entry in changes)),
                "created": created, "command": command, "statusline": line}


def add(project: str | Path, agent: str, *, command: str | None = None, codex_user_dir: Path | None = None,
        codex_hooks: bool = True, codex_found: dict[str, Any] | None = None, mcp: bool | None = None) -> dict[str, Any]:
    """Wire one more agent into an existing install, or repair one that is
    already wired (only missing hooks or a deleted block are re-added).
    The MCP server is registered when the receipt says so, or `mcp=True`
    (which the receipt then remembers)."""
    project = Path(project).resolve()
    if agent not in AGENTS:
        raise ValueError("Unknown agent.")
    directory = project / ".autosave"
    with exclusive(directory / ".install.lock"):
        receipt_path = directory / "install.json"
        receipt = read_json(receipt_path)
        if receipt is None:
            raise FileNotFoundError("Not installed.")
        command = command or receipt.get("command")
        if command is None:
            from .detect import hook_command
            command = hook_command()
        files = receipt.setdefault("files", [])
        for item in files:
            item["agents"] = owners_of(item)
        repaired = agent in receipt.get("agents", [])
        if mcp:
            receipt["mcp"] = True
        jobs = [job + (agent,) for job in jobs_for(agent, command, codex_user_dir=codex_user_dir,
                                                    codex_hooks=codex_hooks, mcp=bool(receipt.get("mcp")))]
        jobs = without_user_servers(project, jobs)
        if agent == "claude-code" and (receipt.get("statusline") or {}).get("mode", "added") == "added":
            extra, line = statusline_jobs(project, command, "auto")
            jobs += [job + (agent,) for job in extra]
            receipt["statusline"] = line if extra else receipt.get("statusline", line)
        if receipt.get("gitignore", True) and not any(CORE in owners_of(e) for e in files):
            jobs.append((".gitignore", "managed_block", GITIGNORE_LINES, CORE))
        redactor = Redactor.from_project(project, resolve())
        for relative, policy, _addition, _owner in jobs:
            if policy != "merge_json":
                continue
            path = regular_path(project / relative)
            current = path.read_bytes().decode("utf-8") if path.exists() else ""
            for entry in files:
                if entry["path"] == relative and agent in entry["agents"] and any(
                        current.count(span["text"]) != 1 for span in entry.get("spans", [])):
                    # Our insertion was edited or reformatted: remove finds
                    # these hooks by content from now on.
                    entry["by_content"] = True
        new, touched = _plan_changes(project, jobs, files, redactor)
        for entry in new:
            if any(e.get("by_content") and e["path"] == entry["path"] for e in files):
                entry["by_content"] = True
        receipt["files"] = [e for e in files if e["agents"]] + new
        if agent not in receipt.setdefault("agents", []):
            receipt["agents"].append(agent)
        receipt["hooks"] = _layers(new, [agent], codex_found, receipt.get("hooks"))
        atomic_write(receipt_path, receipt, redactor=redactor)
        _apply(project, new)
        try:
            _register(project, receipt["hooks"] if agent == "codex" else {}, new)
        except Exception:
            pass
        return {"agent": agent, "repaired": repaired, "changed": list(dict.fromkeys(e["path"] for e in new)),
                "owners_added": sorted({e["path"] for e in touched}), "command": command}


def set_statusline(project: str | Path, choice: str = "wrap") -> dict[str, Any]:
    """Add Claude Code's status line to an existing install (the wizard's
    --reconfigure): ours where none is set, or wrapping theirs."""
    project = Path(project).resolve()
    directory = project / ".autosave"
    with exclusive(directory / ".install.lock"):
        receipt_path = directory / "install.json"
        receipt = read_json(receipt_path)
        if receipt is None:
            raise FileNotFoundError("Not installed.")
        jobs, line = statusline_jobs(project, receipt.get("command") or "agent-autosave", choice)
        if not jobs:
            return {"changed": [], "statusline": line}
        redactor = Redactor.from_project(project, resolve())
        files = receipt.setdefault("files", [])
        new, _ = _plan_changes(project, [job + ("claude-code",) for job in jobs], files, redactor)
        receipt["files"] = files + new
        receipt["statusline"] = line
        atomic_write(receipt_path, receipt, redactor=redactor)
        write_wrap(directory, line, redactor)
        _apply(project, new)
        return {"changed": [e["path"] for e in new], "statusline": line}
