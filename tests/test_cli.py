import json

import pytest

from agent_autosave import __version__
from agent_autosave.contract import ERROR_TYPES
from tests.helpers import ROOT


INVOCATIONS = [
    ("init", "--agents", "claude-code,codex", "--user-level"),
    ("status",),
    ("show",),
    ("resume", "codex", "--dry-run"),
    ("save", "--note", "Keep the existing API"),
    ("note", "Keep the existing API", "--type", "decision"),
    ("history", "--limit", "5"),
    ("clear",),
    ("doctor",),
    ("add", "claude-code"),
    ("remove", "claude-code"),
    ("upgrade", "--dry-run"),
]


def envelope(result, exit_code, error=None):
    assert result.returncode == exit_code, result.stderr
    assert result.stderr == b""
    value = json.loads(result.stdout)
    assert len(result.stdout.splitlines()) == 1
    assert value["ok"] is (exit_code == 0)
    assert value["meta"]["version"] == __version__
    assert value["meta"]["save_v"] == 1
    assert isinstance(value["meta"]["elapsed_ms"], int)
    assert value["meta"]["elapsed_ms"] >= 0
    if error:
        assert set(value) == {"ok", "error", "meta"}
        assert value["error"]["code"] == error
        assert value["error"]["type"] == ERROR_TYPES[error]
        assert isinstance(value["error"]["detail"], dict)
        assert value["error"]["hint"]
    return value


@pytest.mark.parametrize("console", [False, True])
def test_version_from_installed_entrypoints(run_cli, console):
    result = run_cli("--version", console=console)
    value = envelope(result, 0)
    assert value["data"] == {"version": __version__}


@pytest.mark.parametrize("console", [False, True])
def test_status_in_empty_directory(run_cli, tmp_path, console):
    value = envelope(run_cli("status", "--json", console=console), 1, "not_initialized")
    assert value["error"]["hint"] == "Run agent-autosave init"
    assert list(tmp_path.iterdir()) == []


def test_piped_output_is_json_without_flag(run_cli):
    envelope(run_cli("status"), 1, "not_initialized")


def test_status_finds_parent_autosave_without_modifying_it(run_cli, tmp_path):
    autosave = tmp_path / ".autosave"
    autosave.mkdir()
    marker = autosave / "save.json"
    original = b'{ "sentinel": true }\r\n'
    marker.write_bytes(original)
    nested = tmp_path / "src" / "nested"
    nested.mkdir(parents=True)
    envelope(run_cli("status", "--json", cwd=nested), 1, "no_save")
    assert marker.read_bytes() == original
    assert list(autosave.iterdir()) == [marker]


def test_regular_file_is_not_an_autosave_directory(run_cli, tmp_path):
    (tmp_path / ".autosave").write_bytes(b"user file")
    envelope(run_cli("status"), 1, "not_initialized")
    assert (tmp_path / ".autosave").read_bytes() == b"user file"


INSTALL_BARE = {"doctor": (1, None), "add": (1, "not_initialized"), "remove": (0, None), "upgrade": (1, "not_initialized")}


@pytest.mark.parametrize("args", INVOCATIONS[8:])  # doctor, add, remove, upgrade
@pytest.mark.parametrize("global_flags", [True, False])
def test_install_commands_on_a_bare_autosave_do_not_write(run_cli, tmp_path, args, global_flags):
    (tmp_path / ".autosave").mkdir()
    before = set(tmp_path.rglob("*"))
    flags = ("--json", "--quiet")
    command = flags + args if global_flags else args + flags
    code, error = INSTALL_BARE[args[0]]
    result = run_cli(*command)
    assert result.returncode == code, result.stdout
    if error:
        envelope(result, code, error)
    assert set(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("args", INVOCATIONS[1:8])
@pytest.mark.parametrize("global_flags", [True, False])
def test_task_commands_without_a_task_report_no_save_and_do_not_write(run_cli, tmp_path, args, global_flags):
    (tmp_path / ".autosave").mkdir()
    before = set(tmp_path.rglob("*"))
    flags = ("--json", "--quiet")
    command = flags + args if global_flags else args + flags
    envelope(run_cli(*command), 1, "no_save")
    assert set(tmp_path.rglob("*")) == before


def test_init_rejects_unknown_agents(run_cli, tmp_path):
    envelope(run_cli("init", "--agents", "claude-code,vim"), 2, "invalid_args")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("args", [
    (), ("missing",), ("status", "--unknown"), ("--js", "status"),
    ("resume",), ("add", "unknown"), ("remove", "unknown"),
    ("init", "--agents", "codex,unknown"), ("init", "--agents", ""),
    ("note",), ("note", "text", "--type", "unknown"),
    ("history", "--limit", "zero"), ("history", "--limit", "0"),
    ("history", "--limit", "-1"), ("upgrade", "--dry-run", "--yes"),
    ("show", "--md", "--context"), ("show", "--md", "--json"),
    ("--json", "show", "--context"), ("--version", "status"),
])
def test_bad_arguments_use_contract_and_exit_two(run_cli, args):
    envelope(run_cli(*args), 2, "invalid_args")


def test_invalid_arguments_do_not_echo_user_secrets(run_cli):
    secret = "password=private-value-for-test"
    result = run_cli("add", secret)
    envelope(result, 2, "invalid_args")
    assert secret.encode() not in result.stdout + result.stderr


@pytest.mark.parametrize("command", [args[0] for args in INVOCATIONS] + ["hook"])
def test_each_command_exposes_help(run_cli, command):
    result = run_cli(command, "--help")
    assert result.returncode == 0
    assert b"--json" in result.stdout
    assert b"--quiet" in result.stdout
    assert result.stderr == b""


@pytest.mark.parametrize("args,payload", [
    (("hook", "claude-code", "Stop"), b'{"prompt":"private text"}'),
    (("hook", "codex", "SessionStart", "--json"), b"{invalid"),
    (("--quiet", "--json", "hook", "unknown", "FutureEvent"), b"\xff\x00"),
    (("hook",), b""),
    (("hook", "--invalid"), b""),
])
def test_hook_stub_is_silent_fail_open_and_writes_nothing(run_cli, tmp_path, args, payload):
    result = run_cli(*args, payload=payload)
    assert result.returncode == 0
    assert result.stdout == result.stderr == b""
    assert list(tmp_path.iterdir()) == []


def test_every_command_and_flag_has_help_text():
    from agent_autosave.arguments import build_parser

    def parsers(parser):
        yield parser
        for action in parser._actions:
            choices = getattr(action, "choices", None)
            for sub in (choices.values() if isinstance(choices, dict) else ()):
                yield from parsers(sub)

    for parser in parsers(build_parser()):
        assert parser.description, parser.prog
        for action in parser._actions:
            if action.dest in ("help",) or action.__class__.__name__ == "_SubParsersAction":
                continue
            assert action.help, "{}: {} has no help".format(parser.prog, action.dest)


def test_every_error_carries_a_hint():
    # Every fail(code, message, hint) in the package passes a non-empty hint.
    import ast
    for path in sorted((ROOT / "agent_autosave").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "fail":
                assert len(node.args) >= 3, "{}:{} fail() without a hint".format(path.name, node.lineno)
                hint = node.args[2]
                assert not (isinstance(hint, ast.Constant) and not str(hint.value).strip()), path.name


def test_usage_errors_point_at_the_commands_own_help(run_cli):
    result = run_cli("history", "--limit", "0", "--json")
    assert json.loads(result.stdout)["error"]["hint"] == "Run agent-autosave history --help to see the arguments."
