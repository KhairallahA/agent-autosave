"""Explicitly regenerate compiler goldens; review every resulting diff.

Run from the repository root: python scripts/update-goldens.py
Fixtures are synthetic normalized events, not recorded vendor hook payloads.
Also rewrites the context block goldens in tests/fixtures/context and the
styled and plain command output goldens in tests/fixtures/ui.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_autosave.compiler import compile_events, serialize
from agent_autosave.render import render


def main():
    directories = sorted((ROOT / "tests/fixtures/compiler").glob("*/"))
    for directory in directories:
        options = json.loads((directory / "inputs.json").read_text(encoding="utf-8"))
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        save = compile_events(events, **options)
        (directory / "save.json").write_bytes(serialize(save))
        (directory / "SAVE.md").write_bytes(render(save, options.get("config")).encode("utf-8"))
    print("Updated {} golden streams; inspect git diff before accepting.".format(len(directories)))
    # Context block goldens (tests/test_context_v2.py): one rich save packed
    # into each budget.
    from tests.context_cases import BUDGETS, packed
    for budget in BUDGETS:
        result = packed(budget)
        (ROOT / "tests/fixtures/context/context-{}.txt".format(budget)).write_text(result["context"] + "\n", encoding="utf-8")
        (ROOT / "tests/fixtures/context/explain-{}.json".format(budget)).write_text(
            json.dumps(result["sections"], indent=2) + "\n", encoding="utf-8")
    print("Updated {} context goldens in tests/fixtures/context.".format(len(BUDGETS)))
    # Command output goldens (tests/test_ui.py), rendered in UTC.
    import os
    import time
    os.environ["TZ"] = "UTC"
    time.tzset()
    from tests import ui_cases
    ui_cases.GOLDENS.mkdir(parents=True, exist_ok=True)
    rendered = ui_cases.render_all()
    for name, text in rendered.items():
        (ui_cases.GOLDENS / name).write_text(text, encoding="utf-8")
    print("Updated {} output goldens in tests/fixtures/ui.".format(len(rendered)))


if __name__ == "__main__":
    main()
