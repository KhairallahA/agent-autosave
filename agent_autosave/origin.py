"""Who ran an `agent-autosave` command: an agent, or you.

Only a marker an agent sets on the commands it runs for its own tool calls
counts. Anything else, including an editor's built-in terminal
(TERM_PROGRAM=vscode, CURSOR_*, VSCODE_*) and an agent session left open in
the project, means a person typed it: "you".
"""
from __future__ import annotations

from contextvars import ContextVar
import os
from typing import Mapping

# (agent, variable, where it is documented). The first one set wins.
MARKERS = (
    # Claude Code: "Set to 1 in subprocesses Claude Code spawns via the Bash,
    # PowerShell, and Monitor tools, hook commands, and status line commands
    # ... only set by Claude Code itself when it launches a subprocess and not
    # by IDE extensions" (v2.1.172+). CLAUDECODE=1 does not count: the IDE
    # extensions also set it in their integrated terminals.
    # https://code.claude.com/docs/en/env-vars
    ("claude-code", "CLAUDE_CODE_CHILD_SESSION",
     "https://code.claude.com/docs/en/env-vars"),
    # Codex: set_tool_call_id_env_var() puts the tool call's id in the
    # environment of the command a tool call runs. CODEX_THREAD_ID alone does
    # not count: it is the thread, not a tool call.
    # https://github.com/openai/codex/blob/70b3bb6d8814ab3f2045238b4ae14c43da99783b/codex-rs/protocol/src/shell_environment.rs
    ("codex", "CODEX_TOOL_CALL_ID",
     "https://github.com/openai/codex/blob/70b3bb6d8814ab3f2045238b4ae14c43da99783b/"
     "codex-rs/protocol/src/shell_environment.rs"),
    # Cursor: "Use the CURSOR_AGENT environment variable in your shell config
    # to detect when Cursor is running" commands for the agent.
    # https://cursor.com/docs/agent/terminal
    ("cursor", "CURSOR_AGENT", "https://cursor.com/docs/agent/terminal"),
)
# Not used: GEMINI_CLI=1 is also set on commands a person types after "!" in
# Gemini CLI (https://geminicli.com/docs/tools/shell/,
# https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/commands.md),
# so it cannot tell the agent from you. Antigravity documents no marker.
# Agents that can only be named by the MCP server (from the client's
# clientInfo), since their shell commands carry no marker.
MCP_ONLY = ("gemini", "antigravity", "copilot", "opencode")
SOURCES = ("you",) + tuple(agent for agent, _, _ in MARKERS) + MCP_ONLY
# The agent the MCP server is answering for, set around each tool call. A
# server spawned by an agent gets none of the tool-call markers above.
CALLER: ContextVar[str | None] = ContextVar("agent_autosave_caller", default=None)


def note_source(env: Mapping[str, str] | None = None) -> str:
    caller = CALLER.get()
    if caller in SOURCES:
        return caller
    env = os.environ if env is None else env
    for agent, name, _ in MARKERS:
        if env.get(name, "").strip():
            return agent
    return "you"
