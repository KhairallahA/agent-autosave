from copy import deepcopy
import json
from pathlib import Path

import pytest

from agent_autosave.compiler import compile_events, serialize, substantive
from agent_autosave.config import resolve
from agent_autosave.events import EventError, prepare, validate
from agent_autosave.render import render
from tests.helpers import START, event, tool


def test_goal_threshold_continuations_and_first_substantive_prompt():
    for text in ("continue", "go on", "yes", "ok", "keep going", " CONTINUE ", "x" * 19):
        assert not substantive(text)
    assert substantive("x" * 20)
    save = compile_events([event("prompt", i, text=text) for i, text in enumerate([
        "yes", "x" * 19, "Build the portable autosave core.", "Keep all runtime code dependency free."])])
    assert save["goal"] == "Build the portable autosave core."
    assert save["steering"] == ["Keep all runtime code dependency free."]


def test_steering_keeps_newest_five_capped_at_300():
    save = compile_events([event("prompt", i, text=str(i) * 400) for i in range(8)])
    assert save["steering"] == [str(i) * 300 for i in range(3, 8)]


def test_latest_plan_replaces_whole_list_and_absent_plan_is_omitted():
    assert "plan" not in compile_events([tool()])
    first = [{"step": "old step", "state": "todo"}]
    last = [{"step": "new step", "state": "doing"}]
    save = compile_events([event("plan", items=first), event("plan", 1, items=last)])
    assert save["plan"] == last
    assert compile_events([event("plan", items=first), event("plan", 1, items=[])])["plan"] == []


def test_last_actions_newest_ten_one_line_each():
    save = compile_events([tool(i, summary="command\n" + str(i)) for i in range(12)])
    assert save["last_actions"] == ["Ran command " + str(i) + " (exit 0)" for i in range(11, 1, -1)]


def test_failure_resolution_trimmed_command_and_older_unresolved_failure():
    events = [tool(summary=" test a ", exit=1, output_tail="first"),
              tool(1, summary="test b", exit=2, output_tail="second")]
    assert compile_events(events)["failing"]["command"] == "test b"
    events += [tool(2, summary=" test b  ", exit=0)]
    assert compile_events(events)["failing"] == {"command": "test a", "exit": 1, "tail": "first"}
    events += [tool(3, summary="test a", exit=0)]
    assert compile_events(events)["failing"] is None
    # A different command, even sharing a prefix, does not resolve it.
    assert compile_events([tool(summary="test", exit=1), tool(1, summary="test --verbose")])["failing"]


def test_files_from_edits_writes_and_git_deduplicated_capped():
    events = [event("tool", tool="Read", summary="read", files=["not-edited"]),
              event("tool", 1, tool="Edit", summary="edit", files=["first.py", "first.py", "node_modules/x", "a/dist/x"]),
              event("tool", 2, tool="Write", summary="write", files=["second.py"])]
    save = compile_events(events, {"dirty": ["second.py"] + [str(i) + ".py" for i in range(40)]})
    assert save["files_touched"] == ["first.py", "second.py"] + [str(i) + ".py" for i in range(28)]


def test_decisions_oldest_first_other_notes_remain_only_in_ledger():
    save = compile_events([event("note", i, text=text, type=kind) for i, (text, kind) in enumerate([
        ("first", "decision"), ("todo", "todo"), ("watch", "warning"), ("second", "decision")])])
    assert save["decisions"] == ["first", "second"]


@pytest.mark.parametrize("steps,extra,expected", [
    ([], [], "Build the portable autosave core."),
    ([], [event("prompt", 2, text="Use only Python standard libraries.")], "Use only Python standard libraries."),
    ([{"step": "todo first", "state": "todo"}, {"step": "todo second", "state": "todo"}], [], "todo first"),
    ([{"step": "todo first", "state": "todo"}, {"step": "work now", "state": "doing"}], [], "work now"),
    ([{"step": "work now", "state": "doing"}], [tool(2, exit=1)], "Make `pytest` pass."),
])
def test_next_action_precedence(steps, extra, expected):
    events = [event("prompt", text="Build the portable autosave core."), event("plan", 1, items=steps)] + extra
    assert compile_events(events)["next_action"] == expected


