"""Read-only, bounded git facts. Called by rebuild, never by the pure compiler."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable

from .timeouts import bound


def read(project: str | Path, *, start_head: str | None = None, timeout_ms: int = 150,
         on_error: Callable[[Exception], Any] | None = None) -> dict[str, Any] | None:
    timeout = bound(min(150, timeout_ms) / 1000)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("GIT_")}
    environment.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")

    def git(*arguments):
        return subprocess.run(
            ["git", "--no-optional-locks", "-c", "core.quotepath=false", *arguments],
            cwd=project, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, check=False, input=b"",
        )

    try:
        # One status call yields branch, HEAD and dirty files (each git spawn
        # costs tens of milliseconds on some runners; hooks are budgeted).
        status = git("status", "--porcelain=v2", "-z", "--branch", "--untracked-files=all")
        if status.returncode:
            return None  # not inside a work tree
        records = iter(status.stdout.decode("utf-8", "replace").split("\0"))
        branch = head = None
        dirty, untracked = [], []
        for record in records:
            if not record:
                continue
            if record.startswith("# branch.oid "):
                value = record[len("# branch.oid "):]
                head = None if value == "(initial)" else value
            elif record.startswith("# branch.head "):
                value = record[len("# branch.head "):]
                branch = None if value == "(detached)" else value
            elif record.startswith("#"):
                continue
            elif record[0] == "1":
                dirty.append(record.split(" ", 8)[8])
            elif record[0] == "2":
                dirty.append(record.split(" ", 9)[9])
                next(records, None)  # original path follows in -z mode
            elif record[0] == "u":
                dirty.append(record.split(" ", 10)[10])
            elif record[0] == "?":
                dirty.append(record[2:])
                untracked.append(record[2:])
        baseline = "HEAD"
        if not head:
            # Hash an empty tree without writing an object; works with either
            # SHA-1 or SHA-256 repositories and includes staged initial files.
            empty_tree = git("hash-object", "-t", "tree", "--stdin")
            if empty_tree.returncode:
                raise OSError("Cannot determine git empty tree.")
            baseline = empty_tree.stdout.decode("ascii").strip()
        diff = git("diff", "--no-ext-diff", "--no-textconv", "--shortstat", baseline, "--")
        if diff.returncode:
            raise OSError("Cannot read git diffstat.")
        commits = 0
        if head and start_head:
            if not re.fullmatch(r"[0-9a-fA-F]{40,64}", start_head):
                raise ValueError("Invalid initial git commit identifier.")
            count = git("rev-list", "--count", start_head + "..HEAD", "--")
            commits = int(count.stdout) if count.returncode == 0 else None
        return {"branch": branch, "head": head, "dirty": sorted(set(dirty)),
                "untracked": sorted(set(untracked)),
                "diffstat": diff.stdout.decode("utf-8", "replace").strip(),
                "commits_since_start": commits}
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        if on_error is not None:
            on_error(error)
        return None


def commits_between(project: str | Path, old: str, new: str, *, timeout_ms: int = 150) -> int | None:
    """Number of commits in old..new, or None when git cannot tell (CLI only)."""
    if not (old and new and re.fullmatch(r"[0-9a-fA-F]{40,64}", old) and re.fullmatch(r"[0-9a-fA-F]{40,64}", new)):
        return None
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        result = subprocess.run(["git", "--no-optional-locks", "rev-list", "--count", old + ".." + new, "--"],
                                cwd=project, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=bound(min(150, timeout_ms) / 1000), check=False, input=b"")
        return int(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
