"""`agent-autosave share on|off`: git tracks MEMORY.md and the journal, the
rest of .autosave/ stays ignored; secrets refuse; remove stays byte exact."""
import json
import subprocess

from agent_autosave.installer.install import install
from agent_autosave.installer.remove import remove


def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("node_modules/\n")
    install(tmp_path, ["claude-code"], command="agent-autosave", statusline="off")
    (tmp_path / ".autosave/MEMORY.md").write_text("# Memory\n\n- Use SQLite for the cache.\n")
    (tmp_path / ".autosave/journal").mkdir(exist_ok=True)
    (tmp_path / ".autosave/journal/t_1.md").write_text("# Task\n")
    return tmp_path


def ignored(project, name):
    return subprocess.run(["git", "check-ignore", "-q", name], cwd=str(project)).returncode == 0


def test_share_on_tracks_memory_and_journal_only(tmp_path, run_cli):
    project = repo(tmp_path)
    assert ignored(project, ".autosave/MEMORY.md")
    result = run_cli("share", "on")
    assert result.returncode == 0, result.stdout
    assert not ignored(project, ".autosave/MEMORY.md") and not ignored(project, ".autosave/journal/t_1.md")
    for private in (".autosave/events.jsonl", ".autosave/save.json", ".autosave/install.json",
                    ".autosave/memory/candidates.jsonl"):
        assert ignored(project, private), private
    assert "MEMORY.md merge=union" in (project / ".gitattributes").read_text()
    assert json.loads((project / ".autosave/install.json").read_text())["share"] is True
    assert run_cli("share", "off").returncode == 0
    assert ignored(project, ".autosave/MEMORY.md")


def test_secrets_refuse_with_the_lines(tmp_path, run_cli):
    project = repo(tmp_path)
    with (project / ".autosave/journal/t_1.md").open("a") as stream:
        stream.write("token ghp_" + "a" * 36 + "\n")
    result = run_cli("share", "on")
    assert result.returncode == 1
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "secrets_found"
    assert error["detail"]["findings"] == [{"file": ".autosave/journal/t_1.md", "line": 2}]
    assert ignored(project, ".autosave/MEMORY.md")


def test_remove_after_share_restores_gitignore_and_gitattributes(tmp_path, run_cli):
    project = repo(tmp_path)
    run_cli("share", "on")
    remove(project)
    assert (project / ".gitignore").read_text() == "node_modules/\n"
    assert not (project / ".gitattributes").exists()
