"""Run agent-autosave for real and paste its output into the docs.

    python scripts/build-docs.py           # rewrite every output block
    python scripts/build-docs.py --check   # exit 1 if a block is stale

A block in any Markdown file under docs/ or README.md looks like:

    <!-- run: status -->
    ```console
    $ agent-autosave status
    (output)
    ```
    <!-- end run -->

`run: <name>` names a step of the tour below. The tour runs each command in
a pseudo-terminal (so the output is what a person sees, with NO_COLOR) in a
scratch git repository with a scratch home folder, and keeps the output
verbatim except for four substitutions: the scratch home becomes `~`, the
project becomes `~/projects/hello`, this Python becomes `python3`, and the
id of a task started during the run (made from the current time) becomes
t_20261015_031500000000.

The machine has no Claude Code or Codex, so the tour puts stand-ins on PATH
that report the versions the fixtures were recorded with (Claude Code
2.1.291, Codex 0.162.1); the `codex` stand-in, run with no arguments, adds
the folder to Codex's trusted projects as accepting Codex's prompt does.
The agent's work is the real Claude Code session replayed in
examples/claude-to-codex, appended to the ledger. Its timestamps are from
October 2026, so the tour sets inject.stale_after_hours high enough that the
save never reads as stale when the docs are rebuilt later. POSIX only.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/claude-to-codex/.autosave/events.jsonl"
BLOCK = re.compile(r"(<!-- run: (?P<name>[\w-]+) -->\n)```console\n.*?```\n(<!-- end run -->)", re.S)
CLAUDE_VERSION, CODEX_VERSION = "2.1.291", "0.162.1"
TASK_ID = "t_20261015_015800000000"


def pty_run(argv: list, cwd: Path, env: dict, keys: bytes = b"") -> tuple:
    """(exit code, output) of argv in a pseudo-terminal, 100 columns wide."""
    import fcntl
    import pty
    import struct
    import termios
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(str(cwd))
        os.execvpe(argv[0], argv, env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
    if keys:
        os.write(fd, keys)
    out = b""
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            try:
                data = os.read(fd, 65536)
            except OSError:
                data = b""
            if not data:
                break
            out += data
    else:
        os.kill(pid, 9)  # hung, e.g. waiting for a key: fail instead of blocking
    _, status = os.waitpid(pid, 0)
    os.close(fd)
    return os.waitstatus_to_exitcode(status), out.decode("utf-8", "replace").replace("\r\n", "\n")


TOUR_ENV = {"TERM": "xterm-256color", "NO_COLOR": "1", "COLUMNS": "100", "LANG": "C.UTF-8", "TZ": "UTC"}
# Lenient timeouts: on a loaded machine a 150 ms git call or a 2 s --version
# check can time out, which logs to errors.log (shown by `find .autosave` and
# doctor) or drops a version from init's output; the docs must not depend on
# load (agent_autosave/timeouts.py). Every `agent-autosave` the tour runs is
# this wrapper, which turns the setting on before calling the CLI.
LENIENT_CLI = ROOT / "tests/tools/lenient_cli.py"


class Tour:
    def __init__(self, scratch: Path) -> None:
        self.scratch = scratch
        self.home = scratch / "home"
        self.project = scratch / "hello"
        self.bin = self.home / ".local/bin"
        self.bin.mkdir(parents=True)
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.project)], check=True)
        self.env = dict(TOUR_ENV, HOME=str(self.home), CODEX_HOME=str(self.home / ".codex"),
                        PATH=str(self.bin) + os.pathsep + "/usr/bin:/bin", PYTHONPATH=str(ROOT))
        self._stub("agent-autosave", 'exec "{}" "{}" "$@"'.format(sys.executable, LENIENT_CLI))
        self._stub("claude", 'echo "{} (Claude Code)"'.format(CLAUDE_VERSION))
        self._stub("codex", '[ "$1" = --version ] && {{ echo "codex-cli {}"; exit 0; }}\n'
                            'mkdir -p "$CODEX_HOME"; touch "$CODEX_HOME/config.toml"\n'
                            'grep -qF "[projects.\\"$(pwd -P)\\"]" "$CODEX_HOME/config.toml" || '
                            'printf \'[projects."%s"]\\ntrust_level = "trusted"\\n\' "$(pwd -P)" >> "$CODEX_HOME/config.toml"'
                   .format(CODEX_VERSION))
        self.outputs: dict = {}

    def _stub(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        path.chmod(0o755)

    def clean(self, text: str) -> str:
        for real, shown in ((str(self.project.resolve()), "~/projects/hello"), (str(self.project), "~/projects/hello"),
                            (str(self.home.resolve()), "~"), (str(self.home), "~"), (sys.executable, "python3")):
            text = text.replace(real, shown)
        text = re.sub(r"t_(?!20261015_015800000000)\d{8}_\d{12}", "t_20261015_031500000000", text)
        text = re.sub(r'"elapsed_ms": \d+', '"elapsed_ms": 3', text)
        # Memory entries are dated the day they are accepted, and the vault
        # folder ends in a hash of the (temporary) repo path.
        today = datetime.now(timezone.utc).date().isoformat()
        text = re.sub(re.escape(today) + r"T\d\d:\d\d:\d\d(\.\d+)?Z", "2026-10-15T03:15:00.000000Z", text)
        text = re.sub(re.escape(today) + r" \d\d:\d\d\b", "2026-10-15 03:15", text)  # local "YYYY-MM-DD HH:MM"
        text = text.replace(today, "2026-10-15")
        text = re.sub(r"\bhello-[0-9a-f]{4}\b", "hello-5d2e", text)
        text = re.sub(r"\bsaved \d+s ago\b", "saved 4s ago", text)
        text = re.sub(r"usage limit at \d\d:\d\d", "usage limit at 03:15", text)
        return "\n".join(line.rstrip() for line in text.rstrip("\n").split("\n"))

    def run(self, name: str, command: str, *, cwd: Path | None = None, expect: int = 0, argv: list | None = None) -> str:
        """Run `command` (or `argv`, shown as `command`) and keep its output under `name`."""
        import shlex
        code, out = pty_run(argv or shlex.split(command), cwd or self.project, self.env)
        if code != expect:
            raise SystemExit("{}: `{}` exited {} (expected {}):\n{}".format(name, command, code, expect, out))
        self.outputs[name] = "$ {}\n{}".format(command, self.clean(out))
        return out

    def seed(self, part: str) -> None:
        """Append the replayed Claude Code session (part="claude") or Codex's pick-up (part="codex")."""
        rows = [json.loads(line) for line in EXAMPLE.read_text(encoding="utf-8").splitlines()]
        first = next(i for i, r in enumerate(rows) if r["agent"] == "codex")
        rows = rows[:first] if part == "claude" else rows[first:]
        directory = self.project / ".autosave"
        slots = json.loads((directory / "task.json").read_text(encoding="utf-8"))
        slots["slots"][slots["current"]].update(task_id=TASK_ID, started="2026-10-15T01:58:00.000000Z")
        (directory / "task.json").write_text(json.dumps(slots), encoding="utf-8")
        with open(directory / "events.jsonl", "a", encoding="utf-8") as ledger:
            for row in rows:
                ledger.write(json.dumps(dict(row, task_id=TASK_ID), sort_keys=True) + "\n")
        config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        config["inject"]["stale_after_hours"] = 10 ** 6
        (directory / "config.json").write_text(json.dumps(config), encoding="utf-8")


