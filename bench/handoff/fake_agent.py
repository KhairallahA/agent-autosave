"""A scripted stand-in for Claude Code, so the benchmark runner is tested offline.

It speaks Claude Code's hook protocol: it reads the hook commands from
.claude/settings.json and runs them with the payload fields Claude Code
sends (code.claude.com/docs/en/hooks), as the real agent would. It does the
task by writing each step's solution from tasks.py and running its test.

What it knows at start is only what a real agent would see: the context a
SessionStart hook returns, and whether CLAUDE.md asks it to record a plan. A
handed-off task ('Unfinished: "<step>"' in the context) resumes at that
step; otherwise it starts from step 1. `--hang-after K` makes it stall
after its K-th tool call, with a child process, the way a busy agent looks
to the runner that kills it. Its token counts are made up and only prove
that the runner reads them; they are never results.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tasks  # noqa: E402  (a script next to this one, not a package)

FAKE_TOKENS = {"input_tokens": 1000, "output_tokens": 100}


class Agent:
    def __init__(self, repo: Path, transcript: Path, hang_after: int | None):
        self.repo, self.transcript, self.hang_after = repo, transcript, hang_after
        self.session, self.tools = str(uuid.uuid4()), 0
        settings = repo / ".claude/settings.json"
        self.hooks = json.loads(settings.read_text(encoding="utf-8")).get("hooks", {}) if settings.exists() else {}
        block = repo / "CLAUDE.md"
        self.records_plan = block.exists() and "agent-autosave plan set" in block.read_text(encoding="utf-8")

    def emit(self, event: str, **fields) -> str:
        """Run every hook command for `event`; returns the context they add."""
        payload = dict(session_id=self.session, transcript_path=str(self.transcript), cwd=str(self.repo),
                       hook_event_name=event, **fields)
        added = []
        for group in self.hooks.get(event, []):
            for hook in group.get("hooks", []):
                result = subprocess.run(hook["command"], shell=True, cwd=str(self.repo), input=json.dumps(payload),
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)
                added.append(_context(result.stdout))
        return "\n".join(text for text in added if text)

    def think(self) -> None:
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "usage": FAKE_TOKENS}}) + "\n")

    def tool(self, name: str, tool_input: dict, run=None) -> None:
        """One tool call, then its PostToolUse (or PostToolUseFailure) hook."""
        self.think()
        identifier = "toolu_" + uuid.uuid4().hex[:12]
        self.emit("PreToolUse", tool_name=name, tool_input=tool_input, tool_use_id=identifier)
        code = run() if run else 0
        last = self.hang_after is not None and self.tools + 1 >= self.hang_after
        if last:
            # The runner kills us as soon as it counts this event, so the
            # child that must die with the process group starts before it.
            self.spawn_child()
        if code:
            self.emit("PostToolUseFailure", tool_name=name, tool_input=tool_input, tool_use_id=identifier,
                      error="Exit code {}".format(code))
        else:
            self.emit("PostToolUse", tool_name=name, tool_input=tool_input, tool_use_id=identifier,
                      tool_response={"success": True})
        self.tools += 1
        if last:
            self.hang()

    def spawn_child(self) -> None:
        """A long-lived child, so the test can see the kill reach the whole process group."""
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        marker = os.environ.get("BENCH_FAKE_CHILD_PID")
        if marker:
            Path(marker).write_text(str(child.pid), encoding="utf-8")

    def hang(self) -> None:
        while True:
            time.sleep(1)

    def bash(self, command: str) -> None:
        self.tool("Bash", {"command": command},
                  lambda: subprocess.call(command, shell=True, cwd=str(self.repo),
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

    def write(self, path: str, text: str) -> None:
        def run():
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            return 0
        self.tool("Write", {"file_path": str(self.repo / path), "content": text}, run)


def _context(stdout: str) -> str:
    try:
        output = json.loads(stdout)
    except ValueError:
        return stdout.strip()
    return str((output.get("hookSpecificOutput") or {}).get("additionalContext") or "")


def start_step(context: str, item: dict) -> int | None:
    """0-based step to resume at from a handoff context, or None when there is none."""
    match = re.search(r'Unfinished: "([^"]+)"', context)
    names = [s["name"] for s in item["steps"]]
    if match and match.group(1) in names:
        return names.index(match.group(1))
    if re.search(r"Plan: all \d+ steps done", context):
        return len(names)
    return None


def work(agent: Agent, item: dict, prompt: str) -> None:
    context = agent.emit("SessionStart", source="startup")
    agent.emit("UserPromptSubmit", prompt=prompt)
    first = start_step(context, item)
    if first is None:
        first = 0
        if agent.records_plan:
            agent.bash("agent-autosave plan set --quiet " + " ".join(shlex.quote(s["name"]) for s in item["steps"]))
        agent.bash("python -m pytest -q -p no:cacheprovider")
    for number, step in enumerate(item["steps"][first:], first + 1):
        agent.write(step["file"], step["solution"])
        agent.bash("python -m pytest -q -p no:cacheprovider " + step["test_file"])
        if agent.records_plan:
            agent.bash("agent-autosave plan done --quiet {}".format(number))
    agent.bash("python -m pytest -q -p no:cacheprovider")
    agent.think()
    agent.emit("Stop", stop_hook_active=False, last_assistant_message="All tests pass.")
    agent.emit("SessionEnd", reason="other")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--task", required=True)
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--hang-after", type=int)
    parser.add_argument("prompt")
    args = parser.parse_args(argv)
    agent = Agent(Path.cwd(), Path(args.transcript), args.hang_after)
    work(agent, tasks.by_name(args.task), args.prompt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
