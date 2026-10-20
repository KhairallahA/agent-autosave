"""The plan CLI, record on/off through config, the absolute CLI path in blocks."""
import json
import os
from pathlib import Path
import subprocess

from agent_autosave.capture import handle
from agent_autosave.compiler import compile_events
from agent_autosave.installer.install import install
from agent_autosave.render import _text, render
from tests.helpers import LENIENT_CLI, event, load_payload, tool

FIX = Path(__file__).resolve().parent / "fixtures/codex"


def cli(project, *args):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return subprocess.run([*LENIENT_CLI] + list(args), cwd=str(project), env=env,
                          capture_output=True, timeout=20)


def test_record_on_off_via_config_reaches_hooks_without_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ch"))
    project = tmp_path / "repo"
    project.mkdir()
    install(project, agents=("codex",), command="agent-autosave")
    target = tmp_path / "rec"
    result = cli(project, "record", "on", "--dir", str(target), "--agent-version", "codex-cli 0.162.1", "--json")
    assert result.returncode == 0, result.stdout
    config = json.loads((project / ".autosave/config.json").read_text())
    assert config["record"] == {"enabled": True, "dir": str(target.resolve()), "agent_version": "codex-cli 0.162.1"}
    data = load_payload("codex", "Stop")
    data["cwd"] = str(project)
    handle("codex", "Stop", json.dumps(data).encode(), env={})  # no AGENT_AUTOSAVE_* at all
    [saved] = list((target / "codex").glob("Stop-*.json"))
    assert json.loads(saved.read_text())["agent_version"] == "codex-cli 0.162.1"
    assert cli(project, "record", "off", "--json").returncode == 0
    handle("codex", "Stop", json.dumps(data).encode(), env={})
    assert len(list((target / "codex").glob("Stop-*.json"))) == 1
    assert json.loads((project / ".autosave/config.json").read_text())["record"]["enabled"] is False


def test_record_env_var_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ch"))
    project = tmp_path / "repo"
    project.mkdir()
    install(project, agents=("codex",), command="agent-autosave")
    data = dict(load_payload("codex", "Stop"), cwd=str(project))
    handle("codex", "Stop", json.dumps(data).encode(),
           env={"AGENT_AUTOSAVE_RECORD": "1", "AGENT_AUTOSAVE_RECORD_DIR": str(tmp_path / "e")})
    assert list((tmp_path / "e/codex").glob("Stop-*.json"))


def test_blocks_use_the_resolved_quoted_command(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ch"))
    project = tmp_path / "repo"
    project.mkdir()
    install(project, agents=("claude-code", "codex"), command="/Users/k/my venv/bin/agent-autosave")
    for name in ("AGENTS.md", "CLAUDE.md"):
        text = (project / name).read_text()
        assert "`'/Users/k/my venv/bin/agent-autosave' plan set --quiet" in text
        assert "`'/Users/k/my venv/bin/agent-autosave' plan done --quiet <n>`" in text
        assert "`agent-autosave plan" not in text
    assert "update_plan" in (project / "AGENTS.md").read_text() and "- [ ] open step" in (project / "AGENTS.md").read_text()
    plain = tmp_path / "plain"
    plain.mkdir()
    install(plain, agents=("codex",), command="agent-autosave")
    assert "`agent-autosave plan set --quiet" in (plain / "AGENTS.md").read_text()


def test_absolute_cli_path_is_still_our_own_command():
    save = compile_events([event("prompt", text="Fix the failing auth test suite."),
                           tool(1, summary="'/Users/k/my venv/bin/agent-autosave' plan set --quiet a b", exit=0)])
    assert save["last_actions"] == []


def test_underscores_render_without_stray_backslashes():
    assert _text("Use your update_plan tool") == "Use your update_plan tool"
    assert _text("_emph_ and __init__") == "\\_emph\\_ and \\_\\_init\\_\\_"
    save = compile_events([event("prompt", text="Use your update_plan tool to plan.")])
    assert "**Goal:** Use your update_plan tool to plan." in render(save)


def test_steering_is_a_list_and_identical_consecutive_prompts_collapse():
    run = 'Run python3 hello.py. Then run python3 -c "raise SystemExit(7)".'
    save = compile_events([event("prompt", 0, text="Write hello.py that prints hello."),
                           event("prompt", 1, text=run), tool(2, summary="python3 hello.py"),
                           event("prompt", 3, text=run), event("prompt", 4, text="Then commit it please."),
                           event("prompt", 5, text=run)])
    assert save["steering"] == [run, "Then commit it please.", run]
    md = render(save)
    assert "**User steering:**\n- Run python3" in md and "\n- Then commit it please.\n" in md and "; " not in md


def test_failing_block_hidden_when_output_empty():
    md = render(compile_events([event("prompt", text="Fix the failing auth test suite."),
                                tool(1, summary='python3 -c "raise SystemExit(7)"', exit=7, output_tail="")]))
    section = md.split("## Failing\n", 1)[1].split("\n## ", 1)[0]
    assert section == '`python3 -c "raise SystemExit(7)"` (exit 7)\n'
    md = render(compile_events([event("prompt", text="Fix the failing auth test suite."),
                                tool(1, summary="pytest", exit=1, output_tail="FAIL x")]))
    assert "`pytest` (exit 1)\n\n    FAIL x\n" in md


def test_real_ledger_compiles_with_list_steering_and_real_failure():
    """A real two-layer ledger, compiled (duplicates included)."""
    rows = [json.loads(l) for l in (FIX / "ledgers/two-hook-layers.jsonl").read_text().splitlines()]
    for r in rows:
        r.pop("task_id", None)
    save = compile_events(rows)
    assert save["failing"]["exit"] == 7
    assert len(save["steering"]) == len(set(save["steering"]))  # twin prompts collapse
    md = render(save)
    assert "update\\_plan" not in md and "**Goal:** Use your update_plan tool" in md
