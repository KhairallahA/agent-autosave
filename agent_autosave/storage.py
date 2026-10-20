"""Private atomic snapshots and bounded redacted diagnostics shared by the core."""
from __future__ import annotations

import json
import os
from pathlib import Path

# typing, redact and tempfile are imported only where a write needs them: the
# status line only reads views here, and it runs after every Claude Code message.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Any

    from .redact import Redactor


def _redactor() -> Redactor:
    from .redact import Redactor
    return Redactor()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def private_dir(path: Path) -> Path:
    path = Path(path)
    if path.is_symlink():
        raise OSError("Refusing a symlink state directory.")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        path.chmod(0o700)
    return path


def regular_path(path: Path) -> Path:
    path = Path(path)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise OSError("Expected a regular autosave file.")
    return path


def atomic_write(path: Path, value: Any, *, redactor: Redactor | None = None, durable: bool = True) -> None:
    """Redact before even creating a temporary file; replace on the same volume.

    durable=False skips fsync for derived views (save.json, SAVE.md, adapter
    state) that are always rebuildable from the ledger: on the box an fsync
    occasionally stalls 30-240 ms in the ext4 journal, which alone can exceed
    the hook budget. The rename still keeps readers from seeing a torn file.
    """
    path = regular_path(path)
    private_dir(path.parent)
    redactor = redactor or _redactor()
    clean = redactor.value(value)
    payload = clean.encode("utf-8") if isinstance(clean, str) else json_bytes(clean)
    try:
        # Unchanged content: skip the write and its fsync (hooks are budgeted).
        if path.stat().st_size == len(payload) and path.read_bytes() == payload:
            return
    except OSError:
        pass
    import tempfile
    fd, temporary = tempfile.mkstemp(prefix=".autosave-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            if durable:
                os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        # Cleanup also runs after failed replacement. No raw data entered tmp.
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_view(path: Path) -> dict[str, Any] | None:
    """read_json for a rebuildable view: unreadable (e.g. after power loss
    without fsync) counts as missing, and the caller rebuilds it."""
    try:
        return read_json(path)
    except (OSError, ValueError):
        return None


def read_json(path: Path) -> Any:
    path = regular_path(path)
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as stream:
        result = json.load(stream)
    if not isinstance(result, dict) or type(result.get("v")) is not int or result["v"] != 1:
        raise ValueError("Unsupported or invalid autosave document version.")
    return result


def log_error(directory: Path, operation: str, error: BaseException, *, redactor: Redactor | None = None) -> None:
    """Best effort: logging must never replace the original failure.

    Diagnostic writes use a separate bounded lock. If the disk or lock is itself
    unavailable, no logger can guarantee a write; the original error still rises.
    """
    from .locking import exclusive
    try:
        directory = private_dir(directory)
        with exclusive(directory / ".errors.lock", timeout=0.005):
            path = regular_path(directory / "errors.log")
            prior = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
            sanitizer = redactor or _redactor()
            message = json.dumps(sanitizer.value({"v": 1, "operation": operation,
                                  "type": type(error).__name__, "message": str(error)}), ensure_ascii=True)
            clean = sanitizer.text(prior) + message + "\n"
            # ASCII diagnostic encoding means character and byte caps agree.
            clean = clean.encode("ascii", "backslashreplace")[-1024 * 1024:].decode("ascii")
            atomic_write(path, clean, redactor=redactor)
    except Exception:
        # The only intentionally unlogged exception is failure of logging itself.
        return
