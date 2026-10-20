"""Instruction-block agents (Gemini CLI, Antigravity, generic): blocks and resume."""
from pathlib import Path
import subprocess

import pytest

from agent_autosave.installer.install import install
from tests.helpers import CMD, ROOT


def test_generic_and_codex_share_one_agents_block():
    assert (ROOT / "adapters/generic/AGENTS.block.md").read_bytes() == (ROOT / "adapters/codex/AGENTS.block.md").read_bytes()


@pytest.mark.parametrize("name", ["GEMINI", "ANTIGRAVITY"])
def test_hooked_blocks_are_slim_like_claude_md(name):
    """Gemini CLI and Antigravity have hooks now: no save-by-hand steps."""
    agent = name.lower()
    text = (ROOT / "adapters" / agent / (name + ".block.md")).read_text()
    assert "agent-autosave plan set --quiet" in text and "save by hand" not in text
    assert text == (ROOT / "adapters/claude-code/CLAUDE.block.md").read_text()


# --- resume for Gemini CLI and Antigravity

@pytest.mark.parametrize("agent,binary", [("gemini", "gemini"), ("antigravity", "agy")])
def test_resume_gemini_and_antigravity_use_prompt_interactive(tmp_path, monkeypatch, capsys, agent, binary):
    from tests.test_commands import handoff_events, run, seed
    project = tmp_path / "repo"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    install(project, (agent,), command=CMD)
    seed(project, *handoff_events())
    code, out = run(monkeypatch, capsys, project, "resume", agent, "--dry-run")
    assert code == 0
    command = out["data"]["command"]
    assert Path(command[0]).name == binary and command[1] == "--prompt-interactive"
    assert command[2].startswith("[agent-autosave resume context]")


def test_resume_generic_explains_the_manual_handoff(tmp_path, monkeypatch, capsys):
    from tests.test_commands import handoff_events, run, seed
    project = tmp_path / "repo"
    project.mkdir()
    install(project, ("generic",), command=CMD)
    seed(project, *handoff_events())
    code, out = run(monkeypatch, capsys, project, "resume", "generic", "--dry-run")
    assert code == 2 and "show --context" in out["error"]["hint"]
