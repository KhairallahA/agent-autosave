"""The suite passes from any shell: a sample of it runs with HOME, PATH,
XDG_CONFIG_HOME, the agent markers and AGENT_AUTOSAVE_* set to junk.

conftest.py resets all of them for every test; this proves nothing leaks
past it (a developer's earlier manual check, or an agent running the tests).
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.helpers import ROOT

SAMPLE = ["tests/test_memory_wiring.py", "tests/test_init.py", "tests/test_note_source.py",
          "tests/test_add_remove.py", "tests/test_codex_layers.py", "tests/test_vault.py", "tests/test_capture.py"]
MARKERS = ("CLAUDE_CODE_CHILD_SESSION", "CODEX_TOOL_CALL_ID", "CURSOR_AGENT", "CLAUDECODE", "GEMINI_CLI")


def junk_home(root: Path) -> Path:
    """A home that looks lived in: agents installed, hooks at user level and
    a user config pointing memory at a vault."""
    home = root / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude/settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "agent-autosave hook claude-code Stop"}]}]}, "statusLine": {
        "type": "command", "command": "echo mine"}}))
    (home / ".codex").mkdir()
    (home / ".codex/hooks.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "agent-autosave hook codex Stop"}]}]}}))
    (home / ".config/agent-autosave").mkdir(parents=True)
    (home / ".config/agent-autosave/config.json").write_text(json.dumps({"vault": {"path": str(root / "vault")}}))
    return home


def junk_bin(root: Path) -> Path:
    """Agent binaries (and a stale agent-autosave) that fail if anything runs them."""
    folder = root / "bin"
    folder.mkdir()
    for name in ("claude", "codex", "cursor", "cursor-agent", "gemini", "agy", "copilot", "opencode", "agent-autosave"):
        (folder / name).write_text("#!/bin/sh\necho junk >&2\nexit 3\n")
        (folder / name).chmod(0o755)
    return folder


@pytest.mark.slow
@pytest.mark.skipif(os.name == "nt", reason="the junk binaries are POSIX shell scripts")
def test_a_sample_of_the_suite_passes_with_junk_in_every_variable_it_could_read(tmp_path):
    env = dict(os.environ)
    env.update({name: "junk" for name in MARKERS})
    env.update(HOME=str(junk_home(tmp_path)), XDG_CONFIG_HOME=str(tmp_path / "home/.config"),
               PATH=str(junk_bin(tmp_path)) + os.pathsep + env.get("PATH", ""),
               AGENT_AUTOSAVE_SESSION_END="detached", AGENT_AUTOSAVE_RECORD_DIR=str(tmp_path / "record"),
               AGENT_AUTOSAVE_AGENTIC_STACK=str(tmp_path / "nowhere"), AGENT_AUTOSAVE_JUNK="1",
               CODEX_HOME=str(tmp_path / "home/.codex"))
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
                             "-m", "not slow", "-x"] + SAMPLE, cwd=str(ROOT), env=env, capture_output=True,
                            text=True, timeout=600)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert not (tmp_path / "record").exists()
