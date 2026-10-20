"""`agent-autosave record on|off`: keep raw hook payloads for fixtures."""
from __future__ import annotations

import argparse
from pathlib import Path

from ..contract import ExitCode, Outcome, fail, success
from . import common


def run(args: argparse.Namespace) -> Outcome:
    """Toggle fixture recording in .autosave/config.json (read by every hook)."""
    from ..config import ConfigError, resolve
    from ..redact import Redactor
    from ..storage import atomic_write, read_json
    project = common.find_project()
    path = project / ".autosave/config.json"
    try:
        current = read_json(path) or {}
        record = dict(current.get("record") or {}, enabled=args.state == "on")
        if args.state == "on":
            record["dir"] = str(Path(args.dir).expanduser().resolve()) if args.dir else ""
            record["agent_version"] = args.agent_version or ""
        resolve(dict(current, record=record))
    except (ConfigError, ValueError, OSError):
        return fail("invalid_config", "Could not update .autosave/config.json.",
                    "Check it is valid agent-autosave configuration.")
    atomic_write(path, dict(current, record=record), redactor=Redactor())
    where = record.get("dir") or str(project / ".autosave/recordings")
    text = ("Recording on: raw hook payloads (secrets redacted, home as ~) go to {}/<agent>/.".format(where)
            if record["enabled"] else "Recording off.")
    return success({"record": record}), ExitCode.SUCCESS, text
