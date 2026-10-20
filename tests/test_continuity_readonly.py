"""Read-only tools: never last actions, their files are "Looked at",
consecutive identical actions collapse."""
from agent_autosave.compiler import compile_events
from agent_autosave.loader import context
from agent_autosave.normalize.claude_code import normalize
from tests.helpers import event, load_payload, tool
from tests.scenario import Repo


def edit(n, path):
    return event("tool", n, tool="Edit", summary="Edited " + path, files=[path])


def claude_tool(tool_name, tool_input, cwd="/repo", name="PostToolUse"):
    payload = dict(load_payload("claude-code", "PostToolUse-Edit"), hook_event_name=name, tool_name=tool_name,
                   tool_input=tool_input, tool_response={}, cwd=cwd)
    [row], _ = normalize(payload, ts="2026-10-17T12:00:00Z", turn=1)
    return row


def test_every_read_only_claude_tool_is_marked():
    for name, inputs in [("Read", {"file_path": "/repo/a.py"}), ("Glob", {"pattern": "**/*.py"}),
                         ("Grep", {"pattern": "def", "path": "src"}), ("LS", {"path": "/repo"}),
                         ("NotebookRead", {"notebook_path": "/repo/n.ipynb"}), ("WebFetch", {"url": "https://x"}),
                         ("WebSearch", {"query": "x"}), ("TodoRead", {})]:
        assert claude_tool(name, inputs)["readonly"] is True, name


def test_read_and_notebook_read_carry_a_project_relative_path_only():
    assert claude_tool("Read", {"file_path": "/repo/src/a.py"})["files"] == ["src/a.py"]
    assert claude_tool("NotebookRead", {"notebook_path": "/repo/n.ipynb"})["files"] == ["n.ipynb"]
    assert claude_tool("Read", {"file_path": "/etc/passwd"})["files"] == []
    assert claude_tool("Grep", {"pattern": "x", "path": "/repo/src"})["files"] == []
    assert claude_tool("NotebookRead", {})["files"] == []  # shape absent: nothing read from it


def test_a_failing_read_tool_is_not_marked_read_only():
    assert "readonly" not in claude_tool("Read", {"file_path": "/repo/a.py"}, name="PostToolUseFailure")


def test_multi_edit_and_notebook_edit_are_edits():
    multi = claude_tool("MultiEdit", {"file_path": "/repo/src/a.py", "edits": []})
    notebook = claude_tool("NotebookEdit", {"notebook_path": "/repo/n.ipynb", "new_source": "x"})
    assert (multi["summary"], multi["files"]) == ("Edited src/a.py", ["src/a.py"])
    assert (notebook["summary"], notebook["files"]) == ("Edited n.ipynb", ["n.ipynb"])
    assert claude_tool("MultiEdit", {"edits": []})["files"] == []  # shape absent


def test_successful_reads_never_enter_last_actions_and_go_to_files_read():
    save = compile_events([event("prompt", 0, text="rewrite the CSV exporter to stream"),
                           tool(1, tool="Read", summary="Read", files=["a.py"], readonly=True),
                           tool(2, summary="cat b.py", exit=0), tool(3, summary="git diff --stat", exit=0),
                           edit(4, "a.py"),
                           tool(5, tool="Read", summary="Read", files=["node_modules/x.js"], readonly=True),
                           tool(6, tool="Read", summary="Read", files=["c.py"], readonly=True),
                           tool(7, tool="Read", summary="Read", files=["a.py"], readonly=True)])
    assert save["last_actions"] == ["Edited a.py"]
    assert save["files_read"] == ["a.py", "c.py"]


def test_files_read_keeps_the_newest_ten_unique():
    events = [tool(i, tool="Read", summary="Read", files=["f{}.py".format(i % 12)], readonly=True) for i in range(30)]
    save = compile_events(events)
    assert save["files_read"] == ["f{}.py".format(i % 12) for i in range(29, 19, -1)]


def test_a_failing_read_only_command_still_fails():
    save = compile_events([tool(0, summary="grep -rn missing src", exit=2, output_tail="no such file")])
    assert save["failing"]["command"] == "grep -rn missing src"
    assert save["last_actions"] == ["Ran grep -rn missing src (exit 2)"]


def test_a_read_only_command_with_unknown_exit_stays_visible():
    save = compile_events([tool(0, summary="cat a.py", exit=None)])
    assert save["last_actions"] == ["Ran cat a.py (exit unknown)"]


def test_consecutive_identical_actions_collapse():
    events = [tool(i, summary="pytest -q", exit=1) for i in range(3)] + [edit(3, "a.py")] \
        + [tool(4, summary="pytest -q", exit=1)]
    save = compile_events(events)
    assert save["last_actions"] == ["Ran pytest -q (exit 1)", "Edited a.py", "Ran pytest -q (exit 1) x3"]


def test_end_to_end_reads_are_looked_at_in_the_context(tmp_path):
    repo = Repo(tmp_path / "repo")
    repo.prompt("rewrite the CSV exporter so it streams rows instead of loading them all")
    repo.tool("Read", {"file_path": str(repo.root / "export.py")})
    repo.tool("Grep", {"pattern": "def export"})
    repo.bash("sed -n '1,40p' rows.py")
    repo.claude("PostToolUse-Edit")
    repo.bash("pytest -q", exit=1)
    repo.bash("pytest -q", exit=1)
    save = repo.save()
    assert save["last_actions"][0] == "Ran pytest -q (exit 1) x2"
    assert not any("Read" == a or "sed" in a for a in save["last_actions"])
    text = context(save, force=True)
    assert 'Looked at: "export.py".' in text
    assert "## Files and git" in repo.markdown() and "Looked at: export.py" in repo.markdown()
