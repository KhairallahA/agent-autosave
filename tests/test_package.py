import ast
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import sys

from agent_autosave import __version__
from tests.helpers import ALL, ROOT


def test_installed_package_metadata():
    distribution = metadata.distribution("agent-autosave")
    assert distribution.version == __version__
    assert distribution.metadata["Requires-Python"] == ">=3.9"
    assert any(
        point.group == "console_scripts"
        and point.name == "agent-autosave"
        and point.value == "agent_autosave.cli:main"
        for point in distribution.entry_points
    )
    assert all('extra == "test"' in requirement for requirement in distribution.requires or [])


def test_runtime_source_has_no_network_imports():
    root = Path(__file__).resolve().parents[1] / "agent_autosave"
    forbidden = {"socket", "urllib", "http", "ssl"}
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] if node.level == 0 else []
            else:
                continue
            assert not forbidden.intersection(name.split(".")[0] for name in names), path


def test_hook_startup_does_not_import_future_pipeline_modules():
    # Startup stays small: unsupported agents and uninitialized folders never
    # load the compiler pipeline.
    code = (
        "import sys; from agent_autosave.cli import main; "
        "assert main(['hook', 'codex', 'Stop']) == 0; "
        "assert not any('agent_autosave.' + name in sys.modules "
        "for name in ('compiler', 'gitstate', 'render', 'ledger', 'state'))"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_every_adapter_folder_matches_packaged_resources():
    # README.md documents the adapter folder for readers; it is not installed.
    for agent in ALL:
        packaged = ROOT / "agent_autosave/resources" / agent
        mine = ROOT / "adapters" / agent
        files = sorted(p.name for p in mine.iterdir() if p.name != "README.md")
        assert sorted(p.name for p in packaged.iterdir() if p.name != "__pycache__") == files, agent
        for path in mine.iterdir():
            if path.name == "README.md":
                continue
            assert path.read_bytes() == (packaged / path.name).read_bytes(), path


def test_every_public_function_has_type_hints():
    package = Path(__file__).resolve().parents[1] / "agent_autosave"
    missing = []
    for path in sorted(package.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        functions += [m for c in tree.body if isinstance(c, ast.ClassDef) for m in c.body
                      if isinstance(m, ast.FunctionDef)]
        if functions and "from __future__ import annotations" not in source:
            missing.append("{}: no `from __future__ import annotations`".format(path.name))
        for node in functions:
            if node.name.startswith("_") and node.name != "__init__":
                continue
            arguments = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            arguments += [a for a in (node.args.vararg, node.args.kwarg) if a]
            if node.returns is None or any(a.annotation is None for a in arguments if a.arg not in ("self", "cls")):
                missing.append("{}:{} {}".format(path.relative_to(package), node.lineno, node.name))
    assert missing == []


def test_hook_path_never_imports_the_terminal_ui(tmp_path):
    # Import trace of a real hook in a set-up project: the wizard, the shared
    # terminal style and the commands stay out of every hook.
    from agent_autosave.installer.install import install
    install(tmp_path, ("claude-code",), command="agent-autosave")
    payload = json.dumps({"session_id": "s1", "hook_event_name": "UserPromptSubmit", "cwd": str(tmp_path),
                          "prompt": "Fix the failing test"})
    code = (
        "import sys; from agent_autosave.cli import main; "
        "assert main(['hook', 'claude-code', 'UserPromptSubmit']) == 0; "
        "bad = [m for m in sys.modules if m.split('.')[:2] in (['agent_autosave', 'ui'], ['agent_autosave', 'wizard'], "
        "['agent_autosave', 'commands'])]; "
        "assert not bad, bad"
    )
    env = dict(os.environ, AGENT_AUTOSAVE_SESSION_END="sync")
    result = subprocess.run([sys.executable, "-X", "importtime", "-c", code], input=payload.encode(), cwd=str(tmp_path),
                            capture_output=True, timeout=10, env=env)
    assert result.returncode == 0, result.stderr[-2000:]
    trace = result.stderr.decode()
    assert "agent_autosave.capture" in trace  # the hook really ran
    assert "agent_autosave.ui" not in trace and "agent_autosave.wizard" not in trace
