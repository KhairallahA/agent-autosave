"""The Obsidian vault: layout, the home note, safety, fallback and `vault move`."""
import json
import os

import pytest

from agent_autosave import config as config_module, vault
from tests.memory_helpers import accept_all, finish_task, repo, set_config  # noqa: F401


@pytest.fixture
def in_vault(repo, tmp_path):
    folder = tmp_path / "Obsidian"
    folder.mkdir()
    set_config(repo, vault={"path": str(folder)})
    return folder


def _project(repo, folder):
    config = config_module.load(repo.root)
    return folder / "agent-autosave" / vault.project_name(repo.root, config)


def test_the_project_name_is_the_folder_plus_4_hex_and_is_saved_once(repo, in_vault):
    finish_task(repo)
    name = json.loads((repo.root / ".autosave/config.json").read_text())["vault"]["project"]
    assert name.startswith("repo-") and len(name) == len("repo-") + 4
    assert (in_vault / "agent-autosave" / name).is_dir()


def test_the_layout_and_the_home_note(repo, in_vault):
    save = finish_task(repo)
    accept_all(repo)
    project = _project(repo, in_vault)
    assert (project / "MEMORY.md").is_file() and (project / "CURRENT.md").is_file()
    assert list((project / "journal").rglob("*.md"))
    assert not (repo.root / ".autosave/MEMORY.md").exists() and not (repo.root / ".autosave/journal").exists()
    home = (in_vault / "agent-autosave/README.md").read_text()
    assert home.startswith('---\ntags: ["agent-autosave", "agent-autosave/home"]\n---\n# agent-autosave\n')
    row = [line for line in home.splitlines() if line.startswith("| repo |")]
    assert row and "Add a CSV export for the billing rows" in row[0] and "| done |" in row[0]
    assert "[[{0}/CURRENT\\|save]] · [[{0}/MEMORY\\|memory]]".format(project.name) in row[0]
    current = (project / "CURRENT.md").read_text()
    assert current.startswith("---\n") and 'task_id: "{}"'.format(save["task_id"]) in current
    listed = json.loads((project / vault.MANIFEST).read_text())["files"]
    assert "MEMORY.md" in listed and "CURRENT.md" in listed


def test_status_and_journal_name_the_vault_paths(repo, in_vault):
    finish_task(repo)
    [page] = repo.cli("journal")[1]["data"]["pages"]
    assert page["path"].startswith(str(in_vault).replace(os.path.expanduser("~"), "~"))


def test_a_file_the_user_wrote_is_never_overwritten(repo, in_vault):
    project = _project(repo, in_vault)
    project.mkdir(parents=True)
    (project / "MEMORY.md").write_text("my own notes\n")
    with pytest.raises(vault.VaultError):
        vault.write(project, "MEMORY.md", "ours\n")
    assert (project / "MEMORY.md").read_text() == "my own notes\n"


@pytest.mark.parametrize("relative", ["../escape.md", "/etc/passwd", "journal/../../x.md", "a\\b.md", ""])
def test_paths_outside_the_folder_are_refused(tmp_path, relative):
    base = tmp_path / "vault/agent-autosave/repo-abcd"
    base.mkdir(parents=True)
    with pytest.raises(vault.VaultError):
        vault.target(base, relative)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlinks_in_the_vault_are_refused(tmp_path):
    base = tmp_path / "vault/agent-autosave/repo-abcd"
    (base / "journal").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (base / "journal/2026").symlink_to(outside, target_is_directory=True)
    with pytest.raises(vault.VaultError):
        vault.write(base, "journal/2026/10/page.md", "x")
    (base / "MEMORY.md").symlink_to(outside / "MEMORY.md")
    with pytest.raises(vault.VaultError):
        vault.write(base, "MEMORY.md", "x")
    linked = tmp_path / "linked"
    linked.symlink_to(base, target_is_directory=True)
    with pytest.raises(vault.VaultError):
        vault.write(linked, "CURRENT.md", "x")
    assert list(outside.iterdir()) == []


def test_a_missing_vault_falls_back_to_autosave_and_doctor_says_so(repo, tmp_path):
    set_config(repo, vault={"path": str(tmp_path / "not-there")})
    finish_task(repo)
    accept_all(repo)
    assert (repo.root / ".autosave/MEMORY.md").is_file()
    checks = repo.cli("doctor")[1]["data"]["checks"]
    [warning] = [c for c in checks if c["agent"] == "vault"]
    assert warning["level"] == "yellow" and "fall back to .autosave/" in warning["message"]
    assert "the vault folder does not exist" in warning["message"]


