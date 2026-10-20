"""Team sharing: commit MEMORY.md and the journal (in-repo mode only).

Our .gitignore block is `.autosave/` by default. Sharing turns it into
`.autosave/*` plus two negations, so git tracks .autosave/MEMORY.md and
.autosave/journal/ while the ledger, saves, slots and candidates stay
ignored (git cannot re-include a file inside an ignored directory, hence
`/*`). The receipt span is rewritten with the block, so `remove` stays byte
exact. `.gitattributes` gets `MEMORY.md merge=union` (git's built-in union
merge driver, gitattributes(5)) so two teammates' entries both survive.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import resolve
from ..locking import exclusive
from ..redact import Redactor
from ..storage import atomic_write, read_json, regular_path
from .install import CORE, GITIGNORE_LINES, HASH_END, HASH_START, _apply, _plan_changes, digest, replace_user_file

SHARED_LINES = ".autosave/*\n!.autosave/MEMORY.md\n!.autosave/journal/"
ATTRIBUTES = "MEMORY.md merge=union"


def block(lines: str) -> str:
    return HASH_START + "\n" + lines + "\n" + HASH_END + "\n"


def _set_ignore(project: Path, receipt: dict[str, Any], lines: str) -> bool:
    """Swap the body of our recorded .gitignore block; False when the block
    is missing or was edited (then nothing is changed)."""
    for item in receipt.get("files", []):
        if item.get("path") != ".gitignore" or len(item.get("spans", [])) != 1:
            continue
        old = item["spans"][0]["text"]
        path = regular_path(project / ".gitignore")
        current = path.read_bytes().decode("utf-8") if path.exists() else ""
        if current.count(old) != 1 or HASH_START not in old:
            return False
        lead = "\n" if old.startswith("\n") else ""
        new = lead + block(lines)
        at = current.find(old)
        after = current[:at] + new + current[at + len(old):]
        item["spans"] = [{"at": at, "text": new}]
        item["after"] = digest(after)
        replace_user_file(path, after)
        return True
    return False


def set_sharing(project: str | Path, on: bool) -> dict[str, Any]:
    """Turn sharing on or off. Returns {"changed": [...], "ok": bool}."""
    project = Path(project).resolve()
    directory = project / ".autosave"
    with exclusive(directory / ".install.lock"):
        receipt_path = directory / "install.json"
        receipt = read_json(receipt_path)
        if receipt is None:
            raise FileNotFoundError("Not installed.")
        if not _set_ignore(project, receipt, SHARED_LINES if on else GITIGNORE_LINES):
            return {"ok": False, "changed": []}
        changed = [".gitignore"]
        new = []
        if on and not any(i.get("path") == ".gitattributes" for i in receipt.get("files", [])):
            redactor = Redactor.from_project(project, resolve())
            new, _ = _plan_changes(project, [(".gitattributes", "managed_block", ATTRIBUTES, CORE)],
                                   receipt["files"], redactor)
            receipt["files"] = receipt["files"] + new
            changed.append(".gitattributes")
        receipt["share"] = bool(on)
        atomic_write(receipt_path, receipt)
        _apply(project, new)
        return {"ok": True, "changed": changed}
