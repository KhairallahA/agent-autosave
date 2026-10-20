"""A deterministic Markdown view with ordered reductions and a hard size cap."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from . import display
from .config import resolve
from .redact import Redactor

REMINDER = "## Before you continue\nRun `git status` and confirm the files above match. If they don't, trust git over this save.\n"


def _text(value):
    value = " ".join(str(value).splitlines())
    value = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", value)
    value = re.sub(r"([\\`*\[\]<>#])", r"\\\1", value)
    # Intraword underscores (update_plan) cannot start emphasis: leave them.
    return re.sub(r"(?<![^\W_])_|_(?![^\W_])", r"\\_", value)


def _literal(value):
    """One line inside an indented code block: control characters and tabs
    removed (a tab could end the indentation's meaning); nothing else needs
    escaping there."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "", str(value)).rstrip()


def _code(value):
    """Inline code span: Markdown is inert inside it, so no escaping needed."""
    value = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", " ".join(str(value).splitlines()))
    fence = "`" * (max([len(run) for run in re.findall(r"`+", value)] or [0]) + 1)
    pad = " " if value.startswith("`") or value.endswith("`") or not value.strip() else ""
    return fence + pad + value + pad + fence


def _next(save):
    failing = save["failing"]
    if failing and save["next_action"] == "Make `" + failing["command"] + "` pass.":
        return "Make " + _code(failing["command"]) + " pass."
    return _text(save["next_action"])


def _body(save, clock=None):
    clock = clock or (lambda ts: ts)
    status = save["status"]
    stopped = save.get("interrupted")
    detail = status
    if stopped:
        detail += " (" + _text(stopped["reason"]) + " in " + _text(stopped["agent"]) + ")"
    lines = ["<!-- agent-autosave v:1 -->", "# agent-autosave — " + status + " task", "",
             "**Status:** " + detail + ", " + _text(clock(save["updated"])),
             "**Goal:** " + (_text(save["goal"]) or "No substantive prompt yet.")]
    if save.get("latest_request"):
        lines.append("**Latest request:** " + _text(save["latest_request"]))
    lines.append("**User steering:**" + ("" if save["steering"] else " None."))
    lines.extend("- " + _text(x) for x in save["steering"])
    lines.append("")
    if "plan" in save:
        lines.extend(["## Plan"])
        for item in save["plan"]:
            lines.append("- [" + ("x" if item["state"] == "done" else " ") + "] " + _text(item["step"])
                         + (" <- in progress" if item["state"] == "doing" else ""))
        if not save["plan"]:
            lines.append("No plan steps.")
        lines.append("")
    lines.append("## Where it stopped")
    lines.extend(["- " + _text(item) for item in save["last_actions"]] or ["No tool actions recorded."])
    lines.extend(["", "## Failing"])
    failing = save["failing"]
    if failing:
        lines.append(_code(failing["command"]) + " (exit " + str(failing["exit"]) + ")")
        if failing["tail"].strip():
            lines.append("")
        # Indented code cannot close a fence and inject arbitrary headings.
        lines.extend("    " + re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", line)
                     for line in failing["tail"].splitlines() if failing["tail"].strip())
    else:
        lines.append("No unresolved failing command.")
    stuck = save.get("stuck")
    if stuck:
        lines.extend(["", "Stuck: " + _code(stuck["command"]) + " failed {} times since {} with {} edit{} tried. "
                      "Try a different approach before re-running it.".format(
                          stuck["failures"], _text(stuck["since"]), stuck["edits_between"],
                          "" if stuck["edits_between"] == 1 else "s")])
    for command in save.get("unknown_exit") or []:
        lines.append("- " + _code(command) + " (exit unknown: result not reported, not counted as a pass)")
    if save.get("checks"):
        lines.extend(["", "## Checks"])
        lines.extend("- " + _code(c["command"]) + (" passed" if c["exit"] == 0 else " failing (exit {})".format(c["exit"]))
                     + " at " + _text(clock(c["at"])) for c in save["checks"])
    lines.extend(["", "## Files and git"])
    git = save["git"]
    if git:
        where = (" at " + _text(git["head"])) if git.get("head") else ", no commits yet"
        lines.append(("Branch " + _text(git["branch"]) if git.get("branch") else "Detached HEAD") + where
                     + "." + ((" " + _text(git["diffstat"])) if git.get("diffstat") else ""))
    else:
        lines.append("Git state unavailable.")
    lines.append("Files: " + (", ".join(_text(x) for x in save["files_touched"]) or "None recorded") + ".")
    if save.get("files_read"):
        lines.append("Looked at: " + ", ".join(_text(x) for x in save["files_read"]) + ".")
    if save.get("last_report"):
        lines.extend(["", "## Agent's last message"])
        # An indented code block: its lines stay literal, so the message keeps
        # its line breaks and cannot open a heading, fence, list or HTML.
        lines.extend(("    " + _literal(line)).rstrip() for line in str(save["last_report"]).splitlines())
        lines.extend(["", "(" + _text(save.get("last_report_agent") or "") + ", "
                      + _text(save.get("last_report_at") or "") + ")"])
    summary = save.get("summary")
    if summary:
        from .loader import AGENT_NAMES
        lines.extend(["", "## " + summary_heading(summary, AGENT_NAMES)])
        # Indented like the last message: line breaks kept, nothing can
        # open a heading, fence or list.
        lines.extend(("    " + _literal(line)).rstrip() for line in str(summary["text"]).splitlines())
        lines.extend(["", "(" + _text(clock(summary["at"])) + ")"])
    lines.extend(["", "## Decisions"])
    lines.extend(["- " + _text(item) for item in save["decisions"]] or ["None recorded."])
    if save.get("notes"):
        lines.extend(["", "## Notes"])
        lines.extend("- " + item["type"] + ": " + _text(item["text"]) for item in save["notes"])
    return "\n".join(lines).rstrip() + "\n\n"


def _ending(save, next_limit=None):
    action = _next(save) or "Await a substantive task prompt."
    if next_limit is not None and len(action) > next_limit:
        action = _text(save["next_action"])  # a cut code span would not close
        action = action[:max(0, next_limit - 1)] + "…"
    return "## Next action\n" + action + "\n\n" + REMINDER


def render(save: dict[str, Any], config: dict[str, Any] | None = None, *, redactor: Redactor | None = None,
           clock: Any = None) -> str:
    """SAVE.md. `clock` formats shown timestamps (CURRENT.md passes local
    time); by default they stay the recorded UTC values."""
    config = resolve(config)
    limit = min(16000, config["compile"]["save_md_max_chars"])
    view = deepcopy((redactor or Redactor()).value(save))
    view["last_actions"] = display.actions(view)
    for stage in range(5):
        if stage == 1:
            view["last_actions"] = view["last_actions"][:5]
        elif stage == 2:
            view["steering"] = view["steering"][-3:]
        elif stage == 3 and view["failing"]:
            view["failing"]["tail"] = "\n".join(view["failing"]["tail"].splitlines()[-10:])
        elif stage == 4:
            view["files_touched"] = view["files_touched"][:15]
        text = _shown(view, clock) + _ending(view)
        if len(text) <= limit:
            return text
    ending = _ending(view, next_limit=min(512, limit // 4))
    marker = "\n\n[Details truncated to fit the save budget.]\n\n"
    available = max(0, limit - len(ending) - len(marker))
    return _shown(view, clock)[:available].rstrip() + marker + ending


def _shown(view, clock):
    return _body(view, clock) if clock else _body(view)


def _tokens(count: int) -> str:
    return "{:.1f}k".format(count / 1000) if count >= 1000 else str(count)


def summary_heading(summary: dict, names: dict) -> str:
    """"Session summary (Claude Code compacted its context: 39.4k to 3.4k tokens)"."""
    name = names.get(summary.get("agent"), summary.get("agent") or "the agent")
    pre, post = summary.get("pre_tokens"), summary.get("post_tokens")
    if isinstance(pre, int) and isinstance(post, int):
        detail = "{} compacted its context: {} to {} tokens".format(name, _tokens(pre), _tokens(post))
    else:
        detail = "{} compacted its context".format(name)
    return _text("Session summary (" + detail + ")")
