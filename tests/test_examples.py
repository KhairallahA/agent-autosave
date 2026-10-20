"""examples/: each ledger compiles to its save, and nothing private is left in it."""
import importlib
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

from agent_autosave.compiler import compile_events, serialize
from agent_autosave.redact import Redactor
from agent_autosave.render import render

ROOT = Path(__file__).resolve().parents[1]
HANDOFFS = ["claude-to-codex", "codex-to-claude"]
EXAMPLES = HANDOFFS + ["interrupted-by-limit", "one-key-switch"]


def files(name):
    return ROOT / "examples" / name / ".autosave"


@pytest.mark.parametrize("name", EXAMPLES)
def test_events_compile_to_the_committed_save(name):
    folder = files(name)
    events = [json.loads(line) for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    save = compile_events(events, started=events[0]["ts"], task_id=events[0]["task_id"])
    assert serialize(save) == (folder / "save.json").read_bytes()
    assert render(save).encode("utf-8") == (folder / "SAVE.md").read_bytes()


@pytest.mark.parametrize("name", HANDOFFS)
def test_each_example_is_a_handoff_between_two_agents(name):
    first, second = name.replace("claude", "claude-code").split("-to-")
    save = json.loads((files(name) / "save.json").read_text(encoding="utf-8"))
    assert [(h["from"], h["to"]) for h in save["handoffs"]] == [(first, second)]
    assert save["status"] == "paused" and save["failing"]["exit"] == 7
    assert [step["state"] for step in save["plan"]] == ["done", "done", "todo"]
    assert (ROOT / "examples" / name / "README.md").is_file()


@pytest.mark.parametrize("name", EXAMPLES)
def test_replay_rebuilds_every_committed_file(name):
    # The example's own replay script, run as a user would.
    folder = ROOT / "examples" / name
    result = subprocess.run([sys.executable, "replay.py", "--check"], cwd=str(folder), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "up to date" in result.stdout


@pytest.mark.parametrize("name", EXAMPLES)
def test_readme_shows_the_replayed_views(name):
    sys.path.insert(0, str(ROOT / "scripts"))
    build = importlib.import_module("build-examples")
    folder = ROOT / "examples" / name
    text = (folder / "README.md").read_text(encoding="utf-8")
    assert build.readme_text(text, folder) == text, "run python scripts/build-examples.py"
    for part in ("before/SAVE.md", "context.txt", ".autosave/SAVE.md"):
        assert (folder / part).read_text(encoding="utf-8").rstrip("\n") in text


def test_limit_example_is_interrupted_then_handed_off():
    folder = ROOT / "examples/interrupted-by-limit"
    before = (folder / "before/SAVE.md").read_text(encoding="utf-8")
    assert "**Status:** interrupted (rate_limit in claude-code)" in before
    assert "because Claude Code hit a usage limit" in (folder / "context.txt").read_text(encoding="utf-8")
    save = json.loads((files("interrupted-by-limit") / "save.json").read_text(encoding="utf-8"))
    assert [(h["from"], h["to"], h["reason"]) for h in save["handoffs"]] == [("claude-code", "codex", "rate_limit")]
    events = [json.loads(line) for line in (files("interrupted-by-limit") / "events.jsonl").read_text().splitlines()]
    assert [e["reason"] for e in events if e["kind"] == "interrupted"] == ["rate_limit"]
    assert "documented" in (folder / "README.md").read_text(encoding="utf-8")  # says it is not a real recording


@pytest.mark.parametrize("name", EXAMPLES)
def test_examples_hold_no_private_paths(name):
    folder = ROOT / "examples" / name
    paths = sorted(files(name).iterdir()) + [folder / "before/SAVE.md", folder / "context.txt"]
    text = "".join(p.read_text(encoding="utf-8") for p in paths)
    assert not re.search(r"/Users/|/home/|/private/|/tmp/|Desktop|\.venv|aa-(codex|scratch)", text)
    assert Redactor().text(text) == text  # nothing the redactor would still mask
    paths = set(re.findall(r"~/[^\s\"\\)`]+", text))
    assert all(p.startswith(("~/projects/hello", "~/.claude/projects/-projects-hello/", "~/.codex/sessions/"))
               for p in paths), paths


def test_example_saves_survive_the_gitignore_block_init_writes(tmp_path):
    from agent_autosave.installer.install import GITIGNORE_LINES, managed_block
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    root_ignore, _ = managed_block((ROOT / ".gitignore").read_text(encoding="utf-8"), GITIGNORE_LINES, ".gitignore")
    (tmp_path / ".gitignore").write_text(root_ignore, encoding="utf-8")
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples/.gitignore").write_bytes((ROOT / "examples/.gitignore").read_bytes())
    save = tmp_path / "examples/claude-to-codex/.autosave/SAVE.md"
    save.parent.mkdir(parents=True)
    save.write_text("x", encoding="utf-8")
    (tmp_path / ".autosave").mkdir()
    (tmp_path / ".autosave/SAVE.md").write_text("x", encoding="utf-8")
    ignored = subprocess.run(["git", "check-ignore", "--no-index", "examples/claude-to-codex/.autosave/SAVE.md",
                              ".autosave/SAVE.md"], cwd=tmp_path, capture_output=True, text=True).stdout.split()
    assert ignored == [".autosave/SAVE.md"]  # the project's own save is still ignored


@pytest.mark.skipif(not (ROOT / ".git").exists(), reason="needs the git checkout, not an sdist")
def test_example_saves_are_not_ignored_by_git():
    tracked = [str(files(name).relative_to(ROOT) / f) for name in EXAMPLES
               for f in ("SAVE.md", "save.json", "events.jsonl")]
    result = subprocess.run(["git", "check-ignore", "--no-index", *tracked], cwd=ROOT, capture_output=True)
    assert result.returncode == 1, result.stdout  # 1: none of them is ignored


MEMORY_EXAMPLE = ROOT / "examples/memory-recall"


def test_memory_recall_replay_rebuilds_every_committed_file():
    result = subprocess.run([sys.executable, "replay.py", "--check"], cwd=str(MEMORY_EXAMPLE), capture_output=True,
                            text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_memory_recall_gives_claude_the_codex_decision():
    text = (MEMORY_EXAMPLE / "recall.txt").read_text(encoding="utf-8")
    assert text.startswith("[agent-autosave memory]\n")
    assert '- decision, 2026-10-15, Codex: "hello.py prints with print()' in text
    assert '"agent":"codex"' in (MEMORY_EXAMPLE / ".autosave/MEMORY.md").read_text(encoding="utf-8")
    assert "[[MEMORY#^m-" in next((MEMORY_EXAMPLE / ".autosave/journal").rglob("*.md")).read_text(encoding="utf-8")
    assert "Codex" in (MEMORY_EXAMPLE / "start.txt").read_text(encoding="utf-8")


def test_memory_recall_readme_shows_the_replayed_files():
    sys.path.insert(0, str(ROOT / "scripts"))
    build = importlib.import_module("build-examples")
    text = (MEMORY_EXAMPLE / "README.md").read_text(encoding="utf-8")
    assert build.memory_readme_text(text, MEMORY_EXAMPLE) == text, "run python scripts/build-examples.py"


def test_memory_recall_holds_no_private_paths():
    for path in MEMORY_EXAMPLE.rglob("*"):
        if path.is_file() and path.suffix in (".md", ".txt", ".json", ".jsonl"):
            text = path.read_text(encoding="utf-8")
            assert "/Users/" not in text and "/home/" not in text and "/private/" not in text, path


def test_one_key_switch_hands_off_at_the_limit_with_the_note():
    """go asks at Claude Code's rate limit; Codex's first prompt carries the
    handoff note Claude wrote at 88%, the plan and the failing command."""
    folder = ROOT / "examples/one-key-switch"
    go = (folder / "go.txt").read_text(encoding="utf-8")
    assert "Claude Code hit its usage limit. Press Enter to continue in Codex, or q to stop." in go
    for part in ("Handoff notes:", "Plan: 1 of 3 steps done.", 'Failing: "python3 -c', "Claude Code 88% of 5-hour limit"):
        assert part in go, part
    assert "88% of its 5-hour limit" in (folder / "ask.txt").read_text(encoding="utf-8")
    save = json.loads((files("one-key-switch") / "save.json").read_text(encoding="utf-8"))
    assert [(h["from"], h["to"], h["reason"]) for h in save["handoffs"]] == [("claude-code", "codex", "rate_limit")]
