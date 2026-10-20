"""`agent-autosave` for the test suite and the docs tour: the CLI with
lenient timeouts, so a slow git or `--version` on a loaded machine never
changes what a test or a docs block sees (agent_autosave/timeouts.py).

    python tests/tools/lenient_cli.py <arguments>

The product has no switch for this, not even an environment variable: the
setting is a module attribute that only this wrapper (and conftest, for
in-process tests) turns on.
"""
import sys

from agent_autosave import timeouts
from agent_autosave.cli import main

if __name__ == "__main__":
    timeouts.LENIENT = True
    sys.exit(main())
