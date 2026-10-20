"""`agent-autosave doctor`: audit every installed adapter."""
from __future__ import annotations

import argparse

from ..contract import ExitCode, Outcome, success
from . import common


def run(args: argparse.Namespace) -> Outcome:
    from ..installer import doctor, registry
    project = common.find_project()
    common.wait_for_settle(project / ".autosave")
    pruned = registry.prune() if args.prune else []
    checks = doctor.audit(project)
    red = any(c["level"] == "red" for c in checks)
    from ..ui import style
    text = doctor.render(checks, style=style())
    if args.prune:
        text += "\n  Pruned {} stale registry entr{}.".format(len(pruned), "y" if len(pruned) == 1 else "ies")
    data = {"checks": checks, "pruned": len(pruned), "healthy": not red}
    return success(data), (ExitCode.FAILED if red else ExitCode.SUCCESS), text
