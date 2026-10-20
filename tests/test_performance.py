import pytest

pytestmark = pytest.mark.slow
import math
import time

from agent_autosave.compiler import compile_events
from agent_autosave.render import render
from tests.helpers import event, load_payload, tool


def percentile95(operation, repeats=20):
    operation()  # warm imports and interpreter caches
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - start) * 1000)
    return sorted(samples)[math.ceil(.95 * len(samples)) - 1]


def test_compile_1000_events_under_200_ms():
    events = [event("prompt", text="Implement reliable portable task autosaves.")]
    for index in range(1, 1000):
        if index % 25 == 0:
            events.append(event("turn_end", index))
        else:
            events.append(tool(index, summary="pytest tests/test_auth.py", exit=1 if index % 17 == 0 else 0,
                               output_tail="\n".join("AssertionError: expected a session" for _ in range(20))))
    measured = percentile95(lambda: compile_events(events))
    print("\ncompile 1,000 events p95 (20 runs): {:.2f} ms".format(measured))
    assert measured < 200


def test_render_under_20_ms():
    save = compile_events([event("prompt", text="Implement reliable portable task autosaves.")] +
                          [tool(index, exit=1, output_tail="failure\n" * 20) for index in range(1, 21)])
    measured = percentile95(lambda: render(save))
    print("\nrender p95 (20 runs): {:.2f} ms".format(measured))
    assert measured < 20


def test_append_under_50_ms(tmp_path):
    from agent_autosave.ledger import Ledger
    ledger = Ledger(tmp_path)
    measured = percentile95(lambda: ledger.append(event("turn_end")))
    print("\nappend p95 (20 runs): {:.2f} ms".format(measured))
    assert measured < 50


# Hook timings live here, not next to the functional tests: check.sh runs this
# file serially after the parallel suites, so load from other tests cannot
# turn a budget into a flaky failure.

def _hook_project(tmp_path):
    import subprocess
    from agent_autosave.installer.install import install
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    install(root, command="agent-autosave")
    return root


def _payload(name, project):
    data = load_payload("claude-code", name)
    data["cwd"], data["session_id"] = str(project), "perf"
    return data


def test_in_process_hook_work_p95_under_300_ms(tmp_path):
    import json
    from agent_autosave.capture import handle
    project = _hook_project(tmp_path)
    samples = []
    for name, kind in [("PostToolUse-Bash", "PostToolUse"), ("Stop", "Stop")] * 10:
        raw = json.dumps(_payload(name, project)).encode()
        start = time.perf_counter()
        handle("claude-code", kind, raw, env={})
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    assert samples[int(0.95 * len(samples)) - 1] < 300
    assert not (project / ".autosave/errors.log").exists()


def _run_hook(project, kind, raw, sync=False):
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_AUTOSAVE_")}
    if sync:
        env["AGENT_AUTOSAVE_SESSION_END"] = "sync"
    start = time.perf_counter()
    result = subprocess.run([sys.executable, "-m", "agent_autosave", "hook", "claude-code", kind], input=raw,
                            cwd=str(project), env=env, capture_output=True, timeout=10)
    return result, (time.perf_counter() - start) * 1000


def test_10mb_payload_hook_exits_within_1_s(tmp_path):
    project = _hook_project(tmp_path)
    result, elapsed = _run_hook(project, "Stop", b"x" * (10 * 1024 * 1024))
    assert result.returncode == 0 and result.stdout == b"" and elapsed < 1000


def test_session_end_settles_the_save_inside_the_hook_within_budget(tmp_path):
    """SAVE.md must say paused the moment the SessionEnd hook returns
    (Codex kills the hook's process group), still within 300 ms."""
    import json
    from agent_autosave.capture import handle
    project = _hook_project(tmp_path)
    prompt = json.dumps(_payload("UserPromptSubmit", project)).encode()
    end = json.dumps(_payload("SessionEnd", project)).encode()
    in_process, whole = [], []
    for _ in range(10):
        handle("claude-code", "UserPromptSubmit", prompt, env={})
        start = time.perf_counter()
        handle("claude-code", "SessionEnd", end, env={"AGENT_AUTOSAVE_SESSION_END": "sync"})
        in_process.append((time.perf_counter() - start) * 1000)
        assert json.loads((project / ".autosave/save.json").read_text())["status"] == "paused"
        _run_hook(project, "UserPromptSubmit", prompt)
        result, elapsed = _run_hook(project, "SessionEnd", end)
        whole.append(elapsed)
        assert result.returncode == 0
        # No waiting: the views are already settled when the process exits.
        assert json.loads((project / ".autosave/save.json").read_text())["status"] == "paused"
        assert "**Status:** paused" in (project / ".autosave/SAVE.md").read_text()
    in_process.sort()
    whole.sort()
    p95 = lambda xs: xs[int(0.95 * len(xs)) - 1]
    print("\nSessionEnd p95: in-process {:.1f} ms, whole process {:.0f} ms".format(p95(in_process), p95(whole)))
    assert p95(in_process) < 300
    import os
    if os.environ.get("AGENT_AUTOSAVE_SKIP_STARTUP_BUDGET") != "1":
        assert p95(whole) < 300
    assert not (project / ".autosave/errors.log").exists()


