import json
import subprocess
import sys

import pytest

from agent_autosave.events import EventError
from agent_autosave.ledger import Ledger
from agent_autosave.state import TaskStore
from tests.helpers import START, event


APPEND_WORKER = """
import json
import sys
from agent_autosave.ledger import Ledger
ledger = Ledger(sys.argv[1], lock_timeout=10)
base = json.loads(sys.argv[2])
print('ready', flush=True)
sys.stdin.readline()
for index in range(5000):
    ledger.append(dict(base, turn=index + 1))
"""


@pytest.mark.slow
def test_two_processes_append_five_thousand_each(tmp_path):
    # Standalone workers also work when testing an installed wheel from outside
    # the source checkout; no pickled test-module import is needed in children.
    workers = [subprocess.Popen([sys.executable, "-c", APPEND_WORKER, str(tmp_path),
                                 json.dumps(event("turn_end", session=name))],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True) for name in ("a", "b")]
    try:
        for worker in workers:
            worker.stdin.write("go\n")
            worker.stdin.flush()
        for worker in workers:
            output, errors = worker.communicate(timeout=60)
            assert worker.returncode == 0, errors
            assert output.strip() == "ready"
        rows = Ledger(tmp_path).read()
        assert len(rows) == 10000
        assert len({(row["session"], row["turn"]) for row in rows}) == 10000
        for session in ("a", "b"):
            assert [row["turn"] for row in rows if row["session"] == session] == list(range(1, 5001))
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
                worker.communicate()


@pytest.mark.slow
def test_default_five_mb_rotation_preserves_active_task(tmp_path):
    store = TaskStore(tmp_path, {})
    store.start(START)
    # Deliberately large notes exercise the real 5 MiB default, not a mocked size.
    for index in range(5):
        store.append(event("note", index, text=str(index) + "x" * (1024 * 1024), type="warning"))
    assert list((store.directory / "history").glob("events-*.jsonl"))
    assert len(store.ledger.read()) == 5
    before = store.rebuild(git_state=None)
    assert len(before["agents"]) == 1
    threshold = json.loads((store.directory / "ledger.json").read_text())["next_rotation_bytes"]
    assert threshold > 5 * 1024 * 1024
    count = len(list((store.directory / "history").glob("events-*.jsonl")))
    store.append(event("turn_end", 6))
    assert len(list((store.directory / "history").glob("events-*.jsonl"))) == count


def test_rotation_discards_old_task_from_active_file_but_archives_it(tmp_path):
    store = TaskStore(tmp_path, {"ledger": {"rotate_mb": .001}})
    old = store.start(START)
    store.append(event("note", text="old " + "x" * 650, type="decision"))
    store.start(event("turn_end", 1)["ts"], reason="clear")
    store.append(event("prompt", 2, text="This is the new current task prompt."))
    store.append(event("note", 3, text="new " + "y" * 650, type="decision"))
    assert all(row["task_id"] != old["task_id"] for row in store.ledger.read())
    assert store.rebuild(git_state=None)["goal"] == "This is the new current task prompt."
    assert any(old["task_id"] in path.read_text() for path in (store.directory / "history").glob("events-*.jsonl"))


def test_partial_final_line_repaired_before_append_and_logged(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.append(event("turn_end"))
    with ledger.path.open("ab") as stream:
        stream.write(b'{"v":1,"partial":')
    with pytest.raises(EventError):
        ledger.read()
    ledger.append(event("turn_end", 1))
    assert len(ledger.read()) == 2
    assert "ledger.repair" in (ledger.directory / "errors.log").read_text()


@pytest.mark.parametrize("line", ["not json\n", '{"v":2}\n', "\xff\n"])
def test_malformed_history_rejected_and_logged(tmp_path, line):
    ledger = Ledger(tmp_path)
    ledger.path.write_bytes(line.encode("latin1"))
    with pytest.raises(EventError):
        ledger.read()
    assert "ledger.read" in (ledger.directory / "errors.log").read_text()


def test_rotation_failure_leaves_source_intact(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path, {"ledger": {"rotate_mb": .0002}})
    ledger.append(event("note", text="x" * 300, type="decision"))
    original = ledger.path.read_bytes()
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr("agent_autosave.ledger.atomic_write", fail)
    with pytest.raises(OSError):
        ledger.append(event("turn_end", 1))
    assert ledger.path.read_bytes() == original
