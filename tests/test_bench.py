"""bench/handoff: the 10 tasks are sound, and the runner measures both arms
end to end with the fake agent (offline, no real agent)."""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "bench/handoff"


def _load(name):
    sys.path.insert(0, str(BENCH))
    spec = importlib.util.spec_from_file_location("bench_" + name, str(BENCH / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tasks = _load("tasks")


def test_ten_tasks_cover_bug_fixes_features_and_a_refactor():
    assert len(tasks.TASKS) == 10 and len({t["name"] for t in tasks.TASKS}) == 10
    assert {t["kind"] for t in tasks.TASKS} == {"bug fix", "feature", "refactor"}
    for item in tasks.TASKS:
        files = [s["file"] for s in item["steps"]]
        assert len(files) == len(set(files)) >= 2, item["name"]  # a step's files are its own
        assert 0 < item["k"] < 3 * len(item["steps"]) + 2, item["name"]  # stops mid-task


@pytest.mark.slow
@pytest.mark.parametrize("item", tasks.TASKS, ids=lambda t: t["name"])
def test_each_step_test_fails_before_and_passes_after_its_solution(tmp_path, item):
    repo = tasks.generate(item, tmp_path / "repo")
    assert tasks.finished_steps(repo, item) == [] and not tasks.accepted(repo)
    for number, step in enumerate(item["steps"], 1):
        (repo / step["file"]).write_text(step["solution"], encoding="utf-8")
        assert number in tasks.finished_steps(repo, item)
    assert tasks.accepted(repo)


def test_measure_counts_redone_edits_and_repeated_passing_commands(tmp_path):
    run = _load("run")
    item = tasks.by_name("add-word-count")
    repo = tasks.generate(item, tmp_path / "repo")
    rows = [{"agent": "codex", "kind": "session_start", "ts": "2026-10-19T10:00:00Z", "transcript": None},
            {"agent": "codex", "kind": "tool", "ts": "2026-10-19T10:00:01Z", "files": ["app/tokens.py"], "summary": "Write"},
            {"agent": "codex", "kind": "tool", "ts": "2026-10-19T10:00:02Z", "files": ["app/count.py"], "summary": "Write"},
            {"agent": "codex", "kind": "tool", "ts": "2026-10-19T10:00:03Z", "exit": 0, "summary": "pytest -q tests/test_tokens.py"},
            {"agent": "codex", "kind": "tool", "ts": "2026-10-19T10:00:04Z", "exit": 0, "summary": "agent-autosave plan done 1"},
            {"agent": "claude-code", "kind": "tool", "ts": "2026-10-19T10:00:05Z", "files": ["app/tokens.py"]},
            {"agent": "codex", "kind": "tool", "ts": "2026-10-19T09:00:00Z", "files": ["app/tokens.py"]}]
    (repo / ".autosave").mkdir()
    (repo / ".autosave/events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows) + '{"cut', encoding="utf-8")
    got = run.measure(repo, item, "codex", "2026-10-19T09:30:00Z", [1],
                      {"pytest -q tests/test_tokens.py", "agent-autosave plan done 1"}, 3.25)
    assert got == {"finished": False, "redone_edits": 1, "repeated_commands": 1, "tool_events": 4,
                   "wall_s": 3.2, "tokens": None}


def test_tokens_read_claude_usage_rows_and_the_codex_rollout_total(tmp_path):
    run = _load("run")
    claude = tmp_path / "c.jsonl"
    claude.write_text('{"type":"assistant","message":{"usage":{"input_tokens":10,"output_tokens":2,'
                      '"cache_read_input_tokens":99}}}\n{"type":"user","message":{}}\n', encoding="utf-8")
    assert run.tokens("claude-code", str(claude)) == 12
    rollout = ROOT / "tests/fixtures/codex/rollouts/rollout-real-session.jsonl"
    assert run.tokens("codex", str(rollout)) == 153214
    assert run.tokens("codex", None) is None and run.tokens("claude-code", str(tmp_path / "none.jsonl")) is None


def test_fake_report_is_labelled_and_named_so_it_is_never_taken_for_results(tmp_path):
    run = _load("run")
    row = {"finished": True, "redone_edits": 0, "repeated_commands": 0, "tool_events": 3, "wall_s": 1.0,
           "tokens": None, "timed_out": False}
    runs = [{"task": "t", "kind": "bug fix", "k": 3, "a": {"finished_steps": [1], "steps": 2},
             "arms": {"autosave": dict(row), "control": dict(row, finished=False)}}]
    report = {"v": 1, "date": "2026-10-19", "mode": "fake", "agents": {"a": "x", "b": "y"},
              "runs": runs, "summary": run.summary(runs)}
    path = run.write(report, tmp_path)
    assert path.name == "2026-10-19-fake.md" and (tmp_path / "2026-10-19-fake.json").exists()
    text = path.read_text(encoding="utf-8")
    assert "it measures nothing" in text and "| autosave | 1/1 |" in text and "| control | 0/1 |" in text


@pytest.mark.slow
@pytest.mark.skipif(os.name == "nt", reason="process groups")
def test_fake_run_kills_a_and_hands_off_only_in_the_autosave_arm(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    marker = tmp_path / "child.pid"
    monkeypatch.setenv("BENCH_FAKE_CHILD_PID", str(marker))
    run = _load("run")
    assert run.main(["--fake", "--tasks", "add-word-count", "--out", str(tmp_path / "out"), "--timeout", "120"]) == 0
    [found] = (tmp_path / "out").glob("*-fake.json")
    [result] = json.loads(found.read_text(encoding="utf-8"))["runs"]
    assert result["a"] == {"stopped": "killed", "tool_events": 5, "finished_steps": [1], "steps": 3}
    child = int(marker.read_text(encoding="utf-8"))
    with pytest.raises(OSError):  # the killed agent's child went with its process group
        for _ in range(50):
            os.kill(child, 0)
            __import__("time").sleep(0.1)
    autosave, control = result["arms"]["autosave"], result["arms"]["control"]
    assert autosave["finished"] and control["finished"]
    assert autosave["redone_edits"] == 0 and control["redone_edits"] == 1
    assert control["repeated_commands"] == 1 and autosave["tokens"] and control["tokens"]


def test_control_arm_has_silent_hooks_and_no_instruction_block(tmp_path):
    from agent_autosave.installer.install import install
    run = _load("run")
    repo = tasks.generate(tasks.by_name("split-utils"), tmp_path / "repo")
    install(repo, ["claude-code", "codex"], command="agent-autosave", statusline="off", mcp=True)
    (repo / ".autosave/events.jsonl").write_text('{"kind": "prompt"}\n', encoding="utf-8")
    run.make_control(repo, "claude-code", "agent-autosave")
    assert "agent-autosave" not in (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert not (repo / "AGENTS.md").exists() and not (repo / ".codex").exists()
    assert "agent-autosave hook claude-code" in (repo / ".claude/settings.json").read_text(encoding="utf-8")
    assert not (repo / ".autosave/events.jsonl").exists()
    config = json.loads((repo / ".autosave/config.json").read_text(encoding="utf-8"))
    assert config["inject"]["on_status"] == [] and config["recall"]["enabled"] is False


# --- The control arm: B gets nothing but the repo -----------------------------

ALL_AGENTS = ["claude-code", "codex", "cursor", "gemini", "antigravity", "copilot", "opencode"]
RECALLED = "Make hello.py print the greeting in uppercase, using print like before"


def _hook(agent, project, name, session, **extra):
    """One recorded hook payload for `agent`, replayed in `project`; its stdout."""
    from agent_autosave import capture
    from tests.helpers import load_payload
    data = dict(load_payload(agent, name), cwd=str(project), session_id=session, **extra)
    return capture.handle(agent, name.split("-")[0], json.dumps(data).encode(), env=dict(os.environ))


def _arm_after_a(tmp_path, b, control):
    """A's repo with every agent wired, MCP and the status line on, project
    memory and a journal page, and an interrupted task; then one arm of it."""
    import shutil
    from agent_autosave.installer.install import install
    repo = tasks.generate(tasks.by_name("split-utils"), tmp_path / "repo")
    (repo / ".agent/memory/semantic").mkdir(parents=True)  # agentic-stack, as a user's repo may have it
    (repo / ".agent/memory/semantic/lessons.jsonl").write_text(
        '{"id": "l-1", "claim": "hello.py greeting: print uppercase with print", "status": "accepted"}\n')
    install(repo, ALL_AGENTS, command="agent-autosave", mcp=True, statusline="auto")
    example = ROOT / "examples/memory-recall/.autosave"
    shutil.copy(str(example / "MEMORY.md"), str(repo / ".autosave/MEMORY.md"))
    shutil.copytree(str(example / "journal"), str(repo / ".autosave/journal"))
    for name in ("SessionStart-startup", "UserPromptSubmit", "Stop"):
        _hook("claude-code", repo, name, "a-session", **({"prompt": "Split utils.py"} if name == "UserPromptSubmit" else {}))
    if control:
        _load("run").make_control(repo, b, "agent-autosave")
    return repo


def _given_to_b(repo, b):
    """Everything agent-autosave hands B in its first session: the session
    start block and the prompt recall."""
    return [_hook(b, repo, "SessionStart-startup", "b-session"),
            _hook(b, repo, "UserPromptSubmit", "b-session", prompt=RECALLED)]


@pytest.mark.parametrize("b", ["claude-code", "codex"])
def test_the_autosave_arm_hands_b_the_task_and_the_memory(tmp_path, b):
    """The positive case, so the control test below is not passing vacuously."""
    start, prompt = _given_to_b(_arm_after_a(tmp_path, b, control=False), b)
    assert "[agent-autosave project memory]" in start and "hello.py prints with print()" in start
    assert "[agent-autosave memory]" in prompt


@pytest.mark.parametrize("b", ["claude-code", "codex"])
def test_control_arm_gives_b_no_instructions_mcp_memory_or_context(tmp_path, b):
    repo = _arm_after_a(tmp_path, b, control=True)
    hooks = {"claude-code": ".claude/settings.json", "codex": ".codex/hooks.json"}[b]
    # No instruction block, MCP registration or status line anywhere in the repo:
    # the only file outside .autosave/ that names us is B's hooks file.
    named = sorted(str(p.relative_to(repo)) for p in repo.rglob("*")
                   if p.is_file() and not {".git", ".autosave"} & set(p.relative_to(repo).parts)
                   and "autosave" in p.read_text(encoding="utf-8", errors="replace"))
    assert named == sorted([hooks, ".gitignore"])
    settings = (repo / hooks).read_text(encoding="utf-8")
    assert "statusLine" not in settings and "mcp" not in settings.lower()
    for path in (".mcp.json", ".cursor/mcp.json", ".codex/config.toml", ".gemini/settings.json"):
        assert not (repo / path).exists() or "autosave" not in (repo / path).read_text(encoding="utf-8")
    # No memory and nothing of A's task: no MEMORY.md, journal, candidates or ledger.
    left = sorted(p.name for p in (repo / ".autosave").iterdir() if not p.name.endswith(".lock"))
    assert left == ["SAVE.md", "config.json", "install.json", "save.json", "task.json"]
    config = json.loads((repo / ".autosave/config.json").read_text(encoding="utf-8"))
    assert config["inject"] == {"on_status": [], "memory_on_start": False}
    assert config["recall"] == {"enabled": False} and config["handoff"] == {"ask": False}
    assert config["integrations"] == {"agentic_stack": "off"}
    # No injected context: B's session start and prompt get nothing back...
    assert _given_to_b(repo, b) == ["", ""]
    # ...while its work is still recorded, which is what the arm measures.
    kinds = [json.loads(line)["kind"] for line in (repo / ".autosave/events.jsonl").read_text().splitlines()]
    assert "session_start" in kinds and "prompt" in kinds
    assert not (repo / ".autosave/MEMORY.md").exists() and not (repo / ".autosave/journal").exists()
