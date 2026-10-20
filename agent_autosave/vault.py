"""Obsidian vault mode: journal and memory written to <vault>/agent-autosave/<project>/.

Only the memory sync (background process) and the memory commands write
here, never a hook. Every write is atomic and private, resolves real paths,
refuses a path that leaves its folder or passes through a symlink, and only
replaces files listed in that folder's manifest (.agent-autosave.json), so
a note the user created by hand with the same name is never overwritten.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

FOLDER = "agent-autosave"
MANIFEST = ".agent-autosave.json"


class VaultError(OSError):
    """A vault write refused for safety (escape, symlink, unlisted file) or impossible."""


def configured(config: dict[str, Any]) -> bool:
    return bool(config["vault"]["path"].strip())


def project_name(project: str | Path, config: dict[str, Any]) -> str:
    """vault.project when set, else the repo folder name plus 4 hex
    characters of a hash of its absolute path (two checkouts named `app`
    stay apart). Pure: saving it is save_project_name's job."""
    chosen = config["vault"]["project"].strip()
    if chosen:
        return chosen
    resolved = str(Path(project).resolve())
    name = "".join(c if c.isalnum() or c in "-_." else "-" for c in Path(resolved).name) or "repo"
    return name + "-" + hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:4]


def save_project_name(project: str | Path, config: dict[str, Any]) -> str:
    """Write vault.project into .autosave/config.json once, so moving the
    repo later does not start a second folder in the vault."""
    from .storage import atomic_write, read_json
    name = project_name(project, config)
    if config["vault"]["project"].strip():
        return name
    path = Path(project) / ".autosave" / "config.json"
    current = read_json(path) or {"v": 1}
    vault = dict(current.get("vault") or {}, project=name)
    atomic_write(path, dict(current, vault=vault))
    return name


def root(config: dict[str, Any]) -> Path | None:
    """<vault>/agent-autosave with the vault's real path, or None when unset."""
    if not configured(config):
        return None
    return Path(os.path.realpath(os.path.expanduser(config["vault"]["path"].strip()))) / FOLDER


def problem(config: dict[str, Any]) -> str:
    """Why the vault cannot be used now ('' when it can)."""
    base = root(config)
    if base is None:
        return "not configured"
    vault = base.parent
    if not vault.is_dir():
        return "the vault folder does not exist"
    if not os.access(str(vault), os.W_OK | os.X_OK):
        return "the vault folder is not writable"
    if base.is_symlink() or (base.exists() and not base.is_dir()):
        return "agent-autosave in the vault is not a plain folder"
    return ""


def project_dir(project: str | Path, config: dict[str, Any]) -> Path | None:
    base = root(config)
    return None if base is None else base / project_name(project, config)


def target(base: Path, relative: str) -> Path:
    """`relative` inside `base`, refusing absolute paths, `..`, and any
    existing component that is a symlink (a link could point anywhere)."""
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(p in ("..", "") for p in parts) or "\\" in relative:
        raise VaultError("Refusing a vault path outside its folder.")
    current = base
    if current.is_symlink():
        raise VaultError("Refusing a symlink in the vault.")
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise VaultError("Refusing a symlink in the vault.")
    resolved = Path(os.path.realpath(str(current)))
    if Path(os.path.realpath(str(base))) not in resolved.parents:
        raise VaultError("Refusing a vault path outside its folder.")
    return current


