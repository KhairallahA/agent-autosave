"""Where journal pages and MEMORY.md live: .autosave/ or the vault.

Repo mode (vault.path empty) keeps everything in .autosave/. In vault mode
they live in <vault>/agent-autosave/<project>/; when the vault cannot be
used, they fall back to .autosave/ and the reason is logged once per call
site (doctor shows it as a yellow check). Reads here are cheap stats, so a
hook may ask; only the sync and the commands write.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import vault


def home(project: str | Path, config: dict[str, Any], *, log: bool = False) -> Path:
    """The folder holding MEMORY.md and journal/."""
    if vault.configured(config):
        reason = vault.problem(config)
        if not reason:
            return vault.project_dir(project, config)
        if log:
            from .storage import log_error
            log_error(Path(project) / ".autosave", "vault.fallback", vault.VaultError(reason))
    return Path(project) / ".autosave"


def in_vault(project: str | Path, config: dict[str, Any]) -> bool:
    return home(project, config) != Path(project) / ".autosave"


def memory_file(project: str | Path, config: dict[str, Any]) -> Path:
    return home(project, config) / "MEMORY.md"


def journal_dir(project: str | Path, config: dict[str, Any]) -> Path:
    return home(project, config) / "journal"


def shown(project: str | Path, path: Path) -> str:
    """A path for output: relative to the repo when inside it, else with ~."""
    from .redact import shorten_home
    try:
        return Path(path).relative_to(Path(project)).as_posix()
    except ValueError:
        return shorten_home(str(path))


def write(project: str | Path, config: dict[str, Any], relative: str, text: str, *, redactor: Any = None) -> Path:
    """Write MEMORY.md or a journal page (`relative` to the memory home)."""
    base = home(project, config, log=True)
    if base == Path(project) / ".autosave":
        from .storage import atomic_write
        path = vault.target(base, relative)  # same escape and symlink checks in repo mode
        atomic_write(path, text, redactor=redactor)
        return path
    vault.save_project_name(project, config)
    path = vault.write(base, relative, text, redactor=redactor)
    if relative != "CURRENT.md":
        vault.ensure(project, config, only_missing=True)  # the first vault write: home note and CURRENT.md too
    return path
