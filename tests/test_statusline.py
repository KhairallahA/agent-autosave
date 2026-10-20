"""`agent-autosave statusline`: the line, the usage it records, and the
install, wrap and byte-exact remove of Claude Code's statusLine setting."""
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path

from agent_autosave import statusline
from agent_autosave.installer.install import install, set_statusline
from agent_autosave.installer.remove import remove

NOW = datetime(2026, 10, 19, 11, 0, 0, tzinfo=timezone.utc)
SAVE = {"v": 1, "updated": "2026-10-19T10:59:56Z", "failing": {"command": "python -m pytest tests/test_x.py -q"},
        "plan": [{"state": "done"}, {"state": "done"}, {"state": "todo"}, {"state": "todo"}, {"state": "todo"}]}
FULL = {"session_id": "s1", "context_window": {"used_percentage": 41.2},
        "rate_limits": {"five_hour": {"used_percentage": 72, "resets_at": 1792436400},
                        "seven_day": {"used_percentage": 30.4, "resets_at": 1792800000}}}
THEIRS = '{\n  // mine\n  "statusLine": {"type": "command", "command": "echo mine"},\n  "model": "opus"\n}\n'


def project(tmp_path):
    install(tmp_path, ["claude-code"], command="agent-autosave", statusline="off")
    (tmp_path / ".autosave/save.json").write_text(json.dumps(SAVE))
    return tmp_path


def payload(tmp_path, **extra):
    return json.dumps(dict(FULL, workspace={"project_dir": str(tmp_path)}, **extra)).encode()


def usage_events(tmp_path):
    lines = (tmp_path / ".autosave/events.jsonl").read_text().splitlines()
    return [e for e in map(json.loads, lines) if e["kind"] == "usage"]


def test_the_plain_line_shows_every_field(tmp_path):
    assert statusline.render(payload(project(tmp_path)), now=NOW) == \
        "saved 4s ago | plan 2/5 | failing: pytest | 5h 72% | 7d 30% | ctx 41%"


def test_the_emoji_style(tmp_path):
    project(tmp_path)
    (tmp_path / ".autosave/config.json").write_text('{"v": 1, "statusline": {"style": "emoji"}}')
    assert statusline.render(payload(tmp_path), now=NOW) == \
        "💾 4s ago | 📋 2/5 | ❌ pytest | ⏳ 5h 72% | 📅 7d 30% | 🧠 41%"


def test_missing_fields_are_left_out(tmp_path):
    project(tmp_path)
    raw = json.dumps({"workspace": {"project_dir": str(tmp_path)}}).encode()
    assert statusline.render(raw, now=NOW + timedelta(minutes=3)) == "saved 3m ago | plan 2/5 | failing: pytest"
    assert not (tmp_path / ".autosave/events.jsonl").exists() or usage_events(tmp_path) == []


def test_usage_is_recorded_only_when_it_moves_five_points(tmp_path):
    project(tmp_path)
    for used in (72, 74, 76.9, 77, 50):
        statusline.render(payload(tmp_path, rate_limits={"five_hour": {"used_percentage": used,
                                                                        "resets_at": 1792436400}}), now=NOW)
    events = usage_events(tmp_path)
    assert [e["limit_pct"] for e in events] == [72, 77, 50]
    assert events[0]["agent"] == "claude-code" and events[0]["limit_window_minutes"] == 300
    assert events[0]["limit_resets_at"] == "2026-10-19T19:00:00Z"


def test_junk_and_no_project_print_nothing_and_exit_zero(tmp_path, run_cli):
    for raw in (b"", b"not json", b"[1]", b'{"workspace": 3}', b"\xff\xfe"):
        result = run_cli("statusline", payload=raw)
        assert result.returncode == 0 and result.stdout == b"", raw
    out = io.StringIO()
    assert statusline.main(io.BytesIO(b'{"cwd": 5}'), out) == 0


def test_the_cli_prints_the_line(tmp_path, run_cli):
    project(tmp_path)
    result = run_cli("statusline", payload=payload(tmp_path))
    assert result.returncode == 0
    assert result.stdout.decode().strip().endswith("| 5h 72% | 7d 30% | ctx 41%")


