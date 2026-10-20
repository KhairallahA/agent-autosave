"""Task boundaries without a plan: `done`, a clear after finished work, and
the latest request."""
import json
import re

from agent_autosave.loader import context
from agent_autosave.state import TaskStore
from tests.scenario import Repo

OAUTH = "Add OAuth login to the settings page with the Google provider"
CSV = "rewrite the CSV exporter so it streams rows instead of loading them all"
LINE = "When the user's task is finished, run `agent-autosave done --quiet`."


def history(repo):
    return [json.loads(p.read_text()) for p in sorted((repo.root / ".autosave/history").glob("t_*.json"))]


def oauth(repo):
    repo.prompt(OAUTH)
    repo.claude("PostToolUse-Edit")
    repo.bash("pytest -q")
    repo.stop("OAuth login works; the Google provider is wired.")


def test_done_then_a_new_prompt_makes_two_tasks(tmp_path):
    repo = Repo(tmp_path / "repo")
    oauth(repo)
    code, result = repo.cli("done", "--quiet", "--note", "OAuth shipped behind a flag")
    assert code == 0 and result["data"]["status"] == "done"
    repo.prompt(CSV)
    assert repo.save()["goal"] == CSV and repo.save()["status"] == "active"
    [old] = history(repo)
    assert old["goal"] == OAUTH and old["status"] == "done" and "OAuth shipped behind a flag" in old["decisions"]


def test_done_with_no_task_is_nothing_to_finish(tmp_path):
    repo = Repo(tmp_path / "repo")
    code, result = repo.cli("done")
    assert code == 1 and result["error"]["code"] == "nothing_to_finish"
    (repo.root / ".autosave/task.json").unlink()
    code, result = repo.cli("done")
    assert code == 1 and result["error"]["code"] == "nothing_to_finish"


def test_done_twice_records_one_event(tmp_path):
    repo = Repo(tmp_path / "repo")
    oauth(repo)
    repo.cli("done")
    repo.cli("done")
    rows = (repo.root / ".autosave/events.jsonl").read_text().splitlines()
    assert sum('"task_done"' in row for row in rows) == 1


def test_a_clear_after_finished_work_makes_two_tasks(tmp_path):
    repo = Repo(tmp_path / "repo")
    oauth(repo)
    repo.end("s1")
    assert repo.start("s2", source="clear") == ""
    repo.prompt(CSV, session="s2")
    assert repo.save()["goal"] == CSV
    [old] = history(repo)
    assert old["goal"] == OAUTH


def test_a_clear_while_a_command_fails_keeps_one_task(tmp_path):
    repo = Repo(tmp_path / "repo")
    repo.prompt(OAUTH)
    repo.bash("pytest -q", exit=1)
    repo.stop()
    text = repo.start("s2", source="clear")
    assert "pytest -q" in text
    repo.prompt(CSV, session="s2")
    assert repo.save()["goal"] == OAUTH and history(repo) == []


def test_a_clear_with_unfinished_plan_steps_keeps_one_task(tmp_path):
    repo = Repo(tmp_path / "repo")
    repo.prompt(OAUTH)
    repo.cli("plan", "set", "Add the provider", "Add the callback")
    repo.cli("plan", "done", "1")
    repo.stop()
    repo.start("s2", source="clear")
    repo.prompt(CSV, session="s2")
    assert repo.save()["goal"] == OAUTH


def test_without_done_or_clear_the_new_request_is_the_latest_request(tmp_path):
    repo = Repo(tmp_path / "repo")
    oauth(repo)
    repo.prompt(CSV)
    save = repo.save()
    assert save["goal"] == OAUTH and save["latest_request"] == CSV
    text = context(save, force=True)
    goal = text.index('Goal: "')
    assert text.index('Latest request: "' + CSV) > goal
    assert text[goal:].split("\n")[1].startswith("Latest request: ")
    markdown = repo.markdown()
    assert markdown.index("**Latest request:** rewrite the CSV exporter") > markdown.index("**Goal:**")


def test_clear_ends_task_rule():
    assert not TaskStore.clear_ends_task({"goal": ""})
    assert TaskStore.clear_ends_task({"goal": "g", "failing": None, "unknown_exit": [], "plan": [{"step": "a", "state": "done"}]})
    assert not TaskStore.clear_ends_task({"goal": "g", "failing": {"command": "x"}})
    assert not TaskStore.clear_ends_task({"goal": "g", "unknown_exit": ["x"]})
    assert not TaskStore.clear_ends_task({"goal": "g", "plan": [{"step": "a", "state": "doing"}]})


BLOCKS = ("claude-code/CLAUDE.block.md", "codex/AGENTS.block.md", "generic/AGENTS.block.md",
          "cursor/agent-autosave.mdc", "gemini/GEMINI.block.md", "antigravity/ANTIGRAVITY.block.md")
OWN_DONE = re.compile(r"agent-autosave done\b")


def test_no_block_mentions_done():
    """`done` is the user's command: told to run it, agents ran it after every reply (a real macOS run)."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for base in ("adapters", "agent_autosave/resources"):
        for path in BLOCKS:
            text = (root / base / path).read_text()
            assert not OWN_DONE.search(text) and "task is finished" not in text, base + "/" + path


def test_upgrade_flags_and_removes_a_block_with_the_done_line(tmp_path, home, monkeypatch, capsys):
    from agent_autosave import cli
    from agent_autosave.installer import detect
    from agent_autosave.installer.install import install
    from tests.helpers import CMD
    project = tmp_path / "repo"
    project.mkdir()
    install(project, ("claude-code",), command=CMD)
    # An install made while the block asked for `done`: the file and its receipt span.
    anchor = "as each step (numbered from 1) finishes.\n"
    for name in ("CLAUDE.md", ".autosave/install.json"):
        path = project / name
        text = path.read_text()
        if LINE not in text:
            if name == "CLAUDE.md":
                text = text.replace(anchor, anchor + "\n" + LINE + "\n", 1)
            else:
                text = text.replace(json.dumps(anchor)[1:-1], json.dumps(anchor + "\n" + LINE + "\n")[1:-1], 1)
        path.write_text(text)
    assert LINE in (project / "CLAUDE.md").read_text()
    monkeypatch.setattr(detect, "hook_command", lambda **k: CMD)
    monkeypatch.chdir(project)
    assert cli.main(["--json", "upgrade"]) == 0
    plan = json.loads(capsys.readouterr().out)["data"]
    assert "CLAUDE.md text changed (claude-code)" in plan["reasons"]
    assert cli.main(["--json", "upgrade", "--yes"]) == 0
    assert LINE not in (project / "CLAUDE.md").read_text()
