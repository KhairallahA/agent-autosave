"""`handoff export` / `handoff import`: the same events give the same save
in a teammate's clone, on the same branch."""
import json
import shutil
import subprocess

from agent_autosave.installer.install import install
from tests.test_go import seed


def clone_like(tmp_path, name):
    other = tmp_path / name
    other.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(other)], check=True)
    install(other, ["claude-code"], command="agent-autosave", statusline="off")
    return other


def test_export_then_import_hands_off_the_task(tmp_path, run_cli):
    mine = tmp_path / "mine"
    mine.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(mine)], check=True)
    seed(mine)
    assert run_cli("handoff", "export", cwd=mine).returncode == 0
    text = (mine / ".autosave-handoff.md").read_text()
    assert text.startswith("---\nagent-autosave-handoff: 1\n") and '"branch": ' not in text.split("---")[1]
    assert 'branch: "main"' in text and "Fix the flaky login test in tests/test_auth.py" in text
    theirs = clone_like(tmp_path, "theirs")
    shutil.copy(mine / ".autosave-handoff.md", theirs / ".autosave-handoff.md")
    result = run_cli("handoff", "import", cwd=theirs)
    assert result.returncode == 0, result.stdout
    save = json.loads((theirs / ".autosave/save.json").read_text())
    assert save["goal"] == "Fix the flaky login test in tests/test_auth.py" and [i["step"] for i in save["plan"]] == ["reproduce", "fix"]
    assert save["status"] in ("interrupted", "paused")
    context = run_cli("show", "--context", cwd=theirs).stdout.decode()
    assert "Fix the flaky login test in tests/test_auth.py" in context


def test_import_refuses_another_branch(tmp_path, run_cli):
    mine = tmp_path / "mine"
    mine.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "feature", str(mine)], check=True)
    seed(mine)
    run_cli("handoff", "export", cwd=mine)
    theirs = clone_like(tmp_path, "theirs")
    shutil.copy(mine / ".autosave-handoff.md", theirs / ".autosave-handoff.md")
    result = run_cli("handoff", "import", cwd=theirs)
    assert result.returncode == 2 and json.loads(result.stdout)["error"]["code"] == "not_supported"


def test_import_without_a_file(tmp_path, run_cli):
    clone_like(tmp_path, "theirs")
    result = run_cli("handoff", "import", cwd=tmp_path / "theirs")
    assert result.returncode == 1 and json.loads(result.stdout)["error"]["code"] == "no_handoff"
