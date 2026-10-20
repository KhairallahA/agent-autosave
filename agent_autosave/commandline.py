"""Shell command text, read without running it: chains and read-only commands.

Everything here is a conservative reading of the text an agent ran. When a
command could write anything (a redirect, `tee`, an in-place flag, a command
substitution, an unknown program), it is not read-only: such a command
stays in the save's last actions, where a wrong guess costs nothing.
"""
from __future__ import annotations

import functools
import re
import shlex

_SEPARATORS = ("&&", "||")


def chain(command: str, *, pipes: bool = False) -> tuple[list[str], list[str]]:
    """Split a shell command on top-level ; && || and newlines (and | with
    `pipes`), quotes respected. Returns (parts, separators)."""
    parts, separators, current, quote, i = [], [], [], None, 0
    while i < len(command):
        ch = command[i]
        if quote:
            current.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(command):
                current.append(command[i + 1])
                i += 1
            elif ch == quote:
                quote = None
        elif ch == "\\" and i + 1 < len(command):
            current.extend(command[i:i + 2])
            i += 1
        elif ch in "'\"":
            quote = ch
            current.append(ch)
        elif command.startswith(_SEPARATORS, i) or ch in ";\n" or (pipes and ch == "|"):
            separator = command[i:i + 2] if command.startswith(_SEPARATORS, i) else ch
            parts.append("".join(current))
            separators.append(separator)
            current = []
            i += len(separator) - 1
        else:
            current.append(ch)
        i += 1
    parts.append("".join(current))
    return parts, separators


def _unquoted(command):
    """The command with quoted text blanked out: shell syntax only."""
    out, quote, i = [], None, 0
    while i < len(command):
        ch = command[i]
        if quote:
            if ch == "\\" and quote == '"' and i + 1 < len(command):
                i += 1
            elif ch == quote:
                quote = None
            out.append(" ")
        elif ch == "\\" and i + 1 < len(command):
            out.append("  ")
            i += 1
        elif ch in "'\"":
            quote = ch
            out.append(" ")
        else:
            out.append(ch)
        i += 1
    return "".join(out) if quote is None else None


# Syntax that can write files or run other commands, outside quotes:
# redirects (> >> >| &>), background &, command and process substitution.
_UNSAFE = re.compile(r">|`|\$\(|<\(|(?<!&)&(?!&)")
_HARMLESS = re.compile(r"(?:[0-9]*|&)>>?[ \t]*/dev/null(?=[\s;|&)]|\Z)|[0-9]*>&[0-9]+(?=[\s;|&)]|\Z)")
_READERS = {"cat", "head", "tail", "less", "ls", "tree", "pwd", "wc", "file", "stat", "rg", "grep",
            "fd", "find", "sed", "awk", "git"}
_FIND_WRITES = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
_GIT_READS = {"status", "diff", "log", "show"}


@functools.lru_cache(maxsize=4096)
def is_readonly(command: str) -> bool:
    """True only when every part of the chain (pipes included) starts with a
    read-only program used in a read-only way: cat, head, tail, less, ls,
    tree, pwd, wc, file, stat, rg, grep, fd, find (no -delete/-exec), sed -n
    (no -i), awk (no -i), or git status/diff/log/show; and nothing writes
    (no > or >> to a file, tee, or in-place flag; >/dev/null and 2>&1 are fine)."""
    syntax = _unquoted(command)
    if syntax is None:
        return False
    # Output thrown away (>/dev/null, 2>/dev/null, &>/dev/null) or an fd
    # duplicated (2>&1) writes nothing: blank those spans in both texts.
    for match in reversed(list(_HARMLESS.finditer(syntax))):
        start, end = match.span()
        syntax = syntax[:start] + " " * (end - start) + syntax[end:]
        command = command[:start] + " " * (end - start) + command[end:]
    if _UNSAFE.search(syntax):
        return False
    parts = [part.strip() for part in chain(command, pipes=True)[0] if part.strip()]
    return bool(parts) and all(_readonly_part(part) for part in parts)


