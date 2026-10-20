"""Atomic writes for the wizard: the new file appears whole or not at all,
and a file being replaced is first copied to <name>.bak."""
from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any


def backup(path: Path) -> Path | None:
    """Copy `path` to `path.bak` (metadata kept); None if there was nothing to keep."""
    path = Path(path)
    if not path.is_file():
        return None
    target = path.with_name(path.name + ".bak")
    temporary = target.with_name(target.name + ".tmp")
    shutil.copy2(str(path), str(temporary))
    temporary.replace(target)
    return target


def write_json(path: Path, value: Any) -> Path | None:
    """Write JSON atomically, backing up a different existing file; returns the backup path."""
    from ..redact import Redactor
    from ..storage import atomic_write, read_json
    path = Path(path)
    try:
        if read_json(path) == value:
            return None  # unchanged: no write, no backup
    except (OSError, ValueError):
        pass
    saved = backup(path)
    atomic_write(path, value, redactor=Redactor())
    return saved
