"""The output shown in README.md and docs/ is what the tool really prints.

scripts/build-docs.py runs every command in a scratch repository and pastes
its output between `<!-- run: name -->` markers; these tests fail when a
pasted block, a quoted ledger line or a quoted file goes stale.
"""
import importlib
import json
import os
import re
import sys

import pytest

from tests.helpers import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
build_docs = importlib.import_module("build-docs")

pytestmark = [pytest.mark.skipif(os.name == "nt", reason="the tour runs commands in a POSIX pty"),
              pytest.mark.filterwarnings("ignore:.*fork.*:DeprecationWarning")]
FILE = re.compile(r"<!-- file: (?P<path>\S+) -->\n(?P<fence>`{3,})\w*\n(?P<body>.*?)\n(?P=fence)\n", re.S)
LINE = re.compile(r"<!-- line from (?P<path>\S+) -->\n```json\n(?P<body>.*?)\n```\n", re.S)
REQUIRED = ["getting-started", "architecture", "how-a-save-works", "troubleshooting", "configuration", "security",
            "contracts", "testing/README", "licensing", "faq"] + [
    "adapters/" + name for name in ("claude-code", "codex", "cursor", "gemini", "antigravity", "generic")]


@pytest.fixture(scope="module")
def tour(tmp_path_factory):
    return build_docs.tour(tmp_path_factory.mktemp("tour").resolve())


def test_every_output_block_matches_a_real_run(tour):
    import difflib
    stale, diffs = [], []
    for path, text in build_docs.rendered(tour).items():
        old = path.read_text(encoding="utf-8")
        if old != text:
            stale.append(str(path.relative_to(ROOT)))
            diffs += list(difflib.unified_diff(old.splitlines(), text.splitlines(), stale[-1], "tour", n=0, lineterm=""))
    # The diff is in the message so a one-off failure can be diagnosed from the log.
    assert not stale, "stale output in {}: run python scripts/build-docs.py\n{}".format(
        ", ".join(stale), "\n".join(diffs[:80]))


def test_the_docs_show_output_blocks():
    names = {path.relative_to(ROOT).as_posix(): set(build_docs.BLOCK.findall(path.read_text(encoding="utf-8")))
             for path in build_docs.documents()}
    assert len(build_docs.BLOCK.findall((ROOT / "docs/getting-started.md").read_text(encoding="utf-8"))) >= 15
    assert names["docs/troubleshooting.md"]


def test_quoted_files_and_ledger_lines_are_current():
    checked = 0
    for path in build_docs.documents():
        text = path.read_text(encoding="utf-8")
        for match in FILE.finditer(text):
            assert match.group("body") == (ROOT / match.group("path")).read_text(encoding="utf-8").rstrip("\n"), path
            checked += 1
        for match in LINE.finditer(text):
            lines = (ROOT / match.group("path")).read_text(encoding="utf-8").splitlines()
            assert match.group("body") in lines and json.loads(match.group("body")), path
            checked += 1
    assert checked >= 3


@pytest.mark.parametrize("page", REQUIRED)
def test_each_page_says_what_question_it_answers(page):
    text = (ROOT / "docs" / (page + ".md")).read_text(encoding="utf-8")
    title, _, rest = text.partition("\n\n")
    assert title.startswith("# ") and rest.strip(), page
    assert len(text) > 600, page


def test_every_configuration_field_is_documented():
    from agent_autosave.config import DEFAULTS
    text = (ROOT / "docs/configuration.md").read_text(encoding="utf-8")
    for section, value in DEFAULTS.items():
        names = ["{}.{}".format(section, key) for key in value] if isinstance(value, dict) else [section]
        for name in names:
            assert "`{}`".format(name) in text, name


def test_troubleshooting_covers_the_bugs_we_hit():
    text = (ROOT / "docs/troubleshooting.md").read_text(encoding="utf-8")
    for heading in ("## Every event is recorded twice", "## Codex records nothing",
                    "## Status looks out of date right after quitting Codex",
                    "## Hooks do nothing when the agent starts from an IDE or desktop app"):
        assert heading in text