def test_vault_move_needs_yes_and_moves_the_repo_files(repo, tmp_path):
    finish_task(repo)
    accept_all(repo)
    folder = tmp_path / "Obsidian"
    folder.mkdir()
    set_config(repo, vault={"path": str(folder)})
    code, out = repo.cli("vault", "move")
    assert code == 0 and out["data"]["moved"] is False and "MEMORY.md" in out["data"]["moves"]
    assert (repo.root / ".autosave/MEMORY.md").is_file()
    code, out = repo.cli("vault", "move", "--yes")
    project = _project(repo, folder)
    assert code == 0 and out["data"]["moved"] is True
    assert (project / "MEMORY.md").is_file() and not (repo.root / ".autosave/MEMORY.md").exists()
    assert repo.cli("memory", "list")[1]["data"]["entries"]


def test_vault_move_without_a_vault_fails_cleanly(repo):
    code, out = repo.cli("vault", "move")
    assert code == 1 and out["error"]["code"] == "no_vault"


def test_remove_never_deletes_the_vault_and_says_where_it_is(repo, in_vault):
    finish_task(repo)
    accept_all(repo)
    project = _project(repo, in_vault)
    before = sorted(p.relative_to(in_vault).as_posix() for p in in_vault.rglob("*"))
    code, text = repo.text("remove", "--all")
    assert code == 0 and "Your journal and MEMORY.md stay in the vault at" in text
    assert sorted(p.relative_to(in_vault).as_posix() for p in in_vault.rglob("*")) == before
    assert project.is_dir()


def test_vault_move_writes_the_home_note_and_current_md_at_once(repo, tmp_path):
    """A real macOS run: vault move copied MEMORY.md and the journal,
    but README.md and CURRENT.md waited for the next session end."""
    save = finish_task(repo)
    accept_all(repo)
    folder = tmp_path / "Obsidian"
    folder.mkdir()
    set_config(repo, vault={"path": str(folder)})
    assert repo.cli("vault", "move", "--yes")[0] == 0
    project = _project(repo, folder)
    home = (folder / "agent-autosave/README.md").read_text()
    assert "[[{}/CURRENT\\|save]]".format(project.name) in home and "Add a CSV export" in home
    assert 'task_id: "{}"'.format(save["task_id"]) in (project / "CURRENT.md").read_text()
    listed = json.loads((project / vault.MANIFEST).read_text())["files"]
    assert "CURRENT.md" in listed


def test_the_first_memory_write_in_vault_mode_writes_both_files(repo, in_vault):
    code, out = repo.cli("memory", "add", "--type", "convention", "Run make lint before every commit")
    if not out["data"]["accepted"]:
        assert repo.cli("memory", "accept", out["data"]["candidate"]["id"])[0] == 0
    project = _project(repo, in_vault)
    assert (project / "MEMORY.md").is_file()
    assert (project / "CURRENT.md").read_text().startswith("---\n")
    assert "[[{}/CURRENT\\|save]]".format(project.name) in (in_vault / "agent-autosave/README.md").read_text()


def test_current_md_without_a_save_says_no_task_yet(tmp_path):
    folder = tmp_path / "Obsidian"
    folder.mkdir()
    project = tmp_path / "repo"
    (project / ".autosave").mkdir(parents=True)
    import copy
    config = copy.deepcopy(config_module.DEFAULTS)
    config["vault"]["path"] = str(folder)
    vault.ensure(project, config)
    name = vault.project_name(project, config)
    current = (folder / "agent-autosave" / name / "CURRENT.md").read_text()
    assert 'title: "No task yet"' in current
    assert "| No task yet |" in (folder / "agent-autosave/README.md").read_text()


def test_home_note_and_current_md_show_local_times_across_midnight(tmp_path):
    """A second real macOS run: "Last updated" showed raw UTC
    (2026-10-18T23:38:39.455967Z). At UTC+3 that is 2026-10-19 02:38."""
    import copy
    from datetime import timedelta, timezone
    zone = timezone(timedelta(hours=3))
    folder = tmp_path / "Obsidian"
    folder.mkdir()
    project = tmp_path / "repo"
    (project / ".autosave").mkdir(parents=True)
    config = copy.deepcopy(config_module.DEFAULTS)
    config["vault"]["path"] = str(folder)
    from agent_autosave.compiler import compile_events
    save = compile_events([], started="2026-10-18T23:30:00.000000Z", task_id="t_20261018_233000000000")
    save.update(goal="Add a CSV export", status="paused", updated="2026-10-18T23:38:39.455967Z")
    vault.write_current(project, config, save, tz=zone)
    vault.write_home(config, tz=zone)
    home = (folder / "agent-autosave/README.md").read_text()
    row = [line for line in home.splitlines() if line.startswith("| repo |")][0]
    assert "| 2026-10-19 02:38 |" in row and "23:38:39" not in row
    current = (folder / "agent-autosave" / vault.project_name(project, config) / "CURRENT.md").read_text()
    assert 'updated: "2026-10-18T23:38:39.455967Z"' in current  # frontmatter keeps UTC
    body = current.split("---\n", 2)[2]
    assert "2026-10-19 02:38" in body and "2026-10-18T23:38" not in body