def test_agents_per_session_order_turns_transcripts_and_every_switch():
    events = [event("session_start", source="startup", turn=0, transcript="/logs/a.jsonl"),
              tool(1, turn=1), tool(2, turn=1), event("interrupted", 3, reason="rate_limit", turn=1),
              event("prompt", 4, agent="codex", session="session-b", turn=1, text="continue"),
              event("prompt", 5, session="session-c", turn=1, text="continue")]
    save = compile_events(events)
    assert save["agents"] == [
        {"agent": "claude-code", "session": "session-a", "turns": 1, "transcript": "/logs/a.jsonl"},
        {"agent": "codex", "session": "session-b", "turns": 1, "transcript": None},
        {"agent": "claude-code", "session": "session-c", "turns": 1, "transcript": None}]
    assert save["handoffs"] == [
        {"from":"claude-code", "to":"codex", "at":events[4]["ts"], "reason":"rate_limit"},
        {"from":"codex", "to":"claude-code", "at":events[5]["ts"], "reason":"active"}]
    assert save["status"] == "active" and save["interrupted"] is None


@pytest.mark.parametrize("reason", ["rate_limit", "overloaded", "auth", "server_error", "unknown"])
def test_explicit_interruption_persists_until_prompt(reason):
    events = [event("interrupted", reason=reason), event("session_start", 1, source="resume"),
              event("turn_end", 2)]
    assert compile_events(events)["status"] == "interrupted"
    assert compile_events(events + [event("prompt", 3, text="yes")])["interrupted"] is None


def test_compact_without_later_turn_end_is_interrupted():
    events = [event("prompt", text="Build the portable autosave core."), event("compact", 1, trigger="auto")]
    assert compile_events(events)["interrupted"]["reason"] == "compact"
    assert compile_events(events + [event("turn_end", 2)])["status"] == "active"
    assert compile_events(events + [event("session_end", 2, reason="exit"), event("turn_end", 3)])["status"] == "paused"
    explicit = [event("interrupted", reason="auth"), event("compact", 1, trigger="auto"), event("turn_end", 2)]
    assert compile_events(explicit)["interrupted"]["reason"] == "auth"


def test_paused_until_prompt_and_done_requires_all_three_conditions():
    unfinished = event("plan", items=[{"step":"ship", "state":"doing"}])
    done = event("plan", items=[{"step":"ship", "state":"done"}])
    end = event("turn_end", 3)
    assert compile_events([unfinished, event("session_end", 1, reason="exit")])["status"] == "paused"
    assert compile_events([unfinished, event("session_end", 1, reason="exit"), event("prompt", 2, text="yes")])["status"] == "active"
    for events in ([unfinished, end], [done], [done, tool(1, exit=1), end], [end], [event("plan", items=[]), end]):
        assert compile_events(events)["status"] != "done"
    assert compile_events([done, tool(1, exit=1), tool(2), end])["status"] == "done"
    assert compile_events([done, end, event("session_end", 4, reason="exit")])["status"] == "done"


def test_explicit_boundary_excludes_old_and_other_task_events():
    save = compile_events([event("prompt", text="Previous task must be excluded."),
                           event("prompt", 2, text="Wrong task also excluded.", task_id="t_20261014_120001"),
                           event("prompt", 3, text="Current task owns this prompt.", task_id="t_20261014_120002")],
                          started=event("turn_end", 1)["ts"], task_id="t_20261014_120002")
    assert save["goal"] == "Current task owns this prompt."
    assert save["steering"] == []


def test_determinism_no_input_mutation_and_empty_boundary():
    events = [event("prompt", text="Build the portable autosave core."), tool(1)]
    git = {"branch":"main", "head":"a" * 40, "dirty":["a.py"], "diffstat":"", "commits_since_start":0}
    before = deepcopy((events, git))
    expected = serialize(compile_events(events, git))
    for _ in range(5):
        assert serialize(compile_events(iter(events), git)) == expected
    assert (events, git) == before
    assert compile_events([], started=START)["started"] == START
    with pytest.raises(ValueError, match="explicit start"):
        compile_events([])


def test_config_limits_and_ignored_capture():
    events = [event("prompt", text="Build the portable autosave core."), tool(1, exit=1),
              event("tool", 2, tool="Write", summary="write", files=["keep", "skip.log"])]
    save = compile_events(events, config={"capture":{"ignore_tools":["Bash"], "ignore_paths":["*.log"]},
                                         "compile":{"last_actions":1}})
    assert save["failing"] is None and save["last_actions"] == ["write"]
    assert save["files_touched"] == ["keep"]


