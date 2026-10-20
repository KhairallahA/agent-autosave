"""The user-level hooks registry shared by every repo on the machine."""
from agent_autosave.commands import codex_layers
from agent_autosave.installer import doctor, registry
from agent_autosave.installer.install import install
from agent_autosave.installer.remove import remove
from tests.helpers import CMD, write


def codex_user_install(project, home):
    project.mkdir(parents=True, exist_ok=True)
    user_dir, write_hooks, found = codex_layers.choose(project, user_level=True)
    return install(project, ("codex",), command=CMD, codex_user_dir=user_dir, codex_hooks=write_hooks,
                   codex_found=found)


def test_registry_keeps_shared_user_hooks_until_the_last_repo(tmp_path, home):
    user_file = home / ".codex/hooks.json"
    write(user_file, b'{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}}\n')
    original = user_file.read_bytes()
    one, two = tmp_path / "one", tmp_path / "two"
    codex_user_install(one, home)  # untrusted: user level
    codex_user_install(two, home)  # finds the hooks in place
    assert user_file.read_bytes() != original
    data = registry.load()
    [(key, record)] = data["files"].items()
    assert key == str(user_file.resolve()) and [i["repo"] for i in record["installs"]] == [str(one.resolve()), str(two.resolve())]
    assert record["change"]["spans"]
    result = remove(one)
    assert result["kept_shared"] and user_file.read_bytes() != original
    result = remove(two)
    assert user_file.read_bytes() == original
    assert registry.load()["files"] == {}


def test_stale_registry_entries_are_listed_and_pruned(tmp_path, home, monkeypatch):
    one, two = tmp_path / "one", tmp_path / "two"
    codex_user_install(one, home)
    codex_user_install(two, home)
    (one / ".autosave/install.json").unlink()  # repo one was deleted by hand
    stale = [c for c in doctor.audit(two) if "Stale registry entry" in c["message"]]
    assert len(stale) == 1 and str(one.resolve()) in stale[0]["message"] and stale[0]["hint"] == "agent-autosave doctor --prune"
    # Only live installs count: removing two reverts the shared hooks.
    remove(two)
    assert not (home / ".codex/hooks.json").exists() or b"agent-autosave" not in (home / ".codex/hooks.json").read_bytes()


def test_prune_drops_only_stale_installs(tmp_path, home):
    one, two = tmp_path / "one", tmp_path / "two"
    codex_user_install(one, home)
    codex_user_install(two, home)
    (one / ".autosave/install.json").unlink()
    dropped = registry.prune()
    assert [i["repo"] for _, i in dropped] == [str(one.resolve())]
    assert [i["repo"] for i in registry.users(home / ".codex/hooks.json")] == [str(two.resolve())]


def test_registry_respects_xdg_config_home(tmp_path):
    assert registry.path({"XDG_CONFIG_HOME": str(tmp_path / "x")}) == tmp_path / "x/agent-autosave/installs.json"
    assert registry.path({}, home=tmp_path) == tmp_path / ".config/agent-autosave/installs.json"
