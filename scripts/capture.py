"""Drive a terminal program through a pseudo-terminal and capture what a user sees.

Development tool, not part of the package: it needs `pyte` and `Pillow`
(`python -m pip install pyte pillow` in a scratch virtualenv). POSIX only.

A capture is a list of steps:

    ("expect", "regex")   wait until the screen matches (10 s timeout)
    ("keys", "text")      type: "\\r" Enter, " " space, KEYS["down"] arrows ...
    ("sleep", 0.5)        wait
    ("snap", "name")      save the current screen as <out>/<name>.png

`run()` also returns the timed output, which `cast()` writes as an
asciicast v2 file for `agg` to render as a GIF.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import time
from typing import Any, Sequence

KEYS = {"up": "\x1b[A", "down": "\x1b[B", "right": "\x1b[C", "left": "\x1b[D", "enter": "\r", "space": " "}
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
BACKGROUND = (24, 24, 27)
FOREGROUND = (228, 228, 231)
# The 16 ANSI colors (a dark theme), then pyte's names for them.
ANSI = [(39, 39, 42), (248, 113, 113), (74, 222, 128), (250, 204, 21), (96, 165, 250), (192, 132, 252),
        (34, 211, 238), (228, 228, 231), (113, 113, 122), (252, 165, 165), (134, 239, 172), (253, 224, 71),
        (147, 197, 253), (216, 180, 254), (103, 232, 249), (250, 250, 250)]
NAMES = ["black", "red", "green", "brown", "blue", "magenta", "cyan", "white"]


class Session:
    def __init__(self, argv: Sequence[str], *, cwd: str | Path, env: dict[str, str] | None = None,
                 columns: int = 96, rows: int = 30) -> None:
        import fcntl
        import pyte
        import termios
        self.columns, self.rows = columns, rows
        self.screen = pyte.Screen(columns, rows)
        self.stream = pyte.ByteStream(self.screen)
        self.events: list[tuple[float, str]] = []
        self.started = time.monotonic()
        environment = dict(os.environ, TERM="xterm-256color", COLUMNS=str(columns), LINES=str(rows))
        environment.pop("NO_COLOR", None)
        environment.update(env or {})
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(str(cwd))
            os.execvpe(argv[0], list(argv), environment)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))

    def pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            ready, _, _ = select.select([self.fd], [], [], max(0.0, min(0.05, end - time.monotonic())))
            if not ready:
                continue
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                return
            if not data:
                return
            self.stream.feed(data)
            self.events.append((time.monotonic() - self.started, data.decode("utf-8", "replace")))

    def text(self) -> str:
        return "\n".join(line.rstrip() for line in self.screen.display)

    def expect(self, pattern: str, timeout: float = 10.0) -> None:
        end = time.monotonic() + timeout
        while not re.search(pattern, self.text()):
            if time.monotonic() > end:
                raise TimeoutError("{!r} not on screen:\n{}".format(pattern, self.text()))
            self.pump(0.05)

    def keys(self, text: str) -> None:
        for char in [text] if text.startswith("\x1b") else list(text):
            os.write(self.fd, char.encode())
            self.pump(0.04)

    def close(self) -> int:
        self.pump(0.3)
        try:
            _, status = os.waitpid(self.pid, os.WNOHANG)
            if _ == 0:
                os.kill(self.pid, signal.SIGHUP)
                for _attempt in range(20):
                    _, status = os.waitpid(self.pid, os.WNOHANG)
                    if _:
                        break
                    time.sleep(0.05)
                else:
                    os.kill(self.pid, signal.SIGKILL)
                    _, status = os.waitpid(self.pid, 0)
        except ChildProcessError:
            return 0
        return os.waitstatus_to_exitcode(status)


def _color(value: str, default: tuple[int, int, int], bold: bool = False) -> tuple[int, int, int]:
    if value == "default":
        return default
    if value in NAMES:
        return ANSI[NAMES.index(value) + (8 if bold else 0)]
    if value.startswith("bright"):
        name = value[len("bright"):]
        return ANSI[NAMES.index(name) + 8] if name in NAMES else default
    if re.fullmatch(r"[0-9a-fA-F]{6}", value):
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    return default


def snapshot(session: Session, path: str | Path, *, title: str = "") -> None:
    """Render the screen as a PNG with a window frame."""
    from PIL import Image, ImageDraw, ImageFont
    size = 15
    font, bold = ImageFont.truetype(FONT, size), ImageFont.truetype(FONT_BOLD, size)
    cell_w = int(font.getlength("M"))
    cell_h = int(size * 1.35)
    used = max((i for i, line in enumerate(session.screen.display) if line.strip()), default=0) + 2
    rows = min(session.rows, used)
    pad, bar = 18, 30
    image = Image.new("RGB", (session.columns * cell_w + 2 * pad, rows * cell_h + 2 * pad + bar), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, image.width, bar], fill=(39, 39, 42))
    for i, color in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        draw.ellipse([12 + i * 20, 10, 24 + i * 20, 22], fill=color)
    if title:
        draw.text((image.width / 2, bar / 2), title, fill=(161, 161, 170), font=font, anchor="mm")
    for y in range(rows):
        line = session.screen.buffer[y]
        for x in range(session.columns):
            char = line[x]
            fg = _color(char.fg, FOREGROUND, char.bold)
            bg = _color(char.bg, BACKGROUND)
            if char.reverse:
                fg, bg = bg, fg
            left, top = pad + x * cell_w, bar + pad + y * cell_h
            if bg != BACKGROUND:
                draw.rectangle([left, top, left + cell_w, top + cell_h], fill=bg)
            if char.data.strip():
                draw.text((left, top + 2), char.data, fill=fg, font=bold if char.bold else font)
    image.save(str(path))


def run(argv: Sequence[str], steps: Sequence[tuple[str, Any]], *, cwd: str | Path, out: str | Path,
        env: dict[str, str] | None = None, title: str = "", columns: int = 96, rows: int = 30) -> Session:
    Path(out).mkdir(parents=True, exist_ok=True)
    session = Session(argv, cwd=cwd, env=env, columns=columns, rows=rows)
    session.pump(0.3)
    for action, value in steps:
        if action == "expect":
            session.expect(value)
        elif action == "keys":
            session.keys(value)
        elif action == "sleep":
            session.pump(value)
        elif action == "snap":
            session.pump(0.2)
            snapshot(session, Path(out) / (value + ".png"), title=title)
    session.status = session.close()
    return session


def cast(sessions: Sequence[Session], path: str | Path, *, columns: int = 96, rows: int = 30,
         pause: float = 1.5) -> None:
    """Join sessions into one asciicast v2 file, with a pause between them."""
    lines = [json.dumps({"version": 2, "width": columns, "height": rows})]
    offset = 0.0
    for number, session in enumerate(sessions):
        if number:  # a clear screen between sessions; the last one stays on screen
            lines.append(json.dumps([round(offset, 3), "o", "\x1b[2J\x1b[H"]))
        last = 0.0
        for at, text in session.events:
            lines.append(json.dumps([round(offset + at, 3), "o", text]))
            last = at
        offset += last + pause
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
