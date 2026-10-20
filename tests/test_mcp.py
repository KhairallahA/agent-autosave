"""`agent-autosave mcp`: a scripted MCP client over real stdio.

Covers the 2025-era handshake (initialize), the 2026-07-28 stateless form
(`_meta` protocol version, server/discover), tools, resources, bad input, an
unknown tool and a closed stdin.
"""
import json
import os
import subprocess

import pytest

from agent_autosave import mcp
from tests.scenario import Repo
from tests.helpers import LENIENT_CLI

META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "codex-mcp-client", "version": "0.140.0"}}


def session(project, *lines):
    """Run the server on `lines` (objects or raw bytes), stdin then closed."""
    data = b"".join((line if isinstance(line, bytes) else json.dumps(line).encode()) + b"\n" for line in lines)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run([*LENIENT_CLI, "mcp"], cwd=str(project), input=data,
                            capture_output=True, timeout=30, env=env)
    answers = [json.loads(line) for line in result.stdout.decode().splitlines()]
    return result, {a.get("id"): a for a in answers}, answers


def request(ident, method, params=None):
    message = {"jsonrpc": "2.0", "id": ident, "method": method}
    if params is not None:
        message["params"] = params
    return message


INIT = request(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "claude-code", "version": "2.1.0"}})
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path / "repo")


def test_initialize_negotiates_and_tools_list_is_ordered(repo):
    result, by_id, answers = session(repo.root, INIT, INITIALIZED, request(2, "tools/list"),
                                     request(3, "initialize", {"protocolVersion": "1999-01-01", "capabilities": {}}))
    assert result.returncode == 0 and result.stderr == b""
    assert len(answers) == 3  # the notification gets no answer
    assert by_id[1]["result"]["protocolVersion"] == "2025-06-18"
    assert by_id[1]["result"]["serverInfo"]["name"] == "agent-autosave"
    assert set(by_id[1]["result"]["capabilities"]) == {"tools", "resources"}
    names = [tool["name"] for tool in by_id[2]["result"]["tools"]]
    assert names == ["autosave_status", "autosave_context", "autosave_note", "autosave_plan_set",
                     "autosave_plan_done", "autosave_done", "memory_search", "memory_propose"]
    assert by_id[3]["result"]["protocolVersion"] == "2025-11-25"  # an unknown version: we offer ours
    assert not any("accept" in name or "reject" in name for name in names)


def test_tool_calls_are_recorded_like_cli_calls_and_credited_to_the_client(repo):
    calls = [request(10, "tools/call", {"name": "autosave_plan_set", "arguments": {"steps": ["write the parser",
                                                                                            "--add tests"]}}),
             request(11, "tools/call", {"name": "autosave_plan_done", "arguments": {"index": 1}}),
             request(12, "tools/call", {"name": "autosave_note", "arguments": {
                 "type": "decision", "text": "Keep the parser streaming", "keep": True}}),
             request(13, "tools/call", {"name": "autosave_status", "arguments": {}}),
             request(14, "tools/call", {"name": "autosave_context", "arguments": {}}),
             request(15, "tools/call", {"name": "memory_propose", "arguments": {"type": "gotcha",
                                                                                "text": "The parser is not reentrant"}}),
             request(16, "tools/call", {"name": "memory_search", "arguments": {"query": "parser"}})]
    result, by_id, _ = session(repo.root, INIT, INITIALIZED, *calls)
    assert result.returncode == 0
    assert all(by_id[i]["result"]["isError"] is False for i in range(10, 17)), by_id
    assert by_id[13]["result"]["structuredContent"]["plan_done"] == 1
    assert "Keep the parser streaming" in by_id[14]["result"]["content"][0]["text"]
    save = repo.save()
    assert [item["step"] for item in save["plan"]] == ["write the parser", "--add tests"]
    ledger = [json.loads(line) for line in (repo.root / ".autosave/events.jsonl").read_text().splitlines()]
    notes = [e for e in ledger if e["kind"] == "note"]
    assert notes[0]["by"] == "claude-code" and notes[0]["agent"] == "cli"
    candidates = [json.loads(line) for line in (repo.root / ".autosave/memory/candidates.jsonl").read_text().splitlines()]
    assert {c["source"]["agent"] for c in candidates} == {"claude-code"}
    assert {c["status"] for c in candidates} == {"pending"}  # proposed, never accepted
    assert not (repo.root / ".autosave/MEMORY.md").exists() or "reentrant" not in \
        (repo.root / ".autosave/MEMORY.md").read_text()


def test_resources_list_and_read(repo):
    repo.cli("note", "Ship it")
    result, by_id, _ = session(repo.root, INIT, request(2, "resources/list"),
                               request(3, "resources/read", {"uri": "autosave://save"}),
                               request(4, "resources/read", {"uri": "autosave://nope"}),
                               request(5, "resources/read", {"uri": "autosave://memory"}))
    assert [r["uri"] for r in by_id[2]["result"]["resources"]] == ["autosave://save", "autosave://memory"]
    content = by_id[3]["result"]["contents"][0]
    assert content["mimeType"] == "text/markdown" and "Ship it" in content["text"]
    assert by_id[4]["error"]["code"] == -32602 and by_id[4]["error"]["data"] == {"uri": "autosave://nope"}
    assert by_id[5]["error"]["code"] == -32602  # no MEMORY.md yet: not found, never an empty contents list


