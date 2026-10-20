"""Terminal output style shared by every command and the init wizard.

One `Style` decides colour and symbols. It is styled only when stdout is a
terminal; `NO_COLOR` (any value) or `TERM=dumb` turns colour off and
`FORCE_COLOR` turns it on (for screenshots and tests). Symbols fall back to
ASCII when the output encoding cannot print them (old Windows consoles).

The hook path never imports this module.
"""
from __future__ import annotations

import os
import sys
from typing import Iterable, Mapping, Sequence, TextIO

# 16-colour SGR codes, so the user's terminal theme picks the actual colours.
CODES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "blue": "34",
         "magenta": "35", "cyan": "36"}
UNICODE = {"ok": "✓", "warn": "!", "fail": "✗", "info": "•", "arrow": "→", "skip": "–", "add": "+",
           "same": "=", "step": "◆", "open": "◇", "bar": "│", "end": "└", "on": "◉", "off": "○",
           "cursor": "›", "rule": "─"}
ASCII = {"ok": "+", "warn": "!", "fail": "x", "info": "*", "arrow": "->", "skip": "-", "add": "+",
         "same": "=", "step": "*", "open": "o", "bar": "|", "end": "`", "on": "[x]", "off": "[ ]",
         "cursor": ">", "rule": "-"}
# Level -> (symbol, colour) for status lines.
LEVELS = {"ok": ("ok", "green"), "warn": ("warn", "yellow"), "fail": ("fail", "red"), "info": ("info", "cyan"),
          "skip": ("skip", "dim"), "add": ("add", "green"), "same": ("same", "dim")}


def color_wanted(stream: TextIO | None = None, env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    if env.get("NO_COLOR"):
        return False
    if env.get("FORCE_COLOR"):
        return True
    if env.get("TERM") == "dumb":
        return False
    stream = sys.stdout if stream is None else stream
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def unicode_ok(stream: TextIO | None = None) -> bool:
    stream = sys.stdout if stream is None else stream
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        "".join(UNICODE.values()).encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


class Style:
    """Colours and symbols for one output stream."""

    def __init__(self, color: bool = False, unicode: bool = True, width: int = 100) -> None:
        self.color = color
        self.width = max(40, width)
        self.symbols = UNICODE if unicode else ASCII

    def paint(self, text: str, *names: str) -> str:
        if not self.color or not names or not text:
            return text
        return "\033[{}m{}\033[0m".format(";".join(CODES[name] for name in names), text)

    def bold(self, text: str) -> str:
        return self.paint(text, "bold")

    def dim(self, text: str) -> str:
        return self.paint(text, "dim")

    def sym(self, name: str) -> str:
        return self.symbols[name]

    def mark(self, level: str) -> str:
        symbol, colour = LEVELS[level]
        return self.paint(self.symbols[symbol], colour)

    def heading(self, title: str, detail: str = "") -> str:
        """`agent-autosave status  · detail`: the first line of every command."""
        text = self.paint(title, "bold", "cyan")
        return text + ("  " + self.dim(detail) if detail else "")

    def section(self, title: str) -> str:
        return self.bold(title)

    def fields(self, rows: Sequence[tuple[str, str]], indent: int = 2) -> list[str]:
        """Aligned `label   value` rows; continuation lines keep the value column."""
        width = max((len(label) for label, _ in rows), default=0)
        out = []
        for label, value in rows:
            pad = " " * indent
            lines = []
            room = self.width - indent - width - 2
            for line in str(value).split("\n"):
                plain_line = _plain(line)
                if len(plain_line) > room and plain_line == line:
                    import textwrap
                    lines.extend(textwrap.wrap(line, room, break_on_hyphens=False) or [""])
                else:
                    lines.append(line)
            out.append(pad + self.dim(label.ljust(width)) + "  " + lines[0])
            out.extend(pad + " " * (width + 2) + line for line in lines[1:])
        return out

    def item(self, level: str, text: str, indent: int = 2) -> str:
        return " " * indent + self.mark(level) + " " + text

    def hint(self, text: str, indent: int = 4) -> str:
        return " " * indent + self.dim("hint:") + " " + text

    def command(self, text: str) -> str:
        return self.paint(text, "cyan")

    def table(self, rows: Iterable[Sequence[str]], indent: int = 2) -> list[str]:
        rows = [list(r) for r in rows]
        if not rows:
            return []
        widths = [max(len(_plain(r[i])) for r in rows) for i in range(len(rows[0]) - 1)]
        out = []
        for row in rows:
            cells = [cell + " " * (widths[i] - len(_plain(cell))) for i, cell in enumerate(row[:-1])]
            out.append(" " * indent + "  ".join(cells + [row[-1]]))
        return out


def _plain(text: str) -> str:
    import re
    return re.sub(r"\033\[[0-9;]*m", "", text)


def style(stream: TextIO | None = None) -> Style:
    """The style for stdout (or `stream`) in this process."""
    import shutil
    return Style(color_wanted(stream), unicode_ok(stream), min(100, shutil.get_terminal_size((100, 24)).columns))


def plain(text: str) -> str:
    """`text` without colour codes."""
    return _plain(text)


def when(timestamp: object) -> str:
    """An ISO UTC timestamp as local `YYYY-MM-DD HH:MM` plus the zone name."""
    from datetime import datetime
    try:
        moment = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return str(timestamp)
    if moment.tzinfo is None:
        return str(timestamp)
    local = moment.astimezone()
    return local.strftime("%Y-%m-%d %H:%M ") + (local.tzname() or "")


STATUS_COLORS = {"active": "green", "paused": "yellow", "interrupted": "red", "done": "blue"}
