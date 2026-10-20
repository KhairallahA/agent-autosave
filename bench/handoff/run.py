"""Run the handoff benchmark: does agent B finish agent A's interrupted task?

For each task, agent A starts in a fresh repo with agent-autosave wired for
both agents and is killed (its whole process group) after K recorded tool
events. Its repo is then copied into two arms and agent B is started in
each with the same prompt, "Continue the task in this repo.":

- autosave: the copy as A left it, so B's session start is handed the task.
- control: agent-autosave removed, its data deleted, then B's hooks put back
  with no instruction block and nothing given to a new session, so they only
  record what B does. B has nothing to go on but the repo.

Both arms are measured the same way: finished (the task's whole test suite
passes), redone work (B's edits to files of steps A had finished, and B's
re-runs of commands that already passed for A, both from the ledger), B's
wall time, and B's tokens where its transcript reports them.

    python bench/handoff/run.py --fake                     # the runner's own test
    python bench/handoff/run.py --a claude-code --b codex  # a real run

Results go to bench/results/<date>.json and .md (<date>-fake.* with --fake).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
import tasks  # noqa: E402

from agent_autosave.installer.detect import hook_command  # noqa: E402
from agent_autosave.installer.install import install  # noqa: E402
from agent_autosave.installer.remove import remove  # noqa: E402

CONTINUE = "Continue the task in this repo."
# Non-interactive runs. Claude Code: `-p` prints and exits, and
# --dangerously-skip-permissions skips prompts (code.claude.com/docs/en/cli-reference);
# the repos are throwaway. Codex: `codex exec` with the workspace-write sandbox
# (developers.openai.com/codex/noninteractive). Whether Codex runs its hooks in
# `exec` is not confirmed here; the launch rehearsal guide checks it on a real machine.
REAL = {"claude-code": ["claude", "-p", "--dangerously-skip-permissions"],
        "codex": ["codex", "exec", "--sandbox", "workspace-write"]}
# The control arm's config: nothing injected at session start (no task, no
# memory block), no prompt recall, no low-battery ask, and agentic-stack's
# .agent/ left alone, so the hooks only record what B does.
CONTROL_CONFIG = {"v": 1, "inject": {"on_status": [], "memory_on_start": False},
                  "recall": {"enabled": False}, "handoff": {"ask": False},
                  "integrations": {"agentic_stack": "off"}}
BLOCK = re.compile(r"\n?<!-- agent-autosave:start -->.*?<!-- agent-autosave:end -->\n?", re.S)
BLOCK_FILES = ("CLAUDE.md", "AGENTS.md", "GEMINI.md", "ANTIGRAVITY.md")


def events(repo: Path) -> list[dict[str, Any]]:
    path = repo / ".autosave/events.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue  # a line cut off by the kill
    return rows


def tool_events(repo: Path, agent: str) -> int:
    return sum(1 for e in events(repo) if e.get("kind") == "tool" and e.get("agent") == agent)


def command(agent: str, fake: bool, item: dict[str, Any], prompt: str, transcript: Path,
            hang_after: int | None = None) -> list[str]:
    if not fake:
        return REAL[agent] + [prompt]
    extra = ["--hang-after", str(hang_after)] if hang_after else []
    return [sys.executable, str(HERE / "fake_agent.py"), "--task", item["name"],
            "--transcript", str(transcript)] + extra + [prompt]


def run_a(repo: Path, argv: list[str], agent: str, k: int, timeout: float) -> dict[str, Any]:
    """Start A in its own process group; kill the group after K tool events."""
    proc = subprocess.Popen(argv, cwd=str(repo), start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline, stopped = time.monotonic() + timeout, "exited"
    while proc.poll() is None:
        if tool_events(repo, agent) >= k:
            stopped = "killed"
            break
        if time.monotonic() > deadline:
            stopped = "timeout"
            break
        time.sleep(0.1)
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    return {"stopped": stopped, "tool_events": tool_events(repo, agent)}


def make_control(repo: Path, agent: str, command_name: str) -> None:
    """Take agent-autosave out of the copy, then put back silent hooks for B."""
    remove(repo)
    shutil.rmtree(str(repo / ".autosave"), ignore_errors=True)
    install(repo, [agent], command=command_name, mcp=False, statusline="off")
    for name in BLOCK_FILES:
        path = repo / name
        if path.exists():
            path.write_text(BLOCK.sub("\n", path.read_text(encoding="utf-8")).strip("\n") + "\n", encoding="utf-8")
    (repo / ".autosave/config.json").write_text(json.dumps(CONTROL_CONFIG, indent=2) + "\n", encoding="utf-8")


def tokens(agent: str, transcript: str | None) -> int | None:
    """Tokens the transcript reports, or None. Claude Code transcripts carry
    message.usage on assistant rows (code.claude.com/docs/en/agent-sdk/cost-tracking);
    a Codex rollout's newest token_count has info.total_token_usage.total_tokens
    (tests/fixtures/codex/rollouts/rollout-real-session.jsonl)."""
    if not transcript or not Path(transcript).is_file() or not transcript.endswith(".jsonl"):
        return None
    total, seen = 0, False
    for line in Path(transcript).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if agent == "codex":
            info = (row.get("payload") or {}).get("info") if row.get("type") == "event_msg" else None
            usage = (info or {}).get("total_token_usage") if isinstance(info, dict) else None
            if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
                total, seen = usage["total_tokens"], True
        else:
            usage = (row.get("message") or {}).get("usage") if isinstance(row.get("message"), dict) else None
            if isinstance(usage, dict):
                total += sum(v for k, v in usage.items() if k in ("input_tokens", "output_tokens") and isinstance(v, int))
                seen = True
    return total if seen else None


def ours(summary: str) -> bool:
    return summary.strip().startswith("agent-autosave")


def measure(repo: Path, item: dict[str, Any], agent: str, since: str, finished: list[int],
            a_passed: set[str], wall: float) -> dict[str, Any]:
    """The arm's numbers, all from B's ledger events and the repo."""
    mine = [e for e in events(repo) if e.get("agent") == agent and e.get("ts", "") >= since]
    done_files = {item["steps"][n - 1]["file"] for n in finished}
    edits = [e for e in mine if e.get("kind") == "tool" and "exit" not in e and not e.get("readonly")]
    runs = [e for e in mine if e.get("kind") == "tool" and e.get("exit") == 0 and not ours(e.get("summary", ""))]
    transcripts = {e.get("transcript") for e in mine if e.get("kind") == "session_start"}
    counted = [tokens(agent, t) for t in transcripts]
    return {"finished": tasks.accepted(repo),
            "redone_edits": sum(1 for e in edits if done_files & set(e.get("files") or [])),
            "repeated_commands": sum(1 for e in runs if e.get("summary") in a_passed),
            "tool_events": sum(1 for e in mine if e.get("kind") == "tool"),
            "wall_s": round(wall, 1),
            "tokens": sum(c for c in counted if c is not None) if any(c is not None for c in counted) else None}


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def run_task(item: dict[str, Any], a: str, b: str, fake: bool, work: Path, timeout: float,
             command_name: str) -> dict[str, Any]:
    root = work / item["name"]
    first = tasks.generate(item, root / "a")
    install(first, sorted({a, b}), command=command_name, mcp=not fake, statusline="off")
    started = run_a(first, command(a, fake, item, tasks.prompt(item), root / "a.jsonl", item["k"]),
                    a, item["k"], timeout)
    finished = tasks.finished_steps(first, item)
    a_passed = {e["summary"] for e in events(first)
                if e.get("kind") == "tool" and e.get("exit") == 0 and not ours(e.get("summary", ""))}
    result = {"task": item["name"], "kind": item["kind"], "k": item["k"],
              "a": dict(started, finished_steps=finished, steps=len(item["steps"])), "arms": {}}
    for arm in ("autosave", "control"):
        repo = root / arm
        shutil.copytree(str(first), str(repo), symlinks=True)
        if arm == "control":
            make_control(repo, b, command_name)
        since, clock = stamp(), time.monotonic()
        try:
            subprocess.run(command(b, fake, item, CONTINUE, root / (arm + ".jsonl")), cwd=str(repo),
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=timeout, start_new_session=True)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
        result["arms"][arm] = dict(measure(repo, item, b, since, finished, a_passed, time.monotonic() - clock),
                                   timed_out=timed_out)
    return result


def summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for arm in ("autosave", "control"):
        rows = [r["arms"][arm] for r in runs]
        known = [r["tokens"] for r in rows if r["tokens"] is not None]
        out[arm] = {"finished": sum(r["finished"] for r in rows), "of": len(rows),
                    "redone_edits": sum(r["redone_edits"] for r in rows),
                    "repeated_commands": sum(r["repeated_commands"] for r in rows),
                    "wall_s": round(sum(r["wall_s"] for r in rows), 1),
                    "tokens": sum(known) if known else None}
    return out


def markdown(report: dict[str, Any]) -> str:
    lines = ["# Handoff benchmark, {}".format(report["date"]), ""]
    if report["mode"] == "fake":
        lines += ["Fake-agent run: this checks the runner, it measures nothing. Never quote these numbers.", ""]
    lines += ["Agent A: {}. Agent B: {}. A is killed after K recorded tool events; B gets \"{}\"".format(
        report["agents"]["a"], report["agents"]["b"], CONTINUE), "",
        "| Task | K | A finished steps | Arm | Finished | Redone edits | Repeated commands | Wall time (s) | Tokens |",
        "|---|---|---|---|---|---|---|---|---|"]
    for run in report["runs"]:
        for arm, row in run["arms"].items():
            lines.append("| {} | {} | {}/{} | {} | {} | {} | {} | {} | {} |".format(
                run["task"], run["k"], len(run["a"]["finished_steps"]), run["a"]["steps"], arm,
                "yes" if row["finished"] else "no", row["redone_edits"], row["repeated_commands"],
                row["wall_s"], "n/a" if row["tokens"] is None else row["tokens"]))
    lines += ["", "| Arm | Finished | Redone edits | Repeated commands | Wall time (s) | Tokens |", "|---|---|---|---|---|---|"]
    for arm, row in report["summary"].items():
        lines.append("| {} | {}/{} | {} | {} | {} | {} |".format(
            arm, row["finished"], row["of"], row["redone_edits"], row["repeated_commands"], row["wall_s"],
            "n/a" if row["tokens"] is None else row["tokens"]))
    return "\n".join(lines) + "\n"


def write(report: dict[str, Any], out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    base = out / (report["date"] + ("-fake" if report["mode"] == "fake" else ""))
    base.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    base.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    return base.with_suffix(".md")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the agent-autosave handoff benchmark.")
    parser.add_argument("--a", default="claude-code", choices=sorted(REAL), help="The agent that is interrupted.")
    parser.add_argument("--b", default="codex", choices=sorted(REAL), help="The agent that continues.")
    parser.add_argument("--fake", action="store_true", help="Scripted agents (offline): tests the runner.")
    parser.add_argument("--tasks", help="Comma-separated task names (default: all 10).")
    parser.add_argument("--out", default=str(HERE.parent / "results"), help="Where the results go.")
    parser.add_argument("--timeout", type=float, default=1800, help="Seconds each agent run may take.")
    parser.add_argument("--keep", action="store_true", help="Keep the task repos (printed at the end).")
    args = parser.parse_args(argv)
    if os.name == "nt":
        parser.error("The runner kills process groups, which needs macOS or Linux.")
    a, b = ("claude-code", "claude-code") if args.fake else (args.a, args.b)
    chosen = [tasks.by_name(n) for n in args.tasks.split(",")] if args.tasks else tasks.TASKS
    if not args.fake:
        missing = [REAL[x][0] for x in {a, b} if shutil.which(REAL[x][0]) is None]
        if missing:
            parser.error("Not found on PATH: " + ", ".join(missing))
    work = Path(tempfile.mkdtemp(prefix="autosave-bench-"))
    try:
        runs = [run_task(item, a, b, args.fake, work, args.timeout, hook_command()) for item in chosen]
    finally:
        if not args.keep:
            shutil.rmtree(str(work), ignore_errors=True)
    report = {"v": 1, "date": date.today().isoformat(), "mode": "fake" if args.fake else "real",
              "agents": {"a": a, "b": b}, "runs": runs, "summary": summary(runs)}
    print(write(report, Path(args.out)))
    if args.keep:
        print(work)
    return 0


if __name__ == "__main__":
    sys.exit(main())