FIXTURES = Path(__file__).parent / "fixtures/compiler"


@pytest.mark.parametrize("directory", sorted(FIXTURES.glob("*/")), ids=lambda path: path.name)
def test_golden_streams(directory):
    from agent_autosave.render import render
    options = json.loads((directory / "inputs.json").read_text())
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    before = deepcopy(events)
    save = compile_events(events, **options)
    assert serialize(save) == (directory / "save.json").read_bytes()
    assert render(save, options.get("config")).encode("utf-8") == (directory / "SAVE.md").read_bytes()
    assert events == before


def test_at_least_twenty_complete_golden_streams():
    directories = list(FIXTURES.glob("*/"))
    assert len(directories) >= 20
    assert all((path / filename).is_file() for path in directories
               for filename in ("events.jsonl", "inputs.json", "save.json", "SAVE.md"))


def _ev(kind, i, **extra):
    return dict(v=1, ts="2026-10-15T00:00:%02dZ" % i, agent="claude-code", session="s", turn=1, kind=kind, **extra)


def test_compact_then_session_end_is_paused_not_interrupted():
    base = [_ev("prompt", 0, text="Implement the thing properly now."), _ev("compact", 1, trigger="manual")]
    assert compile_events(base)["status"] == "interrupted"
    save = compile_events(base + [_ev("session_end", 2, reason="clear")])
    assert save["status"] == "paused" and save["interrupted"] is None
    save = compile_events(base + [_ev("session_start", 2, source="compact")])
    assert save["status"] == "active" and save["interrupted"] is None
    # An explicit interruption is not cleared by a session end.
    hard = base[:1] + [_ev("interrupted", 1, reason="rate_limit"), _ev("session_end", 2, reason="other")]
    assert compile_events(hard)["status"] == "interrupted"


def _plan(i, steps, origin=None):
    extra = {"origin": origin} if origin else {}
    return _ev("plan", i, items=[{"step": s, "state": "todo"} for s in steps], **extra)


def _cli(i, kind, **fields):
    return dict(_ev(kind, i, **fields), agent="cli", session="cli", turn=0)


def test_plan_precedence_newest_explicit_wins_message_only_fills_gaps():
    p = [_ev("prompt", 0, text="Implement the thing properly now.")]
    steps = lambda events: [i["step"] for i in compile_events(p + events)["plan"]]
    assert steps([_plan(1, ["m"], "message")]) == ["m"]
    assert steps([_plan(1, ["m"], "message"), _plan(2, ["t"])]) == ["t"]
    assert steps([_plan(1, ["t"]), _plan(2, ["m"], "message")]) == ["t"]
    assert steps([_plan(1, ["t"]), _cli(2, "plan_set", steps=["c"])]) == ["c"]
    assert steps([_cli(1, "plan_set", steps=["c"]), _plan(2, ["t"], "tool")]) == ["t"]
    assert steps([_cli(1, "plan_set", steps=["c"]), _plan(2, ["m"], "message")]) == ["c"]


def test_plan_set_and_done_fold_in_ledger_order():
    p = [_ev("prompt", 0, text="Implement the thing properly now."),
         _cli(1, "plan_set", steps=["a", "b", "c"]), _cli(2, "plan_done", index=3), _cli(3, "plan_done", index=1),
         _cli(4, "plan_done", index=9)]
    save = compile_events(p)
    assert [i["state"] for i in save["plan"]] == ["done", "todo", "done"]
    assert save["handoffs"] == [] and save["agents"] == [{"agent": "claude-code", "session": "s", "turns": 1,
                                                          "transcript": None}]
    # plan done -> status done only at the next turn end.
    done = p + [_cli(5, "plan_done", index=2)]
    assert compile_events(done)["status"] == "active"
    assert compile_events(done + [_ev("turn_end", 6)])["status"] == "done"


