"""`agent-autosave vault move`: move the in-repo journal and MEMORY.md into the vault.

Shows the plan; nothing moves without --yes. A file already in the vault
that agent-autosave did not write is never overwritten (it is listed as a
conflict and left in .autosave/).
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Any

from ..contract import ExitCode, Outcome, fail, success
from . import common


def run(args: argparse.Namespace) -> Outcome:
    from .. import vault
    project = common.find_project()
    config = common.store(project).config
    if not vault.configured(config):
        return fail("no_vault", "vault.path is not set.",
                    "Set vault.path in .autosave/config.json (or run agent-autosave init --reconfigure).")
    reason = vault.problem(config)
    if reason:
        return fail("no_vault", "The vault cannot be used: {}.".format(reason), "Check vault.path, then run this again.")
    base = vault.project_dir(project, config)
    plan = moves(project, base)
    data = {"vault": common_shown(base), "moves": [m["file"] for m in plan if not m["conflict"]],
            "conflicts": [m["file"] for m in plan if m["conflict"]], "moved": False}
    if not args.yes:
        return success(data), ExitCode.SUCCESS, render(data, applied=False)
    vault.save_project_name(project, config)
    if not data["moves"]:
        vault.ensure(project, common.store(project).config)
        return success(data), ExitCode.SUCCESS, render(data, applied=False)
    from .. import memory
    from ..memory_sync import rebuild_index
    from ..redact import Redactor
    redactor = Redactor.from_project(project, config)
    with memory.lock(project):
        for item in plan:
            if item["conflict"]:
                continue
            source = project / ".autosave" / item["file"]
            vault.write(base, item["file"], source.read_text(encoding="utf-8"), redactor=redactor)
            source.unlink()
        rebuild_index(project, common.store(project).config, date.today(), full=True)
        vault.ensure(project, common.store(project).config)  # the home note and CURRENT.md, now
    data["moved"] = True
    return success(data), ExitCode.SUCCESS, render(data, applied=True)


def moves(project: Path, base: Path) -> list[dict[str, Any]]:
    """Every in-repo memory file, and whether its vault twin is the user's own."""
    from .. import vault
    local = project / ".autosave"
    files = []
    if (local / "MEMORY.md").is_file():
        files.append("MEMORY.md")
    journal = local / "journal"
    files += sorted(p.relative_to(local).as_posix() for p in journal.rglob("*.md") if not p.is_symlink()) \
        if journal.is_dir() else []
    listed = set(vault.manifest(base)) if base.is_dir() else set()
    return [{"file": f, "conflict": (base / f).exists() and f not in listed} for f in files]


def common_shown(path: Path) -> str:
    from ..redact import shorten_home
    return shorten_home(str(path))


def render(data: dict[str, Any], *, applied: bool) -> str:
    from ..ui import style as current
    style = current()
    lines = [style.heading("agent-autosave vault move", data["vault"]), ""]
    if not data["moves"] and not data["conflicts"]:
        return "\n".join(lines + ["  Nothing to move: no journal or MEMORY.md in .autosave/."])
    verb = "moved" if applied else "move"
    lines += [style.item("ok" if applied else "add", "{} .autosave/{}".format(verb, f)) for f in data["moves"]]
    lines += [style.item("warn", "keep .autosave/{} (the vault already has a file there that agent-autosave did not "
                                 "write)".format(f)) for f in data["conflicts"]]
    if not applied:
        lines += ["", style.hint("Run agent-autosave vault move --yes to move them.", 2)]
    return "\n".join(lines)
