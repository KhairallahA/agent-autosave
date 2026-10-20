#!/usr/bin/env python3
"""Copy a Codex rollout as a test fixture, scrubbed; or check fixtures that
came from real recordings. Read-only on the source. Standard library only.

    python3 scripts/scrub-rollout.py SOURCE.jsonl OUT.jsonl [--keep-prompt TEXT ...]
    python3 scripts/scrub-rollout.py --check FILE ...

Removed by default:
- account and user ids: any key ending in user_id, account_id, org_id,
  organization_id, workspace_id or team_id (creator_user_id, ...);
- encrypted content (encrypted_content, reasoning items) and the model
  catalog;
- base, user, system and developer instructions: session_meta instructions
  and git, developer and system messages, injected user context (AGENTS.md
  instructions, <environment_context>, <permissions ...> and the like),
  turn_context, world_state and thread_settings_applied rows, the same
  inside a compaction's replacement_history;
- plugin and skill names (they come with the instructions and world_state);
- every path outside the scratch repo (the session's cwd): it becomes its
  last component, so `/elsewhere/a/b/check` is `check`;
- every user prompt except the ones passed with --keep-prompt (the scripted
  test prompts), which become "[prompt removed]";
- then the home directory is shown as "~" and secrets are redacted.

--check reports what is left of that list in any fixture (hook payloads,
ledgers, rollouts) and exits 1 when anything is.
"""
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ID_KEY = re.compile(r"(?:^|_)(?:user|account|org|organization|workspace|team)_id$")
DROP_KEYS = frozenset(("encrypted_content", "base_instructions", "user_instructions", "developer_instructions",
                       "instructions", "git", "model_catalog", "approved_command_prefixes", "permission_profile"))
DROP_ROWS = frozenset(("turn_context", "world_state"))
DROP_EVENTS = frozenset(("thread_settings_applied",))
INJECTED = re.compile(r"^\s*(?:<[a-z_ -]+>|# AGENTS\.md instructions)")
MARKERS = re.compile(r"<(?:permissions instructions|skills_instructions|collaboration_mode|multi_agent_[a-z]+|"
                     r"environment_context|INSTRUCTIONS)>|# AGENTS\.md instructions|^## Memory$|plugins/cache/|"
                     r"MEMORY_SUMMARY|\bgAAAAAB[A-Za-z0-9_-]{20,}", re.M)
PATH = re.compile(r"(?:~(?=[/\\])|/Users|/home|/private|/var|/tmp|/Volumes|/mnt|[A-Za-z]:\\\\?Users)(?:[/\\][^\s\"'`<>(),;:\]\[|]*)?")
# The scratch repos record mode runs in (scripts/record-*.sh): fixture files without a cwd use these.
RECORDING_SCRATCH = ("/private/tmp/aa-codex", "/tmp/aa-codex", "/private/tmp/aa-claude", "/tmp/aa-claude",
                     "/private/tmp/aa-cursor", "/tmp/aa-cursor")
REMOVED_PROMPT = "[prompt removed]"
# Agent-owned data folders a hook payload names (the transcript path): never a project of the user.
AGENT_DIRS = ("~/.claude/projects/", "~/.codex/sessions/", "[redacted:env]/.claude/projects/", "/private/tmp/claude-",
              "~/.gemini/antigravity-cli/brain/", "~/.gemini/antigravity/brain/",
              "/tmp/claude-")


def inside(path, roots):
    path = path.rstrip("/\\.")
    return any(path == r or path.startswith(r + "/") for r in roots) or path.startswith(AGENT_DIRS)


def scratch_roots(cwd):
    """The scratch repo as it may be spelled: /private/tmp/x and /tmp/x on macOS."""
    roots = {cwd.rstrip("/")} if cwd else set()
    for root in list(roots):
        if root.startswith("/private/tmp/"):
            roots.add(root[len("/private"):])
        elif root.startswith("/tmp/"):
            roots.add("/private" + root)
    return {r for r in roots if r.count("/") >= 2}


def _paths(text, roots):
    return PATH.sub(lambda m: m.group(0) if inside(m.group(0), roots) else re.split(r"[/\\]", m.group(0).rstrip("/\\"))[-1], text)


def _value(value, roots, dropped):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in DROP_KEYS or ID_KEY.search(key):
                dropped[key] = dropped.get(key, 0) + 1
                continue
            out[key] = _value(item, roots, dropped)
        return out
    if isinstance(value, list):
        return [_value(item, roots, dropped) for item in value]
    if isinstance(value, str):
        return _paths(value, roots)
    return value