def manifest(base: Path) -> list[str]:
    """Files agent-autosave wrote in `base`, relative; [] when none yet."""
    try:
        data = json.loads(target_manifest(base).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    files = data.get("files") if isinstance(data, dict) else None
    return [f for f in files if isinstance(f, str)] if isinstance(files, list) else []


def target_manifest(base: Path) -> Path:
    path = base / MANIFEST
    if path.is_symlink():
        raise VaultError("Refusing a symlink in the vault.")
    return path


def write(base: Path, relative: str, text: str, *, redactor: Any = None) -> Path:
    """Write one file in the vault folder `base` and list it in the manifest.
    An existing file that is not listed belongs to the user: refused."""
    from .storage import atomic_write, private_dir
    if base.is_symlink():
        raise VaultError("Refusing a symlink in the vault.")
    private_dir(base)
    path = target(base, relative)
    listed = manifest(base)
    if path.exists() and relative not in listed:
        raise VaultError("Refusing to overwrite a vault file agent-autosave did not write.")
    _parents(base, path)
    atomic_write(path, text, redactor=redactor)
    if relative not in listed:
        atomic_write(target_manifest(base), {"v": 1, "files": sorted(listed + [relative])})
    return path


def _parents(base: Path, path: Path) -> None:
    from .storage import private_dir
    current = base
    for part in path.relative_to(base).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise VaultError("Refusing a symlink in the vault.")
        private_dir(current)


# --- CURRENT.md and the home note


def write_current(project: str | Path, config: dict[str, Any], save: dict[str, Any] | None, *,
                  tz: Any = None) -> Path:
    """<project>/CURRENT.md: SAVE.md with frontmatter, for the home note and
    Obsidian; a short "No task yet" note before the first save."""
    from .redact import Redactor
    from .render import render
    save = save if isinstance(save, dict) else {}
    head = {"title": (save.get("goal") or "No task yet")[:200], "status": save.get("status"),
            "task_id": save.get("task_id"), "updated": save.get("updated"),
            "repo": Path(project).resolve().name, "tags": ["agent-autosave", "agent-autosave/current"]}
    from .journal import local_clock
    # Times people read are local (`tz`); the frontmatter keeps UTC.
    body = render(save, config, clock=lambda ts: local_clock(ts, tz)) if save else ("# No task yet\n\nagent-autosave writes the save here once an "
                                              "agent session starts in this repo.\n")
    text = frontmatter(head) + body
    save_project_name(project, config)
    return write(project_dir(project, config), "CURRENT.md", text, redactor=Redactor.from_project(project, config))


def ensure(project: str | Path, config: dict[str, Any], *, only_missing: bool = False) -> bool:
    """Write <project>/CURRENT.md and the home note now, so a vault is
    complete from its first use. With only_missing, only when CURRENT.md is
    not there yet (cheap). False when the vault is not configured or unusable."""
    if not configured(config) or problem(config):
        return False
    base = project_dir(project, config)
    if only_missing and (base / "CURRENT.md").is_file():
        return True
    from .storage import read_view
    save = read_view(Path(project) / ".autosave" / "save.json")
    from .memory_sync import local_zone
    zone = local_zone()
    write_current(project, config, save if isinstance(save, dict) else None, tz=zone)
    write_home(config, tz=zone)
    return True


def start(project: str | Path) -> None:
    """ensure() for init and the wizard: never fails them (doctor shows the vault)."""
    from . import config as config_module
    from .storage import log_error
    try:
        ensure(project, config_module.load(project))
    except Exception as error:
        log_error(Path(project) / ".autosave", "vault.start", error)


def frontmatter(fields: dict[str, Any]) -> str:
    return "---\n" + "".join("{}: {}\n".format(k, json.dumps(v, ensure_ascii=False)) for k, v in fields.items()) + "---\n"


def projects(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Every project folder in the vault with its CURRENT.md frontmatter."""
    from .journal import read_frontmatter
    base = root(config)
    rows = []
    for folder in sorted(base.iterdir()) if base is not None and base.is_dir() else []:
        current = folder / "CURRENT.md"
        if folder.is_symlink() or not folder.is_dir() or current.is_symlink() or not current.is_file():
            continue
        try:
            with open(str(current), encoding="utf-8") as stream:
                head = read_frontmatter(stream.read(4096))
        except OSError:
            continue
        rows.append(dict(head, folder=folder.name))
    return rows


def write_home(config: dict[str, Any], *, tz: Any = None) -> Path:
    """<vault>/agent-autosave/README.md: one row per project with its open
    task, status, last update and links."""
    lines = ["---", 'tags: ["agent-autosave", "agent-autosave/home"]', "---", "# agent-autosave", "",
             "Tasks, journals and project memory that agent-autosave keeps for each repo. "
             "Each project folder holds CURRENT.md (the save), MEMORY.md and journal/.", "",
             "| Project | Open task | Status | Last updated | Links |", "| --- | --- | --- | --- | --- |"]
    from .journal import local_clock
    for row in projects(config):
        folder = row["folder"]
        task = _cell(row.get("title") or "")
        links = "[[{0}/CURRENT\\|save]] · [[{0}/MEMORY\\|memory]]".format(folder)
        lines.append("| {} | {} | {} | {} | {} |".format(_cell(row.get("repo") or folder), task,
                                                         _cell(row.get("status") or ""),
                                                         _cell(local_clock(row["updated"], tz) if row.get("updated") else ""),
                                                         links))
    return write(root(config), "README.md", "\n".join(lines) + "\n")


def _cell(value: Any) -> str:
    """A table cell: one line, pipes escaped, no Markdown or HTML openers."""
    from .render import _text
    return _text(str(value))[:120].replace("|", "\\|")
