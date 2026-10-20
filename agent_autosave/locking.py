"""Bounded advisory locks on a stable sidecar, including native Windows."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import time
from typing import Iterator

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class LockTimeout(TimeoutError):
    pass


@contextmanager
def exclusive(path: str | Path, *, timeout: float = 0.05) -> Iterator[None]:
    """Use nonblocking OS calls, retrying only until a monotonic deadline."""
    path = Path(path)
    if path.is_symlink():
        raise OSError("Refusing a symlink lock file.")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags, 0o600)
    acquired = False
    deadline = time.monotonic() + max(0, timeout)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        while True:
            try:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockTimeout("Autosave writer lock is busy.") from None
                time.sleep(min(0.001, remaining))
        yield
    finally:
        try:
            if acquired:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