def _message(item, keep, dropped):
    """A message item with injected context and unlisted prompts removed, or None."""
    if not isinstance(item, dict) or item.get("type") == "reasoning":
        dropped["reasoning"] = dropped.get("reasoning", 0) + (1 if isinstance(item, dict) else 0)
        return None
    role = item.get("role")
    if role in ("developer", "system"):
        dropped["message:" + role] = dropped.get("message:" + role, 0) + 1
        return None
    if role == "user" and isinstance(item.get("content"), list):
        content = []
        for part in item["content"]:
            text = part.get("text") if isinstance(part, dict) else None
            if isinstance(text, str) and INJECTED.match(text):
                dropped["injected user context"] = dropped.get("injected user context", 0) + 1
                continue
            if isinstance(text, str) and text not in keep:
                dropped["prompt"] = dropped.get("prompt", 0) + 1
                part = dict(part, text=REMOVED_PROMPT)
            content.append(part)
        if not content:
            return None
        item = dict(item, content=content)
    return item


def scrub_rows(rows, keep_prompts=()):
    """(scrubbed rows, what was dropped) for a list of parsed rollout rows."""
    from agent_autosave.redact import Redactor, shorten_home
    keep, dropped, kept = set(keep_prompts), {}, []
    cwd = next((r.get("payload", {}).get("cwd") for r in rows if r.get("type") == "session_meta"), "") or ""
    roots = scratch_roots(cwd)
    redactor = Redactor()
    for row in rows:
        kind, payload = row.get("type"), row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if kind in DROP_ROWS or (kind == "event_msg" and payload.get("type") in DROP_EVENTS):
            key = kind if kind in DROP_ROWS else kind + ":" + payload["type"]
            dropped[key] = dropped.get(key, 0) + 1
            continue
        if kind == "response_item":
            payload = _message(payload, keep, dropped)
            if payload is None:
                continue
            row = dict(row, payload=payload)
        if kind == "compacted":
            history = [m for m in (_message(m, keep, dropped) for m in payload.get("replacement_history") or [])
                       if m is not None]
            row = dict(row, payload=dict(payload, replacement_history=history))
        if kind == "event_msg" and payload.get("type") in ("user_message",) and payload.get("message") not in keep:
            row = dict(row, payload=dict(payload, message=REMOVED_PROMPT))
            dropped["prompt"] = dropped.get("prompt", 0) + 1
        kept.append(redactor.value(shorten_home(_value(row, roots, dropped))))
    return kept, dropped


def problems(value, roots, where=""):
    """Every item from the scrub list still present in `value` (any JSON)."""
    found = []
    if isinstance(value, dict):
        if value.get("type") in DROP_ROWS:
            found.append((where, "row type " + value["type"]))
        if value.get("role") in ("developer", "system"):
            found.append((where, value["role"] + " message"))
        for key, item in value.items():
            if (key in DROP_KEYS and key != "git") or ID_KEY.search(key):  # "git" is also agent-autosave's own save field
                found.append((where + "." + key, "forbidden key"))
            found += problems(item, roots, where + "." + key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += problems(item, roots, "{}[{}]".format(where, index))
    elif isinstance(value, str):
        if MARKERS.search(value):
            found.append((where, "instructions, plugins or encrypted content"))
        for match in PATH.finditer(value):
            if not inside(match.group(0), roots):
                found.append((where, "path outside the scratch repo"))
                break
    return found


def check_file(path):
    """Problems in one fixture file: JSON, or JSON lines."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        docs = [json.loads(text)]
    except ValueError:
        docs = [json.loads(line) for line in text.splitlines() if line.strip()]
    cwds = set()

    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("cwd", "workspace_root") and isinstance(item, str):
                    cwds.add(item)
                elif key == "workspacePaths" and isinstance(item, list):
                    cwds.update(x for x in item if isinstance(x, str))
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    collect(docs)
    roots = set(RECORDING_SCRATCH)  # ledgers carry no cwd; recordings always ran in /tmp/aa-*
    for cwd in cwds:
        roots |= scratch_roots(cwd.replace("file://", ""))
    return [(where or "$", why) for doc in docs for where, why in problems(doc, roots)]


def main(argv):
    if argv[:1] == ["--check"]:
        bad = 0
        for path in argv[1:]:
            for where, why in check_file(path):
                bad += 1
                print("{}: {} ({})".format(path, why, where))  # never the value
        print("checked {} files: {} problems".format(len(argv) - 1, bad))
        return 1 if bad else 0
    keep, files = [], []
    args = iter(argv)
    for arg in args:
        if arg == "--keep-prompt":
            keep.append(next(args))
        else:
            files.append(arg)
    if len(files) != 2:
        sys.exit(__doc__)
    rows = [json.loads(line) for line in Path(files[0]).expanduser().read_text(encoding="utf-8").splitlines()
            if line.strip()]
    kept, dropped = scrub_rows(rows, keep)
    Path(files[1]).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept), encoding="utf-8")
    left = [p for r in kept for p in problems(r, scratch_roots(
        next((x.get("payload", {}).get("cwd") for x in rows if x.get("type") == "session_meta"), "") or ""))]
    print("kept {} lines; removed {}; problems left {}".format(len(kept), json.dumps(dropped, sort_keys=True), len(left)))
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
