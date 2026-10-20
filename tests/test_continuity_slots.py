"""Branch slots: one task per branch, parked and restored on a switch."""
import json
import shutil
import subprocess

from agent_autosave.branches import head_branch, slug
from tests.helpers import FIXTURES
from tests.scenario import Repo

OAUTH = "Add OAuth login to the settings page with the Google provider"
CSV = "rewrite the CSV exporter so it streams rows instead of loading them all"


def switch(repo, branch):
    subprocess.run(["git", "-C", str(repo.root), "symbolic-ref", "HEAD", "refs/heads/" + branch], check=True)


def slots(repo):
    return json.loads((repo.root / ".autosave/task.json").read_text())


def test_main_feature_main_keeps_each_branch_save(tmp_path):
    repo = Repo(tmp_path / "repo")
    repo.prompt(OAUTH, session="s1")
    repo.bash("pytest -q", exit=1, session="s1")
    switch(repo, "feature/csv")
    assert repo.start("s2") == ""  # a new branch: a new, empty task
    repo.prompt(CSV, session="s2")
    assert repo.save()["goal"] == CSV
    switch(repo, "main")
    text = repo.start("s3")
    assert '"' + OAUTH + '"' in text and CSV not in text and '"pytest -q"' in text
    assert repo.save()["goal"] == OAUTH and repo.save()["failing"]["command"] == "pytest -q"
    data = slots(repo)
    assert data["current"] == "main" and set(data["slots"]) == {"main", "feature/csv"}
    parked = json.loads((repo.root / ".autosave/slots" / (slug("feature/csv") + ".json")).read_text())
    assert parked["goal"] == CSV and parked["task_id"] == data["slots"]["feature/csv"]["task_id"]
    switch(repo, "feature/csv")
    assert CSV in repo.start("s4", source="startup") or repo.save()["goal"] == CSV
    assert repo.save()["goal"] == CSV


def test_an_old_task_json_migrates_atomically(tmp_path):
    repo = Repo(tmp_path / "repo")
    old = FIXTURES / "compat/main-d672782"
    directory = repo.root / ".autosave"
    for name in ("events.jsonl", "save.json", "task.json"):
        shutil.copy(str(old / name), str(directory / name))
    before = json.loads((old / "task.json").read_text())
    code, result = repo.cli("status")
    assert code == 0 and result["data"]["task_id"] == before["task_id"]
    data = slots(repo)
    assert data == {"v": 1, "current": "main", "slots": {"main": before}}
    repo.prompt("Keep going with the second task please", session="S2")
    assert repo.save()["task_id"] == before["task_id"]


def test_a_detached_head_keeps_the_current_slot(tmp_path):
    repo = Repo(tmp_path / "repo")
    repo.prompt(OAUTH)
    (repo.root / ".git/HEAD").write_text("4f2a9c1d0e7b4f2a9c1d0e7b4f2a9c1d0e7b4f2a\n")
    repo.prompt(CSV)
    assert repo.save()["goal"] == OAUTH and slots(repo)["current"] == "main"


def test_head_branch_reads_head_and_follows_a_worktree_file(tmp_path):
    assert head_branch(tmp_path) == ""
    main = tmp_path / "main"
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(main)], check=True)
    assert head_branch(main) == "trunk" and head_branch(main / "sub") == "trunk"
    tree = tmp_path / "tree"
    tree.mkdir()
    gitdir = main / ".git/worktrees/tree"
    gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/feature/x\n")
    (tree / ".git").write_text("gitdir: " + str(gitdir) + "\n")
    assert head_branch(tree) == "feature/x"
    (gitdir / "HEAD").write_text("0123456789abcdef0123456789abcdef01234567\n")
    assert head_branch(tree) is None


def test_slug_is_a_safe_unique_file_name():
    assert slug("feature/csv") != slug("feature_csv") and "/" not in slug("a/../b") and slug("") == "_none"


def test_rotation_keeps_every_slot_events(tmp_path):
    repo = Repo(tmp_path / "repo")
    path = repo.root / ".autosave/config.json"
    config = json.loads(path.read_text())
    config["ledger"]["rotate_mb"] = 0.002  # about 2 KB
    path.write_text(json.dumps(config))
    repo.prompt(OAUTH)
    switch(repo, "feature/csv")
    repo.prompt(CSV, session="s2")
    for i in range(20):
        repo.bash("pytest tests/test_{}.py".format(i), exit=1, session="s2")
    assert list((repo.root / ".autosave/history").glob("events-*.jsonl"))  # it rotated
    switch(repo, "main")
    repo.start("s3")
    assert repo.save()["goal"] == OAUTH


def test_status_all_and_any_task_by_id(tmp_path):
    repo = Repo(tmp_path / "repo")
    repo.prompt(OAUTH)
    switch(repo, "feature/csv")
    repo.prompt(CSV, session="s2")
    feature = repo.save()["task_id"]
    switch(repo, "main")
    code, result = repo.cli("status", "--all")
    assert code == 0
    rows = result["data"]["branches"]
    assert [(r["branch"], r["current"], r["goal"]) for r in rows] == [("main", True, OAUTH), ("feature/csv", False, CSV)]
    code, result = repo.cli("show", "--task", feature)
    assert code == 0 and result["data"]["goal"] == CSV
    code, result = repo.cli("resume", "claude-code", "--dry-run", "--task", feature)
    assert code == 0 and CSV in result["data"]["command"][-1]
    code, result = repo.cli("show", "--task", "t_1_1")
    assert code == 1 and result["error"]["code"] == "unknown_task"
    repo.cli("done")
    repo.prompt("now add the audit log for login attempts please")
    code, result = repo.cli("history")
    old = result["data"]["saves"][0]["task_id"]
    code, result = repo.cli("show", "--task", old, "--context")
    assert code == 0 and OAUTH in result["data"]["context"]


def test_show_task_renders_that_task_as_markdown(tmp_path, monkeypatch, capsys):
    from agent_autosave import cli
    repo = Repo(tmp_path / "repo")
    repo.prompt(OAUTH)
    switch(repo, "feature/csv")
    repo.prompt(CSV, session="s2")
    feature = repo.save()["task_id"]
    switch(repo, "main")
    repo.start("s3")
    monkeypatch.chdir(repo.root)
    assert cli.main(["show", "--task", feature, "--md"]) == 0
    out = capsys.readouterr().out
    assert CSV in json.loads(out)["data"]["markdown"] and OAUTH not in json.loads(out)["data"]["markdown"]
