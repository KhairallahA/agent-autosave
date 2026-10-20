"""Claude Code hooks are one shell command string, like the Codex and Cursor hooks.

Source: a real macOS run (the Cursor check in docs/testing/release-checklist.md). Cursor runs a
project's Claude Code hooks too (third-party import) and ignores "args", so the
old {"command": "<path>", "args": [...]} form ran agent-autosave with no
arguments and Cursor showed an invalid_args envelope.
"""
import json
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from agent_autosave.installer import install as install_module
from agent_autosave.installer import upgrade
from agent_autosave.installer.install import install
from agent_autosave.installer.remove import remove
from tests.helpers import CMD, snapshot, write

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX shell quoting")

USER_SETTINGS = (b'{\n  "hooks": {\n    "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]\n'
                 b'  },\n  "env": {"A": "1"}\n}\n')



def old_hooks(command):
    """The shipped hooks.json in the old form: the executable plus an args list."""
    hooks = json.loads((install_module._resources("claude-code") / "hooks.json").read_text())
    for event, entries in hooks["hooks"].items():
        for entry in entries:
            for hook in entry["hooks"]:
                hook["command"], hook["args"] = command, ["hook", "claude-code", event]
                # keep the old key order: type, command, args, then the rest
                rest = {k: v for k, v in hook.items() if k not in ("type", "command", "args")}
                entry["hooks"][entry["hooks"].index(hook)] = dict(
                    {"type": "command", "command": command, "args": hook["args"]}, **rest)
    return hooks


def claude_hooks(project):
    data = json.loads((project / ".claude/settings.json").read_text())
    return [(event, hook) for event, entries in data["hooks"].items() for entry in entries
            for hook in entry.get("hooks", []) if "agent-autosave" in hook["command"]]


