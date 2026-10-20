"""A pipe into a filter hides the command's exit code, and a test runner's
failure summary beats a misleading exit 0: neither is ever saved as a pass."""
import json
from pathlib import Path
import subprocess

import pytest

from agent_autosave import shellexit
from agent_autosave.installer.install import install
from tests.test_capture import hook, payload

HIDDEN = [
    "python3 -m pytest -q 2>&1 | tail -20",
    "pytest | head -5",
    "make test | tee build.log",
    "npm test 2>&1 | grep -v warn",
    "cargo test | less",
    "go test ./... | more",
    "pytest | cat",
    "pytest | sed -n 1,5p",
    "pytest | awk '{print $1}'",
    "pytest | sort | uniq",
    "pytest | wc -l",
    "cd app && pytest -q | /usr/bin/tail -3",
    "LC_ALL=C pytest | env tail -n 3",
]
VISIBLE = [
    "python3 -m pytest -q",
    "set -o pipefail; pytest -q | tail -20",
    "set -euo pipefail && pytest | tail",
    "bash -o pipefail -c 'pytest | tail'",
    "echo 'pytest | tail'",
    "pytest || tail -5 log",
    "pytest | python3 report.py",
    "grep -n x file",
    "pytest -q | tail -3; echo done",
]


@pytest.mark.parametrize("command", HIDDEN)
def test_a_filter_at_the_end_of_a_pipe_hides_the_exit(command):
    assert shellexit.pipe_hides_exit(command)


@pytest.mark.parametrize("command", VISIBLE)
def test_pipefail_quotes_or_no_trailing_filter_keep_the_exit(command):
    assert not shellexit.pipe_hides_exit(command)


SUMMARIES = [
    ("pytest -q", "..F.\nFAILED tests/test_x.py::test_a - assert 1 == 2\n1 failed, 3 passed in 0.12s\n",
     "1 failed, 3 passed in 0.12s"),
    ("python3 -m pytest", "===== 2 failed, 1 passed, 1 warning in 0.30s =====\n", "===== 2 failed, 1 passed, 1 warning in 0.30s ====="),
    ("npx jest", "FAIL src/a.test.js\nTests:       1 failed, 4 passed, 5 total\nTime: 1 s\n", "Tests:       1 failed, 4 passed, 5 total"),
    ("npx vitest run", " Test Files  1 failed (1)\n      Tests  1 failed | 2 passed (3)\n", "Tests  1 failed | 2 passed (3)"),
    ("mocha", "  3 passing (5ms)\n  1 failing\n\n  1) adds:\n", "1 failing"),
    ("go test ./...", "--- FAIL: TestAdd (0.00s)\nFAIL\nFAIL\texample.com/m\t0.002s\n", "FAIL\texample.com/m\t0.002s"),
    ("cargo test", "test tests::adds ... FAILED\ntest result: FAILED. 1 passed; 1 failed; 0 ignored\n",
     "test result: FAILED. 1 passed; 1 failed; 0 ignored"),
    ("bundle exec rspec", "Failures:\n...\n3 examples, 1 failure\n", "3 examples, 1 failure"),
    ("vendor/bin/phpunit", "FAILURES!\nTests: 3, Assertions: 3, Failures: 1.\n", "Tests: 3, Assertions: 3, Failures: 1."),
    ("pnpm test", "Tests:       2 failed, 2 total\n", "Tests:       2 failed, 2 total"),
    ("yarn test", "  1 failing\n", "1 failing"),
    ("npm run test", "test result: FAILED. 0 passed; 2 failed\n", "test result: FAILED. 0 passed; 2 failed"),
]


@pytest.mark.parametrize("command,output,line", SUMMARIES)
def test_each_runner_failure_summary_is_found(command, output, line):
    assert shellexit.is_test_runner(command)
    assert shellexit.failure_summary(output) == line.strip()


@pytest.mark.parametrize("output", ["4 passed in 0.10s\n", "Tests:       5 passed, 5 total\n", "ok  \texample.com/m\t0.002s\n",
                                    "test result: ok. 3 passed; 0 failed\n", "3 examples, 0 failures\n", "OK (3 tests)\n", ""])
def test_passing_output_has_no_failure_summary(output):
    assert shellexit.failure_summary(output) is None


def test_not_a_runner():
    assert not shellexit.is_test_runner("python3 hello.py") and not shellexit.is_test_runner("ls tests/")


def test_judge():
    hidden = "python3 -m pytest -q 2>&1 | tail -20"
    assert shellexit.judge(hidden, 0, "1 failed, 2 passed in 0.1s\nnoise") == (1, "1 failed, 2 passed in 0.1s")
    assert shellexit.judge(hidden, 0, "3 passed in 0.1s") == (None, None)
    assert shellexit.judge("cat log | grep error", 1, "") == (None, None)  # grep's 1 is not the command's
    assert shellexit.judge("npm test", 0, "Tests: 1 failed, 1 total\nsecret stack trace") == (1, "Tests: 1 failed, 1 total")
    assert shellexit.judge("npm test", 2, "Tests: 1 failed, 1 total\nmore") == (2, "Tests: 1 failed, 1 total")
    assert shellexit.judge("python3 hello.py", 0, "1 failed") == (0, None)  # not a runner: output is not read
    assert shellexit.judge("pytest -q", 0, "3 passed") == (0, None)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    install(root)
    return root


def bash(project, command, stdout):
    data = payload("PostToolUse-Bash", project)
    data["tool_input"]["command"] = command
    data["tool_response"] = {"stdout": stdout, "stderr": "", "interrupted": False}
    result, _ = hook(project, "PostToolUse", data)
    assert result.returncode == 0
    hook(project, "Stop", payload("Stop", project))
    events = [json.loads(line) for line in (project / ".autosave/events.jsonl").read_text().splitlines()]
    return [e for e in events if e["kind"] == "tool"][-1], json.loads((project / ".autosave/save.json").read_text())


def test_claude_pytest_through_tail_with_a_failure_is_saved_as_failing_with_only_the_summary(project):
    out = "..F\n_____ test_median _____\n    assert median([1, 2]) == 1.5\nE   assert 1 == 1.5\nFAILED tests/test_stats.py::test_median\n1 failed, 2 passed in 0.05s\n"
    event, save = bash(project, "python3 -m pytest -q 2>&1 | tail -20", out)
    assert event["exit"] == 1 and event["output_tail"] == "1 failed, 2 passed in 0.05s"
    assert save["failing"]["command"] == "python3 -m pytest -q 2>&1 | tail -20"
    assert "assert median" not in (project / ".autosave/events.jsonl").read_text()


def test_a_passing_command_through_a_filter_is_saved_as_hidden_never_as_a_pass(project):
    event, save = bash(project, "python3 build.py 2>&1 | tail -5", "built\n")
    assert event["exit"] is None and "output_tail" not in event
    assert save["failing"] is None
    text = (project / ".autosave/SAVE.md").read_text()
    assert "exit hidden by a pipe" in text and "(exit 0)" not in text
