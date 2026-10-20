"""Scripted Claude Code and Codex for the demo GIF: `standin.py claude|codex [prompt]`.

The machine that renders the demo has neither agent, so these two play
their part in the terminal and speak each agent's real hook protocol to
agent-autosave: they read the hook commands the installer wrote
(.claude/settings.json, .codex/hooks.json) and send the payload fields the
recorded fixtures show (tests/fixtures/claude-code, tests/fixtures/codex).
Everything agent-autosave says on screen (the status line, the low-usage
ask, the context Codex starts with) is its real output.

Claude Code works until its 5-hour window reads 88%, records the handoff
note it is asked for, and stops on its usage limit. Codex starts with the
handed-off save, fixes the bug, and finishes the plan.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

DIM, BOLD, RED, GREEN, ORANGE, CYAN, RESET = ("\033[2m", "\033[1m", "\033[31m", "\033[32m", "\033[38;5;173m",
                                             "\033[36m", "\033[0m")
FIXED = '''def valid_email(address):
    """Signup accepts an address only with something on both sides of one @."""
    name, at, domain = address.partition("@")
    return bool(name and at and domain and "@" not in domain)
'''
TEST = '''from signup import valid_email


def test_rejects_an_address_without_an_at():
    assert not valid_email("ada.example.com")


def test_accepts_a_plain_address():
    assert valid_email("ada@example.com")
'''


def say(text: str = "", pause: float = 0.5) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    time.sleep(pause)


class Agent:
    """One session: runs the hooks the installer wrote, with `payload` fields."""

    def __init__(self, name: str, hooks_file: str, **fields) -> None:
        self.name, self.repo = name, Path.cwd()
        hooks = self.repo / hooks_file
        self.hooks = json.loads(hooks.read_text(encoding="utf-8")).get("hooks", {}) if hooks.exists() else {}
        self.fields = dict(session_id=str(uuid.uuid4()), cwd=str(self.repo), **fields)

    def emit(self, event: str, **fields) -> str:
        payload = dict(self.fields, hook_event_name=event, **fields)
        added = []
        for group in self.hooks.get(event, []):
            for hook in group.get("hooks", []):
                out = subprocess.run(hook["command"], shell=True, cwd=str(self.repo), input=json.dumps(payload),
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True).stdout
                try:
                    out = (json.loads(out).get("hookSpecificOutput") or {}).get("additionalContext") or ""
                except ValueError:
                    pass
                added.append(out.strip())
        return "\n".join(text for text in added if text)

    def bash(self, command: str, tool: str = "Bash", **extra) -> str:
        """Run `command` as the agent's shell tool; returns its output."""
        identifier = extra.pop("tool_use_id", "toolu_" + uuid.uuid4().hex[:12])
        self.emit("PreToolUse", tool_name=tool, tool_input={"command": command}, tool_use_id=identifier)
        result = subprocess.run(command, shell=True, cwd=str(self.repo), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, universal_newlines=True)
        self.after(identifier, result.returncode)
        event = "PostToolUseFailure" if result.returncode and self.name == "claude" else "PostToolUse"
        fields = {"error": "Exit code {}".format(result.returncode)} if event == "PostToolUseFailure" else {
            "tool_response": "" if self.name == "codex" else {"stdout": result.stdout, "exit_code": 0}}
        self.emit(event, tool_name=tool, tool_input={"command": command}, tool_use_id=identifier, **fields)
        return result.stdout

    def after(self, identifier: str, code: int) -> None:
        """Codex keeps a shell call's exit code in its rollout, where agent-autosave reads it."""
        if self.name == "codex":
            row = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()), "type": "event_msg",
                   "payload": {"type": "item_completed", "item": {"type": "CommandExecution", "id": identifier,
                                                                    "exit_code": code}}}
            with open(self.fields["transcript_path"], "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")


def summary(output: str) -> str:
    """pytest's own last line, in red or green."""
    last = (output.strip().splitlines() or ["(no output)"])[-1].strip("= ")
    return (RED if "failed" in last or "error" in last else GREEN) + last + RESET


def statusline(percent: int, context: int) -> str:
    payload = {"session_id": "demo", "cwd": os.getcwd(), "context_window": {"used_percentage": context},
               "rate_limits": {"five_hour": {"used_percentage": percent, "resets_at": int(time.time()) + 3600}}}
    return subprocess.run(["agent-autosave", "statusline"], input=json.dumps(payload), stdout=subprocess.PIPE,
                          universal_newlines=True).stdout.strip()


