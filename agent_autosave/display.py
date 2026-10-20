"""How commands and messages are shown (SAVE.md, the resume context, status):
short, without temp paths, cut at a word boundary. Pure: no clock, filesystem
or environment; the project root comes from the caller (the compile inputs).
The ledger and save.last_actions keep the full text."""
from __future__ import annotations

import re

ELLIPSIS = "\u2026"
ACTION_MAX = 100
_PATH = re.compile(r"/[^\s'\";|&<>()`]*")
# macOS and Linux temp dirs, and the per-user claude-NNN and scratchpad dirs agents work in.
_TEMP = re.compile(r"^/(?:private/)?(?:tmp|var/folders)(?:/|$)|/claude-[0-9]+(?:/|$)|/scratchpad(?:/|$)")
_SUFFIX = re.compile(r" \(exit [^)]*\)(?: x[0-9]+)?$")


def shorten(text: str, limit: int) -> str:
    """At most `limit` characters; a longer text is cut at the last word
    boundary that fits and ends with an ellipsis."""
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    space = max(cut.rfind(" "), cut.rfind("\n"))
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip() + ELLIPSIS


def command(text: str, root: str | None = None, limit: int | None = None) -> str:
    """A command for display: the project root made relative, temp paths as
    <tmp>, whitespace collapsed, and (with `limit`) shortened."""
    root = (root or "").rstrip("/")

    def path(match):
        value = match.group(0)
        if root and (value == root or value.startswith(root + "/")):
            return value[len(root) + 1:] or "."
        return "<tmp>" if _TEMP.search(value) else value

    out = " ".join(_PATH.sub(path, text).split())
    return shorten(out, limit) if limit else out


def action(text: str, root: str | None = None) -> str:
    """A last-actions line ("Ran <command> (exit N) x3", "Edited <path>"):
    the command part shown with command(), at most ACTION_MAX characters;
    the exit and repeat suffix are kept whole."""
    suffix = _SUFFIX.search(text)
    tail = suffix.group(0) if suffix else ""
    head = text[:len(text) - len(tail)]
    verb = ""
    for word in ("Ran ", "Edited ", "Wrote ", "Used "):
        if head.startswith(word):
            verb, head = word, head[len(word):]
            break
    return verb + command(head, root, ACTION_MAX - len(verb)) + tail


def actions(save: dict) -> list:
    """The save's last actions as shown: last_actions_shown when the compiler
    wrote it for these actions (with the project root), else the stored ones cleaned without it."""
    stored = save.get("last_actions") or []
    shown = save.get("last_actions_shown")
    if isinstance(shown, list) and len(shown) == len(stored):
        return shown
    return [action(item) for item in stored]


def report(text: str, limit: int = 600) -> str:
    """The agent's last message for the save: spaces collapsed inside each
    line, line breaks kept (at most one blank line in a row), shortened."""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    kept: list[str] = []
    for line in lines:
        if line or (kept and kept[-1]):
            kept.append(line)
    return shorten("\n".join(kept).strip(), limit)


def plain(text: str) -> str:
    """One line without Markdown markers (code fences, bold, a leading list
    dash or heading hashes), for quoting in the context."""
    out = []
    for line in text.splitlines():
        line = line.replace("```", "").replace("**", "").strip()
        line = re.sub(r"^(?:[-*+] |#{1,6} )", "", line).strip()
        if line:
            out.append(line)
    return " ".join(out)
