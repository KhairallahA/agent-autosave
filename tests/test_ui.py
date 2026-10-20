"""The shared terminal style and the styled and plain output goldens."""
import io
import os
import time

import pytest

from agent_autosave import ui
from tests import ui_cases


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def utc(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset")
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize("name", sorted(ui_cases.render_all()))
def test_output_matches_golden(name, utc):
    expected = (ui_cases.GOLDENS / name).read_text(encoding="utf-8")
    assert ui_cases.render_all()[name] == expected, "run python scripts/update-goldens.py and review the diff"


@pytest.mark.parametrize("case", sorted(ui_cases.CASES))
def test_plain_output_is_styled_output_without_colour(case, utc):
    styled = ui_cases.CASES[case](ui.Style(color=True))
    plain = ui_cases.CASES[case](ui.Style(color=False))
    assert "\033[" in styled and "\033[" not in plain
    if case != "show":  # show in a terminal also drops the version comment
        assert ui.plain(styled) == plain


def test_colour_follows_the_terminal_and_no_color():
    assert ui.color_wanted(Terminal(), env={}) is True
    assert ui.color_wanted(io.StringIO(), env={}) is False
    assert ui.color_wanted(Terminal(), env={"NO_COLOR": "1"}) is False
    assert ui.color_wanted(Terminal(), env={"TERM": "dumb"}) is False
    assert ui.color_wanted(io.StringIO(), env={"FORCE_COLOR": "1"}) is True
    assert ui.color_wanted(io.StringIO(), env={"FORCE_COLOR": "1", "NO_COLOR": "1"}) is False


def test_symbols_fall_back_to_ascii_for_legacy_consoles():
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    assert not ui.unicode_ok(stream)
    style = ui.Style(unicode=False)
    assert style.mark("ok") == "+" and style.sym("on") == "[x]"
    text = ui_cases.CASES["init"](style)
    assert text.encode("cp1252")


def test_fields_align_and_wrap():
    style = ui.Style(width=40)
    lines = style.fields([("Goal", "word " * 12), ("Next", "x")])
    assert lines[0].startswith("  Goal  word") and lines[1].startswith("        word")
    assert all(len(line) <= 40 for line in lines)


def test_json_is_unchanged_by_styling(tmp_path, monkeypatch, capsys):
    from agent_autosave.cli import main
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert main(["status", "--json"]) == 1
    assert "\033[" not in capsys.readouterr().out


def test_errors_are_coloured_only_on_a_terminal(monkeypatch):
    from agent_autosave.contract import emit, failure
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    out, err = Terminal(), Terminal()
    emit(failure("no_save", "No save.", "Run init."), stdout=out, stderr=err)
    assert err.getvalue() == "\033[1;31merror:\033[0m No save.\n\033[33mhint:\033[0m Run init.\n"
    if os.name != "nt":
        monkeypatch.setenv("NO_COLOR", "1")
        err = Terminal()
        emit(failure("no_save", "No save.", "Run init."), stdout=out, stderr=err)
        assert err.getvalue() == "error: No save.\nhint: Run init.\n"
