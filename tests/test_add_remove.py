"""add and remove across every agent: byte-exact restores, shared files, reformatted hooks, upgrade."""
import json

import pytest

from agent_autosave.installer import doctor
from agent_autosave.installer.install import add, install
from agent_autosave.installer.remove import remove
from tests.helpers import ALL, CMD, snapshot, user_files, write


def test_install_all_then_remove_all_is_byte_exact(tmp_path, home):
    project = tmp_path / "repo"
    user_files(project)
    before = snapshot(project)
    install(project, ALL, command=CMD)
    after = snapshot(project)
    for name in (".claude/settings.json", "CLAUDE.md", ".codex/hooks.json", "AGENTS.md", ".cursor/hooks.json",
                 "GEMINI.md", ".gitignore"):
        assert after[name] != before[name], name
    assert "ANTIGRAVITY.md" in after and ".cursor/rules/agent-autosave.mdc" in after
    assert after["AGENTS.md"].count(b"agent-autosave:start") == 1
    # Absolute command path in every block.
    assert b"'/opt/my tools/agent-autosave' plan set" in after["GEMINI.md"]
    assert b"'/opt/my tools/agent-autosave' plan set" in after["ANTIGRAVITY.md"]
    remove(project)
    assert snapshot(project) == before
    assert not (project / ".cursor/rules").exists()
    assert not (project / ".autosave/install.json").exists() and (project / ".autosave").is_dir()


@pytest.mark.parametrize("order", [ALL, tuple(reversed(ALL)), ("generic", "cursor", "codex", "gemini", "claude-code", "antigravity")])
def test_removing_agents_one_by_one_is_byte_exact(tmp_path, home, order):
    project = tmp_path / "repo"
    user_files(project)
    before = snapshot(project)
    install(project, ALL, command=CMD)
    for index, agent in enumerate(order):
        result = remove(project, agent)
        assert agent not in result["agents"]
        if index < len(order) - 1:
            assert (project / ".autosave/install.json").exists()
    assert snapshot(project) == before


def test_shared_agents_block_stays_until_last_owner(tmp_path, home):
    project = tmp_path / "repo"
    write(project / "AGENTS.md", b"# Rules\n")
    install(project, ("codex", "generic"), command=CMD, codex_hooks=False)
    remove(project, "codex")
    assert b"agent-autosave:start" in (project / "AGENTS.md").read_bytes()
    receipt = json.loads((project / ".autosave/install.json").read_text())
    assert [e["agents"] for e in receipt["files"] if e["path"] == "AGENTS.md"] == [["generic"]]
    remove(project, "generic")
    assert (project / "AGENTS.md").read_bytes() == b"# Rules\n"
    assert not (project / ".gitignore").exists()


def test_add_to_an_install_then_remove_restores_bytes(tmp_path, home):
    project = tmp_path / "repo"
    user_files(project)
    before = snapshot(project)
    install(project, ("claude-code",), command=CMD)
    result = add(project, "cursor")
    assert not result["repaired"] and set(result["changed"]) == {".cursor/hooks.json", ".cursor/rules/agent-autosave.mdc"}
    hooks = json.loads((project / ".cursor/hooks.json").read_text())["hooks"]
    assert hooks["stop"][0] == {"command": "./audit.sh"}
    assert hooks["stop"][1]["command"] == "'/opt/my tools/agent-autosave' hook cursor stop"
    assert hooks["postToolUse"][0]["matcher"] == "Shell"
    again = add(project, "cursor")
    assert again["repaired"] and again["changed"] == []
    add(project, "generic")  # AGENTS.md: a new block (codex is not installed)
    remove(project)
    assert snapshot(project) == before


def test_legacy_receipt_without_owners_still_removes_per_agent(tmp_path, home):
    project = tmp_path / "repo"
    user_files(project)
    before = snapshot(project)
    install(project, ("claude-code", "codex"), command=CMD)
    path = project / ".autosave/install.json"
    receipt = json.loads(path.read_text())
    for item in receipt["files"]:
        del item["agents"]  # an older receipt without owners
    path.write_text(json.dumps(receipt))
    remove(project, "codex")
    assert b"agent-autosave" not in (project / "AGENTS.md").read_bytes()
    assert b"agent-autosave" in (project / "CLAUDE.md").read_bytes()
    remove(project, "claude-code")
    assert snapshot(project) == before


# --- upgrade

def test_upgrade_dry_run_then_yes_refreshes_the_command(tmp_path, home, monkeypatch, capsys):
    from agent_autosave import cli
    from agent_autosave.installer import detect
    project = tmp_path / "repo"
    user_files(project)
    before = snapshot(project)
    install(project, ("claude-code", "cursor"), command="/old/agent-autosave")
    monkeypatch.setattr(detect, "hook_command", lambda **k: CMD)
    monkeypatch.chdir(project)
    assert cli.main(["--json", "upgrade"]) == 0
    plan = json.loads(capsys.readouterr().out)["data"]
    assert plan["dry_run"] and any("/old/agent-autosave" in r for r in plan["reasons"])
    assert b"/old/agent-autosave" in (project / ".cursor/hooks.json").read_bytes()
    assert cli.main(["--json", "upgrade", "--yes"]) == 0
    assert b"/old/agent-autosave" not in (project / ".cursor/hooks.json").read_bytes()
    assert json.loads((project / ".autosave/install.json").read_text())["command"] == CMD
    remove(project)
    assert snapshot(project) == before


