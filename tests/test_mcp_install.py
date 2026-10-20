"""MCP server registration: init, add, doctor and a byte-exact remove for
.mcp.json, .codex/config.toml and .cursor/mcp.json."""
import json

from agent_autosave.installer.doctor import audit
from agent_autosave.installer.install import add, install
from agent_autosave.installer.remove import remove

COMMAND = "/opt/agent autosave/bin/agent-autosave"  # a space: quoting must hold everywhere
USER_TOML = '# my settings\r\nmodel = "gpt-5"\r\n\r\n[mcp_servers.docs]\r\ncommand = "docs-server"\r\n'
USER_MCP_JSON = '{\n  // mine\n  "mcpServers": {\n    "docs": {"command": "docs-server"}\n  }\n}\n'


def toml(text):
    """Parsed TOML on 3.11+ (tomllib), else None: the byte checks still run."""
    try:
        import tomllib
    except ImportError:
        return None
    return tomllib.loads(text)


def test_each_agent_gets_the_server_and_remove_restores_every_file_byte_for_byte(tmp_path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/config.toml").write_bytes(USER_TOML.encode())
    (tmp_path / ".mcp.json").write_text(USER_MCP_JSON)
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor/mcp.json").write_text('{"mcpServers": {}}')
    install(tmp_path, ["claude-code", "codex", "cursor"], command=COMMAND, mcp=True)
    claude = json.loads("\n".join(line for line in (tmp_path / ".mcp.json").read_text().splitlines()
                                  if "//" not in line))
    assert claude["mcpServers"]["agent-autosave"] == {"command": COMMAND, "args": ["mcp"]}
    assert claude["mcpServers"]["docs"] == {"command": "docs-server"}
    cursor = json.loads((tmp_path / ".cursor/mcp.json").read_text())
    assert cursor["mcpServers"]["agent-autosave"]["args"] == ["mcp", "--project", "${workspaceFolder}"]
    text = (tmp_path / ".codex/config.toml").read_bytes().decode()
    assert "# agent-autosave:start\n[mcp_servers.agent-autosave]\n" in text
    assert 'command = "{}"\nargs = ["mcp"]\n'.format(COMMAND) in text
    parsed = toml(text)
    if parsed is not None:
        assert parsed["mcp_servers"]["agent-autosave"] == {"command": COMMAND, "args": ["mcp"]}
        assert parsed["mcp_servers"]["docs"] == {"command": "docs-server"} and parsed["model"] == "gpt-5"
    assert json.loads((tmp_path / ".autosave/install.json").read_text())["mcp"] is True
    remove(tmp_path)
    assert (tmp_path / ".codex/config.toml").read_bytes() == USER_TOML.encode()
    assert (tmp_path / ".mcp.json").read_text() == USER_MCP_JSON
    assert (tmp_path / ".cursor/mcp.json").read_text() == '{"mcpServers": {}}'


def test_files_we_created_are_deleted_again(tmp_path):
    install(tmp_path, ["claude-code", "codex", "cursor"], command="agent-autosave", mcp=True)
    for name in (".mcp.json", ".codex/config.toml", ".cursor/mcp.json"):
        assert (tmp_path / name).is_file(), name
    remove(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [".autosave"]


def test_a_server_the_user_defined_under_our_name_is_left_alone(tmp_path):
    (tmp_path / ".codex").mkdir()
    original = '[mcp_servers."agent-autosave"]\ncommand = "my-fork"\n'
    (tmp_path / ".codex/config.toml").write_text(original)
    install(tmp_path, ["codex"], command="agent-autosave", mcp=True)
    assert (tmp_path / ".codex/config.toml").read_text() == original
    assert any(c["level"] == "green" and "MCP server registered" in c["message"] for c in audit(tmp_path))


def test_codex_gets_no_project_server_when_its_hooks_live_elsewhere(tmp_path):
    install(tmp_path, ["codex"], command="agent-autosave", mcp=True, codex_hooks=False)
    assert not (tmp_path / ".codex").exists()


def test_without_mcp_nothing_is_registered_and_add_mcp_turns_it_on(tmp_path):
    install(tmp_path, ["claude-code"], command="agent-autosave")
    assert not (tmp_path / ".mcp.json").exists()
    add(tmp_path, "claude-code", mcp=True)
    assert json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["agent-autosave"]["args"] == ["mcp"]
    add(tmp_path, "cursor")  # the receipt remembers the choice
    assert (tmp_path / ".cursor/mcp.json").is_file()
    remove(tmp_path)
    assert not (tmp_path / ".mcp.json").exists() and not (tmp_path / ".cursor").exists()


def test_doctor_flags_a_missing_registration(tmp_path):
    install(tmp_path, ["claude-code"], command="agent-autosave", mcp=True)
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {}}')
    checks = [c for c in audit(tmp_path) if "MCP" in c["message"]]
    assert checks[0]["level"] == "yellow" and checks[0]["hint"] == "agent-autosave add claude-code --mcp"


def test_a_reformatted_mcp_json_is_cleaned_by_content(tmp_path):
    install(tmp_path, ["claude-code"], command="agent-autosave", mcp=True)
    data = json.loads((tmp_path / ".mcp.json").read_text())
    data["mcpServers"]["docs"] = {"command": "docs-server"}
    (tmp_path / ".mcp.json").write_text(json.dumps(data, indent=4) + "\n")
    remove(tmp_path)
    assert json.loads((tmp_path / ".mcp.json").read_text()) == {"mcpServers": {"docs": {"command": "docs-server"}}}


def test_init_no_mcp_flag(run_cli, tmp_path):
    result = run_cli("--json", "init", "--yes", "--agents", "generic", "--no-mcp")
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / ".autosave/install.json").read_text())["mcp"] is False
