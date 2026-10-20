"""agentic-stack: detection, the WORKSPACE.md block, cases, lessons, doctor and remove.

The synthetic tree copies only the shape confirmed in agentic-stack at commit
11a75e16b6cb08a436b3124474273fdb9f08b4de (.agent/AGENTS.md, .agent/memory/,
memory/working/WORKSPACE.md headings, memory/semantic/lessons.jsonl rows,
tools/track.py `add case` flags); none of its code. Set
AGENT_AUTOSAVE_AGENTIC_STACK=/path/to/a/clone to also run against the real one.
"""
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from agent_autosave import agentic, config as config_module, recall
from tests.memory_helpers import context_of, finish_task, repo, set_config  # noqa: F401

TEMPLATE = ("# Workspace (live task state)\n\n## Current task\n\n## Open files\n\n## Active hypotheses\n\n"
            "## Checkpoints\n\n## Next step\n")
STUB = """import json, sys
from pathlib import Path
log = Path(__file__).resolve().parent / "calls.jsonl"
with log.open("a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
"""


def make_stack(root, *, lessons=(), track=STUB):
    agent = root / ".agent"
    for folder in ("memory/working", "memory/semantic", "memory/cases", "tools"):
        (agent / folder).mkdir(parents=True, exist_ok=True)
    (agent / "AGENTS.md").write_text("# agentic-stack\n")
    (agent / "memory/working/WORKSPACE.md").write_text(TEMPLATE)
    (agent / "memory/semantic/lessons.jsonl").write_text("".join(json.dumps(row) + "\n" for row in lessons))
    if track is not None:
        (agent / "tools/track.py").write_text(track)
    return agent


def lesson(id_, claim, status="accepted", **extra):
    return dict({"id": id_, "claim": claim, "conditions": [], "evidence_ids": [], "status": status,
                 "accepted_at": "2026-10-01T00:00:00Z", "reviewer": "host-agent", "rationale": "",
                 "cluster_size": 2, "canonical_salience": 1.0, "confidence": 0.8, "support_count": 2,
                 "contradiction_count": 0, "supersedes": None, "source_candidate": None}, **extra)


def calls(repo):
    path = repo.root / ".agent/tools/calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_detection_needs_both_markers_and_respects_the_setting(tmp_path):
    assert not agentic.detected(tmp_path)
    (tmp_path / ".agent").mkdir()
    (tmp_path / ".agent/AGENTS.md").write_text("x")
    assert not agentic.detected(tmp_path)
    (tmp_path / ".agent/memory").mkdir()
    assert agentic.detected(tmp_path)
    config = config_module.load(tmp_path)
    assert agentic.enabled(tmp_path, config)
    off = dict(config, integrations={"agentic_stack": "off"})
    assert not agentic.enabled(tmp_path, off)


def test_the_workspace_block_is_kept_current_and_the_rest_untouched(repo):
    make_stack(repo.root)
    repo.prompt("Add a CSV export for the billing rows")
    repo.bash("pytest -q")
    repo.stop("Working on it.")
    text = (repo.root / ".agent/memory/working/WORKSPACE.md").read_text()
    assert text.startswith(TEMPLATE)
    block = text[len(TEMPLATE):]
    assert block.count(agentic.START) == 1 and block.count(agentic.END) == 1
    assert '"Add a CSV export for the billing rows"' in block
    repo.prompt("Also stream JSON rows")
    repo.stop("Still going.")
    again = (repo.root / ".agent/memory/working/WORKSPACE.md").read_text()
    assert again.count(agentic.START) == 1 and again.startswith(TEMPLATE)


def test_a_block_round_trips_out_byte_for_byte():
    for original in (TEMPLATE, "", TEMPLATE + "\n\nnotes\n", "# Workspace\n\n\n"):
        added = agentic.with_block(original, "<!-- agent-autosave:start -->\nx\n<!-- agent-autosave:end -->\n"
                                   .replace("<!-- agent-autosave:start -->", agentic.START)
                                   .replace("<!-- agent-autosave:end -->", agentic.END))
        assert agentic.without_block(added) == original


