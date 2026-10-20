"""Memory through every entry point: Codex and Cursor recall, the
instruction blocks, init --yes, add, upgrade and status."""
import json
import re

from tests.helpers import load_payload
from tests.memory_helpers import accept_all, codex_prompt, context_of, finish_task, repo  # noqa: F401

PARAGRAPH = ('Record decisions with `agent-autosave note --quiet --type decision "..."` and dead ends with '
             '`agent-autosave note --quiet --type tried "..."`. They are proposed for this repo\'s memory.')
PROMPT = "Add a JSON export for the billing rows with a generator like the CSV one"


def test_every_instruction_block_has_the_memory_paragraph_and_never_says_accept():
    from tests.helpers import ROOT
    from tests.test_continuity_tasks import BLOCKS
    for base in ("adapters", "agent_autosave/resources"):
        for path in BLOCKS:
            text = (ROOT / base / path).read_text()
            assert PARAGRAPH in text, path
            assert not re.search(r"memory (accept|reject|review)", text), path


def test_codex_prompts_get_the_recall_block(repo):
    finish_task(repo)
    accept_all(repo)
    text = context_of(codex_prompt(repo, PROMPT, "codex-1"))
    assert text.startswith("[agent-autosave memory]") and "Stream rows with a generator" in text


def test_cursor_prompts_get_the_recall_block(repo):
    from agent_autosave.installer.install import install
    install(repo.root, ["cursor"], command="agent-autosave")
    finish_task(repo)
    accept_all(repo)
    payload = load_payload("cursor", "beforeSubmitPrompt")
    payload.update(prompt=PROMPT, conversation_id="cursor-1", workspace_roots=[str(repo.root)])
    output = json.loads(repo.send("cursor", payload))
    assert output["continue"] is True
    assert "[agent-autosave memory]" in output["additional_context"]


def test_upgrade_flags_a_block_without_the_memory_paragraph(repo, monkeypatch):
    from agent_autosave.installer import detect
    for name in ("CLAUDE.md", ".autosave/install.json"):
        path = repo.root / name
        text = path.read_text()
        old = PARAGRAPH + "\n\n" if name == "CLAUDE.md" else json.dumps(PARAGRAPH + "\n\n")[1:-1]
        assert old in text
        path.write_text(text.replace(old, "", 1))
    monkeypatch.setattr(detect, "hook_command", lambda **k: "agent-autosave")
    code, out = repo.cli("upgrade")
    assert code == 0 and "CLAUDE.md text changed (claude-code)" in out["data"]["reasons"]
    assert repo.cli("upgrade", "--yes")[0] == 0
    assert PARAGRAPH in (repo.root / "CLAUDE.md").read_text()


def test_upgrade_writes_journal_pages_for_tasks_finished_before(repo, monkeypatch):
    from agent_autosave.installer import detect
    finish_task(repo)
    for page in (repo.root / ".autosave/journal").rglob("*.md"):
        page.unlink()
    (repo.root / ".autosave/memory/state.json").unlink()
    monkeypatch.setattr(detect, "hook_command", lambda **k: "agent-autosave")
    repo.cli("upgrade", "--yes")
    assert list((repo.root / ".autosave/journal").rglob("*.md"))


def test_init_yes_keeps_memory_in_the_repo_and_agentic_stack_on_auto(tmp_path, monkeypatch):
    import subprocess
    from agent_autosave import cli
    project = tmp_path / "fresh"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / ".agent/memory").mkdir(parents=True)
    (project / ".agent/AGENTS.md").write_text("x")
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert cli.main(["--json", "init", "--yes", "--agents", "generic"]) == 0
    config = json.loads((project / ".autosave/config.json").read_text())
    assert config.get("vault", {}).get("path", "") == ""
    assert config.get("integrations", {}).get("agentic_stack", "auto") == "auto"
    # The block names the command init found on PATH (an absolute path when
    # agent-autosave is installed there), so compare against that command.
    command = json.loads((project / ".autosave/install.json").read_text())["command"]
    assert PARAGRAPH.replace("`agent-autosave ", "`" + command + " ") in (project / "AGENTS.md").read_text()


def test_old_saves_and_configs_keep_loading(repo):
    config = repo.root / ".autosave/config.json"
    value = json.loads(config.read_text())
    for key in ("memory", "recall", "vault", "integrations"):
        value.pop(key, None)
    value.get("inject", {}).pop("memory_on_start", None)
    value.get("inject", {}).pop("memory_max_chars", None)
    config.write_text(json.dumps(value))
    finish_task(repo)
    assert repo.cli("status")[0] == 0 and repo.cli("memory", "list")[0] == 0
