"""Shared test builders: ledger events, fixture payloads and file snapshots."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

START = "2026-10-14T12:00:00Z"
# The CLI as a subprocess, with lenient timeouts (tests/tools/lenient_cli.py).
LENIENT_CLI = [sys.executable, str(Path(__file__).resolve().parent / "tools" / "lenient_cli.py")]


def event(kind, n=0, **payload):
    ts = (datetime(2026, 10, 14, 12, tzinfo=timezone.utc) + timedelta(seconds=n)).isoformat().replace("+00:00", "Z")
    return dict({"v": 1, "ts": ts, "agent": "claude-code", "session": "session-a",
                 "turn": n + 1, "kind": kind}, **payload)


def tool(n=0, **payload):
    return event("tool", n, **dict({"tool": "Bash", "summary": "pytest", "files": [], "exit": 0}, **payload))


# --- files and fixtures

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"
CMD = "/opt/my tools/agent-autosave"
ALL = ("claude-code", "codex", "cursor", "gemini", "antigravity", "generic")

# A named payload that is byte-identical to one file of a recorded session is
# not stored twice: the name points at the recording.
RECORDED = {
    "claude-code": {
        "SessionStart-startup": "sessions/lifecycle/SessionStart-31f495d22b60",
        "SessionStart-compact": "sessions/lifecycle/SessionStart-bea063bd7b85",
        "SessionStart-clear": "sessions/lifecycle/SessionStart-32e9d26f5fa7",
    },
    "codex": {
        "SessionStart-startup": "sessions/plan-handoff/SessionStart-0bc14bc0efc7",
        "SessionStart-resume": "sessions/resume-quit/SessionStart-a3af2f1b9847",
        "UserPromptSubmit": "sessions/plan-handoff/UserPromptSubmit-17be2b700d80",
        "PostToolUse-apply_patch": "sessions/plan-handoff/PostToolUse-506d7a4a54c5",
        "PostToolUse-Bash": "sessions/plan-handoff/PostToolUse-7fb2dfe329e2",
        "PostToolUse-Bash-fail": "sessions/plan-handoff/PostToolUse-66beb010f031",
        "Stop": "sessions/plan-handoff/Stop-9f956362cdb2",
        "PreCompact": "sessions/plan-handoff/PreCompact-cf4e6dc3cc66",
        "SessionEnd": "sessions/plan-handoff/SessionEnd-536a2b45037e",
    },
}


def payload_path(agent, name):
    """The fixture file for a named payload (payloads/<name>.json or its recording)."""
    return FIXTURES / agent / (RECORDED.get(agent, {}).get(name, "payloads/" + name) + ".json")


def payload_names(agent):
    """Every named payload of an agent."""
    stored = {path.stem for path in (FIXTURES / agent / "payloads").glob("*.json")}
    return sorted(stored | set(RECORDED.get(agent, {})))


def load_envelope(agent, name):
    return json.loads(payload_path(agent, name).read_text(encoding="utf-8"))


def load_payload(agent, name):
    return load_envelope(agent, name)["payload"]


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and ".autosave" not in p.parts}


def user_files(project):
    """Pre-existing user config for every file an adapter touches (CRLF and JSONC included)."""
    write(project / ".claude/settings.json", b'{\r\n  // mine\r\n  "model": "opus",\r\n  "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say hi"}]}]}\r\n}\r\n')
    write(project / "CLAUDE.md", b"# Claude rules\r\n")
    write(project / ".codex/hooks.json", b'{"hooks": {}}\n')
    write(project / "AGENTS.md", b"# Shared rules")
    write(project / ".cursor/hooks.json", b'{\n  "version": 1,\n  "hooks": {"stop": [{"command": "./audit.sh"}]}\n}\n')
    write(project / "GEMINI.md", b"Use tabs.\n")
    write(project / ".gitignore", b"node_modules/\n")
    write(project / "README.md", b"readme\n")
