"""Gemini CLI, Antigravity, Copilot CLI and OpenCode end to end: install,
doctor, a hook call that records and injects, and a byte-exact remove."""
import json

import pytest

from agent_autosave.capture import handle
from agent_autosave.installer.doctor import audit
from agent_autosave.installer.install import install
from agent_autosave.installer.remove import remove

ENV = {"AGENT_AUTOSAVE_SESSION_END": "sync"}
FILES = {"gemini": ".gemini/settings.json", "antigravity": ".agents/hooks.json",
         "copilot": ".github/hooks/agent-autosave.json", "opencode": ".opencode/plugins/agent-autosave.js"}


def start_payload(agent, project):
    if agent == "gemini":
        return {"hook_event_name": "SessionStart", "session_id": "s", "cwd": str(project), "source": "startup",
                "transcript_path": "/t", "timestamp": "2026-10-19T10:00:00Z"}
    if agent == "antigravity":
        return {"conversationId": "s", "workspacePaths": [str(project)], "transcriptPath": "/t", "modelName": "m"}
    if agent == "copilot":
        return {"hook_event_name": "SessionStart", "session_id": "s", "cwd": str(project), "source": "startup",
                "timestamp": "2026-10-19T10:00:00Z"}
    return {"session_id": "s", "cwd": str(project)}


EVENTS = {"gemini": "SessionStart", "antigravity": "SessionStart", "copilot": "SessionStart",
          "opencode": "session.created"}


@pytest.mark.parametrize("agent", sorted(FILES))
def test_install_doctor_hook_and_remove(tmp_path, agent):
    install(tmp_path, [agent], command="agent-autosave", mcp=False)
    path = tmp_path / FILES[agent]
    assert path.is_file()
    checks = audit(tmp_path, which=lambda name: None)
    reds = [c for c in checks if c["level"] == "red" and c["agent"] == agent]
    assert reds == [], reds
    handle(agent, EVENTS[agent], json.dumps(start_payload(agent, tmp_path)).encode(), env=ENV)
    ledger = [json.loads(line) for line in (tmp_path / ".autosave/events.jsonl").read_text().splitlines()]
    assert ledger[-1]["agent"] == agent and ledger[-1]["kind"] == "session_start"
    remove(tmp_path)
    assert not path.exists()


def test_session_start_output_uses_each_agents_shape(tmp_path):
    from agent_autosave import capture
    assert json.loads(capture._session_start_output("hi", "copilot")) == {"additionalContext": "hi"}
    assert json.loads(capture._session_start_output("hi", "antigravity")) == {
        "injectSteps": [{"systemMessage": {"systemMessage": "hi"}}]}
    assert json.loads(capture._session_start_output("hi", "gemini"))["hookSpecificOutput"]["additionalContext"] == "hi"
    assert capture._session_start_output("hi", "opencode") == ""


def test_existing_gemini_settings_come_back_byte_for_byte(tmp_path):
    original = '{\n  "theme": "Dracula",\n  "hooks": {"BeforeTool": [{"matcher": "x", "hooks": []}]}\n}\n'
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".gemini/settings.json").write_text(original)
    install(tmp_path, ["gemini"], command="agent-autosave", mcp=True)
    data = json.loads((tmp_path / ".gemini/settings.json").read_text())
    assert data["theme"] == "Dracula" and "AfterTool" in data["hooks"] and "autosave" in data["mcpServers"]
    assert data["hooks"]["SessionStart"][0]["hooks"][0]["timeout"] == 5000  # milliseconds in Gemini CLI
    remove(tmp_path)
    assert (tmp_path / ".gemini/settings.json").read_text() == original


def test_existing_antigravity_hooks_come_back_byte_for_byte(tmp_path):
    original = '{\n  "my-linter-hook": {"PostToolUse": [{"matcher": "run_command", "hooks": []}]}\n}\n'
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents/hooks.json").write_text(original)
    install(tmp_path, ["antigravity"], command="agent-autosave")
    assert "agent-autosave" in json.loads((tmp_path / ".agents/hooks.json").read_text())
    remove(tmp_path)
    assert (tmp_path / ".agents/hooks.json").read_text() == original


def test_claude_hooks_run_by_copilot_are_dropped(tmp_path):
    """Copilot CLI also reads .claude/settings.json; its payloads carry a timestamp."""
    install(tmp_path, ["claude-code"], command="agent-autosave", statusline="off")
    payload = {"hook_event_name": "SessionStart", "session_id": "x", "cwd": str(tmp_path), "source": "startup",
               "timestamp": "2026-10-19T10:00:00Z"}
    assert handle("claude-code", "SessionStart", json.dumps(payload).encode(), env=ENV) == ""
    assert not (tmp_path / ".autosave/events.jsonl").exists()


def test_the_opencode_plugin_runs_our_command(tmp_path):
    install(tmp_path, ["opencode"], command="/opt/my tools/agent-autosave")
    text = (tmp_path / ".opencode/plugins/agent-autosave.js").read_text()
    assert 'const COMMAND = "/opt/my tools/agent-autosave"' in text


def test_handoff_context_names_every_hook_agent():
    """A task left by Copilot CLI, OpenCode or Antigravity was introduced as
    "another agent": the loader's names stopped at Gemini CLI."""
    from agent_autosave import loader
    from agent_autosave.commands.common import NAMES
    for agent in ("gemini", "antigravity", "copilot", "opencode"):
        assert loader.AGENT_NAMES[agent] == NAMES[agent]