def test_bad_input_and_unknown_tools(repo):
    result, by_id, answers = session(
        repo.root, b"{not json", json.dumps([INIT]).encode(), request(2, "nope/method"),
        request(3, "tools/call", {"name": "rm_rf", "arguments": {}}),
        request(4, "tools/call", {"name": "autosave_note", "arguments": {"text": ""}}),
        request(5, "tools/call", {"name": "autosave_plan_done", "arguments": {"index": 0}}),
        request(6, "tools/call", {"name": "autosave_note", "arguments": {"text": "x", "by": "you"}}),
        request(7, "tools/call", {"name": "autosave_plan_done", "arguments": {"index": 9}}),
        request(8, "tools/call", {"name": "autosave_note", "arguments": "text"}),
        request(9, "tools/list", []))
    assert result.returncode == 0
    assert answers[0] == {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
    assert answers[1]["error"]["code"] == -32600  # a batch is not a request
    assert by_id[2]["error"]["code"] == -32601
    assert by_id[3]["error"] == {"code": -32602, "message": "Unknown tool: rm_rf"}
    for ident in (4, 5, 6, 7):
        assert by_id[ident]["result"]["isError"] is True, by_id[ident]
    assert "No plan step 9" in by_id[7]["result"]["content"][0]["text"]
    assert by_id[8]["error"]["code"] == -32602 and by_id[9]["error"]["code"] == -32602
    assert "note" not in {json.loads(line)["kind"] for line in
                          (repo.root / ".autosave/events.jsonl").read_text().splitlines()}


def test_the_stateless_2026_form(repo):
    result, by_id, _ = session(
        repo.root, request(1, "server/discover", {"_meta": META}), request(2, "tools/list", {"_meta": META}),
        request(3, "tools/call", {"_meta": META, "name": "autosave_note", "arguments": {"text": "From Codex"}}),
        request(4, "tools/list", {"_meta": dict(META, **{"io.modelcontextprotocol/protocolVersion": "1900-01-01"})}),
        request(5, "tools/list", {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}}),
        request(6, "resources/read", {"_meta": META, "uri": "autosave://save"}))
    discover = by_id[1]["result"]
    assert discover["resultType"] == "complete" and discover["supportedVersions"][0] == "2026-07-28"
    assert discover["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "agent-autosave"
    listed = by_id[2]["result"]
    assert listed["resultType"] == "complete" and listed["ttlMs"] == 0 and listed["cacheScope"] == "private"
    assert by_id[3]["result"]["resultType"] == "complete" and by_id[3]["result"]["isError"] is False
    assert by_id[4]["error"]["code"] == -32022
    assert by_id[4]["error"]["data"] == {"supported": list(mcp.SUPPORTED), "requested": "1900-01-01"}
    assert by_id[5]["error"]["code"] == -32602  # clientCapabilities is required
    assert by_id[6]["result"]["cacheScope"] == "private"
    notes = [json.loads(line) for line in (repo.root / ".autosave/events.jsonl").read_text().splitlines()
             if json.loads(line)["kind"] == "note"]
    assert notes[-1]["by"] == "codex"


def test_a_closed_stdin_ends_the_server_cleanly(repo):
    result, _, answers = session(repo.root)
    assert result.returncode == 0 and answers == [] and result.stdout == b""
    process = subprocess.Popen([*LENIENT_CLI, "mcp", "--project", str(repo.root)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    process.stdin.write(json.dumps(INIT).encode() + b"\n")
    process.stdin.flush()
    first = json.loads(process.stdout.readline())
    process.stdin.close()
    assert process.wait(timeout=10) == 0 and first["id"] == 1
    assert process.stdout.read() == b"" and process.stderr.read() == b""


def test_outside_a_set_up_folder_tools_fail_as_results(tmp_path):
    result, by_id, _ = session(tmp_path, INIT, request(2, "tools/call", {"name": "autosave_status", "arguments": {}}))
    assert result.returncode == 0
    assert by_id[2]["result"]["isError"] is True
    assert "agent-autosave init" in by_id[2]["result"]["content"][0]["text"]


@pytest.mark.parametrize("name, agent", [("claude-code", "claude-code"), ("codex-mcp-client", "codex"),
                                         ("gemini-cli-mcp-client", "gemini"), ("Cursor", "cursor"),
                                         ("github-copilot-cli", "copilot"), ("opencode", "opencode"),
                                         ("some-editor", None), (None, None)])
def test_client_names_map_to_agents(name, agent):
    assert mcp.agent_of({"name": name} if name else None) == agent
