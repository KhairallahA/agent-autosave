"""Drive capture.handle in-process with recorded-shape payloads, then read the save.

Each hook call goes through the real entry point (`capture.handle`) exactly as
an agent's hook would, so these scenarios cover normalizers, TaskStore and the
compiler together. SessionEnd settles synchronously (no detached git refresh).
"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import uuid

from agent_autosave import capture, cli
from agent_autosave.installer.install import install
from tests.helpers import FIXTURES, load_payload

ENV = dict(os.environ, AGENT_AUTOSAVE_SESSION_END="sync")
ENV.pop("AGENT_AUTOSAVE_RECORD", None)


class Terminal(io.StringIO):
    def isatty(self):
        return True


class Repo:
    def __init__(self, root, agents=("claude-code",)):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True)
        install(self.root, list(agents), command="agent-autosave")

    # --- hooks
    def send(self, agent, payload, *, fresh_ids=True):
        """One hook call. Each call gets fresh vendor ids, as a real agent's
        would (fixtures reuse theirs); `fresh_ids=False` replays a recording."""
        payload = dict(payload, cwd=str(self.root))
        if fresh_ids:
            for key in ("tool_use_id", "prompt_id", "turn_id", "generation_id"):
                if key in payload:
                    payload[key] = key + "-" + uuid.uuid4().hex
        return capture.handle(agent, payload["hook_event_name"], json.dumps(payload).encode(), cwd=self.root, env=ENV)

    def claude(self, name, session="s1", **fields):
        payload = load_payload("claude-code", name)
        for key in ("file_path", "notebook_path"):
            if isinstance(payload.get("tool_input"), dict) and key in payload["tool_input"]:
                payload["tool_input"][key] = str(self.root / Path(payload["tool_input"][key]).name)
        payload.update(session_id=session, **fields)
        return self.send("claude-code", payload)

    def prompt(self, text, session="s1", agent="claude-code", **fields):
        name = "UserPromptSubmit"
        payload = load_payload(agent, name)
        payload.update(prompt=text, session_id=session, **fields)
        return self.send(agent, payload)

    def bash(self, command, exit=0, session="s1", **fields):
        if exit:
            payload = load_payload("claude-code", "PostToolUseFailure")
            payload["error"] = "Exit code {}\nboom".format(exit)
        else:
            payload = load_payload("claude-code", "PostToolUse-Bash")
        payload["tool_input"] = dict(payload["tool_input"], command=command)
        payload.update(session_id=session, **fields)
        return self.send("claude-code", payload)

    def tool(self, tool, tool_input, session="s1", **fields):
        payload = load_payload("claude-code", "PostToolUse-Edit")
        payload.update(tool_name=tool, tool_input=tool_input, tool_response={}, session_id=session, **fields)
        return self.send("claude-code", payload)

    def stop(self, message="", session="s1"):
        payload = load_payload("claude-code", "Stop")
        payload.update(last_assistant_message=message, session_id=session)
        return self.send("claude-code", payload)

    def start(self, session, source="startup"):
        payload = load_payload("claude-code", "SessionStart-" + {"startup": "startup", "clear": "clear",
                                                                    "compact": "compact"}.get(source, "startup"))
        payload.update(session_id=session, source=source)
        output = self.send("claude-code", payload)
        return json.loads(output)["hookSpecificOutput"]["additionalContext"] if output else ""

    def end(self, session="s1"):
        payload = load_payload("claude-code", "SessionEnd")
        payload.update(session_id=session)
        return self.send("claude-code", payload)

    # --- the CLI, in-process, JSON envelope
    def cli(self, *args):
        out = io.StringIO()
        cwd = os.getcwd()
        os.chdir(str(self.root))
        try:
            with contextlib.redirect_stdout(out):
                # show --context answers in JSON when stdout is not a terminal
                # (--json with --context is a usage error); everything else
                # is asked explicitly so --quiet still prints the envelope.
                code = cli.main(list(args) if "--context" in args else ["--json"] + list(args))
        finally:
            os.chdir(cwd)
        return code, json.loads(out.getvalue())

    def text(self, *args):
        """The CLI as a person sees it (a terminal, no colour): exit code and text."""
        out = Terminal()
        cwd = os.getcwd()
        os.chdir(str(self.root))
        try:
            with contextlib.redirect_stdout(out):
                code = cli.main(list(args))
        finally:
            os.chdir(cwd)
        return code, out.getvalue()

    def save(self):
        return json.loads((self.root / ".autosave/save.json").read_text(encoding="utf-8"))

    def markdown(self):
        return (self.root / ".autosave/SAVE.md").read_text(encoding="utf-8")


def codex_recording(name):
    return json.loads((FIXTURES / "codex/sessions" / name).read_text(encoding="utf-8"))["payload"]
