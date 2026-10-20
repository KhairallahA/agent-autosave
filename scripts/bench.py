"""Measure the performance budgets (docs/contracts.md) as p95 over warm runs.

python scripts/bench.py          # one summary line; exit 1 if a budget fails
Whole-process hook time includes interpreter start-up. It is enforced here
(the developer's machine) unless AGENT_AUTOSAVE_SKIP_STARTUP_BUDGET=1.
"""
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/claude-code/payloads"


def p95(operation, repeats=20):
    operation()
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - start) * 1000)
    return sorted(samples)[math.ceil(.95 * len(samples)) - 1]


def events(n=1000):
    def ev(kind, i, **extra):
        ts = (datetime(2026, 10, 14, 12, tzinfo=timezone.utc) + timedelta(seconds=i)).isoformat().replace("+00:00", "Z")
        return dict(v=1, ts=ts, agent="claude-code", session="s", turn=i + 1, kind=kind, **extra)
    rows = [ev("prompt", 0, text="Implement reliable portable task autosaves.")]
    for i in range(1, n):
        if i % 25 == 0:
            rows.append(ev("turn_end", i))
        else:
            rows.append(ev("tool", i, tool="Bash", summary="pytest tests/test_auth.py", files=[],
                           exit=1 if i % 17 == 0 else 0,
                           output_tail="\n".join("AssertionError: expected a session" for _ in range(20))))
    return rows


def hook_payload(name, project):
    data = json.loads((FIXTURES / (name + ".json")).read_text())["payload"]
    data["cwd"], data["session_id"] = str(project), "bench"
    return json.dumps(data).encode()


def background_pids(project):
    """Detached `agent_autosave.background <project>` processes still running."""
    out = subprocess.run(["ps", "-A", "-o", "pid=,command="], capture_output=True, text=True, timeout=10).stdout
    needle = "agent_autosave.background " + str(project)
    return [int(line.split(None, 1)[0]) for line in out.splitlines()
            if needle in line and len(line.split(None, 1)) == 2]


def wait_for_background(project, timeout=60.0):
    """SessionEnd hooks spawn detached rebuilds; wait for them before the
    temp project is deleted (else rmtree races their writes)."""
    deadline = time.monotonic() + timeout
    while background_pids(project):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)
    return True


def main():
    from agent_autosave.capture import handle
    from agent_autosave.compiler import compile_events
    from agent_autosave.installer.install import install
    from agent_autosave.ledger import Ledger
    from agent_autosave.render import render
    rows = events()
    results = {"compile_1000": p95(lambda: compile_events(rows))}
    save = compile_events(rows[:40])
    results["render"] = p95(lambda: render(save))
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "repo"
        project.mkdir()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        install(project, command="agent-autosave")
        ledger = Ledger(project)
        results["append"] = p95(lambda: ledger.append(rows[25]))
        stop, tool = hook_payload("Stop", project), hook_payload("PostToolUse-Bash", project)
        results["hook_in_process"] = p95(lambda: (handle("claude-code", "PostToolUse", tool, env={}),
                                                  handle("claude-code", "Stop", stop, env={})), 10)
        end = hook_payload("SessionEnd", project)
        results["session_end_in_process"] = p95(lambda: handle("claude-code", "SessionEnd", end,
                                                               env={"AGENT_AUTOSAVE_SESSION_END": "sync"}))
        env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_AUTOSAVE_RECORD")}
        command = [sys.executable, "-m", "agent_autosave", "hook", "claude-code", "Stop"]
        results["hook_process"] = p95(lambda: subprocess.run(command, input=stop, cwd=str(project), env=env,
                                                             capture_output=True, timeout=10))
        end_command = command[:-1] + ["SessionEnd"]
        results["session_end_process"] = p95(lambda: subprocess.run(end_command, input=end, cwd=str(project),
                                                                     env=env, capture_output=True, timeout=10))
        if os.name != "nt":
            wait_for_background(project)
        if (project / ".autosave/errors.log").exists():
            print("bench: hook logged errors: " + (project / ".autosave/errors.log").read_text()[:300])
            return 1
        from agent_autosave import statusline as statusline_mod
        (project / ".autosave/save.json").write_text(json.dumps({
            "v": 1, "updated": "2026-10-19T10:59:56Z",
            "plan": [{"state": "done"}, {"state": "todo"}],
            "failing": {"command": "pytest", "exit": 1, "tail": ""}}))
        status_raw = json.dumps({"session_id": "s", "workspace": {"project_dir": str(project)},
                                 "context_window": {"used_percentage": 41},
                                 "rate_limits": {"five_hour": {"used_percentage": 72,
                                                                "resets_at": 1792436400}}}).encode()
        results["statusline_render"] = p95(lambda: statusline_mod.render(status_raw))
        status_cmd = [sys.executable, "-m", "agent_autosave", "statusline"]
        results["statusline_process"] = p95(lambda: subprocess.run(status_cmd, input=status_raw, cwd=str(project),
                                                                    env=env, capture_output=True, timeout=5))
    budgets = {"compile_1000": 200, "render": 20, "append": 50, "hook_in_process": 300, "hook_process": 300,
               "session_end_in_process": 300, "session_end_process": 300,
               "statusline_render": 50, "statusline_process": 50}
    if os.environ.get("AGENT_AUTOSAVE_SKIP_STARTUP_BUDGET") == "1":
        budgets.pop("hook_process")
        budgets.pop("session_end_process")
    over = [k for k, limit in budgets.items() if results[k] >= limit]
    print("compile 1,000 events {compile_1000:.1f} ms | render {render:.2f} ms | append {append:.2f} ms | "
          "hook in-process (PostToolUse+Stop) {hook_in_process:.1f} ms | "
          "hook whole process incl. start-up {hook_process:.1f} ms | "
          "SessionEnd in-process {session_end_in_process:.1f} ms, whole process {session_end_process:.1f} ms | "
          "statusline render {statusline_render:.2f} ms, whole process {statusline_process:.1f} ms".format(**results)
          + ("" if not over else " | OVER BUDGET: " + ", ".join(over)))
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())
