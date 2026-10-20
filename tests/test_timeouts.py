"""Slow external calls (git, an agent's --version) are bounded so hooks stay
fast. The test suite and the docs and example builders lift the bounds, so
their output never depends on how loaded the machine is, through a module
setting (agent_autosave.timeouts.LENIENT) that the product never turns on and
no environment variable reaches.

The slow call is forced here (a git that sleeps past the 150 ms bound), so
these tests do not depend on machine speed either."""
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from agent_autosave import timeouts
from agent_autosave.installer import detect
from agent_autosave.installer.install import install
from tests.helpers import LENIENT_CLI

ROOT = Path(__file__).resolve().parents[1]
SLOW_GIT = '#!/bin/sh\nsleep 0.4\nexec {} "$@"\n'
SLOW_VERSION = '#!/bin/sh\n[ "$1" = --version ] && {{ sleep {}; echo "codex-cli 0.162.1"; }}\nexit 0\n'
# A variable a product switch would plausibly be called, set to "on" in the
# behaviour check below to show nothing reads it.
TEMPTING = {"AGENT_AUTOSAVE_LENIENT_TIMEOUTS": "1", "AGENT_AUTOSAVE_LENIENT": "1", "AGENT_AUTOSAVE_TIMEOUTS": "lenient"}


def slow_tool(folder: Path, name: str, text: str) -> str:
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_text(text)
    path.chmod(0o755)
    return str(folder) + os.pathsep + os.environ["PATH"]


def save_with_slow_git(tmp_path, cli):
    project = tmp_path / "repo"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    install(project, command="agent-autosave")
    git = subprocess.run(["sh", "-c", "command -v git"], capture_output=True, text=True).stdout.strip()
    env = dict(os.environ, PATH=slow_tool(tmp_path / "bin", "git", SLOW_GIT.format(git)), **TEMPTING)
    run = subprocess.run(cli + ["save", "--json"], cwd=str(project), env=env,
                         capture_output=True, text=True, timeout=120)
    return project, json.loads(run.stdout)


def test_the_product_cli_skips_a_slow_git_and_logs_it_whatever_the_environment(tmp_path):
    project, result = save_with_slow_git(tmp_path, [sys.executable, "-m", "agent_autosave"])
    assert result["ok"]
    log = (project / ".autosave/errors.log").read_text()
    assert "git.read" in log and "TimeoutExpired" in log


def test_the_lenient_test_cli_waits_for_a_slow_git(tmp_path):
    project, result = save_with_slow_git(tmp_path, LENIENT_CLI)
    assert result["ok"] and not (project / ".autosave/errors.log").exists()
    assert json.loads((project / ".autosave/save.json").read_text())["git"]["branch"]


def test_a_slow_version_check_is_dropped_unless_lenient(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", slow_tool(tmp_path / "bin", "codex", SLOW_VERSION.format(0.4)))
    for name, value in TEMPTING.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(timeouts, "LENIENT", False)
    assert detect.codex(timeout=0.1)["version"] is None
    monkeypatch.setattr(timeouts, "LENIENT", True)
    assert detect.codex(timeout=0.1)["version"] == "0.162.1"


def test_the_package_never_reads_an_environment_variable_for_it():
    source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "agent_autosave").rglob("*.py"))
    assert "LENIENT_TIMEOUTS" not in source
    # The setting is only ever read, never assigned, inside the package.
    assigned = [line for line in source.splitlines() if re.search(r"\bLENIENT\s*=", line)]
    assert assigned == ["LENIENT = False  # tests and scripts/build-*.py only"]


def test_the_suite_and_the_docs_tour_run_lenient(tmp_path):
    assert timeouts.LENIENT is True  # conftest, for in-process tests
    sys.path.insert(0, str(ROOT / "scripts"))
    build_docs = importlib.import_module("build-docs")
    tour = build_docs.Tour(tmp_path)
    stub = (tour.bin / "agent-autosave").read_text()
    assert str(build_docs.LENIENT_CLI) in stub and LENIENT_CLI[1] == str(build_docs.LENIENT_CLI)
    assert not any("LENIENT" in name for name in tour.env)
