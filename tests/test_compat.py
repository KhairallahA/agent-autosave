"""Files written by main d672782 still load: ledger, save.json, task.json, config.json, history."""
import json
import shutil

from agent_autosave import compiler, config, loader, render
from agent_autosave.state import TaskStore
from tests.helpers import FIXTURES

OLD = FIXTURES / "compat/main-d672782"


def copy(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(str(OLD), str(root / ".autosave"))
    (root / ".git").mkdir()
    (root / ".git/HEAD").write_text("ref: refs/heads/main\n")
    return root


def events():
    return [json.loads(line) for line in OLD.joinpath("events.jsonl").read_text().splitlines() if line.strip()]


def test_the_old_config_loads_and_validates(tmp_path):
    loaded = config.load(copy(tmp_path))
    assert loaded["inject"]["max_chars"] == 1500  # explicit values are kept; upgrade moves the old defaults
    assert loaded["inject"]["resume_max_chars"] == 8000 and loaded["handoff"]["warn_at_percent"] == 85


def test_the_old_ledger_compiles_to_the_same_task(tmp_path):
    old = json.loads(next(OLD.joinpath("history").glob("*.json")).read_text())
    save = compiler.compile_events(events(), started=old["started"], task_id=old["task_id"])
    assert save["v"] == 1 and save["goal"] == old["goal"] and save["plan"] == old["plan"]


def test_the_old_save_renders_and_injects():
    old = json.loads(OLD.joinpath("save.json").read_text())
    assert render.render(old).strip()
    text = loader.context(old)
    assert isinstance(text, str)


def test_the_old_project_loads_and_task_json_migrates(tmp_path):
    root = copy(tmp_path)
    before = json.loads((root / ".autosave/task.json").read_text())
    loader.load(root)
    slots = TaskStore(root).slots()
    assert slots["slots"]["main"]["task_id"] == before["task_id"]
    history = json.loads(next((root / ".autosave/history").glob("*.json")).read_text())
    assert history["v"] == 1 and render.render(history).strip()
