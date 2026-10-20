"""scripts/scrub-rollout.py removes account ids, outside paths, encrypted
content, instructions, plugin names and unscripted prompts by default; the
committed fixtures from real recordings carry none of them."""
import importlib.util
import json
import subprocess

import pytest

from tests.helpers import ROOT

spec = importlib.util.spec_from_file_location("scrub_rollout", ROOT / "scripts/scrub-rollout.py")
scrub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scrub)

CWD = "/private/tmp/aa-demo"
PROMPT = 'Use your update_plan tool to make exactly this plan: "Write hello.py".'
OTHER = "/Users/someone/Desktop/Projects/otherproject/scripts/check"
ROWS = [
    {"type": "session_meta", "payload": {"id": "s1", "cwd": CWD, "creator_user_id": "user-AAAAAAAAAAAAAAAAAAAA",
                                         "creator_account_id": "00000000-1111", "org_id": "o", "base_instructions": "B",
                                         "git": {"branch": "main"}, "runtime_workspace_roots": [CWD]}},
    {"type": "turn_context", "payload": {"cwd": CWD}},
    {"type": "world_state", "payload": {"state": {"approved_command_prefixes": [[OTHER]],
                                                  "model_catalog": "abc"}}},
    {"type": "event_msg", "payload": {"type": "thread_settings_applied", "thread_settings": {
        "permission_profile": {"entries": ["/private/var/folders/xx/abcdefghijkl/T"]}}}},
    {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": [
        {"type": "input_text", "text": "<permissions instructions>approved: " + OTHER}]}},
    {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "# AGENTS.md instructions for " + CWD},
        {"type": "input_text", "text": "<environment_context><cwd>" + CWD + "</cwd></environment_context>"},
        {"type": "input_text", "text": PROMPT}]}},
    {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "also fix my other project at " + OTHER}]}},
    {"type": "response_item", "payload": {"type": "reasoning", "encrypted_content": "gAAAAAB" + "x" * 60}},
    {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [
        {"type": "output_text", "text": "Created " + CWD + "/hello.py; see ~/.codex/plugins/cache/x"}]}},
    {"type": "compacted", "payload": {"message": "", "replacement_history": [
        {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "<skills_instructions>"}]},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": PROMPT}]},
        {"type": "compaction", "encrypted_content": "gAAAAAB" + "y" * 60}]}},
    {"type": "event_msg", "payload": {"type": "token_count", "info": None, "rate_limits": {"primary": {"used_percent": 5}}}},
]


def test_the_raw_rollout_has_every_problem_the_check_looks_for():
    found = {why for row in ROWS for _, why in scrub.problems(row, scrub.scratch_roots(CWD))}
    assert found >= {"forbidden key", "row type turn_context", "row type world_state", "developer message",
                     "instructions, plugins or encrypted content", "path outside the scratch repo"}


def test_scrubbing_removes_everything_on_the_list_by_default():
    kept, dropped = scrub.scrub_rows(json.loads(json.dumps(ROWS)), keep_prompts=[PROMPT])
    text = json.dumps(kept)
    for gone in ("creator_user_id", "creator_account_id", "org_id", "base_instructions", "approved_command_prefixes",
                 "model_catalog", "permission_profile", "gAAAAAB", "otherproject", "someone", "var/folders",
                 "plugins", "AGENTS.md instructions", "environment_context", "permissions instructions",
                 "skills_instructions", "also fix my other project"):
        assert gone not in text, gone
    assert [r["type"] for r in kept] == ["session_meta", "response_item", "response_item", "response_item",
                                         "compacted", "event_msg"]
    assert json.dumps(PROMPT)[1:-1] in text and CWD + "/hello.py" in text and kept[-1] == ROWS[-1]
    assert not [p for r in kept for p in scrub.problems(r, scrub.scratch_roots(CWD))]
    assert dropped["creator_user_id"] == 1 and dropped["world_state"] == 1 and dropped["prompt"] == 1


def test_without_keep_prompt_no_prompt_survives():
    kept, _ = scrub.scrub_rows(json.loads(json.dumps(ROWS)))
    assert json.dumps(PROMPT)[1:-1] not in json.dumps(kept) and scrub.REMOVED_PROMPT in json.dumps(kept)


def test_the_command_line_writes_and_checks(tmp_path):
    source, out = tmp_path / "in.jsonl", tmp_path / "out.jsonl"
    source.write_text("".join(json.dumps(r) + "\n" for r in ROWS))
    run = subprocess.run(["python3", str(ROOT / "scripts/scrub-rollout.py"), str(source), str(out),
                          "--keep-prompt", PROMPT], capture_output=True, text=True)
    assert run.returncode == 0 and "problems left 0" in run.stdout
    check = subprocess.run(["python3", str(ROOT / "scripts/scrub-rollout.py"), "--check", str(source), str(out)],
                           capture_output=True, text=True)
    assert check.returncode == 1 and str(source) in check.stdout and str(out) + ":" not in check.stdout
    assert "otherproject" not in check.stdout and "user-AAAA" not in check.stdout  # never the values


SCRIPTED = {
    'Use your update_plan tool to make exactly this 3-step plan: "Write hello.py", "Run it", "Add a failing check". '
    'Then do step 1 only: create hello.py that prints "hello autosave".',
    'Run python3 hello.py. Then run python3 -c "raise SystemExit(7)" and stop there; do not fix it.',
}


def test_the_real_rollout_fixture_is_scrubbed_and_keeps_only_the_scripted_prompts():
    path = ROOT / "tests/fixtures/codex/rollouts/rollout-real-session.jsonl"
    assert scrub.check_file(path) == []
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    prompts = {part["text"] for row in rows for message in ([row["payload"]] + row["payload"].get("replacement_history", [])
                                                             if row["type"] in ("response_item", "compacted") else [])
               if message.get("role") == "user" for part in message.get("content", [])}
    assert prompts == SCRIPTED


RECORDED = sorted(p for folder in ("claude-code", "codex", "cursor", "compat", "context", "antigravity")
                  for p in (ROOT / "tests/fixtures" / folder).rglob("*") if p.suffix in (".json", ".jsonl"))


@pytest.mark.parametrize("path", RECORDED, ids=lambda p: str(p.relative_to(ROOT / "tests/fixtures")))
def test_every_fixture_from_a_real_recording_passes_the_same_check(path):
    assert scrub.check_file(path) == []
