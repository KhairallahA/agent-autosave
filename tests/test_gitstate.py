import subprocess

from agent_autosave.gitstate import read


def git(path, *args):
    result = subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                             "-c", "commit.gpgsign=false", *args], cwd=path, check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.decode().strip()


def test_git_facts_dirty_rename_commit_count_detached_and_no_mutation(tmp_path, monkeypatch):
    git(tmp_path, "init")
    (tmp_path / "before.py").write_text("first\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "first")
    first = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "before.py").write_text("second\n")
    git(tmp_path, "commit", "-am", "second")
    git(tmp_path, "mv", "before.py", "after space.py")
    (tmp_path / "untracked space.py").write_text("new\n")
    index = (tmp_path / ".git/index").read_bytes()
    monkeypatch.setenv("GIT_DIR", "/does-not-exist")
    state = read(tmp_path, start_head=first)
    assert state is not None
    assert state["dirty"] == ["after space.py", "untracked space.py"]
    assert state["head"] != first and state["commits_since_start"] == 1
    assert state["branch"]
    assert state["diffstat"]
    assert (tmp_path / ".git/index").read_bytes() == index
    monkeypatch.delenv("GIT_DIR")
    git(tmp_path, "reset", "--hard", "HEAD")
    git(tmp_path, "checkout", "--detach")
    assert read(tmp_path)["branch"] is None


def test_non_repo_and_unborn_repo(tmp_path):
    assert read(tmp_path) is None
    git(tmp_path, "init")
    (tmp_path / "new.py").write_text("hello\n")
    git(tmp_path, "add", ".")
    state = read(tmp_path)
    assert state["head"] is None
    assert state["dirty"] == ["new.py"]
    assert state["commits_since_start"] == 0
    assert "1 insertion" in state["diffstat"]


def test_timeout_is_capped_returns_null_and_reports_error(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_autosave.timeouts.LENIENT", False)
    seen = []
    errors = []
    def timeout(*args, **kwargs):
        seen.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired("git", kwargs["timeout"])
    monkeypatch.setattr("agent_autosave.gitstate.subprocess.run", timeout)
    assert read(tmp_path, timeout_ms=1000, on_error=errors.append) is None
    assert 0 < seen[0] <= .150
    assert isinstance(errors[0], subprocess.TimeoutExpired)


def test_missing_git_reports_error(tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("git missing")
    monkeypatch.setattr("agent_autosave.gitstate.subprocess.run", missing)
    errors = []
    assert read(tmp_path, on_error=errors.append) is None
    assert len(errors) == 1