def tour(scratch: Path) -> dict:
    from agent_autosave import timeouts
    timeouts.LENIENT = True  # for anything the tour runs in process
    t = Tour(scratch)
    t.run("version", "agent-autosave --version")
    wizard = scratch / "wizard"
    wizard.mkdir()
    subprocess.run(["git", "init", "-q", str(wizard)], check=True)
    # The wizard with its defaults (Enter on every question; Codex is opened
    # to trust the folder). Its screens are images in the docs, not text.
    code, _ = pty_run(["agent-autosave", "init"], wizard, t.env, keys=b"\r" * 9)
    if code != 0 or not (wizard / ".codex/hooks.json").is_file():
        raise SystemExit("wizard: `agent-autosave init` did not finish with its defaults")
    t.outputs["wizard"] = "$ agent-autosave init"
    t.run("init", "agent-autosave init --yes")
    t.run("init-again", "agent-autosave init --yes")
    subprocess.run(["codex"], cwd=str(t.project), env=t.env, check=True, capture_output=True)  # trust the folder
    t.run("add-codex", "agent-autosave add codex")
    t.run("doctor", "agent-autosave doctor")
    t.seed("claude")
    t.run("status-paused", "agent-autosave status")
    t.run("show", "agent-autosave show")
    t.run("show-context", "agent-autosave show --context")
    t.run("show-md", "agent-autosave show --md")
    t.run("status-json", "agent-autosave status --json")
    t.seed("codex")
    t.run("status-handoff", "agent-autosave status")
    t.run("resume-dry-run", "agent-autosave resume claude-code --dry-run")
    t.run("error-resume-cursor", "agent-autosave resume cursor", expect=2)
    t.run("resume-codex", "agent-autosave resume codex")
    t.run("note", 'agent-autosave note "Keep hello.py printing exactly hello autosave" --type decision')
    t.run("plan-set", 'agent-autosave plan set "Write hello.py" "Run it" "Add a failing check"')
    t.run("plan-done", "agent-autosave plan done 1")
    t.run("clear", "agent-autosave clear")
    t.run("history", "agent-autosave history")
    t.run("journal", "agent-autosave journal")
    out = t.run("memory-review", "agent-autosave memory review",
                argv=["sh", "-c", "agent-autosave memory review </dev/null"])  # the list; a terminal then walks it
    candidate = re.search(r"\bc-[0-9a-f]{6,}\b", out).group(0)
    t.run("memory-accept", "agent-autosave memory accept " + candidate)
    t.run("memory-list", "agent-autosave memory list")
    t.run("memory-file", "cat .autosave/MEMORY.md")
    t.run("remove-codex", "agent-autosave remove codex")
    elsewhere = scratch / "elsewhere"
    elsewhere.mkdir()
    t.run("error-not-set-up", "agent-autosave status", cwd=elsewhere, expect=1)
    t.run("error-agent-missing", "agent-autosave init --agents gemini", cwd=elsewhere, expect=1)
    t.run("error-usage", "agent-autosave history --limit 0", expect=2)
    t.run("help", "agent-autosave --help")
    # The rest of the verbs, on the fresh task left by `clear`.
    t.run("save", "agent-autosave save")
    t.run("save-note", 'agent-autosave save --note "Paused before the failing check"')
    t.run("doctor-prune", "agent-autosave doctor --prune")
    t.run("upgrade-dry-run", "agent-autosave upgrade --dry-run")
    t.run("upgrade", "agent-autosave upgrade --yes")
    t.run("record-on", "agent-autosave record on")
    t.run("record-off", "agent-autosave record off")
    t.run("redacted", "grep -o 'staging key is [^\"]*' .autosave/events.jsonl",
          argv=["sh", "-c", 'agent-autosave note "The staging key is sk-ant-api03-' + "a" * 40 + '" --type warning >/dev/null'
                ' && grep -o "staging key is [^\\"]*" .autosave/events.jsonl'])
    # Troubleshooting: the hook command is gone (an IDE that cannot see it fails the same way).
    shim = t.bin / "agent-autosave"
    shim.rename(t.bin / "agent-autosave.moved")
    t.run("doctor-no-command", "agent-autosave doctor", expect=1, argv=[sys.executable, str(LENIENT_CLI), "doctor"])
    (t.bin / "agent-autosave.moved").rename(shim)
    # The wizard again in a set-up folder, with its defaults (screens are images).
    code, _ = pty_run(["agent-autosave", "init", "--reconfigure"], t.project, t.env, keys=b"\r" * 9)
    if code != 0:
        raise SystemExit("reconfigure: `agent-autosave init --reconfigure` did not finish with its defaults")
    t.outputs["reconfigure"] = "$ agent-autosave init --reconfigure"
    t.run("tree", "find .autosave | sort", argv=["sh", "-c", "find .autosave -not -name '.*.lock' | sort"])
    # Troubleshooting: Codex hooks in two layers.
    for name in ("first", "second"):
        folder = scratch / name
        folder.mkdir()
        subprocess.run(["git", "init", "-q", str(folder)], check=True)
        subprocess.run(["codex"], cwd=str(folder), env=t.env, check=True, capture_output=True)
    t.run("layers-user-level", "agent-autosave init --agents codex --user-level", cwd=scratch / "first")
    t.run("layers-warning", "agent-autosave init --agents codex", cwd=scratch / "second")
    # The vault: point vault.path at an Obsidian folder, then move the journal and MEMORY.md there.
    (t.home / "Obsidian").mkdir()
    config_path = t.project / ".autosave/config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["vault"] = {"path": "~/Obsidian", "project": ""}
    config_path.write_text(json.dumps(config), encoding="utf-8")
    t.run("vault-move", "agent-autosave vault move")
    t.run("vault-move-yes", "agent-autosave vault move --yes")
    t.run("vault-journal", "agent-autosave journal")
    t.run("vault-home", "cat ~/Obsidian/agent-autosave/README.md",
          argv=["cat", str(t.home / "Obsidian/agent-autosave/README.md")])
    switching(t)
    t.run("remove-all", "agent-autosave remove --all")
    return t.outputs


