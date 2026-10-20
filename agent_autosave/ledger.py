"""Locked JSONL append, strict reads, and rotation preserving the active task."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, ContextManager, Iterable

from .config import resolve
from .events import EventError, prepare, validate
from .locking import exclusive
from .redact import Redactor
from .storage import atomic_write, log_error, private_dir, read_json, regular_path

MAX_LINE_BYTES = 2 * 1024 * 1024


def identity(path: Path) -> list[int] | None:
    if not path.exists():
        return None
    stat = path.stat()
    return [stat.st_dev, stat.st_ino]


def _line(event):
    return json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


class Ledger:
    def __init__(self, project: str | Path, config: dict[str, Any] | None = None, *,
                 redactor: Redactor | None = None, lock_timeout: float = 0.05) -> None:
        self.project = Path(project)
        self.config = resolve(config)
        self.redactor = redactor or Redactor.from_project(project, self.config)
        self.directory = private_dir(self.project / ".autosave")
        self.path = self.directory / "events.jsonl"
        self.lock_path = self.directory / ".writer.lock"
        self.lock_timeout = lock_timeout

    def lock(self) -> ContextManager[Any]:
        return exclusive(self.lock_path, timeout=self.lock_timeout)

    def append(self, event: dict[str, Any], *, active_task: str | Iterable[str] | None = None) -> dict[str, Any]:
        """`active_task`: the task id (or ids, one per branch slot) whose
        events stay in events.jsonl when it rotates; the rest is archived."""
        try:
            clean = prepare(event, self.config, self.redactor)
            with self.lock():
                self._append_locked(clean, active_task=active_task)
            return clean
        except Exception as error:
            log_error(self.directory, "ledger.append", error, redactor=self.redactor)
            raise

    def _repair_tail(self):
        regular_path(self.path)
        if not self.path.exists() or self.path.stat().st_size == 0:
            return
        with self.path.open("r+b") as stream:
            stream.seek(-1, os.SEEK_END)
            if stream.read(1) == b"\n":
                return
            end = stream.tell()
            start = max(0, end - MAX_LINE_BYTES)
            stream.seek(start)
            tail = stream.read()
            newline = tail.rfind(b"\n")
            if newline < 0 and start:
                raise EventError("Oversized incomplete ledger line; manual repair required.")
            stream.truncate(start + newline + 1 if newline >= 0 else 0)
            stream.flush()
            os.fsync(stream.fileno())
        log_error(self.directory, "ledger.repair", EventError("Removed an incomplete final ledger line."), redactor=self.redactor)

    def _append_locked(self, event, *, active_task=None):
        payload = _line(event).encode("utf-8")
        if len(payload) > MAX_LINE_BYTES:
            raise EventError("Event exceeds the ledger line size limit.")
        self._repair_tail()
        limit = max(1, int(self.config["ledger"]["rotate_mb"] * 1024 * 1024))
        metadata = read_json(self.directory / "ledger.json")
        threshold = metadata["next_rotation_bytes"] if metadata else limit
        if type(threshold) is not int or threshold < 1:
            raise EventError("Invalid ledger rotation threshold.")
        if self.path.exists() and self.path.stat().st_size + len(payload) >= threshold:
            self._rotate_locked(active_task, limit)
        path = regular_path(self.path)
        fd = os.open(str(path), os.O_CREAT | os.O_APPEND | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if os.name == "posix":
                os.fchmod(fd, 0o600)
            remaining = memoryview(payload)
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError("Incomplete ledger write.")
                remaining = remaining[written:]
            os.fsync(fd)
        finally:
            os.close(fd)

    def _rotate_locked(self, active_task, limit):
        events = self._read_locked()
        if not events:
            return
        # Re-redact even manually edited, otherwise valid historical rows.
        events = [prepare(event, self.config, self.redactor) for event in events]
        archive = "".join(_line(event) for event in events)
        name = "events-" + hashlib.sha256(archive.encode("utf-8")).hexdigest()[:24] + ".jsonl"
        private_dir(self.directory / "history")
        atomic_write(self.directory / "history" / name, archive, redactor=self.redactor)
        keep = None if active_task is None else ({active_task} if isinstance(active_task, str) else set(active_task))
        active = events if keep is None else [event for event in events if event.get("task_id") in keep]
        content = "".join(_line(event) for event in active)
        # Archive first: interruption between replacements cannot lose events.
        atomic_write(self.path, content, redactor=self.redactor)
        atomic_write(self.directory / "ledger.json", {"v": 1, "next_rotation_bytes": len(content.encode("utf-8")) + limit}, redactor=self.redactor)
        self.prune_history()

    def read(self, *, task_id: str | None = None, cursor: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        try:
            with self.lock():
                return self._read_locked(task_id=task_id, cursor=cursor)
        except Exception as error:
            log_error(self.directory, "ledger.read", error, redactor=self.redactor)
            raise

    def _read_locked(self, *, task_id=None, cursor=None):
        path = regular_path(self.path)
        if not path.exists():
            return []
        result = []
        with path.open("rb") as stream:
            if cursor and cursor["identity"] == identity(path):
                stream.seek(cursor["offset"])
            while True:
                raw = stream.readline(MAX_LINE_BYTES + 1)
                if not raw:
                    break
                if len(raw) > MAX_LINE_BYTES or not raw.endswith(b"\n"):
                    raise EventError("Oversized or incomplete ledger line.")
                try:
                    event = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise EventError("Invalid ledger JSON; repair required.") from None
                validate(event)
                if task_id is None or event.get("task_id") == task_id:
                    result.append(event)
        return result

    def cursor(self) -> dict[str, Any]:
        regular_path(self.path)
        return {"identity": identity(self.path), "offset": self.path.stat().st_size if self.path.exists() else 0}

    def prune_history(self) -> None:
        history = self.directory / "history"
        if not history.exists():
            return
        private_dir(history)
        candidates = [path for path in history.iterdir() if path.is_file() and not path.is_symlink()
                      and ((path.name.startswith("t_") and path.suffix == ".json")
                           or (path.name.startswith("events-") and path.suffix == ".jsonl"))]
        candidates.sort(key=lambda path: (path.stat().st_mtime_ns, path.name))
        for path in candidates[:-self.config["history"]["keep"]]:
            path.unlink()
