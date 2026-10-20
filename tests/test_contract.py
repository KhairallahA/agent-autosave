import io
import json

import pytest

from agent_autosave.cli import main
from agent_autosave.contract import ERROR_TYPES, TYPE_EXITS, ExitCode, emit, failure, success


class Terminal(io.StringIO):
    def isatty(self):
        return True


def test_exit_codes_are_the_public_contract():
    assert {code.name: code.value for code in ExitCode} == {
        "SUCCESS": 0, "FAILED": 1, "INVALID_ARGS": 2, "INTERNAL": 5,
    }


def test_every_error_code_has_a_type_and_every_type_an_exit():
    assert set(ERROR_TYPES.values()) == set(TYPE_EXITS)
    assert {kind: int(code) for kind, code in TYPE_EXITS.items()} == {
        "invalid_args": 2, "not_found": 1, "conflict": 1, "permission": 1, "io": 5, "internal": 5,
    }


def test_failure_envelope_has_type_code_message_hint_and_detail():
    error = failure("nothing_to_resume", "Nothing.", "Run status.", detail={"task": "t1"})["error"]
    assert error == {"type": "not_found", "code": "nothing_to_resume", "message": "Nothing.",
                     "hint": "Run status.", "detail": {"task": "t1"}}


def test_success_envelope_preserves_unicode_data():
    result = success({"goal": "إصلاح الاختبار"}, elapsed_ms=12)
    out = io.StringIO()
    emit(result, stdout=out)
    assert json.loads(out.getvalue()) == result
    assert set(result) == {"ok", "data", "meta"}
    assert result["meta"]["elapsed_ms"] == 12


@pytest.mark.parametrize("quiet", [False, True])
def test_human_errors_use_stderr_even_when_quiet(quiet):
    out, err = Terminal(), io.StringIO()
    emit(failure("not_initialized", "No save.", "Run init."),
         quiet=quiet, stdout=out, stderr=err)
    assert out.getvalue() == ""
    assert err.getvalue() == "error: No save.\nhint: Run init.\n"


def test_quiet_suppresses_only_human_success_text():
    out = Terminal()
    emit(success({}), text="Complete", stdout=out, quiet=True)
    assert out.getvalue() == ""
    emit(success({}), text="Complete", stdout=out)
    assert out.getvalue() == "Complete\n"


def test_json_flag_overrides_terminal_and_quiet():
    out, err = Terminal(), io.StringIO()
    result = failure("not_initialized", "No save.", "Run init.")
    emit(result, json_output=True, quiet=True, stdout=out, stderr=err)
    assert json.loads(out.getvalue()) == result
    assert err.getvalue() == ""


@pytest.mark.parametrize("args", [("--json", "status"), ("status", "--json")])
def test_json_flag_works_on_both_sides_of_command_in_terminal(monkeypatch, tmp_path, args):
    out = Terminal()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdout", out)
    assert main(args) == 1
    assert json.loads(out.getvalue())["error"]["code"] == "not_initialized"


def test_version_is_human_readable_in_terminal(monkeypatch):
    from agent_autosave import __version__
    out = Terminal()
    monkeypatch.setattr("sys.stdout", out)
    assert main(["--version"]) == 0
    assert out.getvalue() == "agent-autosave " + __version__ + "\n"


def test_quiet_success_is_silent_even_when_piped_but_failures_are_not():
    import io
    out, err = io.StringIO(), io.StringIO()  # StringIO is not a TTY: "piped"
    emit(success({"plan": []}), quiet=True, stdout=out, stderr=err)
    assert out.getvalue() == "" and err.getvalue() == ""
    emit(failure("invalid_args", "No plan step 9.", "Run plan set."), quiet=True, stdout=out, stderr=err)
    assert '"ok": false' in out.getvalue()
