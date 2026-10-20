"""`agent-autosave mcp`: the save and the memory as MCP tools and resources.

A stdio server (newline-delimited JSON-RPC 2.0, standard library only). It
implements MCP 2026-07-28, the current specification:
https://modelcontextprotocol.io/specification/2026-07-28 . That version is
stateless: each request names its protocol version in `_meta`, and
`server/discover` lists what the server supports. Clients built for earlier
versions open with `initialize` instead, so the server also negotiates
2025-11-25 and older there
(https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle).

Every tool runs the same code as the CLI command it mirrors, so a tool call
is recorded exactly like that command. No tool accepts or rejects memory:
that stays with a person (`agent-autosave memory review`).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, BinaryIO, Callable

from . import __version__

PROTOCOL = "2026-07-28"
# Versions a client may ask for in `initialize`; the first is offered when
# the client asks for one we do not speak.
LEGACY = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SUPPORTED = (PROTOCOL,) + LEGACY
META = "io.modelcontextprotocol/"
SERVER = {"name": "agent-autosave", "version": __version__}
INSTRUCTIONS = ("agent-autosave keeps this repo's task save and project memory. Call autosave_context at the start "
                "of a session, record decisions and dead ends with autosave_note, keep the plan with "
                "autosave_plan_set and autosave_plan_done, and call autosave_done when the task is finished. "
                "Propose lasting lessons with memory_propose; a person reviews them.")

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603
UNSUPPORTED_VERSION = -32022
NOTE_TYPES = ("decision", "todo", "warning", "tried", "handoff")
MEMORY_TYPES = ("decision", "tried", "gotcha", "convention")
# MCP client names (clientInfo.name) of the agents we know, from their
# source: Codex https://github.com/openai/codex/blob/d28be12577cf5e267c97d3018a490d76da4d28e7/codex-rs/rmcp-client/src/utils.rs
# and Gemini CLI https://github.com/google-gemini/gemini-cli/blob/ed96faedfd5571f055c44f2c9509d62321d1138d/packages/core/src/tools/mcp-client.ts
CLIENTS = {"codex-mcp-client": "codex", "gemini-cli-mcp-client": "gemini"}
# Other clients document no name; a product name inside it is enough to
# credit the note (it only sets who a note is from, nothing else).
CLIENT_WORDS = (("claude", "claude-code"), ("codex", "codex"), ("cursor", "cursor"), ("gemini", "gemini"),
                ("antigravity", "antigravity"), ("copilot", "copilot"), ("opencode", "opencode"))


def _schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


TEXT = {"type": "string", "minLength": 1}
TOOLS = (
    ("autosave_status", "Where the task stands: status, goal, plan progress, next step, failing command.",
     _schema({})),
    ("autosave_context", "The handoff block: the save as the next agent should read it. Call it first.",
     _schema({})),
    ("autosave_note", "Record a decision, todo, warning, dead end (tried) or handoff note in the save. "
                      "keep=true also proposes a decision, tried or warning for project memory.",
     _schema({"type": {"type": "string", "enum": list(NOTE_TYPES)}, "text": TEXT, "keep": {"type": "boolean"}},
             ("text",))),
    ("autosave_plan_set", "Replace the task plan with these steps, in order.",
     _schema({"steps": {"type": "array", "items": TEXT, "minItems": 1}}, ("steps",))),
    ("autosave_plan_done", "Mark plan step `index` (1-based) done.",
     _schema({"index": {"type": "integer", "minimum": 1}}, ("index",))),
    ("autosave_done", "The task is finished; the next prompt starts a new task. `note` says what was done.",
     _schema({"note": {"type": "string"}})),
    ("memory_search", "Search this repo's project memory, agentic-stack lessons and journal for `query`.",
     _schema({"query": TEXT}, ("query",))),
    ("memory_propose", "Propose an entry for project memory. A person reviews it; it is not accepted by this call.",
     _schema({"type": {"type": "string", "enum": list(MEMORY_TYPES)}, "text": TEXT}, ("type", "text"))),
)
RESOURCES = (
    {"uri": "autosave://save", "name": "save", "title": "SAVE.md", "mimeType": "text/markdown",
     "description": "The task save: goal, plan, decisions, failing command, next step."},
    {"uri": "autosave://memory", "name": "memory", "title": "MEMORY.md", "mimeType": "text/markdown",
     "description": "This repo's reviewed project memory."},
)


class RequestError(Exception):
    """A JSON-RPC error answer: code, message and optional data."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def agent_of(client: Any) -> str | None:
    """The agent named by an MCP clientInfo, when it is one we know."""
    name = str((client or {}).get("name") or "").lower() if isinstance(client, dict) else ""
    if name in CLIENTS:
        return CLIENTS[name]
    return next((agent for word, agent in CLIENT_WORDS if word in name), None)


