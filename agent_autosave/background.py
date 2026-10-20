"""Detached save rebuild, started by SessionEnd so the hook itself stays fast.

    python -m agent_autosave.background <project>

The hook returns at once; this process waits for the ledger lock, rebuilds
save.json and SAVE.md, logs any error to .autosave/errors.log, and exits 0.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def spawn(project: str | Path, mode: str = "rebuild") -> None:
    """Start the rebuild (or with mode "sync", only the memory sync) fully
    detached: no inherited stdio, no wait, no zombie."""
    command = [sys.executable, "-m", "agent_autosave.background", str(project)] + (["sync"] if mode == "sync" else [])
    quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                 cwd=str(project), close_fds=True)
    if os.name == "nt":
        flags = 0x00000200 | 0x08000000  # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        subprocess.Popen(command, creationflags=flags, **quiet)
        return
    # Double fork without forking Python: sh starts the rebuild in the
    # background and exits at once; waiting for sh (a few ms) reaps it, and the
    # rebuild is re-parented to init, so no zombie even in a long-lived caller.
    subprocess.run(["/bin/sh", "-c", '"$0" "$@" &'] + command, start_new_session=True,
                   timeout=5, check=False, **quiet)


def main(argv: list[str]) -> int:
    from pathlib import Path
    project = Path(argv[0])
    if argv[1:] == ["sync"]:
        _sync(project)
        return 0
    try:
        from .gitstate import read as read_git
        from .state import TaskStore
        store = TaskStore(project, lock_timeout=10)
        try:
            from .capture import retry_summaries
            retry_summaries(project)  # a compaction summary SessionStart could not read yet
        except Exception as error:
            store._error("summary.retry", error)
        # Read git before taking the ledger lock: git is the slowest step and
        # the next session's hooks must not wait behind it.
        slots = store.slots() or {"current": "", "slots": {}}
        task = slots["slots"].get(slots["current"]) or {}
        git_state = read_git(project, start_head=task.get("start_head"),
                             timeout_ms=store.config["budgets_ms"]["git_call"],
                             on_error=lambda error: store._error("git.read", error))
        store.rebuild(git_state=git_state)
    except Exception as error:  # already logged by rebuild in most cases
        try:
            from .storage import log_error
            log_error(project / ".autosave", "background.rebuild", error)
        except Exception:
            pass
    _sync(project)
    return 0


def _sync(project: Path) -> None:
    """Journal pages, candidates, the recall index, vault and agentic-stack."""
    try:
        from .memory_sync import sync
        sync(project)
    except Exception as error:  # a detached process has nowhere else to report
        try:
            from .storage import log_error
            log_error(project / ".autosave", "background.sync", error)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
