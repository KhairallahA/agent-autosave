"""The agent-autosave command line: every command, its flags, and the checks
argparse can't express.

Kept out of cli.py so the hook and status line paths, which skip argparse,
never import it.
"""
from __future__ import annotations

import argparse

AGENTS = ("claude-code", "codex", "cursor", "gemini", "antigravity", "copilot", "opencode", "generic")
COMMANDS = {
    "init": "Set up agent-autosave here and wire your agents (a wizard in a terminal).",
    "status": "Show the save on one screen: status, goal, plan, next step, handoffs.",
    "show": "Print the save: SAVE.md, save.json (--json), or the block an agent is given (--context).",
    "resume": "Start an agent with the save as its first prompt (a handoff).",
    "save": "Rebuild the save from the ledger now.",
    "note": "Add a decision, todo or warning to the save.",
    "done": "Mark the task finished; the next prompt starts a new task.",
    "history": "List archived saves (earlier tasks), newest first.",
    "clear": "Archive the save and start a new task.",
    "doctor": "Check every adapter: green, yellow or red, with a hint for each problem.",
    "add": "Wire one more agent, or repair an agent's adapter.",
    "remove": "Remove one agent's adapter (or all with --all), restoring files byte for byte.",
    "upgrade": "Refresh adapters to this version (a dry run unless --yes).",
    "record": "Keep raw hook payloads as test fixtures: record on [--dir D], record off.",
    "plan": "Record the task plan: plan set \"step\" ..., plan done <n>.",
    "memory": "Project memory (MEMORY.md): list, search, add, propose, review, accept, reject, pin, forget.",
    "journal": "List the journal: one page per finished or paused task (--search, --path).",
    "vault": "Move the in-repo journal and memory into the vault folder (vault move).",
    "mcp": "Serve the save and memory to an agent as MCP tools (stdio; agents start it themselves).",
    "statusline": "Print one status line for Claude Code from its status JSON on stdin.",
    "watch": "A live screen: slots, status, usage per agent, last action, failing command (q quits).",
    "share": "Commit MEMORY.md and the journal for the team (share on), or keep them private (share off).",
    "handoff": "Hand the task to a teammate: handoff export writes .autosave-handoff.md; handoff import loads it.",
    "go": "Hand the terminal to an agent; on a usage limit, offer the next one (Enter continues).",
    "hook": "Internal: the entry point agents call; always exits 0.",
}
EPILOG = """examples:
  agent-autosave init                 set up this folder (asks a few questions)
  agent-autosave status               where the task stands
  agent-autosave resume codex         continue the task in Codex
  agent-autosave doctor               check the hooks

Output is JSON when piped or with --json. Docs: docs/getting-started.md"""


class UsageError(Exception):
    """An invalid CLI invocation; never carries raw user input into output."""


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse can embed arbitrary argument values in its error text.
        # Never echo that text: it may hold a secret the user typed.
        raise UsageError("Invalid command or arguments.")


def _common_flags(parser: argparse.ArgumentParser) -> None:
    # Suppressed defaults let flags work before or after the subcommand without
    # a subparser's default overwriting an explicitly supplied global flag.
    parser.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS,
        help="Print the JSON contract (automatic when piped).",
    )
    parser.add_argument(
        "--quiet", action="store_true", default=argparse.SUPPRESS,
        help="Suppress human success output; retain JSON and errors.",
    )


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Expected a positive integer.")
    return number