def test_cli_remove_needs_an_agent_or_all(tmp_path, home, monkeypatch, capsys):
    from agent_autosave import cli
    project = tmp_path / "repo"
    project.mkdir()
    install(project, ("gemini",), command=CMD)
    monkeypatch.chdir(project)
    assert cli.main(["--json", "remove"]) == 2
    assert cli.main(["--json", "remove", "cursor"]) == 1
    assert cli.main(["--json", "remove", "gemini"]) == 0
    assert not (project / "GEMINI.md").exists()


# --- remove finds our hooks by content; add's repair keeps records honest

JSON_HOOKS = {"claude-code": (".claude/settings.json", "Stop"), "codex": (".codex/hooks.json", "Stop"),
              "cursor": (".cursor/hooks.json", "stop")}


def _reformat(path, delete_event):
    data = json.loads(path.read_text())
    del data["hooks"][delete_event]
    path.write_text(json.dumps(data, indent=4))  # as a user editing with json.dump would


def _mentions(project):
    return [n for n, b in snapshot(project).items() if b"agent-autosave hook" in b or b'"hook", "' in b]


@pytest.mark.parametrize("agent", sorted(JSON_HOOKS))
def test_remove_after_reformat_deletes_files_and_folders_init_created(tmp_path, home, agent):
    project = tmp_path / "repo"
    project.mkdir()
    install(project, (agent,), command=CMD)
    name, event = JSON_HOOKS[agent]
    _reformat(project / name, event)
    result = remove(project)
    assert name in result["by_content"] and result["missing_spans"] == 0
    assert snapshot(project) == {}  # every file and folder init created is gone
    assert [p.name for p in project.iterdir()] == [".autosave"]


@pytest.mark.parametrize("agent", sorted(JSON_HOOKS))
def test_remove_after_reformat_keeps_user_content(tmp_path, home, agent):
    project = tmp_path / "repo"
    name, event = JSON_HOOKS[agent]
    if agent == "cursor":
        user = {"version": 1, "hooks": {"stop": [{"command": "./audit.sh"}]}}
    else:
        user = {"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say hi"}]}]}}
    write(project / name, json.dumps(user).encode())
    install(project, (agent,), command=CMD)
    _reformat(project / name, "SessionStart" if agent != "cursor" else "sessionStart")
    remove(project)
    assert json.loads((project / name).read_text()) == user
    assert b"agent-autosave" not in (project / name).read_bytes()


def test_remove_by_content_on_the_e2e_settings_file(tmp_path, home):
    """Seen in a real run: Stop deleted with json.dump; remove --all left 1474 bytes behind."""
    project = tmp_path / "repo"
    project.mkdir()
    install(project, ("claude-code", "generic"), command=CMD)
    path = project / ".claude/settings.json"
    data = json.loads(path.read_text())
    del data["hooks"]["Stop"]
    path.write_text(json.dumps(data, indent=2))
    remove(project)
    assert not (project / ".claude").exists() and _mentions(project) == []


def test_unreformatted_remove_stays_byte_exact(tmp_path, home):
    project = tmp_path / "repo"
    user_files(project)
    before = snapshot(project)
    install(project, ("claude-code", "codex", "cursor"), command=CMD)
    result = remove(project)
    assert result["by_content"] == [] and snapshot(project) == before


@pytest.mark.parametrize("agent", sorted(JSON_HOOKS))
def test_doctor_add_repair_remove_reports_nothing_missing(tmp_path, home, agent):
    project = tmp_path / "repo"
    project.mkdir()
    exe = tmp_path / "bin/agent-autosave"
    write(exe, b"#!/bin/sh\n")
    exe.chmod(0o755)
    install(project, (agent,), command=str(exe))
    name, event = JSON_HOOKS[agent]
    _reformat(project / name, event)
    assert [c for c in doctor.audit(project, which=lambda n: "/x/" + n) if c["level"] == "red" and event in c["message"]]
    assert add(project, agent)["repaired"]
    assert not [c for c in doctor.audit(project, which=lambda n: "/x/" + n) if c["level"] == "red"]
    receipt = json.loads((project / ".autosave/install.json").read_text())
    assert all(e.get("by_content") for e in receipt["files"] if e["path"] == name)
    result = remove(project, agent)
    assert result["missing_spans"] == 0 and name in result["by_content"]
    assert not (project / name).exists() and not (project / name).parent.exists()


def test_block_repair_then_remove_reports_nothing_missing(tmp_path, home):
    project = tmp_path / "repo"
    write(project / "GEMINI.md", b"# mine\n")
    install(project, ("gemini",), command=CMD)
    (project / "GEMINI.md").write_bytes(b"# mine\n")  # block deleted by hand
    assert add(project, "gemini")["repaired"]
    result = remove(project, "gemini")
    assert result["missing_spans"] == 0 and (project / "GEMINI.md").read_bytes() == b"# mine\n"
