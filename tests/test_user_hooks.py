"""Leftover user-level hooks: init warns (read-only); removal tool."""
import io
import json
import os
import subprocess
import sys

from agent_autosave.installer import userhooks
from tests.test_init import homes, run

USER = {
    "model": "opus",
    "permissions": {"allow": ["Bash(ls:*)"]},
    "hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "say done"},
                            {"type": "command", "command": "agent-autosave", "args": ["hook", "claude-code", "Stop"]}]}],
        "SessionStart": [{"hooks": [{"type": "command", "command": "/Users/k/.local/bin/agent-autosave",
                                     "args": ["hook", "claude-code", "SessionStart"]}]}],
        "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command",
                                                    "command": "python3 -m agent_autosave hook claude-code PostToolUse"}]},
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "./guard.sh"}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo agent-autosave-like-but-not"}]}],
    },
}


def write_user(home, data=USER, indent=4):
    path = home / ".claude/settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=indent) + "\n")
    return path


def test_init_warns_about_user_level_hooks_and_never_touches_that_file(tmp_path):
    project, home, _ = homes(tmp_path)
    path = write_user(home)
    before = path.read_bytes()
    result = run(project, home, "--json")
    assert result.returncode == 0
    [warning] = json.loads(result.stdout)["data"]["warnings"]
    assert warning["code"] == "duplicate_user_hooks" and warning["hooks"] == 3
    assert "-m agent_autosave.installer.userhooks --dry-run" in warning["hint"]
    assert path.read_bytes() == before
    text = run(project, home)  # piped: JSON again; the human text is the same data
    assert path.read_bytes() == before and text.returncode == 0


def test_init_without_user_hooks_has_no_warning(tmp_path):
    project, home, _ = homes(tmp_path)
    write_user(home, {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}})
    assert json.loads(run(project, home, "--json").stdout)["data"]["warnings"] == []


def test_removal_dry_run_then_real_keeps_everything_else(tmp_path):
    path = write_user(tmp_path)
    before = path.read_bytes()
    out = io.StringIO()
    assert userhooks.main(["--dry-run"], home=tmp_path, out=out) == 0
    assert path.read_bytes() == before and out.getvalue().count("would remove") == 3
    out = io.StringIO()
    assert userhooks.main([], home=tmp_path, out=out) == 0
    assert out.getvalue().count("removed") == 3
    [backup] = list(path.parent.glob("settings.json.agent-autosave-backup-*"))
    assert backup.read_bytes() == before
    data = json.loads(path.read_text())
    assert data["model"] == "opus" and data["permissions"] == USER["permissions"]
    assert data["hooks"] == {
        "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}],
        "PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "./guard.sh"}]}],
        "UserPromptSubmit": USER["hooks"]["UserPromptSubmit"],
    }
    assert path.read_text().startswith('{\n    "model"')  # 4-space indent kept
    assert userhooks.main([], home=tmp_path, out=io.StringIO()) == 0  # idempotent
    assert len(list(path.parent.glob("settings.json.agent-autosave-backup-*"))) == 1


def test_removal_refuses_non_json_and_handles_missing(tmp_path):
    out = io.StringIO()
    assert userhooks.main([], home=tmp_path, out=out) == 0 and "nothing to do" in out.getvalue()
    path = tmp_path / ".claude/settings.json"
    path.parent.mkdir()
    path.write_text("{ // comment\n}")
    assert userhooks.main([], home=tmp_path, out=io.StringIO()) == 1
    assert path.read_text() == "{ // comment\n}"


def test_module_entrypoint_runs_on_a_temp_home(tmp_path):
    write_user(tmp_path)
    env = dict(os.environ, HOME=str(tmp_path))
    command = [sys.executable, "-m", "agent_autosave.installer.userhooks", "--dry-run"]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0 and "would remove Stop: agent-autosave hook claude-code Stop" in result.stdout