# The claude stand-in for `go`: a prompt, then Claude Code's StopFailure hook
# as it fires on a usage limit (`error` from code.claude.com/docs/en/hooks).
LIMITED_CLAUDE = ('[ "$1" = --version ] && {{ echo "{} (Claude Code)"; exit 0; }}\n'
                  'hook() {{ printf \'{{"session_id":"tour-limit","transcript_path":"","cwd":"%s",'
                  '"hook_event_name":"%s",%s}}\' "$(pwd)" "$1" "$2" | agent-autosave hook claude-code "$1"; }}\n'
                  'hook UserPromptSubmit \'"prompt":"Add a failing check for hello.py in tests/test_hello.py"\' >/dev/null\n'
                  'hook StopFailure \'"error":"rate_limit","last_assistant_message":"Usage limit reached."\' >/dev/null'
                  ).format(CLAUDE_VERSION)


def switching(t: Tour) -> None:
    """Memory back in .autosave/, then go, the status line, watch and team sharing."""
    config_path = t.project / ".autosave/config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["vault"] = dict(config["vault"], path="")
    config_path.write_text(json.dumps(config), encoding="utf-8")
    t._stub("claude", LIMITED_CLAUDE)
    code, out = pty_run(["agent-autosave", "go", "claude-code", "codex"], t.project, t.env, keys=b"\r")
    if code != 0 or "Press Enter to continue in Codex" not in out:
        raise SystemExit("go: no switch prompt:\n" + out)
    # The pty echoes the Enter (sent up front) before the prompt; show it where a person sees it.
    shown = t.clean(out).lstrip("\n").replace("or q to stop. ", "or q to stop.\n")
    t.outputs["go"] = "$ agent-autosave go claude-code codex\n" + shown
    t._stub("claude", 'echo "{} (Claude Code)"'.format(CLAUDE_VERSION))
    t.run("resume-next", "agent-autosave resume --next")
    t.run("note-handoff", 'agent-autosave note "Next: add the failing check to tests/test_hello.py" --type handoff')
    t.run("statusline", "agent-autosave statusline",
          argv=["sh", "-c", "echo '{\"rate_limits\": {\"five_hour\": {\"used_percentage\": 72}}, "
                "\"context_window\": {\"used_percentage\": 41}}' | agent-autosave statusline"])
    t.run("watch-once", "agent-autosave watch --once")
    code, _ = pty_run(["agent-autosave", "watch"], t.project, t.env, keys=b"q")
    if code != 0:
        raise SystemExit("watch: q did not quit it")
    t.outputs["watch"] = "$ agent-autosave watch"
    t.run("share-on", "agent-autosave share on")
    t.run("share-off", "agent-autosave share off")
    t.run("handoff-export", "agent-autosave handoff export")
    t.run("handoff-import", "agent-autosave handoff import")


