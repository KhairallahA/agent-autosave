"""`agent-autosave handoff export|import`: hand a task to a teammate.

export writes .autosave-handoff.md at the repo root: frontmatter, the
current SAVE.md, and the task's ledger events (already redacted when they
were recorded; redacted again on the way out). The teammate commits it or
sends it. import replays those events into a new task on the teammate's
current branch: the compiler is pure, so the same events give the same
save, and the next session start hands it off as usual. Import only goes
to the branch the file was exported from, so a task never lands on the
wrong branch's slot.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..contract import ExitCode, Outcome, fail, success
from . import common

FILE = ".autosave-handoff.md"
EVENTS_MARKER = "<!-- agent-autosave:events -->"
FORMAT = 1


def task_events(project: Path, task_id: str) -> list[dict[str, Any]]:
    """The current ledger's events of one task, in order."""
    path = project / ".autosave/events.jsonl"
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
        try:
            event = json.loads(line)
        except ValueError:
            continue  # a torn last line; the ledger repairs it on the next write
        if isinstance(event, dict) and event.get("task_id") == task_id:
            rows.append(event)
    return rows


def render(save: dict[str, Any], branch: str | None, markdown: str, events: list[dict[str, Any]]) -> str:
    front = {"agent-autosave-handoff": FORMAT, "task_id": save["task_id"], "branch": branch or "",
             "status": save["status"], "goal": save.get("goal") or "", "exported": common.now()}
    head = "---\n" + "".join("{}: {}\n".format(k, json.dumps(v, ensure_ascii=False)) for k, v in front.items()) + "---\n"
    body = "\n".join(json.dumps(e, ensure_ascii=False, sort_keys=True) for e in events)
    return head + "\n" + markdown.rstrip("\n") + "\n\n" + EVENTS_MARKER + "\n```jsonl\n" + body + "\n```\n"


def parse(text: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(frontmatter, events); ValueError when it is not our file."""
    if not text.startswith("---\n") or EVENTS_MARKER not in text:
        raise ValueError("not an agent-autosave handoff file")
    front = {}
    for line in text[4:text.index("\n---\n", 4)].splitlines():
        key, _, value = line.partition(": ")
        front[key] = json.loads(value)
    if front.get("agent-autosave-handoff") != FORMAT:
        raise ValueError("unsupported handoff file version")
    block = text.split(EVENTS_MARKER, 1)[1].split("```jsonl\n", 1)[1].rsplit("```", 1)[0]
    events = [json.loads(line) for line in block.splitlines() if line.strip()]
    if not all(isinstance(e, dict) for e in events):
        raise ValueError("bad event line")
    return front, events


def run(args: argparse.Namespace) -> Outcome:
    project = common.find_project()
    return export(project) if args.handoff_action == "export" else load(project)


def export(project: Path) -> Outcome:
    from ..branches import head_branch
    from ..redact import Redactor
    from ..render import render as render_md
    from ..installer.install import replace_user_file
    save = common.load_fresh(project)
    if save is None:
        return common.not_ready()
    store = common.store(project)
    redactor = Redactor.from_project(project, store.config)
    text = redactor.text(render(save, head_branch(project), render_md(save), task_events(project, save["task_id"])))
    replace_user_file(project / FILE, text)
    data = {"path": FILE, "task_id": save["task_id"], "events": text.count("\n{")}
    return success(data), ExitCode.SUCCESS, "Wrote {}: commit it (or send it) for your teammate.".format(FILE)


def load(project: Path) -> Outcome:
    from ..branches import head_branch
    path = project / FILE
    if not path.is_file():
        return fail("no_handoff", "There is no {} here.".format(FILE),
                    "Ask your teammate to run agent-autosave handoff export and commit it.")
    try:
        front, events = parse(path.read_text(encoding="utf-8"))
    except (ValueError, KeyError, IndexError) as error:
        return fail("invalid_args", "{} cannot be read: {}.".format(FILE, error), "Export it again.")
    branch = head_branch(project) or ""
    if (front.get("branch") or "") != branch:
        return fail("not_supported", "The handoff is for branch {}, you are on {}.".format(
            front.get("branch") or "(no git)", branch or "(no git)"),
            "Check out {} and run agent-autosave handoff import again.".format(front.get("branch") or "it"))
    if not events:
        return fail("no_handoff", "{} holds no events.".format(FILE), "Export it again.")
    store = common.store(project)
    store.start(min(e["ts"] for e in events), reason="clear")
    for event in events:
        store.append({k: v for k, v in event.items() if k != "task_id"})
    last = events[-1]
    if last.get("kind") != "session_end":
        # The teammate's session is over on this machine: a pause, so the
        # next session start hands the task off.
        store.end_session([{"v": 1, "ts": common.now(), "agent": last["agent"], "session": last["session"],
                            "turn": last.get("turn", 0), "kind": "session_end", "reason": "other"}], git_state=None)
    save = store.rebuild()
    data = {"task_id": save["task_id"], "from": front.get("task_id"), "status": save["status"], "events": len(events)}
    return success(data), ExitCode.SUCCESS, "Imported {} events: {} ({}). The next agent session picks it up.".format(
        len(events), save.get("goal") or save["task_id"], save["status"])
