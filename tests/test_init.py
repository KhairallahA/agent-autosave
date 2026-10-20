"""`agent-autosave init` for Claude Code, and hook-command resolution."""
import json
import os
from pathlib import Path
import re
import subprocess

from agent_autosave.installer import detect
from agent_autosave.installer.install import install
from agent_autosave.installer.remove import remove

from tests.helpers import LENIENT_CLI

SETTINGS = b'{\n  "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}\n}\n'


def run(project, home, *args):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("PYTHONPATH", None)
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep)
                                  if not (Path(p) / "claude").exists() and not (Path(p) / "codex").exists())
    return subprocess.run([*LENIENT_CLI, "init"] + list(args), cwd=str(project),
                          env=env, capture_output=True, timeout=20)


def homes(tmp_path):
    with_claude, without = tmp_path / "home-a", tmp_path / "home-b"
    (with_claude / ".claude").mkdir(parents=True)
    without.mkdir()
    project = tmp_path / "repo"
    project.mkdir()
    return project, with_claude, without


def test_init_wires_claude_code_and_merges(tmp_path):
    project, home, _ = homes(tmp_path)
    (project / ".claude").mkdir()
    (project / ".claude/settings.json").write_bytes(SETTINGS)
    result = run(project, home, "--json")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)["data"]
    assert data["agents"] == ["claude-code"]
    assert set(data["changed"]) == {".claude/settings.json", "CLAUDE.md", ".mcp.json", ".gitignore"}
    assert ".autosave/config.json" in data["created"]
    settings = json.loads((project / ".claude/settings.json").read_text())
    assert settings["hooks"]["Stop"][0]["hooks"][0]["command"] == "say done"
    assert len(settings["hooks"]) == 8
    ignore = (project / ".gitignore").read_text()
    # One line: .autosave/ covers events.jsonl, errors.log and history/.
    assert ignore.endswith("# agent-autosave:start\n.autosave/\n# agent-autosave:end\n") and "<!--" not in ignore
    receipt = json.loads((project / ".autosave/install.json").read_text())
    assert receipt["agents"] == ["claude-code"] and len(receipt["files"]) == 5 and receipt["statusline"] == {"mode": "added"}
    assert receipt["hooks"] == {"claude-code": {"layer": "project", "path": ".claude/settings.json", "added": True}}
    assert json.loads((project / ".autosave/config.json").read_text())["v"] == 1
    remove(project)
    assert (project / ".claude/settings.json").read_bytes() == SETTINGS
    assert not any((project / name).exists() for name in (".gitignore", "CLAUDE.md"))


def test_init_human_output_lists_changes(tmp_path, monkeypatch):
    from agent_autosave import arguments
    from agent_autosave.commands import init as init_command
    project, home, _ = homes(tmp_path)
    monkeypatch.chdir(project)
    monkeypatch.setattr(detect, "claude_code", lambda: {"installed": True, "version": "2.1.291", "path": None})
    result, code, text = init_command.run(arguments.build_parser().parse_args(["init"]))
    assert code == 0 and result["ok"]
    assert re.search(r"Claude Code 2\.1\.291 +8 hooks in \.claude/settings\.json", text)
    assert ".gitignore" in text and "\033[" not in text  # not a terminal: no colour


def test_init_without_claude_creates_autosave_only(tmp_path):
    project, _, home = homes(tmp_path)
    result = run(project, home, "--json")
    assert result.returncode == 0
    data = json.loads(result.stdout)["data"]
    assert data["agents"] == [] and data["changed"] == [".gitignore"]
    assert not (project / ".claude").exists() and (project / ".autosave/config.json").exists()


def test_init_explicit_agent_missing_is_not_found(tmp_path):
    project, _, home = homes(tmp_path)
    result = run(project, home, "--json", "--agents", "claude-code")
    assert result.returncode == 1
    assert json.loads(result.stdout)["error"]["code"] == "agent_not_installed"


def test_init_twice_changes_nothing(tmp_path):
    project, home, _ = homes(tmp_path)
    run(project, home)
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file() and ".autosave" not in p.parts}
    result = run(project, home, "--json")
    assert json.loads(result.stdout)["data"]["already_installed"] is True
    assert before == {p: p.read_bytes() for p in project.rglob("*") if p.is_file() and ".autosave" not in p.parts}


def test_invalid_user_settings_are_left_untouched(tmp_path):
    project, home, _ = homes(tmp_path)
    (project / ".claude").mkdir()
    (project / ".claude/settings.json").write_bytes(b'{"hooks": [broken')
    result = run(project, home, "--json")
    assert result.returncode == 5
    assert (project / ".claude/settings.json").read_bytes() == b'{"hooks": [broken'


def test_hook_command_is_absolute_when_found():
    assert detect.hook_command(which=lambda name: "/opt/bin/agent-autosave") == "/opt/bin/agent-autosave"
    assert detect.hook_command(which=lambda name: None) == "agent-autosave"


def test_install_writes_resolved_command_into_every_hook(tmp_path):
    install(tmp_path, command="/abs/agent-autosave")
    hooks = json.loads((tmp_path / ".claude/settings.json").read_text())["hooks"]
    assert {e[0]["hooks"][0]["command"].split(" hook claude-code ")[0] for e in hooks.values()} == {"/abs/agent-autosave"}
    assert json.loads((tmp_path / ".autosave/install.json").read_text())["command"] == "/abs/agent-autosave"


def test_detect_reports_missing_and_present(tmp_path):
    assert detect.claude_code(which=lambda n: None, home=tmp_path)["installed"] is False
    (tmp_path / ".claude").mkdir()
    assert detect.claude_code(which=lambda n: None, home=tmp_path) == {"installed": True, "version": None, "path": None}


def test_init_reports_the_hooks_of_every_hook_agent(tmp_path, monkeypatch):
    """`init --agents copilot` crashed (KeyError) and Gemini CLI and
    Antigravity were reported as "instructions only; no hooks"."""
    from agent_autosave import arguments
    from agent_autosave.commands import init as init_command
    project, home, _ = homes(tmp_path)
    monkeypatch.chdir(project)
    for name in ("gemini", "antigravity", "copilot", "opencode"):
        monkeypatch.setattr(detect, name, lambda **_: {"installed": True, "version": None, "path": None})
    args = arguments.build_parser().parse_args(["init", "--agents", "gemini,antigravity,copilot,opencode"])
    result, code, text = init_command.run(args)
    assert code == 0 and result["ok"], result
    assert re.search(r"Gemini CLI +6 hooks in \.gemini/settings\.json", text)
    assert re.search(r"Antigravity +3 hooks in \.agents/hooks\.json", text)
    assert re.search(r"Copilot CLI +7 hooks in \.github/hooks/agent-autosave\.json", text)
    assert re.search(r"OpenCode +plugin in \.opencode/plugins/agent-autosave\.js", text)
    assert "no hooks" not in text
