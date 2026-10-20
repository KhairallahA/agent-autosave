"""The rich save behind the context goldens, packed at each golden budget."""
from datetime import datetime, timezone
import json
from pathlib import Path

from agent_autosave.loader import explain

SAVE = Path(__file__).resolve().parent / "fixtures/context/save.json"
BUDGETS = (500, 1500, 4000, 12000)
NOW = datetime(2026, 10, 17, 14, 6, tzinfo=timezone.utc)


def rich_save():
    return json.loads(SAVE.read_text(encoding="utf-8"))


# Project memory as recall.session_memory returns it (two accepted entries,
# and no previous task because the rich save has actions).
MEMORY = {"entries": [
    {"id": "m-3fa2c1", "kind": "memory", "type": "decision", "text": "Keep the CSV API unchanged; stream underneath",
     "date": "2026-10-12", "agent": "codex", "pinned": True},
    {"id": "m-91be07", "kind": "memory", "type": "gotcha", "text": "CI has no network: never fetch fixtures in tests",
     "date": "2026-10-10", "agent": "you", "pinned": False}], "previous": None}


def packed(budget, save=None, memory=MEMORY):
    return explain(save or rich_save(), force=True, budget=budget, now=NOW, current_head="4f2a9c1d0e7b",
                   memory=memory)
