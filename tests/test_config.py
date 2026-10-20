import json

import pytest

from agent_autosave.config import ConfigError, load, load_with_warnings, resolve


def test_defaults_are_complete_private_and_independent():
    defaults = resolve()
    assert "commit" not in defaults and defaults["budgets_ms"] == {"git_call": 150}
    retired = {"commit": {"save_json": False, "save_md": False, "events": False}, "budgets_ms": {"hook": 300}}
    assert resolve(retired) == resolve()
    assert defaults["compile"] == {"save_md_max_chars": 12000, "last_actions": 10, "steering": 5, "files": 30}
    assert defaults["ledger"] == {"rotate_mb": 5}
    assert defaults["history"] == {"keep": 50}
    defaults["capture"]["ignore_paths"].append("mutated")
    assert "mutated" not in resolve()["capture"]["ignore_paths"]


def test_user_defaults_project_override_and_no_writes(tmp_path):
    user = tmp_path / "user.json"
    user.write_text(json.dumps({"capture": {"prompt_max_chars": 1000}, "compile": {"files": 15}}))
    project = tmp_path / "repo"
    (project / ".autosave").mkdir(parents=True)
    config = project / ".autosave/config.json"
    config.write_text(json.dumps({"compile": {"files": 20}}))
    before = config.read_bytes(), user.read_bytes()
    result = load(project, user_path=user)
    assert result["capture"]["prompt_max_chars"] == 1000
    assert result["compile"]["files"] == 20
    assert (config.read_bytes(), user.read_bytes()) == before
    assert load(tmp_path / "absent", user_path=tmp_path / "absent.json") == resolve()


@pytest.mark.parametrize("value", [
    {"v": 2}, {"v": True}, {"unknown": "secret"}, {"record": {"enabled": 1}},
    {"capture": []}, {"capture": {"ignore_tools": "Bash"}},
    {"compile": {"files": 1.5}}, {"ledger": {"rotate_mb": 0}},
    {"ledger": {"rotate_mb": float("nan")}}, {"history": {"keep": -1}},
    {"compile": {"save_md_max_chars": 255}}, {"inject": {"on_status": ["broken"]}},
    {"redact": {"extra_patterns": ["["]}},
])
def test_invalid_config_fails_without_echoing_values(value):
    with pytest.raises(ConfigError) as error:
        resolve(value)
    assert "secret" not in str(error.value)


def test_malformed_config_has_a_safe_error(tmp_path):
    path = tmp_path / "user.json"
    path.write_text('{"password": "never print this"')
    with pytest.raises(ConfigError, match="Cannot read configuration JSON"):
        load(tmp_path, user_path=path)


def test_a_null_vault_path_from_an_earlier_wizard_loads_with_a_clear_warning(tmp_path):
    (tmp_path / ".autosave").mkdir()
    (tmp_path / ".autosave/config.json").write_text(json.dumps({"v": 1, "vault": {"path": None, "project": ""}}))
    user = tmp_path / "none.json"
    config, warnings = load_with_warnings(tmp_path, user_path=user)
    assert config["vault"]["path"] == "" and load(tmp_path, user_path=user) == config
    assert len(warnings) == 1 and "vault.path" in warnings[0] and "null" in warnings[0]
    # Only null is forgiven: any other wrong type is still an error.
    (tmp_path / ".autosave/config.json").write_text(json.dumps({"v": 1, "vault": {"path": 3}}))
    with pytest.raises(ConfigError, match="vault.path must be a string"):
        load(tmp_path, user_path=user)


def test_doctor_warns_about_a_null_vault_path_instead_of_calling_the_config_invalid(tmp_path):
    from agent_autosave.installer.doctor import memory_checks
    (tmp_path / ".autosave").mkdir()
    (tmp_path / ".autosave/config.json").write_text(json.dumps({"v": 1, "vault": {"path": None, "project": ""}}))
    messages = [check["message"] for check in memory_checks(tmp_path)]
    assert not any("Config is invalid" in m for m in messages)
    assert any("vault.path" in m and "null" in m for m in messages)
