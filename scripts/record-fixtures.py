"""Record actual Claude hook stdin in a new scratch repo, never invent payloads.

python scripts/record-fixtures.py --setup /tmp/new-scratch --output /tmp/recordings
AGENT_AUTOSAVE_RECORD=1 claude ...  # run from that scratch repository
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent_autosave.config import resolve
from agent_autosave.redact import Redactor
from agent_autosave.storage import atomic_write, log_error, private_dir

EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure",
          "Stop", "StopFailure", "PreCompact", "SessionEnd")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", default="unknown")
    args = parser.parse_args()
    if args.setup:
        project = args.setup.resolve()
        if project.exists():
            raise ValueError("Use a new, nonexistent scratch directory.")
        project.mkdir(mode=0o700, parents=True)
        subprocess.run(["git", "init", str(project)], check=True, stdout=subprocess.DEVNULL)
        command = [str(Path(__file__).resolve()), "--output", str(args.output.resolve()), "--version", args.version]
        hooks = {name: [{"hooks": [{"type": "command", "command": sys.executable, "args": command, "timeout": 2}]}]
                 for name in EVENTS}
        atomic_write(project / ".claude/settings.json", {"hooks": hooks})
        (project / ".gitignore").write_text(".autosave/\n", encoding="utf-8")
        print(project)
        return 0
    if os.environ.get("AGENT_AUTOSAVE_RECORD") != "1":
        return 0
    try:
        payload = json.loads(sys.stdin.buffer.read(2 * 1024 * 1024 + 1))
        if not isinstance(payload, dict) or payload.get("hook_event_name") not in EVENTS:
            raise ValueError("Unexpected hook payload.")
        project = Path.cwd()
        redactor = Redactor.from_project(project, resolve())
        # Replace local home prefix in fixtures while preserving vendor shapes.
        redactor = Redactor(literals=redactor.literals + (str(Path.home()),))
        output = private_dir(args.output)
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        record = {"v": 1, "recorded_at": stamp, "claude_version": args.version,
                  "provenance": "Claude Code hook stdin; secrets and home prefix redacted", "payload": payload}
        name = payload["hook_event_name"] + "-" + uuid.uuid4().hex + ".json"
        atomic_write(output / name, record, redactor=redactor)
    except Exception as error:
        log_error(Path.cwd() / ".autosave", "fixture.record", error)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