def test_init_adds_ours_and_remove_deletes_it(tmp_path):
    (tmp_path / ".claude").mkdir()
    original = '{\r\n  "model": "opus"\r\n}\r\n'
    (tmp_path / ".claude/settings.json").write_bytes(original.encode())
    result = install(tmp_path, ["claude-code"], command="agent-autosave")
    assert result["statusline"] == {"mode": "added"}
    settings = json.loads((tmp_path / ".claude/settings.json").read_text())
    assert settings["statusLine"]["command"].endswith("statusline")
    remove(tmp_path)
    assert (tmp_path / ".claude/settings.json").read_bytes() == original.encode()


def test_an_existing_line_is_kept_unless_wrapped(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.local.json").write_text(THEIRS)
    result = install(tmp_path, ["claude-code"], command="agent-autosave")
    assert result["statusline"] == {"mode": "kept", "path": ".claude/settings.local.json"}
    assert (tmp_path / ".claude/settings.local.json").read_text() == THEIRS


def test_wrapping_a_project_line_runs_theirs_first_and_remove_restores_it(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.local.json").write_text(THEIRS)
    install(tmp_path, ["claude-code"], command="agent-autosave", statusline="wrap")
    text = (tmp_path / ".claude/settings.local.json").read_text()
    assert "echo mine" not in text and "agent-autosave statusline" in text and '"model": "opus"' in text
    assert json.loads((tmp_path / ".autosave/statusline.json").read_text())["command"] == "echo mine"
    (tmp_path / ".autosave/save.json").write_text(json.dumps(SAVE))
    assert statusline.render(payload(tmp_path), now=NOW).startswith("mine | saved 4s ago | plan 2/5")
    remove(tmp_path)
    assert (tmp_path / ".claude/settings.local.json").read_text() == THEIRS


def test_wrapping_a_user_line_goes_in_the_project_and_never_edits_home(tmp_path):
    home = Path(os.environ["HOME"])
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude/settings.json").write_text(THEIRS)
    install(tmp_path, ["claude-code"], command="agent-autosave", statusline="wrap")
    assert (home / ".claude/settings.json").read_text() == THEIRS
    assert "agent-autosave statusline" in (tmp_path / ".claude/settings.json").read_text()
    remove(tmp_path)
    assert not (tmp_path / ".claude/settings.json").exists()


def test_reconfigure_can_wrap_after_install(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").write_text(THEIRS)
    install(tmp_path, ["claude-code"], command="agent-autosave")
    assert set_statusline(tmp_path, "wrap")["statusline"]["mode"] == "wrapped"
    assert set_statusline(tmp_path, "wrap")["changed"] == []
    remove(tmp_path)
    assert (tmp_path / ".claude/settings.json").read_text() == THEIRS


# Imported only to write, to parse a full command line, or to run another
# program; the per-message status line never needs them.
HEAVY = ("argparse", "typing", "subprocess", "tempfile", "agent_autosave.arguments", "agent_autosave.contract",
         "agent_autosave.redact", "agent_autosave.state", "agent_autosave.ledger", "agent_autosave.compiler",
         "agent_autosave.commands")
# What the run added to the interpreter's own start-up (an editable install's
# finder, for one, pulls in typing on 3.9 before any of our code runs).
LOADED = ("import sys; before = set(sys.modules); from agent_autosave.cli import main; main(['statusline']); "
          "sys.stderr.write(' '.join(sorted(set(sys.modules) - before)))")


def loaded_modules(tmp_path, raw):
    import subprocess
    import sys
    run = subprocess.run([sys.executable, "-c", LOADED], input=raw, cwd=str(tmp_path), capture_output=True,
                         timeout=60, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert run.returncode == 0 and run.stdout.decode().startswith("saved "), run.stderr.decode()[-2000:]
    return set(run.stderr.decode().split())


def test_the_statusline_path_imports_no_heavy_modules(tmp_path):
    raw = payload(project(tmp_path))
    first = loaded_modules(tmp_path, raw)  # records the first usage reading: a write
    assert not first & {"argparse", "subprocess", "agent_autosave.arguments", "agent_autosave.contract"}
    steady = loaded_modules(tmp_path, raw)  # the same reading again: read-only, like most messages
    assert not steady & set(HEAVY), sorted(steady & set(HEAVY))
    assert not {name for name in steady if name.startswith("agent_autosave.commands.")}