def claude() -> None:
    home = Path(os.environ["HOME"]) / ".claude/projects/demo"
    home.mkdir(parents=True, exist_ok=True)
    agent = Agent("claude", ".claude/settings.json", transcript_path=str(home / "session.jsonl"))
    (home / "session.jsonl").touch()
    say(ORANGE + " ✻ " + RESET + BOLD + "Claude Code" + RESET + DIM + " (stand-in)" + RESET, 0.4)
    agent.emit("SessionStart", source="startup")
    prompt = "Signup must reject an email without an @. Plan it, add a failing test, then fix signup.py."
    say(DIM + "> " + RESET + prompt, 1.0)
    agent.emit("UserPromptSubmit", prompt=prompt)
    say(ORANGE + "● " + RESET + "Recording a 3-step plan", 0.3)
    agent.bash('agent-autosave plan set --quiet "Add a failing test" "Fix valid_email" "Run the suite"')
    say(ORANGE + "● " + RESET + "Write " + BOLD + "tests/test_signup.py" + RESET, 0.6)
    target = Path("tests/test_signup.py")
    target.parent.mkdir(exist_ok=True)
    target.write_text(TEST, encoding="utf-8")
    agent.emit("PostToolUse", tool_name="Write", tool_input={"file_path": str(target.resolve()), "content": TEST},
               tool_use_id="toolu_" + uuid.uuid4().hex[:12], tool_response={"success": True})
    say(ORANGE + "● " + RESET + "Bash " + BOLD + "python3 -m pytest -q" + RESET, 0.2)
    say("  " + summary(agent.bash("python3 -m pytest -q")), 0.6)
    agent.bash("agent-autosave plan done --quiet 1")
    agent.emit("Stop", stop_hook_active=False, last_assistant_message="The failing test is in place.")
    say(DIM + "  " + statusline(88, 63) + RESET, 2.0)
    prompt = "Keep going."
    say(DIM + "> " + RESET + prompt, 0.6)
    ask = agent.emit("UserPromptSubmit", prompt=prompt)
    if ask:
        say(DIM + "  ⎿ " + ask.split(". Before")[0] + "." + RESET, 1.6)
    note = "The test is in and fails. Next: fix valid_email in signup.py."
    say(ORANGE + "● " + RESET + "Bash " + BOLD + 'agent-autosave note --type handoff "The test is in and fails. Next: …"'
        + RESET, 1.0)
    agent.bash("agent-autosave note --quiet --type handoff '{}'".format(note))
    agent.emit("StopFailure", error="rate_limit", error_details="usage limit reached")
    say(RED + "  ⎿ Claude usage limit reached." + RESET, 1.2)


def codex(prompt: str) -> None:
    rollout = Path(os.environ["CODEX_HOME"]) / "sessions/rollout-demo.jsonl"
    rollout.parent.mkdir(parents=True, exist_ok=True)
    rollout.write_text("", encoding="utf-8")
    agent = Agent("codex", ".codex/hooks.json", transcript_path=str(rollout), model="gpt-6-astra",
                  permission_mode="default")
    say(CYAN + ">_ " + RESET + BOLD + "Codex" + RESET + DIM + " (stand-in)" + RESET, 0.4)
    started = agent.emit("SessionStart", source="startup")
    turn = str(uuid.uuid4())
    agent.fields["turn_id"] = turn
    agent.emit("UserPromptSubmit", prompt=prompt)
    say(DIM + "  It starts with the save agent-autosave hands it:" + RESET, 0.6)
    for line in shown(prompt + "\n" + started):
        say("  " + line, 0.5)
    say("", 0.8)
    say(CYAN + "• " + RESET + "Edited " + BOLD + "signup.py" + RESET, 0.6)
    Path("signup.py").write_text(FIXED, encoding="utf-8")
    patch = "*** Begin Patch\n*** Update File: signup.py\n*** End Patch"
    agent.emit("PostToolUse", tool_name="apply_patch", tool_input={"command": patch}, tool_response="",
               tool_use_id="call_" + uuid.uuid4().hex[:12])
    agent.bash("agent-autosave plan done --quiet 2", tool_use_id="exec-" + str(uuid.uuid4()))
    say(CYAN + "• " + RESET + "Ran " + BOLD + "python3 -m pytest -q" + RESET, 0.2)
    say("  " + summary(agent.bash("python3 -m pytest -q", tool_use_id="exec-" + str(uuid.uuid4()))), 0.6)
    agent.bash("agent-autosave plan done --quiet 3", tool_use_id="exec-" + str(uuid.uuid4()))
    agent.emit("Stop", stop_hook_active=False, last_assistant_message="valid_email requires an @; the suite passes.")
    agent.emit("SessionEnd", reason="other")
    say(CYAN + "• " + RESET + "Done: valid_email requires an @; the suite passes.", 1.0)


def shown(context: str) -> list:
    """The lines of Codex's starting context worth a viewer's eye: the
    handoff note, the failing command, the plan and the remembered decision."""
    picked = []
    for line in context.splitlines():
        line = line.strip()
        line = line if len(line) <= 92 else line[:91] + "…"
        if line.startswith(("Handoff notes:", "Failing:", "Plan:", "- decision")) and line not in picked:
            picked.append(line)
    return picked


def main(argv) -> int:
    if argv[:1] == ["--version"]:
        return 1
    who = Path(sys.argv[0]).name
    if who == "codex" or argv[:1] == ["codex"]:
        codex(argv[-1] if argv and argv[-1] != "codex" else "")
    else:
        claude()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