def fake_executable(tmp_path):
    """An agent-autosave stand-in under a path with spaces that logs its argv."""
    folder = tmp_path / "my tools dir"
    folder.mkdir()
    path = folder / "agent-autosave"
    path.write_text("#!/bin/sh\nfor a in \"$@\"; do printf '%s|' \"$a\"; done >> \"$ARGV_LOG\"\necho >> \"$ARGV_LOG\"\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def old_form_install(project, monkeypatch, agents=("claude-code",), command=CMD):
    """Install as releases before this fix did: command plus an args list."""
    original_jobs = install_module.jobs_for

    def old_jobs(agent, command, **kwargs):
        jobs = original_jobs(agent, command, **kwargs)
        if agent != "claude-code":
            return jobs
        out = []
        for destination, policy, addition in jobs:
            if policy == "merge_json":
                addition = old_hooks(command)
            out.append((destination, policy, addition))
        return out
    with monkeypatch.context() as patch:
        patch.setattr(install_module, "jobs_for", old_jobs)
        install(project, agents, command=command)
    assert all("args" in hook for _, hook in claude_hooks(project))


def test_every_claude_hook_is_one_shell_quoted_command_string(tmp_path, home):
    project = tmp_path / "repo"
    project.mkdir()
    install(project, ("claude-code",), command=CMD)
    hooks = claude_hooks(project)
    assert {event for event, _ in hooks} >= {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"}
    for event, hook in hooks:
        assert "args" not in hook
        assert hook["command"] == "{} hook claude-code {}".format(shlex.quote(CMD), event)


def test_the_hook_string_runs_through_a_shell_with_spaces_in_the_path(tmp_path, home):
    project = tmp_path / "repo"
    project.mkdir()
    executable = fake_executable(tmp_path)
    install(project, ("claude-code",), command=str(executable))
    log = tmp_path / "argv.log"
    for event, hook in claude_hooks(project):
        subprocess.run(["/bin/sh", "-c", hook["command"]], check=True, cwd=str(project),
                       env=dict(os.environ, ARGV_LOG=str(log)), input=b"{}", timeout=10)
        assert log.read_text().splitlines()[-1] == "hook|claude-code|{}|".format(event)


def test_upgrade_migrates_old_args_hooks_and_remove_restores_bytes(tmp_path, home, monkeypatch):
    project = tmp_path / "repo"
    project.mkdir()
    write(project / ".claude/settings.json", USER_SETTINGS)
    write(project / "CLAUDE.md", b"# Rules\n")
    before = snapshot(project)
    old_form_install(project, monkeypatch)
    reasons = upgrade.plan(project, CMD)["reasons"]
    assert any("one command string" in reason for reason in reasons), reasons
    upgrade.apply(project, CMD)
    for event, hook in claude_hooks(project):
        assert "args" not in hook and hook["command"].endswith(" hook claude-code " + event)
    assert "say done" in (project / ".claude/settings.json").read_text()
    assert upgrade.plan(project, CMD)["reasons"] == []
    remove(project)
    after = snapshot(project)
    assert {k: v for k, v in after.items() if not k.startswith(".autosave")} == \
        {k: v for k, v in before.items() if not k.startswith(".autosave")}


def test_remove_finds_old_form_hooks_by_content_after_a_reformat(tmp_path, home, monkeypatch):
    project = tmp_path / "repo"
    project.mkdir()
    write(project / ".claude/settings.json", USER_SETTINGS)
    old_form_install(project, monkeypatch)
    path = project / ".claude/settings.json"
    path.write_text(json.dumps(json.loads(path.read_text()), indent=4) + "\n")
    remove(project)
    data = json.loads(path.read_text())
    assert "agent-autosave" not in path.read_text()
    assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == "say done" and data["env"] == {"A": "1"}


def run_bare(payload, cwd):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return subprocess.run([sys.executable, "-m", "agent_autosave"], input=payload, capture_output=True,
                          cwd=str(cwd), env=env, timeout=10)


def test_no_subcommand_with_a_json_hook_payload_on_stdin_is_silent(tmp_path):
    payload = json.dumps({"session_id": "c-1", "hook_event_name": "SessionStart", "cursor_version": "2.4.21"})
    result = run_bare(payload.encode(), tmp_path)
    assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")


@pytest.mark.parametrize("payload", [b"", b"not json", b"[1, 2]"])
def test_no_subcommand_without_a_json_object_keeps_the_envelope(tmp_path, payload):
    result = run_bare(payload, tmp_path)
    assert result.returncode == 2 and b"Choose a command or use --version." in result.stdout + result.stderr


def test_no_subcommand_from_a_terminal_keeps_the_envelope(monkeypatch, capsys):
    from agent_autosave import cli

    class Terminal:
        def isatty(self):
            return True

        def read(self, *a):
            raise AssertionError("a terminal is never read")
    monkeypatch.setattr(sys, "stdin", Terminal())
    assert cli.main(["--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_args"


@pytest.mark.parametrize("form", ["string", "old-args"])
def test_a_cursor_imported_claude_hook_records_and_prints_nothing(tmp_path, home, monkeypatch, form):
    """Cursor runs .claude/settings.json hooks with its own payload (cursor_version)."""
    project = tmp_path / "repo"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    launcher = tmp_path / ("bin dir" if form == "string" else "bin") / "agent-autosave"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\nexec {} -m agent_autosave \"$@\"\n".format(shlex.quote(sys.executable)))
    launcher.chmod(0o755)
    if form == "string":
        install(project, ("cursor", "claude-code"), command=str(launcher))
    else:
        old_form_install(project, monkeypatch, ("cursor", "claude-code"), str(launcher))
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    for event, hook in claude_hooks(project):
        payload = {"session_id": "c-1", "conversation_id": "c-1", "hook_event_name": event, "prompt": "hi",
                   "cwd": str(project), "workspace_roots": [str(project)], "cursor_version": "2.4.21"}
        # Cursor ignores "args": it runs only the command string.
        result = subprocess.run(["/bin/sh", "-c", hook["command"]], input=json.dumps(payload).encode(),
                                capture_output=True, cwd=str(project), env=env, timeout=10)
        assert (event, result.returncode, result.stdout, result.stderr) == (event, 0, b"", b"")
    events = project / ".autosave/events.jsonl"
    assert not events.exists() or events.read_text() == ""
