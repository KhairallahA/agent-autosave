"""doctor: what each broken install looks like and the fix it prints."""
import json

from agent_autosave.installer import doctor
from agent_autosave.installer.install import add, install
from tests.helpers import write


def _delete_hook(path, event):
    data = json.loads(path.read_text())
    del data["hooks"][event]
    path.write_text(json.dumps(data))


def test_doctor_flags_a_deleted_hook_in_red_with_its_fix(tmp_path, home, monkeypatch, capsys):
    project = tmp_path / "repo"
    project.mkdir()
    exe = tmp_path / "bin/agent-autosave"
    write(exe, b"#!/bin/sh\n")
    exe.chmod(0o755)
    install(project, ("claude-code",), command=str(exe))
    checks = doctor.audit(project, which=lambda name: "/usr/bin/" + name)
    assert not [c for c in checks if c["level"] == "red"]
    _delete_hook(project / ".claude/settings.json", "Stop")
    checks = doctor.audit(project, which=lambda name: "/usr/bin/" + name)
    [red] = [c for c in checks if c["level"] == "red"]
    assert red["agent"] == "claude-code" and "Stop" in red["message"] and red["hint"] == "agent-autosave add claude-code"
    text = doctor.render(checks, color=True)
    assert "\033[31mFAIL\033[0m" in text and "hint: agent-autosave add claude-code" in text
    # The CLI exits 3, then add repairs only the missing hook.
    monkeypatch.chdir(project)
    from agent_autosave import cli
    assert cli.main(["doctor"]) == 1
    assert add(project, "claude-code")["repaired"]
    assert "Stop" in json.loads((project / ".claude/settings.json").read_text())["hooks"]
    assert not [c for c in doctor.audit(project, which=lambda n: "/x/" + n) if c["level"] == "red"]


def test_doctor_reds_for_missing_block_command_and_invalid_json(tmp_path, home):
    project = tmp_path / "repo"
    project.mkdir()
    install(project, ("cursor", "gemini"), command=str(tmp_path / "gone/agent-autosave"))
    (project / "GEMINI.md").write_text("# mine\n")
    (project / ".cursor/hooks.json").write_text("{oops")
    reds = {(c["agent"], c["hint"]) for c in doctor.audit(project) if c["level"] == "red"}
    assert ("core", "agent-autosave upgrade --yes") in reds
    assert ("gemini", "agent-autosave add gemini") in reds
    assert any(agent == "cursor" for agent, _ in reds)