def build_parser() -> Parser:
    parser = Parser(
        prog="agent-autosave", allow_abbrev=False,
        description="Autosave for coding agents: every session is saved, and the next agent picks up the task.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _common_flags(parser)
    parser.add_argument("--version", action="store_true", help="Show the package version.")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>", title="commands")
    for name, description in COMMANDS.items():
        listed = {} if name == "hook" else {"help": description}  # hook is internal: not listed
        command = subparsers.add_parser(name, description=description, allow_abbrev=False, **listed)
        _common_flags(command)
        if name == "init":
            command.add_argument("--agents", metavar="LIST",
                                 help="Comma-separated agents to wire, e.g. claude-code,codex; asks no questions.")
            command.add_argument("--yes", "-y", action="store_true",
                                 help="Accept the defaults without asking: every detected agent, .autosave/ git-ignored.")
            command.add_argument("--reconfigure", action="store_true",
                                 help="Run the wizard again in a set-up folder: add agents, change preferences.")
            command.add_argument("--user-level", action="store_true",
                                 help="Codex only: put the hooks in ~/.codex/hooks.json (they run in every Codex session).")
            command.add_argument("--no-mcp", action="store_true",
                                 help="Do not register the agent-autosave MCP server with the agents.")
        elif name == "share":
            command.add_argument("share_action", choices=("on", "off"), metavar="on|off",
                                 help="on: commit MEMORY.md and the journal; off: ignore .autosave/ again.")
        elif name == "handoff":
            command.add_argument("handoff_action", choices=("export", "import"), metavar="export|import",
                                 help="export: write .autosave-handoff.md; import: load it into this branch.")
        elif name == "watch":
            command.add_argument("--once", action="store_true", help="Print one screen and exit.")
        elif name == "status":
            command.add_argument("--all", action="store_true", help="Every branch's task, not just this branch's.")
        elif name == "show":
            formats = command.add_mutually_exclusive_group()
            formats.add_argument("--md", action="store_true", help="SAVE.md (the default).")
            formats.add_argument("--context", action="store_true",
                                 help="The block an agent gets at session start, with any staleness warning.")
            command.add_argument("--explain", action="store_true",
                                 help="With --context: list each section, its size, and whether it was kept or dropped.")
            command.add_argument("--task", metavar="ID", help="Another task: a branch slot or a history entry.")
        elif name == "remove":
            command.add_argument("agent", nargs="?", choices=AGENTS, metavar="agent",
                                 help="One of: " + ", ".join(AGENTS) + ".")
            command.add_argument("--all", action="store_true", help="Remove every agent's adapter.")
        elif name == "doctor":
            command.add_argument("--prune", action="store_true",
                                 help="Drop stale entries from the user-level hooks registry.")
        elif name == "go":
            from .config import SWITCH_AGENTS
            command.add_argument("agents", nargs="*", choices=SWITCH_AGENTS, metavar="agent",
                                 help="Agents to try, in order (default: switch.order / installed).")
            command.add_argument("--auto", action="store_true",
                                 help="Continue after a 5 s countdown; any key cancels.")
            command.add_argument("--dry-run", action="store_true",
                                 help="Print the first launch command and exit.")
        elif name == "add":
            command.add_argument("agent", choices=AGENTS, metavar="agent",
                                 help="One of: " + ", ".join(AGENTS) + ".")
            command.add_argument("--user-level", action="store_true",
                                 help="Codex only: put the hooks in ~/.codex/hooks.json (they run in every Codex session).")
            command.add_argument("--mcp", action="store_true",
                                 help="Also register the MCP server (remembered for later agents).")
        elif name == "resume":
            command.add_argument("agent", nargs="?", choices=AGENTS, metavar="agent",
                                 help="One of: " + ", ".join(AGENTS) + ".")
            command.add_argument("--next", action="store_true",
                                 help="Resume in the next agent in switch.order.")
            command.add_argument("--dry-run", action="store_true",
                                 help="Print the command that would start the agent; start nothing.")
            command.add_argument("--task", metavar="ID", help="Resume another task: a branch slot or a history entry.")
        elif name == "save":
            command.add_argument("--note", metavar="TEXT", help="Add a decision note before rebuilding.")
        elif name == "note":
            command.add_argument("text", help="The note, e.g. \"Keep the session API unchanged\".")
            command.add_argument("--type", choices=("decision", "todo", "warning", "tried", "handoff"),
                                 help="decision (the default), todo, warning, tried (an approach that did not work) "
                                      "or handoff (what is done, what is next, anything risky).")
            command.add_argument("--keep", action="store_true",
                                 help="Also propose it for this repo's memory now (decision, tried or warning).")
        elif name == "done":
            command.add_argument("--note", metavar="TEXT", help="Record what was finished (kept as a decision).")
        elif name == "history":
            command.add_argument("--limit", type=_positive_int, metavar="N", help="Show only the newest N saves.")
        elif name == "memory":
            _memory_parser(command)
        elif name == "journal":
            command.add_argument("--search", metavar="TEXT", help="Pages containing every word of TEXT.")
            command.add_argument("--path", metavar="TASK_ID", help="Print the page path of one task.")
        elif name == "vault":
            actions = command.add_subparsers(dest="vault_action", metavar="move")
            mover = actions.add_parser("move", allow_abbrev=False, help="Move .autosave/ journal and MEMORY.md into the vault.",
                                       description="Move .autosave/ journal and MEMORY.md into the vault.")
            mover.add_argument("--yes", action="store_true", help="Move them (otherwise only the plan is shown).")
            _common_flags(mover)
        elif name == "upgrade":
            mode = command.add_mutually_exclusive_group()
            mode.add_argument("--dry-run", action="store_true", help="List what would change (the default).")
            mode.add_argument("--yes", action="store_true", help="Make the changes.")
            command.add_argument("--user-level", action="store_true",
                                 help="Allow rewriting user-level Codex hooks this repo installed.")
        elif name == "plan":
            actions = command.add_subparsers(dest="plan_action")
            setter = actions.add_parser("set", allow_abbrev=False, help="Replace the plan with these steps.",
                                        description="Replace the plan with these steps.")
            setter.add_argument("steps", nargs="+", metavar="step", help="One quoted step per argument.")
            done = actions.add_parser("done", allow_abbrev=False, help="Mark step n (1-based) done.",
                                      description="Mark step n (1-based) done.")
            done.add_argument("number", type=_positive_int, metavar="n", help="The step number, from 1.")
            for sub in (setter, done):
                _common_flags(sub)
        elif name == "record":
            command.add_argument("state", choices=("on", "off"), help="on or off.")
            command.add_argument("--dir", help="Directory for recordings (default .autosave/recordings).")
            command.add_argument("--agent-version", help="Stored in each recording, e.g. \"$(codex --version)\".")
        elif name == "mcp":
            command.add_argument("--project", metavar="DIR",
                                 help="The project folder (default: the current folder or a parent with .autosave/).")
        elif name == "hook":
            command.add_argument("agent", help="The agent calling the hook.")
            command.add_argument("event", help="The agent's hook event name.")
    return parser


MEMORY_TYPES = ("decision", "tried", "gotcha", "convention")


def _memory_parser(command: argparse.ArgumentParser) -> None:
    actions = command.add_subparsers(dest="memory_action", metavar="<action>")

    def action(name, text):
        sub = actions.add_parser(name, allow_abbrev=False, help=text, description=text)
        _common_flags(sub)
        return sub

    lister = action("list", "List the active entries in MEMORY.md.")
    lister.add_argument("--type", choices=MEMORY_TYPES, help="Only this type.")
    lister.add_argument("--all", action="store_true", help="Include expired entries.")
    adder = action("add", "Add an entry (in a terminal), or propose one for review (from an agent).")
    adder.add_argument("--type", choices=MEMORY_TYPES, required=True, help="decision, tried, gotcha or convention.")
    adder.add_argument("text", help="The entry, e.g. \"Use SQLite for the cache, not Redis\".")
    adder.add_argument("--until", metavar="YYYY-MM-DD", help="Expires after this date.")
    adder.add_argument("--replaces", metavar="ID", help="An entry this one replaces (moved to Expired).")
    searcher = action("search", "Search memory, agentic-stack lessons and the journal for words.")
    searcher.add_argument("query", help="The words to look for, e.g. \"billing export\".")
    proposer = action("propose", "Propose an entry for review; never accepts it (what agents call).")
    proposer.add_argument("--type", choices=MEMORY_TYPES, required=True, help="decision, tried, gotcha or convention.")
    proposer.add_argument("text", help="The proposed entry.")
    action("review", "Go through the waiting candidates: accept, edit, reject or skip.")
    accept = action("accept", "Accept a candidate into MEMORY.md.")
    accept.add_argument("id", help="The candidate id, e.g. c-8e1f20.")
    accept.add_argument("--text", help="Accept it with this text instead.")
    reject = action("reject", "Reject a candidate; it is not proposed again.")
    reject.add_argument("id", help="The candidate id.")
    reject.add_argument("--reason", help="Why, kept with the candidate.")
    for name, text in (("pin", "Pin an entry: shown first at session start."), ("forget", "Delete an entry from MEMORY.md.")):
        action(name, text).add_argument("id", help="The entry id, e.g. m-3fa2c1.")


def validate(args: argparse.Namespace) -> None:
    if args.version and args.command:
        raise UsageError("Use --version without a command.")
    if not args.command and not args.version:
        raise UsageError("Choose a command or use --version.")
    if args.command == "init" and args.agents is not None:
        if any(agent.strip() not in AGENTS for agent in args.agents.split(",")):
            raise UsageError("Unknown agent in --agents.")
    if args.command == "memory" and args.memory_action is None:
        raise UsageError("Choose a memory action.")
    if args.command == "vault" and args.vault_action is None:
        raise UsageError("Choose a vault action.")
    if args.command == "journal" and args.search is not None and args.path is not None:
        raise UsageError("Choose --search or --path.")
    if args.command == "resume" and (args.agent is None) == (not args.next):
        raise UsageError("Name an agent or use --next (not both).")
    if args.command == "show" and args.explain and not args.context:
        raise UsageError("Use --explain with --context.")
    if args.command == "show" and getattr(args, "json", False):
        if args.md or args.context:
            raise UsageError("Choose only one of --md, --json, or --context.")