def _readonly_part(part):
    try:
        words = shlex.split(part)
    except ValueError:
        return False
    if not words:
        return False
    program, args = words[0], words[1:]
    if program not in _READERS:
        return False
    if program == "find":
        return not any(arg in _FIND_WRITES for arg in args)
    if program == "sed":
        flags = [arg for arg in args if arg.startswith("-")]
        in_place = any(arg.startswith("--in-place") or (not arg.startswith("--") and "i" in arg[1:]) for arg in flags)
        quiet = any(arg in ("--quiet", "--silent") or (not arg.startswith("--") and "n" in arg[1:]) for arg in flags)
        return quiet and not in_place
    if program == "awk":
        return not any(arg == "-i" or arg.startswith(("-i", "--include")) for arg in args) and \
            not any("system(" in arg or "print >" in arg or "|" in arg for arg in args)
    if program == "rg":
        return not any(arg == "--pre" or arg.startswith("--pre=") for arg in args)
    if program == "fd":
        return not any(arg in ("-x", "-X", "--exec", "--exec-batch") for arg in args)
    if program == "git":
        while args and args[0] in ("--no-pager", "-C"):
            args = args[2:] if args[0] == "-C" else args[1:]
        return bool(args) and args[0] in _GIT_READS and not any(arg.startswith("--output") for arg in args)
    return True


# Programs that are checks on their own, and programs whose subcommand or
# script name makes them one. Wrappers (uv run, npx, bundle exec, ...) are
# skipped before matching.
_CHECK_PROGRAMS = {"pytest", "py.test", "tox", "nox", "vitest", "jest", "mocha", "ruff", "flake8", "mypy",
                   "pyright", "eslint", "tsc", "rspec", "phpunit"}
_CHECK_SUBCOMMANDS = {"go": {"test", "vet"}, "cargo": {"test", "clippy", "check"}, "make": {"test", "check", "lint"},
                      "dotnet": {"test"}, "gradle": {"test"}, "./gradlew": {"test"}, "mvn": {"test"},
                      "./mvnw": {"test"}}
_SCRIPT_RUNNERS = {"npm", "pnpm", "yarn", "bun"}
_SCRIPTS = re.compile(r"(?:test|lint|typecheck|type-check|types|tsc)(?::[\w.-]+)?\Z")
_WRAPPERS = (("uv", "run"), ("poetry", "run"), ("pipenv", "run"), ("hatch", "run"), ("pdm", "run"),
             ("bundle", "exec"), ("pnpm", "exec"), ("npx",), ("bunx",), ("yarn", "dlx"))


@functools.lru_cache(maxsize=4096)
def is_check(command: str) -> bool:
    """A test, lint or type-check run: pytest, python -m pytest/unittest, tox,
    nox, npm/pnpm/yarn/bun test and lint/typecheck scripts, vitest, jest,
    mocha, go test/vet, cargo test/clippy/check, make test/check/lint, ruff,
    flake8, mypy, pyright, eslint, tsc, rspec, phpunit, dotnet test, gradle
    test, mvn test. True when any part of a chain is one."""
    return any(_check_part(part) for part in chain(command, pipes=True)[0] if part.strip())


def _check_part(part):
    try:
        words = shlex.split(part)
    except ValueError:
        return False
    while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
        words = words[1:]  # CI=1 pytest
    for wrapper in _WRAPPERS:
        if tuple(words[:len(wrapper)]) == wrapper:
            words = words[len(wrapper):]
            break
    if not words:
        return False
    program = words[0].rsplit("/", 1)[-1] if not words[0].startswith("./") else words[0]
    rest = words[1:]
    if program in _CHECK_PROGRAMS:
        return True
    if re.fullmatch(r"python[0-9.]*", program):
        return len(rest) >= 2 and rest[0] == "-m" and rest[1] in ("pytest", "unittest", "mypy", "ruff", "flake8")
    if program in _CHECK_SUBCOMMANDS:
        return bool(rest) and rest[0] in _CHECK_SUBCOMMANDS[program]
    if program in _SCRIPT_RUNNERS:
        if rest[:1] == ["run"]:
            rest = rest[1:]
        return bool(rest) and (rest[0] == "t" and program == "npm" or bool(_SCRIPTS.match(rest[0])))
    return False
