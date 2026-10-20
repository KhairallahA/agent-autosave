"""Which branch is checked out, read from .git/HEAD, and where its save is parked.

Hooks run once per event inside a 300 ms budget, so the branch is read from
the HEAD file directly; this module imports no subprocess or git code.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re


def head_branch(project: str | Path) -> str | None:
    """The checked-out branch, read from .git/HEAD without running git. A worktree's `.git` file
    ("gitdir: <path>") is followed. Returns "" outside a git repository and
    None on a detached HEAD (or an unreadable one), which keeps the slot."""
    start = Path(project).resolve()
    for root in (start, *start.parents):
        dotgit = root / ".git"
        try:
            if dotgit.is_dir():
                head = dotgit / "HEAD"
            elif dotgit.is_file():
                text = dotgit.read_text(encoding="utf-8").strip()
                if not text.startswith("gitdir:"):
                    return None
                gitdir = Path(text[len("gitdir:"):].strip())
                head = (gitdir if gitdir.is_absolute() else root / gitdir) / "HEAD"
            else:
                continue
            value = head.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return None
        if value.startswith("ref: refs/heads/"):
            return value[len("ref: refs/heads/"):] or None
        return None  # detached: a commit id
    return ""


def slug(branch: str) -> str:
    """A file name for a branch's parked save: readable, and unique (a short
    hash keeps "a/b" and "a_b" apart). The empty key (no git) is "_none"."""
    if not branch:
        return "_none"
    readable = re.sub(r"[^A-Za-z0-9._-]+", "_", branch).strip("._")[:60] or "branch"
    return readable + "-" + hashlib.sha256(branch.encode("utf-8")).hexdigest()[:8]
