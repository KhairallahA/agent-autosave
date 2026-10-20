"""merge_json / managed_block install and exact removal, byte for byte."""
import json
from pathlib import Path

import pytest

from agent_autosave.installer.install import install, managed_block, merge_json
from agent_autosave.installer.remove import remove
from tests.helpers import snapshot, write

ROOT = Path(__file__).resolve().parents[1]
USER_SETTINGS = (b'{\n  // my hooks\n  "model": "opus",\n  "hooks": {\n    "Stop": [\n'
                 b'      {"hooks": [{"type": "command", "command": "say done"}]}\n    ],\n'
                 b'    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "guard.sh"}]}]\n'
                 b'  },\n  "env": {"TOKEN_HINT": "see /* not a comment */ docs"}\n}\n')


def test_adapter_folder_matches_packaged_resources():
    packaged = ROOT / "agent_autosave/resources/claude-code"
    for name in ("adapter.json", "hooks.json", "CLAUDE.block.md"):
        assert (ROOT / "adapters/claude-code" / name).read_bytes() == (packaged / name).read_bytes()


def test_hooks_cover_plan_events_and_post_tool_use_is_async():
    hooks = json.loads((ROOT / "adapters/claude-code/hooks.json").read_text())["hooks"]
    assert set(hooks) >= {"SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "StopFailure",
                          "PreCompact", "SessionEnd"}
    assert hooks["PostToolUse"][0]["hooks"][0]["async"] is True
    for name, entries in hooks.items():
        command = entries[0]["hooks"][0]
        assert command["command"] == "agent-autosave hook claude-code " + name and "args" not in command


@pytest.mark.parametrize("settings", [USER_SETTINGS, USER_SETTINGS.replace(b"\n", b"\r\n"), None],
                         ids=["existing-hooks-and-comments", "crlf", "no-settings"])
def test_install_then_remove_restores_every_file_byte_for_byte(tmp_path, settings):
    if settings is not None:
        write(tmp_path / ".claude/settings.json", settings)
    write(tmp_path / "CLAUDE.md", b"# Project rules\nBe nice.")
    write(tmp_path / ".gitignore", b"node_modules/\n")
    before = snapshot(tmp_path)
    result = install(tmp_path)
    assert set(result["changed"]) == {".claude/settings.json", "CLAUDE.md", ".gitignore"}
    merged = (tmp_path / ".claude/settings.json").read_text()
    assert "say done" in merged and "guard.sh" in merged or settings is None
    assert "<!-- agent-autosave:start -->" in (tmp_path / "CLAUDE.md").read_text()
    receipt = json.loads((tmp_path / ".autosave/install.json").read_text())
    assert {f["path"] for f in receipt["files"]} == set(result["changed"])
    remove(tmp_path)
    assert snapshot(tmp_path) == before
    assert not (tmp_path / ".autosave/install.json").exists()


def test_merge_keeps_user_hooks_and_appends_ours():
    text, spans = merge_json(USER_SETTINGS.decode(), {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}],
                                                                "SessionStart": [{"hooks": []}]}})
    assert text.startswith("{\n  // my hooks") and "say done" in text and "guard.sh" in text
    assert len(spans) == 2
    restored = text
    for span in reversed(spans):
        restored = restored[:span["at"]] + restored[span["at"] + len(span["text"]):]
    assert restored == USER_SETTINGS.decode()


def test_merge_refuses_to_replace_existing_scalar():
    with pytest.raises(ValueError):
        merge_json('{"model": "opus"}', {"model": "haiku"})


def test_install_is_idempotent(tmp_path):
    install(tmp_path)
    first = snapshot(tmp_path)
    assert install(tmp_path)["already_installed"] is True
    assert snapshot(tmp_path) == first


def test_remove_preserves_later_user_edits(tmp_path):
    write(tmp_path / ".claude/settings.json", USER_SETTINGS)
    install(tmp_path)
    path = tmp_path / "CLAUDE.md"
    path.write_text("User added this first.\n" + path.read_text())
    remove(tmp_path)
    assert path.read_text() == "User added this first.\n"
    assert (tmp_path / ".claude/settings.json").read_bytes() == USER_SETTINGS


def test_managed_block_refuses_foreign_markers():
    with pytest.raises(ValueError):
        managed_block("<!-- agent-autosave:start -->\n", "x")


def test_remove_without_install_is_a_no_op(tmp_path):
    (tmp_path / ".autosave").mkdir()
    assert remove(tmp_path) == {"removed": []}


OTHER_AGENT_FILES = ["AGENTS.md", "GEMINI.md", ".codex/hooks.json", ".codex/config.toml", ".cursor/hooks.json",
                     ".cursor/rules/x.mdc", ".gemini/settings.json"]


def test_claude_code_install_never_touches_other_agents_files(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    project = tmp_path / "repo"
    for name in OTHER_AGENT_FILES:
        write(project / name, b"user content for " + name.encode() + b"\n")
    before = snapshot(project)
    result = install(project, agents=("claude-code",), command="agent-autosave")
    after = snapshot(project)
    assert set(result["changed"]) == {".claude/settings.json", "CLAUDE.md", ".gitignore"}
    assert {k: v for k, v in after.items() if k in before} == before
    assert set(after) - set(before) == {".claude/settings.json", "CLAUDE.md", ".gitignore"}
    assert not any(home.iterdir())
    receipt = json.loads((project / ".autosave/install.json").read_text())
    assert {f["path"] for f in receipt["files"]} == {".claude/settings.json", "CLAUDE.md", ".gitignore"}
    remove(project)
    assert snapshot(project) == before
