"""scripts/scan-history.py finds private data in every version without printing it. Every
repo here is a temporary local one."""
import importlib.util
import json
import os
import shutil
import subprocess

import pytest

from tests.helpers import ROOT

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="needs git")

spec = importlib.util.spec_from_file_location("scan_history", ROOT / "scripts/scan-history.py")
scan_history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan_history)

FIXTURE = "tests/fixtures/codex/rollouts/rollout-real-session.jsonl"
SECRET_ID = "user-PRIVATEPRIVATEPRIVATE1"
SECRET_PATH = "/Users/privateperson/work/secret-plans"
LEAKY = json.dumps({"type": "session_meta", "payload": {"cwd": "/tmp/aa-x", "creator_user_id": SECRET_ID,
                                                        "note": "see " + SECRET_PATH}}) + "\n"
CLEAN = json.dumps({"type": "session_meta", "payload": {"cwd": "/tmp/aa-x", "id": "s1"}}) + "\n"
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="test@example.invalid", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="test@example.invalid", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=ENV).stdout


def make_repo(path, versions):
    git(path.parent, "init", "-q", "-b", "main", str(path))
    (path / "README.md").write_text("hello\n")
    target = path / FIXTURE
    target.parent.mkdir(parents=True)
    for number, text in enumerate(versions):
        target.write_text(text)
        git(path, "add", "-A")
        git(path, "commit", "-qm", "version {}".format(number))
    return path



def test_the_scan_finds_old_versions_and_never_prints_the_value(tmp_path):
    repo = make_repo(tmp_path / "r", [LEAKY, CLEAN])
    hits = scan_history.scan(str(repo))
    assert {rule for rule, *_ in hits} == {"account id", "home path"}
    assert all(path == FIXTURE for _, path, *_ in hits)
    run = subprocess.run(["python3", str(ROOT / "scripts/scan-history.py"), "--repo", str(repo)],
                         capture_output=True, text=True)
    assert run.returncode == 1 and "2 hit(s)" in run.stdout
    assert "PRIVATE" not in run.stdout and "privateperson" not in run.stdout
    assert scan_history.scan(str(repo), ["-n1", "HEAD"]) == []  # the tip alone is clean


def test_forbidden_words_and_commit_messages_are_scanned_but_not_printed(tmp_path):
    repo = make_repo(tmp_path / "r", [CLEAN])
    git(repo, "commit", "-q", "--allow-empty", "-m", "port the Zanzibar runner")
    words = tmp_path / "forbid.txt"
    words.write_text("zanzibar\n")
    run = subprocess.run(["python3", str(ROOT / "scripts/scan-history.py"), "--repo", str(repo),
                          "--forbid-file", str(words)], capture_output=True, text=True)
    assert run.returncode == 1 and "forbidden text\t<commit message>:1" in run.stdout
    assert "anzibar" not in run.stdout.lower()


def test_the_allowlist_names_benign_hits_by_rule_path_and_hash(tmp_path):
    repo = make_repo(tmp_path / "r", [CLEAN])
    (repo / "docs.md").write_text("Contact bench@example.invalid or me@private-company.test\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "docs")
    hits = scan_history.scan(str(repo))
    assert [(rule, path) for rule, path, *_ in hits] == [("email", "docs.md")]  # example.invalid is allowed
    allow = [("email", "docs.md", hits[0][4][:8])]
    assert scan_history.scan(str(repo), extra=allow) == []