def documents() -> list:
    return [ROOT / "README.md"] + sorted((ROOT / "docs").rglob("*.md"))


def rendered(outputs: dict) -> dict:
    """{path: new text} for every document with output blocks."""
    out = {}
    for path in documents():
        text = path.read_text(encoding="utf-8")

        def fill(match):
            name = match.group("name")
            if name not in outputs:
                raise SystemExit("{}: unknown run block {!r}".format(path.relative_to(ROOT), name))
            return match.group(1) + "```console\n" + outputs[name] + "\n```\n" + match.group(3)
        new = BLOCK.sub(fill, text)
        if new != text or BLOCK.search(text):
            out[path] = new
    return out


def build() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        return rendered(tour(Path(tmp).resolve()))


def main(argv: list) -> int:
    if os.name == "nt" or not shutil.which("git"):
        print("needs a POSIX system with git")
        return 2
    stale = []
    for path, text in build().items():
        if path.read_text(encoding="utf-8") != text:
            stale.append(str(path.relative_to(ROOT)))
            if "--check" not in argv:
                path.write_text(text, encoding="utf-8")
    if "--check" in argv:
        print("stale output blocks in: " + ", ".join(stale) if stale else "every output block is current")
        return 1 if stale else 0
    print("rewrote " + (", ".join(stale) or "nothing (already current)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