def test_a_finished_task_becomes_one_case_with_the_confirmed_flags(repo):
    make_stack(repo.root)
    repo.prompt("Warm-up task so the integration is on before the real one")
    repo.stop("ok")
    repo.cli("clear")
    save = finish_task(repo)
    repo.cli("journal")  # syncs again: still one case
    [argv] = [c for c in calls(repo) if save["task_id"] in c]
    assert argv[:2] == ["add", "case"]
    assert argv[argv.index("--intent") + 1] == "Add a CSV export for the billing rows"
    assert argv[argv.index("--session") + 1] == save["task_id"] and "--outcome" in argv


def test_tasks_finished_before_the_integration_are_not_sent(repo):
    finish_task(repo)
    make_stack(repo.root)
    repo.cli("journal")
    assert calls(repo) == []


def test_a_hanging_track_py_is_cut_off_and_logged(repo, monkeypatch):
    make_stack(repo.root, track="import time\ntime.sleep(30)\n")
    monkeypatch.setattr(agentic, "CASE_TIMEOUT", 0.2)
    with pytest.raises(Exception):
        agentic.add_case(repo.root, {"task_id": "t_20261018_101500000000", "goal": "g"}, timeout=0.2)


def test_accepted_lessons_are_recalled_and_retired_ones_are_not(repo):
    make_stack(repo.root, lessons=[
        lesson("l1", "Billing exports must page through rows with a server cursor"),
        lesson("l2", "Billing exports should load rows into pandas", status="provisional"),
        lesson("l3", "Billing rows export via CSV writer is slow"),
        lesson("l4", "Billing rows export via the CSV writer is fine with buffering", supersedes="l3"),
        lesson("l1", "Billing exports must page through rows with a server cursor", status="accepted"),
    ])
    ids = {row["id"] for row in agentic.accepted_lessons(repo.root / ".agent/memory/semantic/lessons.jsonl")}
    assert ids == {"l1", "l4"}
    config = config_module.load(repo.root)
    text, shown = recall.recall(repo.root, config, "Speed up the billing rows export with a server cursor",
                                agent="codex")
    assert "- agentic-stack lesson, " in text and '"Billing exports must page through rows' in text
    assert "pandas" not in text


def test_lessons_are_skipped_for_claude_when_agentic_stack_hooks_run(repo):
    make_stack(repo.root, lessons=[lesson("l1", "Billing exports must page through rows with a server cursor")])
    settings = repo.root / ".claude/settings.json"
    value = json.loads(settings.read_text())
    value["hooks"].setdefault("UserPromptSubmit", []).append({"hooks": [{"type": "command", "command":
        'python3 "$CLAUDE_PROJECT_DIR/.agent/harness/hooks/claude_code_session.py"'}]})
    settings.write_text(json.dumps(value))
    config = config_module.load(repo.root)
    prompt = "Speed up the billing rows export with a server cursor"
    assert "agentic-stack lesson" not in recall.recall(repo.root, config, prompt, agent="claude-code")[0]
    assert "agentic-stack lesson" in recall.recall(repo.root, config, prompt, agent="codex")[0]


def test_off_means_no_block_no_case_no_lessons(repo):
    make_stack(repo.root, lessons=[lesson("l1", "Billing exports must page through rows with a server cursor")])
    set_config(repo, integrations={"agentic_stack": "off"})
    finish_task(repo)
    assert (repo.root / ".agent/memory/working/WORKSPACE.md").read_text() == TEMPLATE
    assert calls(repo) == []
    config = config_module.load(repo.root)
    assert "lesson" not in recall.recall(repo.root, config, "billing rows export with a server cursor")[0]