def test_codex_in_process_hook_work_p95_under_300_ms(tmp_path, monkeypatch):
    """Worst case included: a Bash call whose exit code never shows up in the
    rollout (the hook retries for at most 120 ms, then records exit 0)."""
    import json
    import shutil
    import subprocess
    from pathlib import Path
    from agent_autosave.capture import handle
    from agent_autosave.installer.install import install
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    install(root, agents=("codex",), command="agent-autosave")
    fixtures = Path(__file__).parent / "fixtures/codex"
    transcript = tmp_path / "rollout-2026-10-15T10-00-00-perf.jsonl"
    shutil.copy(fixtures / "rollouts/rollout-2026-10-15T10-00-00-normal.jsonl", transcript)
    samples = []
    for name, kind, call in [("PostToolUse-Bash-fail", "PostToolUse", "call_fail"),
                             ("PostToolUse-Bash", "PostToolUse", "call_missing"), ("Stop", "Stop", None)] * 7:
        data = load_payload("codex", name)
        data.update(cwd=str(root), session_id="perf", transcript_path=str(transcript))
        if call:
            data["tool_use_id"] = call
        start = time.perf_counter()
        handle("codex", kind, json.dumps(data).encode(), env={})
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    assert samples[int(0.95 * len(samples)) - 1] < 300
    assert not (root / ".autosave/errors.log").exists()


def _memory_project(tmp_path, entries=60, pages=2000):
    """A repo with `entries` accepted memory entries and `pages` journal pages, indexed."""
    import random
    from datetime import date
    from agent_autosave import config as config_module, journal, memory
    from agent_autosave.memory_sync import rebuild_index
    project = _hook_project(tmp_path)
    config = config_module.load(project)
    words = ("billing export stream rows parser cache sqlite redis worker queue retry token session auth "
             "migration schema index deploy docker config logging metrics api client server timeout").split()
    pick = random.Random(8)
    document = memory.Document.parse(memory.TEMPLATE)
    for number in range(entries):
        document.add(memory.TYPES[number % 4], " ".join(pick.sample(words, 8)) + " #{}".format(number),
                     source={"agent": "codex", "task_id": None}, today=date(2026, 10, 1))
    memory.write(project, config, document, date(2026, 10, 1))
    for number in range(pages):
        save = {"task_id": "t_2026{:02d}{:02d}_{:06d}000000".format(1 + number % 9, 1 + number % 28, number),
                "goal": " ".join(pick.sample(words, 6)), "started": "2026-{:02d}-{:02d}T10:00:00Z".format(
                    1 + number % 9, 1 + number % 28), "status": "done", "decisions": [" ".join(pick.sample(words, 10))],
                "notes": [], "plan": [], "checks": [], "files_touched": [], "handoffs": []}
        path = project / ".autosave" / journal.relative_path(save)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(journal.render_page(save, [], {}), encoding="utf-8")
    rebuild_index(project, config, date(2026, 10, 1), full=True)
    return project


def test_prompt_hook_with_recall_p95_under_300_ms_at_60_entries_and_2000_pages(tmp_path):
    """The whole hook process (interpreter start included), as the agent runs it."""
    import json
    project = _memory_project(tmp_path)
    samples = []
    for number in range(20):
        payload = _payload("UserPromptSubmit", project)
        payload.update(prompt="Fix the billing export stream retry timeout in the worker queue {}".format(number),
                       session_id="perf-{}".format(number))
        result, elapsed = _run_hook(project, "UserPromptSubmit", json.dumps(payload).encode())
        assert result.returncode == 0
        samples.append(elapsed)
    samples.sort()
    p95 = samples[int(0.95 * len(samples)) - 1]
    print("\nUserPromptSubmit hook with recall, 60 entries + 2,000 pages, p95 (20 runs): {:.1f} ms".format(p95))
    assert b"[agent-autosave memory]" in result.stdout
    assert p95 < 300
    assert not (project / ".autosave/errors.log").exists()


def test_statusline_under_50_ms(tmp_path):
    """Render only: the whole-process budget is in scripts/bench.py."""
    from agent_autosave import statusline
    from agent_autosave.installer.install import install
    import json
    install(tmp_path, ["claude-code"], command="agent-autosave", statusline="off")
    (tmp_path / ".autosave/save.json").write_text(json.dumps({
        "v": 1, "updated": "2026-10-19T10:59:56Z",
        "plan": [{"state": "done"}, {"state": "todo"}],
        "failing": {"command": "pytest", "exit": 1, "tail": ""}}))
    raw = json.dumps({"session_id": "s", "workspace": {"project_dir": str(tmp_path)},
                      "context_window": {"used_percentage": 41},
                      "rate_limits": {"five_hour": {"used_percentage": 72, "resets_at": 1792436400}}}).encode()
    measured = percentile95(lambda: statusline.render(raw))
    print("\nstatusline render p95 (20 runs): {:.2f} ms".format(measured))
    assert measured < 50
