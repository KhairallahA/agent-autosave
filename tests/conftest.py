"""Run the installed public entrypoints in disposable working directories."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig

import pytest

from agent_autosave import timeouts
from tests.helpers import LENIENT_CLI


# Shell tools the tests run, linked into a clean PATH folder; agent
# binaries (claude, codex, ...) are never linked, so detection sees only the
# fakes a test puts there itself.
TOOLS = ("git", "sh", "bash", "zsh", "dash", "env", "uv", "zstd", "brew", "ruby", "pipx", "shellcheck", "pwsh",
         "script", "stty", "tput", "cat", "chmod", "mkdir", "rm", "cp", "mv", "ln", "ls", "grep", "sed", "awk",
         "head", "tail", "tar", "gzip", "unzip", "curl", "mktemp", "uname", "sleep", "touch", "find", "sort",
         "tr", "cut", "wc", "dirname", "basename", "readlink", "realpath", "id", "printf", "test", "true",
         "false", "sha256sum", "shasum", "install", "xargs", "tee", "diff", "python3", "node")
# Variables a developer's or an agent's shell may export that change what
# agent-autosave does: markers on an agent's own shell commands, git's
# repository overrides and colour switches.
CLEARED = ("CLAUDE_CODE_CHILD_SESSION", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT",
           "CLAUDE_PROJECT_DIR", "CODEX_TOOL_CALL_ID", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_HOME",
           "CURSOR_AGENT", "CURSOR_PROJECT_DIR", "GEMINI_CLI", "GEMINI_PROJECT_DIR", "GEMINI_SESSION_ID",
           "GEMINI_CWD", "COPILOT_CLI", "OPENCODE", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
           "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CEILING_DIRECTORIES", "NO_COLOR", "FORCE_COLOR",
           "CLICOLOR_FORCE", "PYTHONPATH", "VIRTUAL_ENV")


@pytest.fixture(scope="session")
def _tool_path(tmp_path_factory):
    """PATH for every test: this interpreter's scripts (the agent-autosave
    under test), then links to the shell tools found on the starting PATH,
    then the system folders. Built once from the PATH pytest started with."""
    folder = tmp_path_factory.mktemp("tools")
    start = os.environ.get("PATH", "")
    for tool in TOOLS:
        found = shutil.which(tool, path=start)
        if found and not (folder / tool).exists():
            (folder / tool).symlink_to(found)
    scripts = sysconfig.get_path("scripts")
    return os.pathsep.join(dict.fromkeys([scripts, os.path.dirname(sys.executable), str(folder),
                                          "/usr/bin", "/bin", "/usr/sbin", "/sbin"]))


@pytest.fixture(autouse=True)
def _clean_autosave_env(monkeypatch, tmp_path_factory, _tool_path):
    """Every test starts from the same environment whatever shell ran pytest:
    a scratch HOME and XDG_CONFIG_HOME, a PATH with no agent binaries, no
    agent markers and no AGENT_AUTOSAVE_* variable. A test that needs one of
    them sets it itself."""
    for name in list(os.environ):
        if name.startswith("AGENT_AUTOSAVE_"):
            monkeypatch.delenv(name)
    # SessionEnd normally rebuilds in a detached process; tests rebuild in
    # the hook itself so nothing races later hooks, reads or tmp cleanup.
    # Tests of the detached path drop this variable explicitly.
    monkeypatch.setenv("AGENT_AUTOSAVE_SESSION_END", "sync")
    # Bounded git and --version calls wait instead of timing out, so a loaded
    # machine never changes what a test sees (agent_autosave/timeouts.py).
    # Subprocesses get the same through LENIENT_CLI; tests of the bounds
    # set LENIENT back to False.
    monkeypatch.setattr(timeouts, "LENIENT", True)
    for name in CLEARED:
        monkeypatch.delenv(name, raising=False)
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("PATH", _tool_path)
    # A scratch HOME has no git identity; commits in tests use this one.
    for kind in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv("GIT_{}_NAME".format(kind), "Test")
        monkeypatch.setenv("GIT_{}_EMAIL".format(kind), "test@example.com")


@pytest.fixture
def run_cli(tmp_path):
    def run(*args, console=False, cwd=None, payload=b""):
        if console:
            suffix = ".exe" if os.name == "nt" else ""
            executable = Path(sysconfig.get_path("scripts")) / ("agent-autosave" + suffix)
            assert executable.is_file(), "Install the package before testing."
            command = [str(executable)]
        else:
            command = list(LENIENT_CLI)
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        return subprocess.run(
            command + list(args), cwd=cwd or tmp_path, env=env,
            input=payload, capture_output=True, timeout=5,
        )
    return run



@pytest.fixture
def home(tmp_path, monkeypatch):
    """A disposable HOME (and CODEX_HOME) for tests that install user-level files."""
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    return home
