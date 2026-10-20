"""Dispatch for the agent-autosave command.

Each command lives in its own module under `agent_autosave.commands` and is
imported only when it runs. The hook and status line paths return before
argparse, JSON and the contract are imported, so Claude Code's per-message
status line and every hook start as little Python as they can.
"""
from __future__ import annotations

from collections.abc import Sequence
from importlib import import_module
import os
from pathlib import Path
import sys
import time

from . import __version__

TYPE_CHECKING = False  # typing itself is not imported on the fast paths
if TYPE_CHECKING:
    import argparse

    from .contract import Outcome

# Commands that read or change the current task: they need task.json.
TASK_COMMANDS = ("status", "show", "resume", "save", "note", "done", "history", "clear", "go")
# Commands that read or write memory: they need .autosave/ but no task yet.
MEMORY_COMMANDS = ("memory", "journal", "vault", "watch", "share", "handoff")
# Commands that change or audit agent files: an unexpected error is an adapter problem.
INSTALL_COMMANDS = ("add", "remove", "doctor", "upgrade")


def _has_autosave() -> bool:
    current = Path.cwd()
    return any((root / ".autosave").is_dir() for root in (current, *current.parents))


def _run(command: str, args: argparse.Namespace) -> Outcome:
    return import_module("agent_autosave.commands." + command).run(args)


def _dispatch(args: argparse.Namespace) -> Outcome:
    from .contract import ExitCode, fail, success
    if args.version:
        return success({"version": __version__}), ExitCode.SUCCESS, None
    if args.command == "init":
        return _run("init", args)
    if not _has_autosave():
        return fail("not_initialized", "No .autosave/ directory exists here or in a parent directory.",
                    "Run agent-autosave init")
    if args.command in INSTALL_COMMANDS:
        try:
            return _run(args.command, args)
        except Exception as error:  # never a traceback
            return fail("command_failed", "{} failed: {}.".format(args.command, type(error).__name__),
                        "Run agent-autosave doctor.")
    if args.command in MEMORY_COMMANDS:
        try:
            return _run(args.command, args)
        except Exception as error:  # never a traceback; details in errors.log
            return fail("command_failed", "{} failed: {}.".format(args.command, type(error).__name__),
                        "See .autosave/errors.log.")
    if args.command in TASK_COMMANDS:
        from .commands import common
        try:
            if args.command == "done" and not common.ready(common.find_project()):
                return common.nothing_to_finish()
            if not common.ready(common.find_project()):
                # A bare .autosave/ (no task yet): report, write nothing.
                return common.not_ready()
            return _run(args.command, args)
        except Exception as error:  # never a traceback; details in errors.log
            return fail("command_failed", "{} failed: {}.".format(args.command, type(error).__name__),
                        "See .autosave/errors.log.")
    return _run(args.command, args)  # plan, record


def _hook_payload_on_stdin() -> bool:
    """True when stdin is not a terminal and holds one JSON object."""
    stream = sys.stdin
    try:
        if stream is None or stream.isatty():
            return False
        import json
        if os.name != "nt":
            import select
            if not select.select([stream], [], [], 0.5)[0]:
                return False  # an open pipe nobody writes to: do not hang
        raw = stream.buffer.read(1 << 20) if hasattr(stream, "buffer") else stream.read(1 << 20)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        return isinstance(json.loads(raw), dict)
    except (OSError, ValueError, TypeError):
        return False


def main(argv: Sequence[str] | None = None) -> int:
    started = time.perf_counter()
    arguments = list(sys.argv[1:] if argv is None else argv)

    # The hook path skips argparse and the CLI modules to stay fast. Even
    # malformed invocations stay fail-open.
    leading = 0
    while leading < len(arguments) and arguments[leading] in ("--json", "--quiet"):
        leading += 1
    if leading < len(arguments) and arguments[leading] == "hook":
        if arguments[leading + 1:] not in (["--help"], ["-h"]):
            # Fail open: capture catches every error and always returns 0.
            rest = arguments[leading + 1:]
            if len(rest) == 2:
                from .capture import main as capture_main
                capture_main(rest[0], rest[1])
            return 0  # ExitCode.SUCCESS, without importing the contract

    if arguments[leading:] == ["statusline"]:
        # Claude Code runs this after every message: skip argparse too.
        from .statusline import main as statusline_main
        return statusline_main()

    if leading == len(arguments) and _hook_payload_on_stdin():
        # An agent ran us without a subcommand (e.g. Cursor importing old
        # Claude Code hooks, which drops "args"): never print into an agent.
        return 0  # ExitCode.SUCCESS

    from .arguments import COMMANDS, UsageError, build_parser, validate
    from .contract import emit, fail
    parser = build_parser()
    try:
        args = parser.parse_args(arguments)
        validate(args)
    except UsageError as exc:
        named = next((a for a in arguments if not a.startswith("-")), None)
        usage = "agent-autosave {} --help".format(named) if named in COMMANDS else "agent-autosave --help"
        result, code, text = fail("invalid_args", str(exc), "Run {} to see the arguments.".format(usage))
        json_output = "--json" in arguments
        quiet = "--quiet" in arguments
    else:
        json_output = getattr(args, "json", False)
        quiet = getattr(args, "quiet", False)
        if args.command == "mcp":
            # stdout carries MCP messages only: no envelope after it.
            from .mcp import serve
            return serve(args.project)
        result, code, text = _dispatch(args)
    result["meta"]["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    if result["ok"] and text is None:
        text = "agent-autosave " + __version__
    elif not result["ok"] and result["error"]["code"] not in ("codex_untrusted",):
        text = None  # failures print message and hint only
    emit(result, json_output=json_output, quiet=quiet, text=text)
    return int(code)
