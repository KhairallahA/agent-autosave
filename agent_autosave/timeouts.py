"""The bound on slow external calls (git, an agent's `--version`).

Hooks bound these calls so an agent never waits on a slow repository. On a
loaded machine the bound changes what a command prints (no version, no git
facts, an errors.log entry), so the test suite and the docs and example
builders set LENIENT, in process or through tests/tools/lenient_cli.py.
Nothing in the product sets it, and no environment variable reaches it.
"""
from __future__ import annotations

LENIENT = False  # tests and scripts/build-*.py only
LENIENT_SECONDS = 60.0


def bound(seconds: float) -> float:
    """`seconds`, or LENIENT_SECONDS when LENIENT is set."""
    return LENIENT_SECONDS if LENIENT else seconds
