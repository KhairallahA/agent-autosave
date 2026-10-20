import json
import os
import stat

import pytest

from agent_autosave.locking import LockTimeout, exclusive
from agent_autosave.storage import atomic_write, log_error, private_dir, read_json


def test_atomic_replace_failure_preserves_previous_and_cleans_temporary(tmp_path, monkeypatch):
    path = tmp_path / "save.json"
    atomic_write(path, {"v": 1, "text": "previous"})
    def fail(*args):
        raise OSError("simulated disk error")
    monkeypatch.setattr("agent_autosave.storage.os.replace", fail)
    with pytest.raises(OSError):
        atomic_write(path, {"v": 1, "text": "next"})
    assert read_json(path)["text"] == "previous"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_state_permissions_and_symlink_refusal(tmp_path):
    directory = private_dir(tmp_path / ".autosave")
    path = directory / "save.json"
    atomic_write(path, {"v": 1})
    with exclusive(directory / ".writer.lock"):
        pass
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(item.stat().st_mode) == 0o600 for item in directory.iterdir())
    target = tmp_path / "user.json"
    target.write_text("user-owned")
    link = directory / "linked.json"
    link.symlink_to(target)
    with pytest.raises(OSError):
        atomic_write(link, {"v": 1})
    with pytest.raises(OSError):
        read_json(link)
    assert target.read_text() == "user-owned"


def test_lock_timeout_and_release_on_exception(tmp_path):
    path = tmp_path / ".writer.lock"
    with exclusive(path):
        with pytest.raises(LockTimeout):
            with exclusive(path, timeout=.005):
                pytest.fail("acquired held lock")
    with pytest.raises(RuntimeError):
        with exclusive(path):
            raise RuntimeError("caller failed")
    with exclusive(path):
        pass


@pytest.mark.parametrize("value", [{"v": 2}, {"v": True}, [], {}])
def test_future_or_invalid_document_versions_fail(tmp_path, value):
    path = tmp_path / "save.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        read_json(path)


def test_error_log_is_bounded_and_logging_failure_never_masks_error(tmp_path, monkeypatch):
    log_error(tmp_path, "test", ValueError("x" * (1024 * 1024 + 100)))
    assert (tmp_path / "errors.log").stat().st_size <= 1024 * 1024
    monkeypatch.setattr("agent_autosave.storage.os.replace", lambda *args: (_ for _ in ()).throw(OSError()))
    log_error(tmp_path, "test", ValueError("cannot log"))


def test_derived_views_skip_fsync_but_ledger_and_task_stay_durable(tmp_path, monkeypatch):
    import os as _os
    import subprocess as _sp
    from agent_autosave.installer.install import install
    from agent_autosave.state import TaskStore
    from agent_autosave.storage import read_view
    _sp.run(["git", "init", "-q", str(tmp_path)], check=True)
    install(tmp_path, command="agent-autosave")
    store = TaskStore(tmp_path)
    synced = []
    real = _os.fsync
    monkeypatch.setattr(_os, "fsync", lambda fd: (synced.append(_os.readlink("/proc/self/fd/%d" % fd)
                                                                if _os.path.exists("/proc/self/fd") else fd), real(fd)))
    store.append({"v": 1, "ts": "2030-01-01T00:00:00Z", "agent": "claude-code", "session": "s", "turn": 1,
                  "kind": "prompt", "text": "Implement the durable ledger please."})
    synced.clear()
    store.rebuild()
    assert not any("save.json" in str(p) or "SAVE.md" in str(p) for p in synced)
    # A torn or empty view (possible after power loss) reads as missing.
    (tmp_path / ".autosave/save.json").write_text("")
    assert read_view(tmp_path / ".autosave/save.json") is None
    assert store.rebuild()["goal"].startswith("Implement")