def _type_ok(value: Any, schema: dict[str, Any]) -> bool:
    kind = schema.get("type")
    if kind == "string":
        return isinstance(value, str) and len(value.strip()) >= schema.get("minLength", 0) and \
            value in schema.get("enum", [value])
    if kind == "boolean":
        return type(value) is bool
    if kind == "integer":
        return type(value) is int and value >= schema.get("minimum", value)
    if kind == "array":
        return isinstance(value, list) and len(value) >= schema.get("minItems", 0) and \
            all(_type_ok(item, schema["items"]) for item in value)
    return False


def check_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> str | None:
    """Why `arguments` do not fit the tool's input schema, or None."""
    for name in schema["required"]:
        if name not in arguments:
            return "Missing argument: {}.".format(name)
    for name, value in arguments.items():
        if name not in schema["properties"]:
            return "Unknown argument: {}.".format(name)
        if not _type_ok(value, schema["properties"][name]):
            return "Invalid value for {}.".format(name)
    return None


class Server:
    """One stdio session. `run_cli` runs a CLI argument list and returns the
    Outcome; tests pass their own."""

    def __init__(self, run_cli: Callable[[list[str]], Any] | None = None) -> None:
        self.run_cli = run_cli or _run_cli
        self.legacy_version: str | None = None
        self.client: dict[str, Any] | None = None

    # --- framing

    def handle_line(self, line: bytes) -> dict[str, Any] | None:
        """The answer to one line, or None for a notification."""
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return error_answer(None, RequestError(PARSE_ERROR, "Parse error"))
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or \
                not isinstance(message.get("method"), str) or \
                ("id" in message and (type(message["id"]) not in (str, int) or message["id"] is None)):
            ident = message.get("id") if isinstance(message, dict) and type(message.get("id")) in (str, int) else None
            return error_answer(ident, RequestError(INVALID_REQUEST, "Invalid request"))
        if "id" not in message:
            return None  # a notification (notifications/initialized, cancelled): nothing to answer
        params = message.get("params", {})
        try:
            if not isinstance(params, dict):
                raise RequestError(INVALID_PARAMS, "Params must be an object.")
            return {"jsonrpc": "2.0", "id": message["id"], "result": self.dispatch(message["method"], params)}
        except RequestError as error:
            return error_answer(message["id"], error)
        except Exception as error:  # the session boundary: one bad call must not end the session
            _log(error)
            return error_answer(message["id"], RequestError(INTERNAL_ERROR, "Internal error: {}".format(
                type(error).__name__)))

    # --- methods

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return self.initialize(params)
        modern = self.modern_version(params)
        if method == "ping":
            return self.finish({}, modern)
        handler = {"server/discover": self.discover, "tools/list": self.list_tools, "tools/call": self.call_tool,
                   "resources/list": self.list_resources, "resources/read": self.read_resource,
                   "resources/templates/list": self.list_templates}.get(method)
        if handler is None:
            raise RequestError(METHOD_NOT_FOUND, "Method not found: {}".format(method))
        return self.finish(handler(params), modern, cacheable=method in (
            "tools/list", "resources/list", "resources/read", "resources/templates/list"))

    def modern_version(self, params: dict[str, Any]) -> bool:
        """True for a 2026-07-28 request (version in `_meta`); raises for a
        version we do not speak or a request missing its capabilities."""
        meta = params.get("_meta")
        meta = meta if isinstance(meta, dict) else {}
        if META + "protocolVersion" not in meta:
            return False  # a legacy request, after (or without) initialize
        requested = meta[META + "protocolVersion"]
        if requested != PROTOCOL:
            raise RequestError(UNSUPPORTED_VERSION, "Unsupported protocol version",
                               {"supported": list(SUPPORTED), "requested": requested})
        if not isinstance(meta.get(META + "clientCapabilities"), dict):
            raise RequestError(INVALID_PARAMS, "Missing _meta io.modelcontextprotocol/clientCapabilities.")
        if isinstance(meta.get(META + "clientInfo"), dict):
            self.client = meta[META + "clientInfo"]
        return True

    def finish(self, result: dict[str, Any], modern: bool, *, cacheable: bool = False) -> dict[str, Any]:
        """Add what a 2026-07-28 result must carry; legacy results stay as they were."""
        if not modern:
            return result
        result = dict(result, resultType="complete")
        result["_meta"] = dict(result.get("_meta") or {}, **{META + "serverInfo": SERVER})
        if cacheable:
            # The save changes on every hook event: never cache it for long.
            result.update(ttlMs=0, cacheScope="private")
        return result

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        self.legacy_version = requested if requested in LEGACY else LEGACY[0]
        if isinstance(params.get("clientInfo"), dict):
            self.client = params["clientInfo"]
        return {"protocolVersion": self.legacy_version, "capabilities": capabilities(), "serverInfo": SERVER,
                "instructions": INSTRUCTIONS}

    def discover(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"supportedVersions": list(SUPPORTED), "capabilities": capabilities(), "instructions": INSTRUCTIONS}

    def list_tools(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"tools": [{"name": name, "title": name.replace("_", " "), "description": text, "inputSchema": schema}
                          for name, text, schema in TOOLS]}

    def list_resources(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"resources": [dict(resource) for resource in RESOURCES]}

    def list_templates(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"resourceTemplates": []}

    def read_resource(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = params.get("uri")
        resource = next((r for r in RESOURCES if r["uri"] == uri), None)
        if resource is None:
            raise RequestError(INVALID_PARAMS, "Resource not found", {"uri": uri})
        text = resource_text(uri)
        if text is None:
            raise RequestError(INVALID_PARAMS, "Resource not found", {"uri": uri})
        return {"contents": [{"uri": uri, "mimeType": resource["mimeType"], "text": text}]}

    def call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name, arguments = params.get("name"), params.get("arguments", {})
        tool = next((t for t in TOOLS if t[0] == name), None)
        if tool is None:
            raise RequestError(INVALID_PARAMS, "Unknown tool: {}".format(name if isinstance(name, str) else "?"))
        if not isinstance(arguments, dict):
            raise RequestError(INVALID_PARAMS, "Tool arguments must be an object.")
        problem = check_arguments(tool[2], arguments)
        if problem:
            return tool_result(problem, None, error=True)
        from .origin import CALLER
        token = CALLER.set(agent_of(self.client))
        try:
            envelope, _, text = self.run_cli(cli_arguments(name, arguments))
        finally:
            CALLER.reset(token)
        if not envelope.get("ok"):
            problem = envelope["error"]
            return tool_result("{} {}".format(problem["message"], problem["hint"]), envelope, error=True)
        return tool_result(text or "Done.", envelope.get("data"), error=False)


def capabilities() -> dict[str, Any]:
    return {"tools": {"listChanged": False}, "resources": {"subscribe": False, "listChanged": False}}


def error_answer(ident: Any, error: RequestError) -> dict[str, Any]:
    body: dict[str, Any] = {"code": error.code, "message": error.message}
    if error.data is not None:
        body["data"] = error.data
    return {"jsonrpc": "2.0", "id": ident, "error": body}


def tool_result(text: str, data: Any, *, error: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": error}
    if isinstance(data, dict) and not error:
        result["structuredContent"] = data
    return result


def cli_arguments(name: str, arguments: dict[str, Any]) -> list[str]:
    """The CLI command a tool call runs ("--" keeps text from being read as a flag)."""
    if name == "autosave_status":
        return ["status"]
    if name == "autosave_context":
        return ["show", "--context"]
    if name == "autosave_note":
        flags = ["--type", arguments.get("type", "decision")] + (["--keep"] if arguments.get("keep") else [])
        return ["note"] + flags + ["--", arguments["text"]]
    if name == "autosave_plan_set":
        return ["plan", "set", "--"] + list(arguments["steps"])
    if name == "autosave_plan_done":
        return ["plan", "done", str(arguments["index"])]
    if name == "autosave_done":
        return ["done"] + (["--note=" + arguments["note"]] if arguments.get("note", "").strip() else [])
    if name == "memory_search":
        return ["memory", "search", "--", arguments["query"]]
    return ["memory", "propose", "--type", arguments["type"], "--", arguments["text"]]


def _run_cli(arguments: list[str]) -> Any:
    """Parse and dispatch like `agent-autosave <arguments>`, without printing."""
    from . import cli
    from .arguments import UsageError, build_parser, validate
    from .contract import fail
    try:
        args = build_parser().parse_args(arguments)
        validate(args)
    except UsageError as error:
        return fail("invalid_args", str(error), "Check the tool arguments.")
    return cli._dispatch(args)


def _log(error: Exception) -> None:
    """Into .autosave/errors.log when there is one; stdout is for MCP only."""
    from .commands.common import find_project
    from .storage import log_error
    directory = find_project() / ".autosave"
    if directory.is_dir():
        log_error(directory, "mcp", error)


def resource_text(uri: str) -> str | None:
    """SAVE.md or MEMORY.md of the project, or None when it is not there."""
    from .commands.common import find_project
    project = find_project()
    if uri == "autosave://save":
        path = project / ".autosave" / "SAVE.md"
    else:
        from . import places
        from .config import load
        path = places.memory_file(project, load(project))
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def serve(project: str | None = None, *, stdin: BinaryIO | None = None, stdout: BinaryIO | None = None,
          server: Server | None = None) -> int:
    """Answer requests until stdin closes, then exit 0. Only MCP messages
    are written to stdout; nothing else may be."""
    if project:
        os.chdir(Path(project).expanduser())
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    server = server or Server()
    for line in stdin:
        if not line.strip():
            continue
        answer = server.handle_line(line)
        if answer is not None:
            stdout.write(json.dumps(answer, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n")
            stdout.flush()
    return 0
