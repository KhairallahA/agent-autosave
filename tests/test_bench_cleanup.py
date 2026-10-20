"""scripts/bench.py waits for the detached SessionEnd rebuilds it started
before deleting its temp project. Without the wait, one check.sh run failed
with "OSError: Directory not empty" while a rebuild was still writing."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _bench():
    spec = importlib.util.spec_from_file_location("bench", str(ROOT / "scripts/bench.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(os.name == "nt", reason="POSIX process table")
def test_bench_waits_for_detached_rebuilds_before_cleanup(tmp_path):
    from agent_autosave.background import spawn
    from agent_autosave.installer.install import install
    project = tmp_path / "repo"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    install(project, command="agent-autosave")
    bench = _bench()
    for _ in range(3):
        spawn(project)
    assert bench.wait_for_background(project, timeout=30)
    assert bench.background_pids(project) == []
