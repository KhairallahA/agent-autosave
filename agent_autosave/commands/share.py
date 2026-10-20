"""`agent-autosave share on|off`: commit MEMORY.md and the journal so the
team shares them (in-repo mode). Before turning it on, every line of those
files is scanned with the redactor; any match refuses the change and lists
where, because once committed a secret is in git history."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..contract import ExitCode, Outcome, fail, success
from . import common


def findings(project: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    """{"file", "line"} for each line the redactor would change."""
    from ..redact import Redactor
    redactor = Redactor.from_project(project, config)
    base = project / ".autosave"
    files = [base / "MEMORY.md"] + sorted((base / "journal").rglob("*.md"))
    found = []
    for path in files:
        if not path.is_file() or path.is_symlink():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if redactor.text(line) != line:
                found.append({"file": path.relative_to(project).as_posix(), "line": number})
    return found


def run(args: argparse.Namespace) -> Outcome:
    from .. import places
    from ..installer.share import set_sharing
    project = common.find_project()
    config = common.store(project).config
    on = args.share_action == "on"
    if on and places.in_vault(project, config):
        return fail("not_supported", "The journal and MEMORY.md live in the vault, outside this repo.",
                    "Share the vault folder instead, or clear vault.path to keep them in .autosave/.")
    if on:
        found = findings(project, config)
        if found:
            lines = ["  {}:{}".format(f["file"], f["line"]) for f in found]
            return fail("secrets_found", "Refusing to share: {} line{} look like secrets.".format(
                len(found), "" if len(found) == 1 else "s"),
                "Edit them out, then run agent-autosave share on again.\n" + "\n".join(lines),
                detail={"findings": found})
    result = set_sharing(project, on)
    if not result["ok"]:
        return fail("edited_config", "Our .gitignore block is missing or was edited.",
                    "Run agent-autosave add <agent> to restore it, then try again.")
    data = {"shared": on, "changed": result["changed"]}
    text = ("Sharing on: commit .autosave/MEMORY.md and .autosave/journal/ (the ledger and saves stay private)."
            if on else "Sharing off: .autosave/ is ignored again (files already committed stay in git).")
    return success(data), ExitCode.SUCCESS, text
