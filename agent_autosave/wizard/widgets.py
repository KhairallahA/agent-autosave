"""Prompts: text, select, multi-select and confirm.

In a terminal that understands cursor movement they redraw in place and
take arrow keys (↑/↓, or k/j), space to toggle, Enter to accept, and Esc,
q or Ctrl-C to cancel. Otherwise (TERM=dumb, or a Windows console without
virtual-terminal support) they fall back to numbered line prompts.
Standard library only: termios on POSIX, msvcrt on Windows.
"""
from __future__ import annotations

import os
import sys
from typing import Callable, Iterator, Sequence, TextIO

from .screen import Screen


class Cancelled(Exception):
    """The user pressed Esc, q or Ctrl-C."""


class Option:
    def __init__(self, value: str, label: str, detail: str = "", checked: bool = False) -> None:
        self.value, self.label, self.detail, self.checked = value, label, detail, checked


def _windows_vt() -> bool:
    """Turn on ANSI handling in a Windows console; False if it cannot."""
    try:
        import ctypes
        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


class Keys:
    """Key presses from the terminal as names: up, down, space, enter, cancel, backspace, or the character."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = sys.stdin if stream is None else stream

    def __iter__(self) -> Iterator[str]:
        if os.name == "nt":
            yield from self._windows()
        else:
            yield from self._posix()

    def _posix(self) -> Iterator[str]:
        import termios
        import tty
        fd = self.stream.fileno()
        saved = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd, termios.TCSANOW)  # keep keys typed ahead
            # setcbreak keeps ISIG; turn it off so Ctrl-C arrives as a key.
            mode = termios.tcgetattr(fd)
            mode[3] &= ~termios.ISIG
            termios.tcsetattr(fd, termios.TCSANOW, mode)
            while True:
                data = os.read(fd, 1)
                if not data:
                    return  # end of input: the prompt reads it as cancel
                while data[0] >= 0xC0 and len(data) < 4:  # the rest of a UTF-8 character
                    try:
                        data.decode("utf-8")
                        break
                    except UnicodeDecodeError:
                        data += os.read(fd, 1)
                char = data.decode("utf-8", "replace")
                if char == "\x1b":
                    import select
                    ready, _, _ = select.select([fd], [], [], 0.05)
                    if not ready:
                        yield "cancel"
                        continue
                    rest = os.read(fd, 2).decode("utf-8", "replace")
                    yield {"[A": "up", "[B": "down", "[C": "right", "[D": "left", "OA": "up", "OB": "down"}.get(rest, "")
                    continue
                yield self._name(char)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)

    def _windows(self) -> Iterator[str]:
        import msvcrt
        while True:
            char = msvcrt.getwch()  # type: ignore[attr-defined]
            if char in ("\x00", "\xe0"):
                yield {"H": "up", "P": "down", "K": "left", "M": "right"}.get(msvcrt.getwch(), "")  # type: ignore[attr-defined]
                continue
            yield self._name(char)

    @staticmethod
    def _name(char: str) -> str:
        return {"\r": "enter", "\n": "enter", " ": "space", "\x03": "cancel", "\x04": "cancel", "\x7f": "backspace",
                "\x08": "backspace"}.get(char, char)


class Prompter:
    """Asks questions on a Screen, redrawing in place when the terminal allows."""

    def __init__(self, screen: Screen, keys: Callable[[], Iterator[str]] | None = None,
                 interactive: bool | None = None, read_line: Callable[[str], str] = input) -> None:
        self.screen = screen
        self.style = screen.style
        self.keys = keys or (lambda: iter(Keys()))
        if interactive is None:
            interactive = os.environ.get("TERM") != "dumb" and (os.name != "nt" or _windows_vt())
        self.interactive = interactive
        self.read_line = read_line
        self._drawn = 0
        self._stream: Iterator[str] | None = None

    def _key(self) -> str:
        if self._stream is None:
            self._stream = iter(self.keys())
        try:
            return next(self._stream)
        except StopIteration:
            raise Cancelled() from None

    def close(self) -> None:
        """Give the terminal back (cooked mode, cursor shown)."""
        if self._stream is not None and hasattr(self._stream, "close"):
            self._stream.close()  # type: ignore[union-attr]
        self._stream = None
        if self.interactive:
            self.screen.stream.write("\x1b[?25h")
            self.screen.stream.flush()

    # --- drawing

    def _draw(self, lines: Sequence[str]) -> None:
        out = self.screen.stream
        if self._drawn:
            out.write("\x1b[{}F\x1b[J".format(self._drawn))
        out.write("\x1b[?25l" if self._drawn == 0 else "")
        for line in lines:
            out.write(line + "\n")
        out.flush()
        self._drawn = len(lines)

    def _finish(self, title: str, answer: str) -> None:
        self._draw([self.screen.done(title, answer)])
        self.screen.stream.write("\x1b[?25h")
        self.screen.stream.flush()
        self._drawn = 0

    def _abort(self) -> None:
        self.screen.stream.write("\x1b[?25h")
        self.screen.stream.flush()
        self._drawn = 0
        raise Cancelled()

    def _rows(self, options: Sequence[Option], at: int, multi: bool) -> list[str]:
        rail, style = self.screen.rail(), self.style
        rows = []
        for index, option in enumerate(options):
            here = index == at
            mark = (style.paint(style.sym("on"), "green") if option.checked else style.dim(style.sym("off"))) if multi \
                else (style.paint(style.sym("on"), "cyan") if here else style.dim(style.sym("off")))
            pointer = style.paint(style.sym("cursor"), "cyan") if here else " "
            label = style.bold(option.label) if here else option.label
            rows.append("{}  {} {}  {}{}".format(rail, pointer, mark, label,
                                                 "  " + style.dim(option.detail) if option.detail else ""))
        return rows

    # --- widgets

    def select(self, title: str, options: Sequence[Option], default: int = 0) -> str:
        if not self.interactive:
            return self._line_select(title, options, default)
        at = default
        hint = "↑↓ move · enter select" if self.style.sym("on") == "◉" else "up/down move, enter select"
        while True:
            self._draw([self.screen.open(title, hint)] + self._rows(options, at, False))
            while True:
                key = self._key()
                if key in ("up", "k"):
                    at = (at - 1) % len(options)
                elif key in ("down", "j"):
                    at = (at + 1) % len(options)
                elif key == "enter":
                    self._finish(title, options[at].label)
                    return options[at].value
                elif key in ("cancel", "q"):
                    self._abort()
                else:
                    continue
                break

    def multiselect(self, title: str, options: Sequence[Option], *, minimum: int = 0) -> list[str]:
        if not self.interactive:
            return self._line_multiselect(title, options)
        at, warning = 0, ""
        hint = "↑↓ move · space toggle · enter confirm" if self.style.sym("on") == "◉" else \
            "up/down move, space toggle, enter confirm"
        while True:
            extra = [self.screen.rail() + "  " + self.style.paint(warning, "yellow")] if warning else []
            self._draw([self.screen.open(title, hint)] + self._rows(options, at, True) + extra)
            while True:
                key = self._key()
                if key in ("up", "k"):
                    at = (at - 1) % len(options)
                elif key in ("down", "j"):
                    at = (at + 1) % len(options)
                elif key == "space":
                    options[at].checked = not options[at].checked
                    warning = ""
                elif key == "a":
                    everything = not all(o.checked for o in options)
                    for option in options:
                        option.checked = everything
                elif key == "enter":
                    chosen = [o for o in options if o.checked]
                    if len(chosen) < minimum:
                        warning = "Choose at least {} (space toggles).".format(minimum)
                        break
                    self._finish(title, ", ".join(o.label for o in chosen) or "none")
                    return [o.value for o in chosen]
                elif key in ("cancel", "q"):
                    self._abort()
                else:
                    continue
                break

    def confirm(self, title: str, default: bool = True) -> bool:
        if not self.interactive:
            answer = self._ask_line("{} [{}] ".format(title, "Y/n" if default else "y/N")).strip().lower()
            return default if not answer else answer.startswith("y")
        choice = default
        while True:
            yes = self.style.paint("Yes", "bold", "cyan") if choice else self.style.dim("Yes")
            no = self.style.dim("No") if choice else self.style.paint("No", "bold", "cyan")
            self._draw([self.screen.open(title, "y/n · enter"), self.screen.rail() + "  " + yes + " / " + no])
            while True:
                key = self._key()
                if key in ("left", "right", "up", "down", "space"):
                    choice = not choice
                elif key in ("y", "Y"):
                    choice = True
                elif key in ("n", "N"):
                    choice = False
                elif key == "enter":
                    self._finish(title, "yes" if choice else "no")
                    return choice
                elif key in ("cancel", "q"):
                    self._abort()
                else:
                    continue
                break

    def text(self, title: str, default: str = "") -> str:
        if not self.interactive:
            return self._ask_line("{}{} ".format(title, " [{}]".format(default) if default else "")).strip() or default
        value = ""
        while True:
            shown = value or self.style.dim(default)
            self._draw([self.screen.open(title, "enter accept"), self.screen.rail() + "  " +
                        self.style.paint(self.style.sym("cursor"), "cyan") + " " + shown])
            while True:
                key = self._key()
                if key == "enter":
                    self._finish(title, value or default or "(none)")
                    return value or default
                if key == "cancel":
                    self._abort()
                if key == "backspace":
                    value = value[:-1]
                elif len(key) == 1 and key.isprintable():
                    value += key
                else:
                    continue
                break

    # --- line fallback

    def _ask_line(self, prompt: str) -> str:
        try:
            return self.read_line(prompt)
        except (EOFError, KeyboardInterrupt):
            raise Cancelled() from None

    def _line_select(self, title: str, options: Sequence[Option], default: int) -> str:
        self.screen.write([title] + ["  {}. {}{}".format(i + 1, o.label, "  (" + o.detail + ")" if o.detail else "")
                                     for i, o in enumerate(options)])
        while True:
            answer = self._ask_line("Number [{}]: ".format(default + 1)).strip()
            if not answer:
                return options[default].value
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1].value

    def _line_multiselect(self, title: str, options: Sequence[Option]) -> list[str]:
        defaults = [str(i + 1) for i, o in enumerate(options) if o.checked]
        self.screen.write([title] + ["  {}. {}{}".format(i + 1, o.label, "  (" + o.detail + ")" if o.detail else "")
                                     for i, o in enumerate(options)])
        while True:
            answer = self._ask_line("Numbers, comma-separated [{}]: ".format(",".join(defaults) or "none")).strip()
            picked = defaults if not answer else [a.strip() for a in answer.split(",") if a.strip()]
            if all(p.isdigit() and 1 <= int(p) <= len(options) for p in picked):
                return [options[int(p) - 1].value for p in picked]
