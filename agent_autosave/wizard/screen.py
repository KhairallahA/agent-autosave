"""The wizard's look: banner and the left rail of steps.

    ◇  open question            ◆  answered question  …  answer
    │  body text                └  end of the wizard
"""
from __future__ import annotations

from typing import Iterable, TextIO

from ..ui import Style

TAGLINE = "your agents' work, saved between sessions"


def banner(style: Style, version: str) -> list[str]:
    """A small framed title; no ASCII art, so it fits any width."""
    title = " agent-autosave "
    rule = style.sym("rule") * (len(title) + 2)
    corners = ("╭", "╮", "╰", "╯") if style.sym("rule") == "─" else ("+", "+", "+", "+")
    return ["",
            "  " + style.paint(corners[0] + rule + corners[1], "cyan"),
            "  " + style.paint(style.sym("bar"), "cyan") + " " + style.paint(title, "bold") + " " +
            style.paint(style.sym("bar"), "cyan") + "  " + style.dim("v" + version),
            "  " + style.paint(corners[2] + rule + corners[3], "cyan") + "  " + style.dim(TAGLINE),
            ""]


class Screen:
    """Writes the rail to one stream."""

    def __init__(self, style: Style, stream: TextIO) -> None:
        self.style = style
        self.stream = stream

    def write(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.stream.write(line + "\n")
        self.stream.flush()

    def rail(self) -> str:
        return self.style.dim(self.style.sym("bar"))

    def open(self, title: str, help_text: str = "") -> str:
        return self.style.paint(self.style.sym("open"), "cyan") + "  " + self.style.bold(title) + (
            "  " + self.style.dim(help_text) if help_text else "")

    def done(self, title: str, answer: str) -> str:
        return self.style.paint(self.style.sym("step"), "green") + "  " + title + "  " + self.style.dim("…") + "  " + \
            self.style.bold(answer)

    def section(self, title: str, body: Iterable[str] = ()) -> None:
        """A heading on the rail with body lines under it."""
        lines = [self.rail(), self.style.paint(self.style.sym("open"), "cyan") + "  " + self.style.bold(title)]
        lines += [self.rail() + "  " + line if line else self.rail() for line in body]
        self.write(lines)

    def note(self, level: str, text: str) -> None:
        self.write([self.rail() + "  " + self.style.mark(level) + " " + text])

    def body(self, lines: Iterable[str]) -> None:
        self.write([self.rail() + "  " + line if line else self.rail() for line in lines])

    def end(self, text: str = "") -> None:
        self.write([self.style.dim(self.style.sym("end")) + ("  " + text if text else "")])
