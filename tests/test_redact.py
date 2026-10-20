import json
from pathlib import Path

import pytest

from agent_autosave.redact import Redactor, env_values
from agent_autosave.state import TaskStore
from agent_autosave.storage import atomic_write, log_error
from tests.helpers import START, event

SECRETS = [
    ("sk-proj-abcdefgh123456789", "api_key"),
    ("ghp_abcdefgh123456789", "github"),
    ("github_pat_abcdefgh123456789", "github"),
    ("xoxb-123456789-abcdefgh", "slack"),
    ("AKIA1234567890ABCDEF", "aws"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature", "jwt"),
    ("Bearer abcdefgh123456", "bearer"),
    ('password="a private phrase"', "assignment"),
    ("API_KEY=plain-private-value", "assignment"),
    ("https://alice:hunter42@example.com/path", "url_credentials"),
    ("-----BEGIN RSA PRIVATE KEY-----\nprivate material\n-----END RSA PRIVATE KEY-----", "private_key"),
    ("-----BEGIN OPENSSH PRIVATE KEY-----\ntruncated private material", "private_key"),
]


@pytest.mark.parametrize("secret,kind", SECRETS)
def test_shapes_are_redacted_idempotently(secret, kind):
    redactor = Redactor()
    result = redactor.text("before " + secret + " after")
    assert secret not in result
    assert "[redacted:" + kind + "]" in result
    assert redactor.text(result) == result


def test_literals_nested_keys_custom_patterns_and_overlap():
    redactor = Redactor(literals=["private", "private phrase", "قيمة سرية"], extra_patterns=[r"INTERNAL-\d+"])
    assert redactor.value({"private phrase": ["private", {"value": "قيمة سرية INTERNAL-123"}]}) == {
        "[redacted:env]": ["[redacted:env]", {"value": "[redacted:env] [redacted:custom]"}]}
    assert redactor.text("ordinary code pytest main") == "ordinary code pytest main"


def test_env_files_nested_quotes_multiline_comments_and_no_execution(tmp_path):
    (tmp_path / ".env").write_text('export FIRST="a long secret"\nEMPTY=\nSECOND=other-private # comment\nMULTI="line one\nline two"\nLITERAL=$(touch should-not-exist)\n')
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / ".env.local").write_text("THIRD='another secret'\n")
    ignored = tmp_path / "node_modules"
    ignored.mkdir()
    (ignored / ".env").write_text("SKIP=ignored\n")
    values = env_values(tmp_path)
    assert set(values) == {"a long secret", "other-private", "line one\nline two", "$(touch should-not-exist)", "another secret"}
    assert not (tmp_path / "should-not-exist").exists()


@pytest.mark.parametrize("content", ['KEY="unfinished', "KEY=" + "x" * (1024 * 1024)])
def test_unsafe_env_input_fails_closed(tmp_path, content):
    (tmp_path / ".env").write_text(content)
    with pytest.raises(ValueError):
        env_values(tmp_path)


def test_secrets_never_enter_snapshots_archives_ledger_or_errors(tmp_path):
    literal = "قيمة سرية unique environment value"
    (tmp_path / ".env").write_text('PRIVATE="' + literal + '"\n', encoding="utf-8")
    store = TaskStore(tmp_path, {"ledger": {"rotate_mb": .001}})
    store.start(START)
    for index, (secret, _) in enumerate(SECRETS):
        store.append(event("note", index, text=secret + " " + literal, type="decision"))
    store.append(event("prompt", 20, text="Implement secure portable autosaves."))
    store.rebuild(git_state=None)
    store.start(event("turn_end", 21)["ts"], reason="clear")
    log_error(store.directory, "redaction test", ValueError(literal + " sk-proj-abcdefgh123456789"), redactor=store.redactor)
    assert list((store.directory / "history").glob("*.jsonl"))
    for path in store.directory.rglob("*"):
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            for secret in [value for value, _ in SECRETS] + [literal]:
                assert secret not in content, path
                assert json.dumps(secret, ensure_ascii=True)[1:-1] not in content, path
    assert "[redacted:env]" in (store.directory / "errors.log").read_text()


def test_redaction_happens_before_temporary_write(tmp_path, monkeypatch):
    import agent_autosave.storage as storage
    observed = []
    replace = storage.os.replace
    def inspect(source, destination):
        data = Path(source)
        observed.append(data.read_bytes())
        replace(source, destination)
    monkeypatch.setattr(storage.os, "replace", inspect)
    atomic_write(tmp_path / "save.json", {"v": 1, "text": "sk-proj-abcdefgh123456789"})
    assert observed and all(b"sk-proj-" not in data for data in observed)


def test_env_escaped_quotes_and_backslashes_are_not_truncated(tmp_path):
    (tmp_path / ".env").write_text(r'KEY="private\"quoted\\value\nnext"' + "\n")
    values = env_values(tmp_path)
    assert values == ['private"quoted\\value\nnext']
    assert Redactor(literals=values).text(values[0]) == "[redacted:env]"


def test_redaction_precedes_prompt_and_output_tail_truncation(tmp_path):
    store = TaskStore(tmp_path, {"capture": {"prompt_max_chars": 25, "output_tail_lines": 1}})
    store.start(START)
    store.append(event("prompt", text="Build secure: sk-proj-" + "A" * 100))
    store.append(event("tool", 1, tool="Bash", summary="test", exit=1, files=[],
                       output_tail="-----BEGIN PRIVATE KEY-----\nprivate-secret-line\n-----END PRIVATE KEY-----"))
    content = store.ledger.path.read_text()
    assert "sk-proj-" not in content and "private-secret-line" not in content
    assert "[redacted:private_key]" in content


def test_assignment_with_escaped_quotes_redacts_entire_value():
    assert Redactor().text(r'password="private\"remaining-secret"') == "[redacted:assignment]"


def test_prefilter_and_cache_never_skip_a_secret():
    from agent_autosave.redact import Redactor
    secrets = ["Bearer abc.def", "TOKEN=xyz", "https://u:p@host/x", "sk-abcdefgh1234",
               "ghp_abcdefghij", "AKIA" + "A" * 16, "-----BEGIN PRIVATE KEY-----\nx",
               "eyJa.bbb.ccc", "xoxb-12345678"]
    redactor = Redactor()
    first = [redactor.text("before " + s + " after") for s in secrets]
    assert all("[redacted:" in r for r in first)
    assert [redactor.text("before " + s + " after") for s in secrets] == first
    assert redactor.text("plain text, nothing secret") == "plain text, nothing secret"
