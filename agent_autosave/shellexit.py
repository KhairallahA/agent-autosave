"""What a shell command's exit code really says.

Two cases where the exit an agent reports is not the command's:

- A pipeline reports its last stage. `pytest -q 2>&1 | tail -20` exits with
  tail's 0 even when a test failed. When the last stage of the final
  pipeline is a filter and `pipefail` is not set, the exit is unknown
  ("exit hidden by a pipe"), never a pass.
- A test runner's own failure summary ("1 failed", "Tests: 1 failed",
  "FAIL", "test result: FAILED") is evidence of a failure whatever the exit
  says. Only that one summary line is kept, never the rest of the output.

Standard library only; imported only for tool events.
"""
from __future__ import annotations

import os
import re

FILTERS = frozenset(("tail", "head", "grep", "egrep", "fgrep", "less", "more", "cat", "tee",
                     "sed", "awk", "gawk", "sort", "uniq", "wc"))
WRAPPERS = frozenset(("env", "command", "nice", "stdbuf", "time"))
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
RUNNER = re.compile(
    r"(?:^|[\s;&|/(])(?:pytest|py\.test|jest|vitest|mocha|rspec|phpunit)(?:$|[\s;&|)])"
    r"|(?:^|[\s;&|(])go\s+test\b|(?:^|[\s;&|(])cargo\s+(?:\+\S+\s+)?test\b"
    r"|(?:^|[\s;&|(])(?:npm|pnpm|yarn)\s+(?:run\s+)?test\b")
SUMMARY = [re.compile(pattern) for pattern in (
    r"^=*\s*(?:\d+ \w+, )*\d+ (?:failed|errors?)\b.*\bin [\d.]+s\b.*$",     # pytest
    r"^Tests:\s+.*\b[1-9]\d* failed\b.*$",                                 # jest
    r"^Tests\s+[1-9]\d* failed\b.*$",                                      # vitest
    r"^[1-9]\d* failing\b.*$",                                             # mocha
    r"^FAIL(?:\t.*)?$",                                                    # go test
    r"^test result: FAILED\b.*$",                                          # cargo test
    r"^\d+ examples?, [1-9]\d* failures?\b.*$",                            # rspec
    r"^Tests: \d+, Assertions: \d+, (?:Errors: \d+, )?Failures: [1-9]\d*.*$",  # phpunit
)]
SUMMARY_MAX = 300


def _split(command: str) -> list[list[str]]:
    """The last statement of `command` as pipeline stages of words, honouring
    quotes and backslashes. Separators: ; & && || and newlines."""
    statements, stages, word, words = [], [], "", []
    quote, i, n = None, 0, len(command)

    def end_word():
        nonlocal word
        if word:
            words.append(word)
        word = ""

    while i < n:
        c = command[i]
        if quote:
            if c == quote:
                quote = None
            elif c == "\\" and quote == '"' and i + 1 < n:
                i += 1
                word += command[i]
            else:
                word += c
        elif c in "'\"":
            quote = c
            word += ""
        elif c == "\\" and i + 1 < n:
            i += 1
            word += command[i]
        elif c in " \t":
            end_word()
        elif c == "|" and command[i + 1:i + 2] == "|" or c in ";\n" or (c == "&" and command[i + 1:i + 2] != ">"
                                                                         and command[i - 1:i] not in (">", "<")):
            end_word()
            stages.append(words)
            statements.append(stages)
            stages, words = [], []
            if command[i:i + 2] in ("||", "&&"):
                i += 1
        elif c == "|":
            end_word()
            stages.append(words)
            words = []
            if command[i + 1:i + 2] == "&":
                i += 1
        else:
            word += c
        i += 1
    end_word()
    stages.append(words)
    statements.append(stages)
    for stages in reversed(statements):
        if any(stages):
            return stages
    return [[]]


def _program(words: list[str]) -> str:
    for word in words:
        if ASSIGNMENT.match(word) or word in WRAPPERS or word.startswith("-"):
            continue
        return os.path.basename(word)
    return ""


def pipe_hides_exit(command: str) -> bool:
    """True when the final pipeline ends in a filter and pipefail is off."""
    if "pipefail" in command:
        return False
    stages = _split(command)
    return len(stages) > 1 and _program(stages[-1]) in FILTERS


def is_test_runner(command: str) -> bool:
    return bool(RUNNER.search(command))


def failure_summary(output: str) -> str | None:
    """The last failure-summary line of a known test runner, or None."""
    found = None
    for line in str(output or "").splitlines():
        line = line.strip()
        if line and any(pattern.match(line) for pattern in SUMMARY):
            found = line
    return found[:SUMMARY_MAX] if found else None


def judge(command: str, exit: int | None, output: str | None) -> tuple[int | None, str | None]:
    """(exit to record, output to keep). Output is only ever the summary line;
    the caller keeps its own failure output when this returns None for it."""
    if is_test_runner(command):
        summary = failure_summary(output or "")
        if summary:
            return (exit if type(exit) is int and exit != 0 else 1), summary
    if pipe_hides_exit(command):
        return None, None
    return exit, None
