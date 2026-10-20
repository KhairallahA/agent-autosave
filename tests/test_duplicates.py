"""SAVE.md repeated steering/last actions in a real session.

These tests pin down where a repeat can and cannot come from: the compiler is
a pure fold over the ledger, so a repeat in the save needs a repeat (ignoring
ts/turn/task_id) in the ledger slice of the current task.
"""
import json
import os
from pathlib import Path
import subprocess
import time

import pytest

from agent_autosave.compiler import compile_events

from tests.helpers import LENIENT_CLI

FIXTURES = Path(__file__).resolve().parent / "fixtures/claude-code"
SESSION = FIXTURES / "sessions/lifecycle"


def recorded(kind, contains=""):
    for path in sorted(SESSION.glob(kind + "-*.json")):
        data = json.loads(path.read_text())["payload"]
        if contains in json.dumps(data):
            return data
    raise LookupError(kind + " " + contains)


def lifecycle_flow():
    """The recorded session: two prompts (with the plan CLI
    chained into the failing command), /compact, /exit, then a new session."""
    first, second = "S-lifecycle-1", "S-lifecycle-2"
    fail = recorded("PostToolUseFailure")
    fail = dict(fail, tool_input=dict(fail["tool_input"],
                                     command='agent-autosave plan done 2; python3 -c "raise SystemExit(7)"'))
    return [
        (first, dict(recorded("SessionStart", '"startup"'))),
        (first, recorded("UserPromptSubmit", "3-step plan")),
        (first, recorded("PostToolUse", '"Write"')),
        (first, recorded("Stop", "finishes step 1")),
        (first, recorded("UserPromptSubmit", "Run python3 hello.py")),
        (first, recorded("PostToolUse", "python3 hello.py")),
        (first, fail),
        (first, recorded("Stop", "Both commands ran")),
        (first, recorded("PreCompact")),
        (first, recorded("SessionStart", '"compact"')),
        (first, dict(recorded("SessionEnd"), reason="prompt_input_exit")),
        (second, dict(recorded("SessionStart", '"startup"'))),
        (second, recorded("UserPromptSubmit", "What did the agent-autosave")),
        (second, recorded("Stop", "At session start")),
        (second, dict(recorded("SessionEnd"), reason="prompt_input_exit")),
    ]


def semantic(row):
    return json.dumps({k: v for k, v in row.items() if k not in ("ts", "turn", "task_id")}, sort_keys=True)


def assert_no_repeats(save):
    assert len(save["steering"]) == len(set(save["steering"])), save["steering"]
    assert len(save["last_actions"]) == len(set(save["last_actions"])), save["last_actions"]


@pytest.fixture
def project(tmp_path):
    from agent_autosave.installer.install import install
    root = tmp_path / "scratch"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    install(root, command="agent-autosave")
    return root


def test_lifecycle_flow_through_real_hook_processes_has_no_repeats(project):
    """Real `agent-autosave hook` processes, detached SessionEnd rebuild ON
    (as on the Mac), plus status/show which recompile."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_AUTOSAVE_")}
    env.pop("PYTHONPATH", None)
    for session, data in lifecycle_flow():
        data = dict(data, cwd=str(project), session_id=session)
        if "file_path" in data.get("tool_input", {}):
            data["tool_input"] = dict(data["tool_input"], file_path=str(project / "hello.py"))
        result = subprocess.run([*LENIENT_CLI, "hook", "claude-code",
                                 data["hook_event_name"]], input=json.dumps(data).encode(), cwd=str(project),
                                env=env, capture_output=True, timeout=20)
        assert result.returncode == 0
    time.sleep(0.5)  # let the last detached rebuild finish
    for command in ("status", "show"):
        subprocess.run([*LENIENT_CLI, command, "--json"], cwd=str(project), env=env,
                       capture_output=True, timeout=20)
    deadline = time.time() + 10
    while time.time() < deadline and json.loads((project / ".autosave/save.json").read_text())["status"] != "paused":
        time.sleep(0.05)
    save = json.loads((project / ".autosave/save.json").read_text())
    rows = [json.loads(l) for l in (project / ".autosave/events.jsonl").read_text().splitlines()]
    assert len({semantic(r) for r in rows}) == len(rows)  # ledger: no repeats
    assert_no_repeats(save)
    assert save["steering"] == ['Run python3 hello.py. Then run python3 -c "raise SystemExit(7)" and stop there; '
                                "do not fix it.", "What did the agent-autosave context at session start tell you?"]
    assert save["last_actions"] == ['Ran python3 -c "raise SystemExit(7)" (exit 7)', "Ran python3 hello.py (exit 0)",
                                    "Wrote hello.py"]
    md = (project / ".autosave/SAVE.md").read_text()
    assert md.count("Ran python3 hello.py") == 1
    assert not (project / ".autosave/errors.log").exists()


def test_save_repeats_exactly_when_the_ledger_repeats():
    """What the real SAVE.md showed is reproduced only by a ledger where the
    second prompt and its two commands occur twice (different ts)."""
    def ev(i, kind, **extra):
        return dict(v=1, ts="2026-10-15T09:00:%02dZ" % i, agent="claude-code", session="s", turn=1, kind=kind, **extra)
    once = [ev(0, "prompt", text="Use your todo tool to make a 3-step plan, then do step 1."),
            ev(1, "tool", tool="Write", summary="Wrote hello.py", files=["hello.py"]),
            ev(2, "prompt", text='Run python3 hello.py. Then run python3 -c "raise SystemExit(7)".'),
            ev(3, "tool", tool="Bash", summary="python3 hello.py", files=[], exit=0),
            ev(4, "tool", tool="Bash", summary='python3 -c "raise SystemExit(7)"', files=[], exit=7)]
    assert_no_repeats(compile_events(once))
    twice = once + [dict(e, ts="2026-10-15T09:01:%02dZ" % i) for i, e in enumerate(once[2:])]
    save = compile_events(twice)
    assert save["last_actions"] == ['Ran python3 -c "raise SystemExit(7)" (exit 7)', "Ran python3 hello.py (exit 0)",
                                    'Ran python3 -c "raise SystemExit(7)" (exit 7)', "Ran python3 hello.py (exit 0)",
                                    "Wrote hello.py"]  # exactly the real "Where it stopped"
    assert save["steering"].count(once[2]["text"]) == 1  # identical consecutive prompts collapse
