"""Codex hook layers, .gitignore upgrades and the Codex -> Claude Code handoff,
replayed from tests/fixtures/codex/sessions/plan-handoff/ (codex-cli 0.162.1)."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from agent_autosave import capture
from agent_autosave.compiler import compile_events
from agent_autosave.installer.install import HASH_START, MARKER_START, install
from agent_autosave.installer.remove import remove
from agent_autosave.loader import context
from agent_autosave.render import render
from tests.helpers import LENIENT_CLI, load_payload

ROOT = Path(__file__).resolve().parents[1]
SESSION = ROOT / "tests/fixtures/codex/sessions/plan-handoff"
REAL_ROLLOUT = ROOT / "tests/fixtures/codex/rollouts/rollout-real-session.jsonl"
OWNER_CWD = "/private/tmp/aa-codex"


def recordings():
    return sorted((json.loads(p.read_text()) for p in SESSION.glob("*.json")), key=lambda r: r["recorded_at"])


def real_rollout(path, exits):
    """Real 0.162.1 CommandExecution rows carrying the given exit codes."""
    template = next(json.loads(line) for line in REAL_ROLLOUT.read_text().splitlines()
                    if '"CommandExecution"' in line and '"exit_code"' in line)
    rows = []
    for ident, code in exits.items():
        row = json.loads(json.dumps(template))
        row["payload"]["item"].update(id=ident, exit_code=code)
        rows.append(json.dumps(row))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n")
    return path


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    # Codex kills each hook's process group when the hook returns, so the
    # detached SessionEnd rebuild is not relied on: make it a no-op here.
    monkeypatch.setattr("agent_autosave.background.spawn", lambda project: None)
    root = tmp_path / "aa-codex"
    root.mkdir()
    install(root, agents=("claude-code", "codex"), command="agent-autosave")
    return root


def replay(project, tmp_path, until=None):
    exits = {}
    for rec in recordings():
        p = rec["payload"]
        if p.get("tool_name") == "Bash":
            exits[p["tool_use_id"]] = 7 if "SystemExit(7)" in p["tool_input"]["command"] else 0
    rollout = real_rollout(tmp_path / "sessions/rollout-real.jsonl", exits)
    outputs = []
    for rec in recordings():
        p = json.loads(json.dumps(rec["payload"]).replace(OWNER_CWD, str(project)))
        p["transcript_path"] = str(rollout)
        out = capture.handle("codex", p["hook_event_name"], json.dumps(p).encode(), env={})
        outputs.append((rec, out))
        if until and until(rec):
            break
    assert not (project / ".autosave/errors.log").exists()
    return outputs


def save_of(project):
    return json.loads((project / ".autosave/save.json").read_text())


# --- 2: clean session end after compact -> paused, settled in the hook itself

def test_compact_then_quit_is_paused_without_the_background_rebuild(project, tmp_path):
    replay(project, tmp_path, until=lambda r: r["payload"]["hook_event_name"] == "PreCompact")
    assert save_of(project)["status"] == "interrupted"
    assert save_of(project)["interrupted"]["reason"] == "compact"
    replay_rest = [r for r in recordings() if r["payload"]["hook_event_name"] == "SessionEnd"
                   and r["payload"]["session_id"].endswith("9b34f3")]
    for rec in replay_rest * 2:  # the real run recorded this SessionEnd twice (quit, then resume + quit)
        p = dict(rec["payload"], cwd=str(project), transcript_path=None)
        capture.handle("codex", "SessionEnd", json.dumps(p).encode(), env={})
        save = save_of(project)
        assert save["status"] == "paused" and save["interrupted"] is None
        assert "**Status:** paused," in (project / ".autosave/SAVE.md").read_text()


def test_full_real_run_ends_paused_with_exit_7_and_plan(project, tmp_path):
    outputs = replay(project, tmp_path)
    save = save_of(project)
    assert save["status"] == "paused" and save["failing"]["exit"] == 7
    assert [p["state"] for p in save["plan"]] == ["done", "todo", "todo"]  # checklist (CLI not run in replay)
    assert save["last_session"] == {"agent": "codex", "session": "01a13fd6-da45-7381-aa2c-03b3674f1527",
                                    "ended": True}
    # The Codex -> Codex handoff: the second session's SessionStart got the save.
    [(_, out)] = [(r, o) for r, o in outputs if r["payload"]["hook_event_name"] == "SessionStart"
                  and r["payload"]["session_id"].endswith("4f1527")]
    text = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "unfinished task from Codex" in text and "The session ended at" in text
    assert "(exit 7)" in text


# --- 3: Codex -> Claude Code handoff, end to end with real fixtures

def claude_start(project):
    data = load_payload("claude-code", "SessionStart-startup")
    data = dict(data, cwd=str(project), session_id="claude-after-codex")
    out = capture.handle("claude-code", "SessionStart", json.dumps(data).encode(), env={})
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def test_claude_code_gets_the_handoff_after_codex_sessions(project, tmp_path):
    replay(project, tmp_path)
    text = claude_start(project)
    assert "agent-autosave: this repo has an unfinished task from Codex." in text
    assert "Use your update_plan tool" in text and "SystemExit(7)" in text and "(exit 7)" in text
    assert "Plan: 1 of 3 steps done." in text


def test_claude_code_gets_the_handoff_even_if_save_json_was_left_stale(project, tmp_path):
    replay(project, tmp_path)
    stale = dict(save_of(project), status="active")  # a SessionEnd rebuild that never landed
    (project / ".autosave/save.json").write_text(json.dumps(stale))
    assert "unfinished task from Codex" in claude_start(project)


def test_claude_code_gets_the_handoff_while_codex_is_still_active(project, tmp_path):
    replay(project, tmp_path, until=lambda r: r["payload"]["hook_event_name"] == "Stop"
           and r["payload"]["session_id"].endswith("9b34f3") and "SystemExit(7)" in r["payload"]["last_assistant_message"])
    assert save_of(project)["status"] == "active"
    text = claude_start(project)
    assert "Codex was last active at" in text and "may still be running" in text


def test_loader_rule_for_active_saves():
    base = compile_events([
        {"v": 1, "ts": "2026-10-15T13:46:06Z", "agent": "codex", "session": "s1", "turn": 1, "kind": "prompt",
         "text": "Write hello.py that prints hello autosave."}])
    assert base["status"] == "active"
    assert context(base, agent="codex", session="s1") == ""  # the same session resumed
    assert "may still be running" in context(base, agent="codex", session="s2")  # e.g. s1 was killed
    assert "may still be running" in context(base, agent="claude-code", session="c1")
    ended = dict(base, last_session=dict(base["last_session"], ended=True))
    assert "The session ended at" in context(ended, agent="codex", session="s2")
    assert context(dict(base, status="done"), agent="claude-code") == ""


# --- 4: Codex fires SessionStart only at the first turn (codex-rs hook_runtime)

def test_recordings_show_session_start_only_at_the_first_prompt():
    recs = recordings()
    by_session = {}
    for rec in recs:
        by_session.setdefault(rec["payload"]["session_id"], []).append(rec)
    def at(rec):
        return datetime.fromisoformat(rec["recorded_at"].replace("Z", "+00:00"))
    for events in by_session.values():
        names = [r["payload"]["hook_event_name"] for r in events]
        if "UserPromptSubmit" not in names:
            assert names == ["SessionEnd"]  # launched (to trust hooks) and quit: no SessionStart at all
            continue
        start, prompt = events[names.index("SessionStart")], events[names.index("UserPromptSubmit")]
        assert names.index("SessionStart") == 0 and 0 <= (at(prompt) - at(start)).total_seconds() < 1
    # /compact queues SessionStart(compact) for the next turn; the user quit first.
    sources = [r["payload"]["source"] for r in recs if r["payload"]["hook_event_name"] == "SessionStart"]
    assert sources == ["startup", "startup"]


def test_resume_reuses_the_session_and_reopens_it(project, tmp_path):
    replay(project, tmp_path, until=lambda r: r["payload"]["hook_event_name"] == "SessionEnd"
           and r["payload"]["session_id"].endswith("9b34f3"))
    assert save_of(project)["last_session"]["ended"] is True
    # `codex resume --last` and a prompt: Codex sends SessionStart(resume) at
    # that first turn, with the same session id.
    start = next(r for r in recordings() if r["payload"]["hook_event_name"] == "SessionStart")["payload"]
    out = capture.handle("codex", "SessionStart", json.dumps(dict(start, cwd=str(project), source="resume",
                                                                    transcript_path=None)).encode(), env={})
    assert "unfinished task from Codex" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
    prompt = next(r for r in recordings() if r["payload"]["hook_event_name"] == "UserPromptSubmit")["payload"]
    # A new turn has a new turn_id (the recorded one is an earlier turn's).
    capture.handle("codex", "UserPromptSubmit", json.dumps(dict(prompt, cwd=str(project), prompt="Fix it now please.",
                                                                turn_id="turn-after-resume",
                                                                transcript_path=None)).encode(), env={})
    assert save_of(project)["status"] == "active" and save_of(project)["last_session"]["ended"] is False


# --- 1: layers recorded in install.json, loud warning

def run_init(project, home, *args, trusted=False, tty=False):
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    if trusted:
        (codex_home / "config.toml").write_text('[projects."{}"]\ntrust_level = "trusted"\n'.format(project.resolve()))
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), CODEX_HOME=str(codex_home))
    env.pop("PYTHONPATH", None)
    env["NO_COLOR"] = "1"  # plain text whatever TERM the suite runs under (check.sh runs with env -i)
    command = [*LENIENT_CLI, "init", "--agents", "codex"] + list(args)
    if tty:  # the human output, as printed in a terminal
        command = [sys.executable, "-c", "import pty, sys; sys.exit(pty.spawn(sys.argv[1:]) >> 8)"] + command
    return subprocess.run(command, cwd=str(project), env=env, capture_output=True, timeout=20)


@pytest.mark.skipif(os.name == "nt", reason="pty")
def test_init_says_loudly_when_codex_hooks_stay_in_the_user_layer(tmp_path):
    home, old, repo = tmp_path / "home", tmp_path / "old", tmp_path / "repo"
    old.mkdir(), repo.mkdir()
    assert run_init(old, home, "--user-level").returncode == 0  # explicit user-level hooks
    result = run_init(repo, home, trusted=True, tty=True)
    text = result.stdout.decode().replace("\r\n", "\n")
    lines = text.splitlines()
    assert lines[2].startswith("  !!! WARNING: Codex hooks were NOT written to this repo: ")
    assert str(home / ".codex/hooks.json") in lines[2] and "user-level" in lines[2]
    assert "userhooks --codex --dry-run" in lines[3]
    receipt = json.loads((repo / ".autosave/install.json").read_text())
    assert receipt["hooks"] == {"codex": {"layer": "user", "path": str(home / ".codex/hooks.json"),
                                          "added": False, "existing_hooks": 6}}
    first = json.loads((old / ".autosave/install.json").read_text())
    assert first["hooks"] == {"codex": {"layer": "user", "path": str((home / ".codex/hooks.json").resolve()),
                                        "added": True}}


def test_install_json_records_project_layer(tmp_path):
    home, repo = tmp_path / "home", tmp_path / "repo"
    repo.mkdir()
    assert run_init(repo, home, trusted=True).returncode == 0
    receipt = json.loads((repo / ".autosave/install.json").read_text())
    assert receipt["hooks"] == {"codex": {"layer": "project", "path": ".codex/hooks.json", "added": True}}


# --- 5: no glued words

TEMPLATES = sorted((ROOT / "agent_autosave/resources").rglob("*.md"))


def test_packaged_templates_have_no_hard_wrapped_paragraphs():
    """Each prose paragraph is one source line, so no consumer that joins
    lines without a space (as seen in a real Codex view: 'endyour reply')
    can glue words."""
    assert TEMPLATES
    for path in TEMPLATES:
        lines = path.read_text().splitlines()
        for before, after in zip(lines, lines[1:]):
            prose = before.strip() and after.strip() and not before.startswith("#") and not after.lstrip().startswith(("-", "*", "#", "`"))
            assert not prose, "{}: wrapped line join {!r} + {!r}".format(path.name, before[-20:], after[:20])


def glue_pairs(text):
    """(last word before, first word after) for every line break in text."""
    pairs = []
    for match in re.finditer(r"(\w+)[ \t]*(?:\r\n|\r|\n|\u2028|\u2029)+[ \t]*(\w+)", text):
        pairs.append(match.group(1) + match.group(2))
    return pairs


def test_line_breaks_in_recorded_text_render_as_spaces():
    events = [{"v": 1, "ts": "2026-10-15T13:46:06Z", "agent": "codex", "session": "s", "turn": 1, "kind": "prompt",
               "text": 'Make the plan "Write hello.py", "Run it", "Add a failing\ncheck" and\r\nthen stop\u2028there.'},
              {"v": 1, "ts": "2026-10-15T13:46:07Z", "agent": "codex", "session": "s", "turn": 1, "kind": "prompt",
               "text": "The command exited\nwith code 7, leave it."}]
    save = compile_events(events)
    md = render(save)
    for glued in glue_pairs(events[0]["text"]) + glue_pairs(events[1]["text"]):
        assert glued not in md and glued not in context(dict(save, status="paused"))
    assert "Add a failing check" in md and "exited with code 7" in md


def test_goldens_never_glue_words_across_line_breaks():
    checked = 0
    for case in sorted((ROOT / "tests/fixtures/compiler").iterdir()):
        md = (case / "SAVE.md").read_text()
        for line in (case / "events.jsonl").read_text().splitlines():
            for value in json.loads(line).values():
                if isinstance(value, str):
                    for glued in glue_pairs(value):
                        checked += 1
                        assert glued not in md, (case.name, glued)
    for path in TEMPLATES:
        rendered = path.read_text()
        for glued in glue_pairs(rendered):
            assert glued not in rendered.replace("\n", " ")


# --- 6: .gitignore markers

OLD_BODY = ("<!-- agent-autosave:start -->\n.autosave/events.jsonl\n.autosave/errors.log\n.autosave/history/\n"
            ".autosave/\n<!-- agent-autosave:end -->\n")


def old_style(project):
    """Rewrite a fresh install to the older .gitignore block and receipt."""
    from agent_autosave.installer.install import digest
    receipt_path = project / ".autosave/install.json"
    receipt = json.loads(receipt_path.read_text())
    item = next(i for i in receipt["files"] if i["path"] == ".gitignore")
    user = "node_modules/\n"
    (project / ".gitignore").write_text(user + OLD_BODY)
    item.update(created=False, before=digest(user), after=digest(user + OLD_BODY),
                spans=[{"at": len(user), "text": OLD_BODY}])
    receipt_path.write_text(json.dumps(receipt))
    return user


def test_gitignore_block_uses_hash_markers_and_one_line(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ch"))
    (tmp_path / "r").mkdir()
    (tmp_path / "r/.gitignore").write_text("node_modules/")
    install(tmp_path / "r", agents=("claude-code", "codex"), command="agent-autosave")
    assert (tmp_path / "r/.gitignore").read_text() == (
        "node_modules/\n# agent-autosave:start\n.autosave/\n# agent-autosave:end\n")
    assert MARKER_START in (tmp_path / "r/AGENTS.md").read_text() and MARKER_START in (tmp_path / "r/CLAUDE.md").read_text()
    remove(tmp_path / "r")
    assert (tmp_path / "r/.gitignore").read_text() == "node_modules/"


def test_reinit_migrates_an_old_marker_block_and_remove_restores(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ch"))
    project = tmp_path / "r"
    project.mkdir()
    install(project, agents=("claude-code",), command="agent-autosave")
    user = old_style(project)
    assert install(project, agents=("claude-code",), command="agent-autosave")["already_installed"]
    assert (project / ".gitignore").read_text() == user + HASH_START + "\n.autosave/\n# agent-autosave:end\n"
    assert install(project, agents=("claude-code",), command="agent-autosave")["already_installed"]  # idempotent
    remove(project)
    assert (project / ".gitignore").read_text() == user


def test_remove_of_an_unmigrated_old_block_restores_exactly(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ch"))
    project = tmp_path / "r"
    project.mkdir()
    install(project, agents=("claude-code",), command="agent-autosave")
    user = old_style(project)
    remove(project)
    assert (project / ".gitignore").read_text() == user


def test_user_edited_old_block_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ch"))
    project = tmp_path / "r"
    project.mkdir()
    install(project, agents=("claude-code",), command="agent-autosave")
    user = old_style(project)
    edited = (user + OLD_BODY).replace(".autosave/history/\n", ".autosave/history/\nmine/\n")
    (project / ".gitignore").write_text(edited)
    install(project, agents=("claude-code",), command="agent-autosave")
    assert (project / ".gitignore").read_text() == edited


@pytest.mark.skipif(os.name == "nt", reason="pty")
def test_untrusted_init_prints_how_to_trust_in_the_terminal(tmp_path):
    home, repo = tmp_path / "home", tmp_path / "repo"
    repo.mkdir()
    only = run_init(repo, home, tty=True)  # codex only: nothing installed, exit 3
    text = only.stdout.decode().replace("\r\n", "\n")
    assert only.returncode == 1 and "error: Codex was skipped" in text
    assert '[projects."{}"]'.format(repo.resolve()) in text and "Then run: agent-autosave add codex" in text
    mixed = run_init(repo, home, "--agents", "codex,generic", tty=True)
    text = mixed.stdout.decode().replace("\r\n", "\n")
    assert mixed.returncode == 0 and "skipped: this folder is not trusted in Codex" in text
    assert '    trust_level = "trusted"' in text and "Then run: agent-autosave add codex" in text
    assert not (home / ".codex/hooks.json").exists()