def test_own_commands_never_reach_last_actions_failing_or_next_action():
    """A real session: Claude chained our command with the failing one."""
    from agent_autosave.normalize.claude_code import normalize
    from agent_autosave.render import render
    ours = '{"ok": true, "data": {"plan": [{"step": "Write hello.py", "state": "done"}]}, ' \
           '"meta": {"version": "0.1.0", "save_v": 1, "elapsed_ms": 13}}'
    failure = {"hook_event_name": "PostToolUseFailure", "session_id": "s", "cwd": "/p", "tool_name": "Bash",
               "tool_input": {"command": 'agent-autosave plan done 2; python3 -c "raise SystemExit(7)"'},
               "error": "Exit code 7\n" + ours}
    ok = {"hook_event_name": "PostToolUse", "session_id": "s", "cwd": "/p", "tool_name": "Bash",
          "tool_input": {"command": 'agent-autosave plan set --quiet "Write hello.py" "Run it"'},
          "tool_response": {"stdout": "", "stderr": "", "interrupted": False}}
    rows = [_ev("prompt", 0, text="Implement the thing properly now.")]
    for i, data in enumerate([ok, failure], 1):
        events, _ = normalize(data, ts="2026-10-15T00:00:%02dZ" % i, turn=1)
        rows += events
    save = compile_events(rows)
    assert save["failing"]["command"] == 'python3 -c "raise SystemExit(7)"'
    assert save["failing"]["exit"] == 7 and '"ok"' not in save["failing"]["tail"]
    assert save["last_actions"] == ['Ran python3 -c "raise SystemExit(7)" (exit 7)']
    assert save["next_action"] == 'Make `python3 -c "raise SystemExit(7)"` pass.'
    assert "agent-autosave plan" not in render(save) and '"ok"' not in render(save)


def test_strip_own_commands_handles_chains_quotes_and_paths():
    from agent_autosave.compiler import strip_own_commands as strip
    assert strip("agent-autosave plan done 1") == ""
    assert strip("/opt/bin/agent-autosave status --quiet && pytest -x") == "pytest -x"
    assert strip("python3 -m agent_autosave plan done 2 || make test") == "make test"
    middle = "pytest; agent-autosave plan done 3; npm test"
    assert strip(middle) == middle
    assert strip("pytest && agent-autosave plan done 1 || echo x") == "pytest && agent-autosave plan done 1 || echo x"
    assert strip("make && npm test; agent-autosave plan done 2") == "make && npm test"
    assert strip("agent-autosave plan done 1 && agent-autosave plan done 2") == ""
    assert strip('echo "agent-autosave; x" && ls') == 'echo "agent-autosave; x" && ls'
    assert strip("grep agent-autosave README.md") == "grep agent-autosave README.md"
    assert strip("make\n  check") == "make\n  check"


# --- `exit: null` = unknown: never a pass, never a failure, never hides one

PLAN_DONE = event("plan", 3, items=[{"step": "Fix it", "state": "done"}])


def compiled(*events):
    return compile_events([event("prompt", text="Fix the failing auth test suite.")] + list(events))


def test_schema_accepts_null_and_rejects_other_types():
    validate(tool(exit=None))
    for bad in (False, "1", 1.0):
        with pytest.raises(EventError):
            validate(tool(exit=bad))


def test_unknown_exit_drops_output_tail():
    assert "output_tail" not in prepare(tool(exit=None, output_tail="secret-ish output"), resolve())


def test_unknown_run_does_not_resolve_an_earlier_failure():
    save = compiled(tool(1, summary="pytest", exit=1, output_tail="FAIL"), tool(2, summary="pytest", exit=None))
    assert save["failing"]["command"] == "pytest" and save["failing"]["exit"] == 1
    assert save["last_actions"][0] == "Ran pytest (exit unknown)"
    assert save["next_action"] == "Make `pytest` pass." and save["unknown_exit"] == []


def test_unknown_run_is_not_a_failure_and_blocks_done():
    save = compiled(tool(1, summary="make test", exit=None), PLAN_DONE, event("turn_end", 4))
    assert save["failing"] is None and save["status"] != "done"
    assert save["unknown_exit"] == ["make test"]
    assert save["next_action"] == "Re-run `make test` and check its result (exit unknown)."
    assert "pass" not in save["next_action"]


def test_known_rerun_clears_unknown():
    save = compiled(tool(1, summary="make test", exit=None), tool(2, summary="make test", exit=0),
                    PLAN_DONE, event("turn_end", 4))
    assert save["unknown_exit"] == [] and save["status"] == "done"
    failed = compiled(tool(1, summary="make test", exit=None), tool(2, summary="make test", exit=2, output_tail="E"))
    assert failed["failing"]["exit"] == 2 and failed["unknown_exit"] == []


def test_render_shows_exit_unknown():
    text = render(compiled(tool(1, summary="make test", exit=None)))
    assert "Ran make test (exit unknown)" in text
    assert "`make test` (exit unknown: result not reported, not counted as a pass)" in text
