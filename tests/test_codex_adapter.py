"""Codex adapter end to end: install/remove, init, and real hook processes.

Payloads are real 0.162.1 recordings where one exists (tests/fixtures/codex/README.md). No Codex binary is
used; HOME and CODEX_HOME always point into tmp_path.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from agent_autosave.commands import codex_layers
from agent_autosave.installer import detect
from agent_autosave.installer.install import install
from agent_autosave.installer.remove import remove
from tests.helpers import LENIENT_CLI, load_payload, snapshot, write
from tests.test_installer import OTHER_AGENT_FILES

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/codex"
CODEX_FILES = {".codex/hooks.json", "AGENTS.md", ".gitignore"}


def test_adapter_folder_matches_packaged_resources():
    packaged = ROOT / "agent_autosave/resources/codex"
    assert sorted(p.name for p in packaged.iterdir()) == sorted(p.name for p in (ROOT / "adapters/codex").iterdir() if p.name != "README.md")
    for name in ("adapter.json", "hooks.json", "AGENTS.block.md"):
        assert (ROOT / "adapters/codex" / name).read_bytes() == (packaged / name).read_bytes()


def test_hooks_are_strings_for_six_documented_events():
    hooks = json.loads((ROOT / "adapters/codex/hooks.json").read_text())["hooks"]
    assert list(hooks) == ["SessionStart", "UserPromptSubmit", "PostToolUse", "PreCompact", "Stop", "SessionEnd"]
    for event, [group] in hooks.items():
        [hook] = group["hooks"]
        assert hook["command"] == "agent-autosave hook codex " + event and "args" not in hook
        assert hook.get("async", False) is (event == "PostToolUse")
        assert hook["timeout"] <= (3 if event == "SessionEnd" else 5)


@pytest.fixture
def homes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    return home


def test_codex_project_install_touches_only_its_files(tmp_path, homes):
    project = tmp_path / "repo"
    others = [n for n in OTHER_AGENT_FILES if n not in ("AGENTS.md", ".codex/hooks.json")] + ["CLAUDE.md", ".claude/settings.json"]
    for name in others:
        write(project / name, b"user content for " + name.encode() + b"\n")
    write(project / "AGENTS.md", b"# My rules\n")
    write(project / ".codex/hooks.json", b'{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}}\n')
    before = snapshot(project)
    result = install(project, agents=("codex",), command="/opt/my tools/agent-autosave")
    after = snapshot(project)
    assert set(result["changed"]) == CODEX_FILES
    assert {k: v for k, v in after.items() if k not in CODEX_FILES} == {k: v for k, v in before.items() if k not in CODEX_FILES}
    assert set(after) - set(before) == {".gitignore"}
    assert not (homes / ".codex/hooks.json").exists()
    hooks = json.loads((project / ".codex/hooks.json").read_text())["hooks"]
    assert hooks["Stop"][0]["hooks"][0]["command"] == "say done"
    assert hooks["Stop"][1]["hooks"][0]["command"] == "'/opt/my tools/agent-autosave' hook codex Stop"
    assert "<!-- agent-autosave:start -->" in (project / "AGENTS.md").read_text()
    remove(project)
    assert {k: v for k, v in snapshot(project).items() if not k.startswith(".autosave")} == before


def test_codex_user_level_install_and_remove(tmp_path, homes):
    project = tmp_path / "repo"
    project.mkdir()
    user = homes / ".codex/hooks.json"
    user.write_bytes(b'{\n  "hooks": {}\n}\n')
    original = user.read_bytes()
    result = install(project, agents=("codex",), command="agent-autosave", codex_user_dir=homes / ".codex")
    assert str(user.resolve()) in result["changed"] and not (project / ".codex").exists()
    assert len(json.loads(user.read_text())["hooks"]) == 6
    receipt = json.loads((project / ".autosave/install.json").read_text())
    assert [f.get("scope") for f in receipt["files"] if f["path"] == str(user.resolve())] == ["user"]
    remove(project)
    assert user.read_bytes() == original


def test_remove_refuses_foreign_absolute_receipt_paths(tmp_path, homes):
    project = tmp_path / "repo"
    project.mkdir()
    install(project, agents=("codex",), command="agent-autosave", codex_user_dir=homes / ".codex")
    receipt = project / ".autosave/install.json"
    data = json.loads(receipt.read_text())
    for item in data["files"]:
        if item.get("scope") == "user":
            item["path"] = str(tmp_path / "elsewhere.json")
    receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        remove(project)


def test_both_agents_install_together(tmp_path, homes):
    project = tmp_path / "repo"
    project.mkdir()
    result = install(project, agents=("claude-code", "codex"), command="agent-autosave")
    assert set(result["changed"]) == CODEX_FILES | {".claude/settings.json", "CLAUDE.md"}
    assert (project / ".gitignore").read_text() == "# agent-autosave:start\n.autosave/\n# agent-autosave:end\n"


def test_trust_detection(tmp_path, homes):
    project = tmp_path / "repo"
    (project / "sub").mkdir(parents=True)
    config = homes / ".codex/config.toml"
    assert not detect.codex_trusted(project)
    config.write_text('model = "x"\n[projects."{}"]\ntrust_level = "trusted"\n[other]\ntrust_level = "untrusted"\n'.format(project))
    assert detect.codex_trusted(project) and detect.codex_trusted(project / "sub")
    config.write_text('[projects."{}"]\ntrust_level = "untrusted"\n'.format(project))
    assert not detect.codex_trusted(project)
    entries = detect._trust_entries("[projects.'/a b']\ntrust_level = 'trusted'\n")
    assert entries == {"/a b": "trusted"}


def run_init(project, home, *args, trusted=False):
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    if trusted:
        (codex_home / "config.toml").write_text('[projects."{}"]\ntrust_level = "trusted"\n'.format(project.resolve()))
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), CODEX_HOME=str(codex_home))
    env.pop("PYTHONPATH", None)
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep)
                                  if not (Path(p) / "claude").exists() and not (Path(p) / "codex").exists())
    return subprocess.run([*LENIENT_CLI, "init"] + list(args), cwd=str(project),
                          env=env, capture_output=True, timeout=20)


def test_init_trusted_folder_writes_project_hooks(tmp_path):
    project, home = tmp_path / "repo", tmp_path / "home"
    project.mkdir()
    result = run_init(project, home, "--json", trusted=True)
    data = json.loads(result.stdout)["data"]
    assert result.returncode == 0 and data["agents"] == ["codex"]
    assert {k: data["codex"][k] for k in ("trusted", "hooks_file", "user_level")} == {
        "trusted": True, "hooks_file": ".codex/hooks.json", "user_level": False}
    assert set(data["changed"]) == CODEX_FILES | {".codex/config.toml"} and not (home / ".codex/hooks.json").exists()


def test_init_untrusted_folder_never_writes_user_level_hooks(tmp_path):
    """No silent fallback to ~/.codex/hooks.json: every Codex session would run our hooks."""
    project, home = tmp_path / "repo", tmp_path / "home"
    project.mkdir()
    result = run_init(project, home, "--agents", "codex", "--json")
    assert result.returncode == 1
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "codex_untrusted" and "agent-autosave add codex" in error["hint"]
    assert '[projects."{}"] trust_level = "trusted"'.format(project.resolve()) in error["hint"]
    assert not (home / ".codex/hooks.json").exists() and not (project / ".codex").exists()
    assert not (project / ".autosave").exists() and not (project / "AGENTS.md").exists()


def test_init_untrusted_skips_codex_installs_the_rest_and_prints_how_to_trust(tmp_path):
    project, home = tmp_path / "repo", tmp_path / "home"
    project.mkdir()
    (home / ".codex").mkdir(parents=True)
    (home / ".codex/hooks.json").write_bytes(b'{"hooks": {}}\n')
    before = (home / ".codex/hooks.json").read_bytes()
    result = run_init(project, home, "--agents", "codex,generic", "--json")
    data = json.loads(result.stdout)["data"]
    assert result.returncode == 0 and data["agents"] == ["generic"] and "codex" not in data
    [warning] = [w for w in data["warnings"] if w["code"] == "codex_untrusted_skipped"]
    assert warning["entry"] == '[projects."{}"]\ntrust_level = "trusted"'.format(project.resolve())
    assert warning["config_toml"] == str(home / ".codex/config.toml")
    assert (home / ".codex/hooks.json").read_bytes() == before and not (project / ".codex").exists()
    help_ = json.loads(run_init(tmp_path, home, "--agents", "codex", "--json").stdout)["error"]
    assert "agent-autosave add codex" in help_["hint"]
    assert (home / ".codex/hooks.json").read_bytes() == before


def test_trust_text_has_the_exact_entry_and_a_working_command(tmp_path, monkeypatch):
    project, home = tmp_path / 'my "odd" repo', tmp_path / "home"
    project.mkdir()
    (home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    help_ = codex_layers.trust_help(project)
    assert 'trust_level = "trusted"' in help_["text"] and "Then run: agent-autosave add codex" in help_["text"]
    assert help_["command"] in help_["text"]
    assert not detect.codex_trusted(project)
    subprocess.run(help_["command"], shell=True, check=True)
    assert detect.codex_trusted(project)


def test_init_user_level_flag_still_writes_user_hooks_in_untrusted_folder(tmp_path):
    project, home = tmp_path / "repo", tmp_path / "home"
    project.mkdir()
    result = run_init(project, home, "--agents", "codex", "--user-level", "--json")
    data = json.loads(result.stdout)["data"]
    assert result.returncode == 0 and data["codex"]["user_level"] is True
    assert (home / ".codex/hooks.json").exists() and not (project / ".codex").exists()


def test_add_codex_untrusted_is_refused_with_instructions(tmp_path, homes, monkeypatch, capsys):
    from agent_autosave import cli
    project = tmp_path / "repo"
    project.mkdir()
    install(project, ("generic",), command="/x/agent-autosave")
    monkeypatch.setattr(detect, "codex", lambda **k: {"installed": True, "version": None, "path": None})
    monkeypatch.chdir(project)
    assert cli.main(["--json", "add", "codex"]) == 1
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "codex_untrusted" and 'trust_level = "trusted"' in error["hint"]
    assert "agent-autosave add codex" in error["hint"]
    assert not (homes / ".codex/hooks.json").exists() and not (project / ".codex").exists()
    assert cli.main(["--json", "add", "codex", "--user-level"]) == 0
    assert (homes / ".codex/hooks.json").exists()


def test_add_codex_after_trusting_writes_project_hooks(tmp_path, homes, monkeypatch):
    from agent_autosave import cli
    project = tmp_path / "repo"
    project.mkdir()
    install(project, ("generic",), command="/x/agent-autosave")
    (homes / ".codex/config.toml").write_text('[projects."{}"]\ntrust_level = "trusted"\n'.format(project.resolve()))
    monkeypatch.setattr(detect, "codex", lambda **k: {"installed": True, "version": None, "path": None})
    monkeypatch.chdir(project)
    assert cli.main(["--json", "add", "codex"]) == 0
    assert (project / ".codex/hooks.json").exists() and not (homes / ".codex/hooks.json").exists()


def test_init_never_writes_a_second_layer(tmp_path):
    project, home = tmp_path / "repo", tmp_path / "home"
    project.mkdir()
    assert run_init(project, home, "--json", "--user-level", trusted=True).returncode == 0
    user = (home / ".codex/hooks.json").read_bytes()
    second = tmp_path / "repo2"
    second.mkdir()
    (home / ".codex/config.toml").write_text('[projects."{}"]\ntrust_level = "trusted"\n'.format(second.resolve()))
    data = json.loads(run_init(second, home, "--json").stdout)["data"]
    assert not (second / ".codex").exists() and (home / ".codex/hooks.json").read_bytes() == user
    assert data["codex"]["hooks_written"] is False and data["codex"]["user_level"] is True
    [warning] = data["warnings"]
    assert warning["code"] == "codex_hooks_in_other_layer" and warning["layer"] == "user"
    assert warning["path"] == str(home / ".codex/hooks.json") and warning["hooks"] == 6
    assert (second / "AGENTS.md").exists()
    receipt = json.loads((second / ".autosave/install.json").read_text())
    assert receipt["hooks"]["codex"] == {"layer": "user", "path": str(home / ".codex/hooks.json"), "added": False,
                                         "existing_hooks": 6}


def test_init_untrusted_with_project_hooks_writes_no_user_hooks(tmp_path):
    project, home = tmp_path / "repo", tmp_path / "home"
    project.mkdir()
    write(project / ".codex/hooks.json", json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "/x/agent-autosave hook codex Stop"}]}]}}).encode())
    result = run_init(project, home, "--json")
    assert result.returncode == 1 and json.loads(result.stdout)["error"]["code"] == "codex_untrusted"
    assert not (home / ".codex/hooks.json").exists()


def both_layers(tmp_path, trusted):
    project, home = tmp_path / "repo", tmp_path / "home"
    project.mkdir()
    ours = json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "'/v/agent-autosave' hook codex Stop"},
                                                     {"type": "command", "command": "say done"}]}]}}).encode()
    write(project / ".codex/hooks.json", ours)
    write(home / ".codex/hooks.json", ours)
    return project, home, json.loads(run_init(project, home, "--json", trusted=trusted).stdout)["data"]


@pytest.mark.parametrize("trusted", [True])
def test_init_warns_when_both_layers_have_our_hooks_with_exact_fix(tmp_path, trusted):
    project, home, data = both_layers(tmp_path, trusted)
    [warning] = [w for w in data["warnings"] if w["code"] == "duplicate_codex_hooks"]
    target = "--codex" if trusted else "--file " + str(project / ".codex/hooks.json")
    assert "agent_autosave.installer.userhooks {} --dry-run".format(target) in warning["hint"]
    assert warning["path"] == str((home / ".codex/hooks.json") if trusted else (project / ".codex/hooks.json"))
    # The warning repeats on a re-run ("already initialized").
    again = json.loads(run_init(project, home, "--json", trusted=trusted).stdout)["data"]
    assert [w["code"] for w in again["warnings"]] == ["duplicate_codex_hooks"]


def test_userhooks_tool_removes_codex_hooks_with_backup(tmp_path, monkeypatch):
    import io
    from agent_autosave.installer import userhooks
    _, home, _ = both_layers(tmp_path, True)
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    path = home / ".codex/hooks.json"
    before = path.read_bytes()
    out = io.StringIO()
    assert userhooks.main(["--codex", "--dry-run"], out=out) == 0 and path.read_bytes() == before
    assert "would remove Stop: '/v/agent-autosave' hook codex Stop" in out.getvalue()
    assert userhooks.main(["--codex"], out=io.StringIO()) == 0
    [backup] = list(path.parent.glob("hooks.json.agent-autosave-backup-*"))
    assert backup.read_bytes() == before
    assert json.loads(path.read_text())["hooks"]["Stop"][0]["hooks"] == [{"type": "command", "command": "say done"}]
    project_file = tmp_path / "repo/.codex/hooks.json"
    assert userhooks.main(["--file", str(project_file)], out=io.StringIO()) == 0
    assert "agent-autosave" not in project_file.read_text()


LEDGER = FIXTURES / "ledgers/two-hook-layers.jsonl"


def test_real_ledger_shows_every_codex_event_twice():
    """A real ledger with hooks in .codex/hooks.json AND ~/.codex/hooks.json.
    Every Codex event has a twin about 1 ms later (turn counter aside); Claude
    Code events (one layer) are single."""
    from datetime import datetime
    rows = [json.loads(l) for l in LEDGER.read_text().splitlines()]
    assert len(rows) == 61
    def key(r):
        return json.dumps({k: v for k, v in r.items() if k not in ("ts", "turn")}, sort_keys=True)
    def at(r):
        return datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
    codex = [r for r in rows if r["agent"] == "codex"]
    claude = [r for r in rows if r["agent"] == "claude-code"]
    # The first event is a lone SessionEnd from a session started before the
    # second layer existed; after it, every Codex event comes as a twin.
    assert codex[0]["kind"] == "session_end"
    pairs = list(zip(codex[1::2], codex[2::2]))
    assert len(codex) == 57 and len(pairs) == 28
    assert all(key(a) == key(b) for a, b in pairs)
    assert all(0 <= (at(b) - at(a)).total_seconds() < 0.01 for a, b in pairs)
    assert len({key(r) for r in claude}) == len(claude) == 4



def test_init_user_level_without_codex_is_rejected(tmp_path):
    project, home = tmp_path / "repo", tmp_path / "home"
    project.mkdir()
    (home / ".claude").mkdir(parents=True)
    result = run_init(project, home, "--json", "--agents", "claude-code", "--user-level")
    assert result.returncode == 2 and json.loads(result.stdout)["error"]["code"] == "not_supported"
    assert list(project.iterdir()) == []


# Hook processes -----------------------------------------------------------

def payload(name, project, transcript=None, session="C1"):
    data = load_payload("codex", name)
    if isinstance(data.get("tool_input"), dict) and isinstance(data["tool_input"].get("command"), str):
        # Real recordings name files under /private/tmp/aa-codex.
        data["tool_input"] = dict(data["tool_input"],
                                  command=data["tool_input"]["command"].replace(data["cwd"], str(project)))
    data["cwd"], data["session_id"] = str(project), session
    data["transcript_path"] = str(transcript) if transcript else None
    return data


def hook(project, event, data):
    env = dict(os.environ)
    return subprocess.run([*LENIENT_CLI, "hook", "codex", event],
                          input=json.dumps(data).encode(), capture_output=True, cwd=str(project), env=env, timeout=10)


@pytest.fixture
def project(tmp_path, homes):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    install(root, agents=("codex",), command="agent-autosave")
    return root


def copy_rollout(tmp_path, name):
    target = tmp_path / "sessions" / "rollout-2026-10-15T10-00-00-C1.jsonl"
    target.parent.mkdir(exist_ok=True)
    source = FIXTURES / "rollouts" / ("rollout-" + name + ".jsonl")
    if not source.exists():
        source = FIXTURES / "rollouts" / ("rollout-2026-10-15T10-00-00-" + name + ".jsonl")
    shutil.copy(source, target)
    return target


def real_shape_rollout(tmp_path, exits):
    """A rollout tail in the real 0.162.1 shape (a CommandExecution row from
    the real rollout) carrying the exit codes of the real hook payloads,
    whose own rollout was not recorded."""
    template = next(json.loads(line) for line in
                    (FIXTURES / "rollouts/rollout-real-session.jsonl").read_text().splitlines()
                    if '"CommandExecution"' in line and '"exit_code"' in line)
    target = tmp_path / "sessions" / "rollout-2026-10-15T16-45-46-real.jsonl"
    target.parent.mkdir(exist_ok=True)
    rows = []
    for ident, code in exits.items():
        row = json.loads(json.dumps(template))
        row["payload"]["item"].update(id=ident, exit_code=code)
        rows.append(json.dumps(row))
    target.write_text("\n".join(rows) + "\n")
    return target


def test_codex_session_builds_failing_save_from_rollout_exit_code(project, tmp_path):
    transcript = real_shape_rollout(tmp_path, {payload("PostToolUse-Bash", project)["tool_use_id"]: 0,
                                               payload("PostToolUse-Bash-fail", project)["tool_use_id"]: 7})
    for event, name in [("SessionStart", "SessionStart-startup"), ("UserPromptSubmit", "UserPromptSubmit"),
                        ("PostToolUse", "PostToolUse-update_plan"), ("PostToolUse", "PostToolUse-apply_patch"),
                        ("PostToolUse", "PostToolUse-Bash"), ("PostToolUse", "PostToolUse-Bash-fail"),
                        ("Stop", "Stop"), ("SessionEnd", "SessionEnd")]:
        result = hook(project, event, payload(name, project, transcript))
        assert result.returncode == 0 and result.stderr == b"" and result.stdout in (b"",)
    assert not (project / ".autosave/errors.log").exists()
    save = json.loads((project / ".autosave/save.json").read_text())
    assert save["status"] == "paused" and save["goal"].startswith("Use your update_plan tool")
    assert save["failing"]["exit"] == 7 and "SystemExit(7)" in save["failing"]["command"]
    assert [p["state"] for p in save["plan"]] == ["done", "doing", "todo"]
    assert "hello.py" in save["files_touched"]
    ledger = (project / ".autosave/events.jsonl").read_text()
    assert "hello autosave\\n" not in ledger and "print(" not in ledger
    assert (project / ".autosave/adapter-codex.json").exists()


def test_codex_limit_in_rollout_interrupts_and_next_session_gets_context(project, tmp_path):
    transcript = copy_rollout(tmp_path, "limit-tokens")
    for event, name in [("UserPromptSubmit", "UserPromptSubmit"), ("PostToolUse", "PostToolUse-Bash-fail"),
                        ("Stop", "Stop"), ("SessionEnd", "SessionEnd")]:
        assert hook(project, event, payload(name, project, transcript)).returncode == 0
    save = json.loads((project / ".autosave/save.json").read_text())
    assert save["status"] == "interrupted" and save["interrupted"]["reason"] == "rate_limit"
    assert save["interrupted"]["agent"] == "codex"
    events = [json.loads(l) for l in (project / ".autosave/events.jsonl").read_text().splitlines()]
    assert [e["kind"] for e in events].count("interrupted") == 1
    out = hook(project, "SessionStart", payload("SessionStart-startup", project, session="C2"))
    context = json.loads(out.stdout)["hookSpecificOutput"]
    assert context["hookEventName"] == "SessionStart"
    assert "unfinished task from Codex" in context["additionalContext"] and "hit a usage limit" in context["additionalContext"]


def test_claude_save_is_injected_into_codex(tmp_path, homes):
    """Claude Code leaves a failing task; Codex SessionStart receives it."""
    from tests.test_capture import hook as claude_hook, payload as claude_payload
    root = tmp_path / "both"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    install(root, agents=("claude-code", "codex"), command="agent-autosave")
    for event, name in [("UserPromptSubmit", "UserPromptSubmit"), ("PostToolUseFailure", "PostToolUseFailure"),
                        ("StopFailure", "StopFailure")]:
        assert claude_hook(root, event, claude_payload(name, root))[0].returncode == 0
    out = hook(root, "SessionStart", payload("SessionStart-startup", root, session="C9"))
    text = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "unfinished task from Claude Code" in text and "Failing:" in text


def test_codex_hook_without_autosave_is_silent(tmp_path, homes):
    folder = tmp_path / "plain"
    folder.mkdir()
    result = hook(folder, "Stop", payload("Stop", folder))
    assert result.returncode == 0 and result.stdout == b"" and list(folder.iterdir()) == []