def test_doctor_reports_the_integration_and_remove_takes_only_our_block(repo):
    make_stack(repo.root)
    repo.prompt("Add a CSV export for the billing rows")
    repo.stop("ok")
    workspace = repo.root / ".agent/memory/working/WORKSPACE.md"
    workspace.write_text(workspace.read_text() + "\nMy own line.\n")
    [check] = [c for c in repo.cli("doctor")[1]["data"]["checks"] if c["agent"] == "agentic-stack"]
    assert check["level"] == "green" and "WORKSPACE.md block present" in check["message"]
    (repo.root / ".agent/tools/track.py").unlink()
    [check] = [c for c in repo.cli("doctor")[1]["data"]["checks"] if c["agent"] == "agentic-stack"]
    assert check["level"] == "yellow"
    code, out = repo.cli("remove", "--all")
    assert code == 0 and out["data"]["workspace_block_removed"] is True
    text = workspace.read_text()
    assert agentic.START not in text and text.startswith(TEMPLATE) and "My own line." in text
    assert (repo.root / ".agent/AGENTS.md").is_file()


REAL = os.environ.get("AGENT_AUTOSAVE_AGENTIC_STACK")


@pytest.mark.skipif(not REAL, reason="set AGENT_AUTOSAVE_AGENTIC_STACK=/path/to/agentic-stack to run")
def test_against_a_real_agentic_stack_clone(repo):
    shutil.copytree(str(Path(REAL) / ".agent"), str(repo.root / ".agent"))
    assert agentic.detected(repo.root)
    repo.prompt("Warm-up so the integration starts before the real task")
    repo.stop("ok")
    repo.cli("clear")
    save = finish_task(repo)
    cases = "".join(p.read_text() for p in (repo.root / ".agent/memory/cases").glob("agent_case-*.md"))
    assert "intent: Add a CSV export for the billing rows" in cases and save["task_id"] in cases
    workspace = (repo.root / ".agent/memory/working/WORKSPACE.md").read_text()
    assert workspace.count(agentic.START) == 1
    assert sys.executable


def test_doctor_warns_when_on_is_set_without_agentic_stack(repo):
    set_config(repo, integrations={"agentic_stack": "on"})
    [check] = [c for c in repo.cli("doctor")[1]["data"]["checks"] if c["agent"] == "agentic-stack"]
    assert check["level"] == "yellow" and "is on, but there is no .agent/" in check["message"]


def _open_files(repo):
    text = (repo.root / ".agent/memory/working/WORKSPACE.md").read_text()
    section = text.split("## Open files\n", 2)[-1].split("\n\n")[0]
    return [json.loads(line[2:]) for line in section.splitlines() if line.startswith('- "')]


def test_open_files_skip_agentic_stack_and_list_edited_then_read_then_changed(repo):
    """A real macOS run: a fresh agentic-stack install is untracked,
    so Open files listed 20 of its own files (.agent/AGENTS.md,
    .agent/harness/...) before add.py."""
    make_stack(repo.root)
    for index in range(25):
        harness = repo.root / ".agent/harness/hooks/h{:02d}.py".format(index)
        harness.parent.mkdir(parents=True, exist_ok=True)
        harness.write_text("x = 1\n")
    (repo.root / "notes.txt").write_text("scratch\n")  # changed, never touched by the agent
    (repo.root / "README.md").write_text("# readme\n")
    repo.prompt("Add a CSV export for the billing rows")
    repo.tool("Read", {"file_path": str(repo.root / "README.md")})
    (repo.root / "add.py").write_text("def add(a, b):\n    return a + b\n")
    repo.tool("Edit", {"file_path": str(repo.root / "add.py"), "old_string": "", "new_string": "x"})
    repo.stop("Edited add.py.")
    repo.cli("save")
    save = repo.save()
    assert not any(p.startswith(".agent/") for p in save["files_touched"])
    git = save.get("git") or {}
    assert not any(p.startswith(".agent/") for p in (git.get("dirty") or []) + (git.get("untracked") or []))
    files = _open_files(repo)
    assert files[0] == "add.py" and not any(f.startswith(".agent/") for f in files), files
    assert files.index("README.md") < files.index("notes.txt"), files
