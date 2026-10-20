"""Project memory: MEMORY.md (the source of truth, editable by hand) and the
candidates agents propose (.autosave/memory/candidates.jsonl).

MEMORY.md has five sections (Decisions, Dead ends, Gotchas, Conventions,
Expired). Each entry is one bullet line:

    - <text> <!-- m:{"type":...,"source":{...},"added":...,"until":...,"replaces":...} --> ^m-<id>

`^m-<id>` is an Obsidian block id, so journal pages can link to an entry.
Obsidian reads a block id only at the end of the line ("place a blank
space followed by a caret ^ and the block identifier at the end of the
line"; Obsidian Help, Internal links, "Link to a block in a note":
https://help.obsidian.md/links#Link+to+a+block+in+a+note), so the id comes
after the comment. Files written before put the id first; both orders
are read, and the old one is rewritten on the next write.
A bullet typed by hand without the comment is an accepted entry from "you";
its id and comment are added the next time agent-autosave writes the file.
Every line that is not an entry is kept byte for byte, and parsing then
rendering an unchanged file returns the same text.

Nothing here becomes memory without a person: agents and archived notes only
create candidates; `memory add` in a terminal, `memory review` and `memory
accept` turn them into entries.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

TYPES = ("decision", "tried", "gotcha", "convention")
HEADINGS = {"decision": "Decisions", "tried": "Dead ends", "gotcha": "Gotchas", "convention": "Conventions",
            "expired": "Expired"}
SECTIONS = {name: kind for kind, name in HEADINGS.items()}
# Note types that are proposed for memory, and the entry type they become.
NOTE_TYPES = {"decision": "decision", "tried": "tried", "warning": "gotcha"}
TEXT_MAX = 500
TEMPLATE = ("# Project memory\n\n"
            "Decisions, dead ends, gotchas and conventions for this repo. Edit freely: one bullet per entry. "
            "agent-autosave keeps the comment and the block id at the end of each line.\n\n"
            "## Decisions\n\n## Dead ends\n\n## Gotchas\n\n## Conventions\n\n## Expired\n")
_HEADING = re.compile(r"^##[ \t]+(.+?)[ \t]*#*[ \t]*$")
_BULLET = re.compile(r"^[-*+][ \t]+(?P<body>.*?)[ \t]*$")
_META = re.compile(r"[ \t]*<!--[ \t]*m:(?P<meta>\{.*\})[ \t]*-->$")
_ID = re.compile(r"[ \t]+\^m-(?P<id>[0-9a-z]{1,32})$")


class UnknownId(ValueError):
    """An unknown entry or candidate id (the CLI reports unknown_entry)."""


def clean_text(text: str) -> str:
    """One line, at most 500 characters, that cannot fake an id or a meta
    comment: whitespace collapsed, control characters, comment markers and
    trailing block ids removed."""
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(text))
    text = text.replace("<!--", "").replace("-->", "")
    text = re.sub(r"\^m-[0-9a-z]+", "", text)
    return " ".join(text.split())[:TEXT_MAX].strip()


def normalized(text: str) -> str:
    """Lowercase words only: two texts that differ in case or punctuation are duplicates."""
    return " ".join(re.findall(r"[a-z0-9]+", str(text).lower()))


class Entry:
    """One bullet: its text, id and meta; `raw` is the line as read (kept
    when nothing about the entry changed)."""

    def __init__(self, text: str, kind: str, entry_id: str | None, meta: dict[str, Any] | None,
                 raw: str | None = None) -> None:
        self.text, self.kind, self.id, self.raw = text, kind, entry_id, raw
        self.meta = dict(meta) if meta else None
        self.old_order = False  # read as "^m-<id> <!-- m:... -->": rewritten on the next write

    @property
    def expired_section(self) -> bool:
        return self.kind == "expired"

    @property
    def type(self) -> str:
        """The entry's own type; an expired entry keeps the type it had."""
        if self.kind != "expired":
            return self.kind
        value = (self.meta or {}).get("type")
        return value if value in TYPES else "decision"

    def active(self, today: date) -> bool:
        if self.kind == "expired":
            return False
        until = (self.meta or {}).get("until")
        return not (isinstance(until, str) and until < today.isoformat())

    def line(self, newline: str) -> str:
        if self.raw is not None:
            return self.raw
        meta = dict(self.meta or {})
        ordered = {"type": self.type, "source": meta.get("source") or {"agent": "you", "task_id": None},
                   "added": meta.get("added"), "until": meta.get("until"), "replaces": meta.get("replaces")}
        if meta.get("pinned"):
            ordered["pinned"] = True
        comment = json.dumps(ordered, ensure_ascii=False, separators=(",", ":")).replace("-->", "--\\u003e")
        return "- {} <!-- m:{} --> ^m-{}{}".format(self.text, comment, self.id, newline)

    def as_dict(self) -> dict[str, Any]:
        meta = self.meta or {}
        return {"id": "m-" + self.id if self.id else None, "type": self.type, "text": self.text,
                "section": HEADINGS[self.kind], "source": meta.get("source") or {"agent": "you", "task_id": None},
                "added": meta.get("added"), "until": meta.get("until"), "replaces": meta.get("replaces"),
                "pinned": bool(meta.get("pinned"))}


class Document:
    """MEMORY.md as a list of items: a raw line (str) or an Entry."""

    def __init__(self, items: list[Any], newline: str = "\n") -> None:
        self.items, self.newline = items, newline

    @classmethod
    def parse(cls, text: str) -> Document:
        newline = "\r\n" if "\r\n" in text else "\n"
        items: list[Any] = []
        section = None
        for line in text.splitlines(keepends=True):
            bare = line.rstrip("\r\n")
            heading = _HEADING.match(bare)
            if heading:
                section = SECTIONS.get(heading.group(1).strip())
                items.append(line)
                continue
            bullet = _BULLET.match(bare) if section else None
            entry = _entry(bullet.group("body"), section, line) if bullet else None
            items.append(entry if entry is not None else line)
        return cls(items, newline)

    def render(self) -> str:
        return "".join(item.line(self.newline) if isinstance(item, Entry) else item for item in self.items)

    def entries(self) -> list[Entry]:
        return [item for item in self.items if isinstance(item, Entry)]

    def find(self, entry_id: str) -> Entry:
        wanted = str(entry_id).lower()
        wanted = wanted[2:] if wanted.startswith("m-") else wanted
        for entry in self.entries():
            if entry.id == wanted:
                return entry
        raise UnknownId("No memory entry with that id.")

    def ids(self) -> set[str]:
        return {entry.id for entry in self.entries() if entry.id}

    def complete(self, today: date) -> None:
        """Before a write: give hand-typed bullets an id and meta, and move
        entries past their `until` date to Expired."""
        for entry in self.entries():
            if entry.id is None or entry.meta is None:
                if entry.id is None:
                    entry.id = new_id(entry.type + "\0" + entry.text, self.ids())
                entry.meta = dict(entry.meta or {}, type=entry.type, source={"agent": "you", "task_id": None},
                                  added=today.isoformat())
                entry.raw = None
            elif entry.old_order:
                entry.raw, entry.old_order = None, False
            if entry.meta.get("type") != entry.type:
                entry.meta["type"] = entry.type  # moved to another section by hand
                entry.raw = None
            if entry.kind != "expired" and not entry.active(today):
                self.move(entry, "expired")

    def add(self, kind: str, text: str, *, source: dict[str, Any], today: date, until: str | None = None,
            replaces: str | None = None) -> Entry:
        if replaces:
            old = self.find(replaces)
            self.move(old, "expired")
            replaces = old.id
        entry = Entry(text, kind, new_id(kind + "\0" + text, self.ids()),
                      {"type": kind, "source": source, "added": today.isoformat(), "until": until,
                       "replaces": replaces})
        self._insert(entry, kind)
        return entry

    def move(self, entry: Entry, kind: str) -> None:
        if entry.kind == kind:
            return
        meta = dict(entry.meta or {}, type=entry.type)
        self.remove(entry)
        entry.kind, entry.meta, entry.raw = kind, meta, None
        self._insert(entry, kind)

    def remove(self, entry: Entry) -> None:
        """Drop the bullet, and a blank line it leaves doubled."""
        index = self.items.index(entry)
        del self.items[index]
        if 0 < index < len(self.items) and all(isinstance(self.items[i], str) and not self.items[i].strip()
                                               for i in (index - 1, index)):
            del self.items[index]

    def _insert(self, entry: Entry, kind: str) -> None:
        """After the last entry (or the heading) of the section, creating the
        section at the end of the file when it is missing."""
        start = None
        for index, item in enumerate(self.items):
            if isinstance(item, str) and _HEADING.match(item.rstrip("\r\n")):
                if start is not None:
                    break
                if SECTIONS.get(_HEADING.match(item.rstrip("\r\n")).group(1).strip()) == kind:
                    start = index
        if start is None:
            last = self.items[-1] if self.items else self.newline
            if isinstance(last, str) and not last.endswith("\n"):
                self.items[-1] = last + self.newline
            self.items += [self.newline, "## " + HEADINGS[kind] + self.newline, self.newline, entry]
            return
        position = start + 1
        for index in range(start + 1, len(self.items)):
            item = self.items[index]
            if isinstance(item, str) and _HEADING.match(item.rstrip("\r\n")):
                break
            if isinstance(item, Entry) or item.strip():
                position = index + 1
        if position == start + 1:
            # The first entry of a section goes below one blank line.
            if position < len(self.items) and isinstance(self.items[position], str) and not self.items[position].strip():
                position += 1
            else:
                self.items.insert(position, self.newline)
                position += 1
        self.items.insert(position, entry)
        if position + 1 < len(self.items) and isinstance(self.items[position + 1], str) \
                and _HEADING.match(self.items[position + 1].rstrip("\r\n")):
            self.items.insert(position + 1, self.newline)


def _entry(body: str, section: str, line: str) -> Entry | None:
    meta, entry_id, old_order = None, None, False
    found = _ID.search(body)  # the current order: the id last
    if found:
        entry_id = found.group("id")
        body = body[:found.start()]
    found = _META.search(body)
    if found:
        try:
            value = json.loads(found.group("meta"))
            meta = value if isinstance(value, dict) else None
        except ValueError:
            meta = None
        body = body[:found.start()]
    found = _ID.search(body) if entry_id is None else None
    if found:  # the older order: id, then the comment
        entry_id, old_order = found.group("id"), meta is not None
        body = body[:found.start()]
    text = body.strip()
    if not text:
        return None
    entry = Entry(text, section, entry_id, meta, raw=line)
    entry.old_order = old_order
    return entry


def new_id(seed: str, taken: Iterable[str]) -> str:
    """6 hex characters from the text, extended on a collision."""
    taken = set(taken)
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    for size in range(6, len(digest) + 1):
        if digest[:size] not in taken:
            return digest[:size]
    raise ValueError("No free memory id.")


# --- reading and writing MEMORY.md


def read(project: str | Path, config: dict[str, Any]) -> Document:
    """MEMORY.md as it is now; the template when there is none yet."""
    from . import places
    path = places.memory_file(project, config)
    try:
        if path.is_symlink():
            raise OSError("Refusing a symlinked MEMORY.md.")
        return Document.parse(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Document.parse(TEMPLATE)


def write(project: str | Path, config: dict[str, Any], document: Document, today: date) -> Path:
    from . import places
    from .redact import Redactor
    document.complete(today)
    return places.write(project, config, "MEMORY.md", document.render(),
                        redactor=Redactor.from_project(project, config))


def lock(project: str | Path) -> Any:
    """One writer at a time for MEMORY.md and candidates (commands and the sync)."""
    from .locking import exclusive
    from .storage import private_dir
    return exclusive(private_dir(Path(project) / ".autosave" / "memory") / ".lock", timeout=5)


def counts(document: Document, today: date) -> dict[str, int]:
    out = {kind: 0 for kind in TYPES}
    expired = 0
    for entry in document.entries():
        if entry.active(today):
            out[entry.type] += 1
        else:
            expired += 1
    out["expired"] = expired
    return out


# --- candidates


def candidates_path(project: str | Path) -> Path:
    return Path(project) / ".autosave" / "memory" / "candidates.jsonl"


def candidates(project: str | Path) -> list[dict[str, Any]]:
    """Every candidate, oldest first; a damaged line is skipped."""
    try:
        text = candidates_path(project).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("v") == 1 and isinstance(row.get("id"), str):
            rows.append(row)
    return rows


def save_candidates(project: str | Path, rows: list[dict[str, Any]], redactor: Any) -> None:
    from .storage import atomic_write
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    atomic_write(candidates_path(project), text, redactor=redactor)


def pending(project: str | Path) -> list[dict[str, Any]]:
    return [row for row in candidates(project) if row.get("status") == "pending"]


def propose(project: str | Path, config: dict[str, Any], kind: str, text: str, *, source: dict[str, Any],
            created: str, document: Document | None = None, rows: list[dict[str, Any]] | None = None,
            redactor: Any = None, until: str | None = None, replaces: str | None = None) -> dict[str, Any] | None:
    """Add a pending candidate, or None when the text is empty or duplicates
    an entry or any candidate (pending, accepted or rejected). The text is
    redacted before anything is written. With `rows` given the caller saves."""
    from .redact import Redactor
    redactor = redactor or Redactor.from_project(project, config)
    text = clean_text(redactor.text(text))
    if kind not in TYPES or not text:
        return None
    document = document if document is not None else read(project, config)
    own = rows is None
    rows = candidates(project) if own else rows
    key = normalized(text)
    if any(normalized(e.text) == key for e in document.entries()) or any(normalized(r.get("text", "")) == key
                                                                         for r in rows):
        return None
    row = {"v": 1, "id": "c-" + new_id(kind + "\0" + text, {r["id"][2:] for r in rows}), "type": kind,
           "text": text, "source": source, "created": created, "status": "pending"}
    row.update({key: value for key, value in (("until", until), ("replaces", replaces)) if value})
    rows.append(row)
    if own:
        save_candidates(project, rows, redactor)
    return row


def find_candidate(rows: list[dict[str, Any]], candidate_id: str) -> dict[str, Any]:
    wanted = str(candidate_id).lower()
    wanted = wanted if wanted.startswith("c-") else "c-" + wanted
    for row in rows:
        if row["id"] == wanted:
            return row
    raise UnknownId("No candidate with that id.")


# --- operations the commands share (each holds the memory lock)


def _after_write(project: str | Path, config: dict[str, Any], today: date) -> None:
    """MEMORY.md changed: refresh the recall index's memory docs now."""
    from . import recall
    recall.current(project, config, today)


def add_entry(project: str | Path, config: dict[str, Any], kind: str, text: str, *, source: dict[str, Any],
              today: date, until: str | None = None, replaces: str | None = None) -> dict[str, Any]:
    from .redact import Redactor
    text = clean_text(Redactor.from_project(project, config).text(text))
    if kind not in TYPES or not text:
        raise ValueError("A memory entry needs a type and some text.")
    with lock(project):
        document = read(project, config)
        entry = document.add(kind, text, source=source, today=today, until=until, replaces=replaces)
        write(project, config, document, today)
    _after_write(project, config, today)
    return entry.as_dict()


def accept(project: str | Path, config: dict[str, Any], candidate_id: str, *, today: date,
           text: str | None = None) -> dict[str, Any]:
    """A person accepts a candidate (optionally with edited text)."""
    from .redact import Redactor
    redactor = Redactor.from_project(project, config)
    with lock(project):
        rows = candidates(project)
        row = find_candidate(rows, candidate_id)
        if row.get("status") != "pending":
            raise UnknownId("That candidate was already reviewed.")
        final = clean_text(redactor.text(text)) if text is not None else row["text"]
        if not final:
            raise ValueError("A memory entry needs some text.")
        document = read(project, config)
        entry = document.add(row["type"], final, source=row.get("source") or {"agent": "you", "task_id": None},
                             today=today, until=row.get("until"), replaces=row.get("replaces"))
        write(project, config, document, today)
        row.update(status="accepted", entry="m-" + entry.id)
        save_candidates(project, rows, redactor)
    _after_write(project, config, today)
    return entry.as_dict()


def reject(project: str | Path, config: dict[str, Any], candidate_id: str, *, reason: str | None = None) -> dict[str, Any]:
    from .redact import Redactor
    redactor = Redactor.from_project(project, config)
    with lock(project):
        rows = candidates(project)
        row = find_candidate(rows, candidate_id)
        if row.get("status") != "pending":
            raise UnknownId("That candidate was already reviewed.")
        row["status"] = "rejected"
        if reason:
            row["reason"] = clean_text(redactor.text(reason))
        save_candidates(project, rows, redactor)
    return dict(row)


def change_entry(project: str | Path, config: dict[str, Any], entry_id: str, action: str, *,
                 today: date) -> dict[str, Any]:
    """`pin` marks an entry pinned (shown first at session start); `forget`
    deletes its bullet."""
    with lock(project):
        document = read(project, config)
        entry = document.find(entry_id)
        if action == "pin":
            entry.meta = dict(entry.meta or {}, pinned=True)
            entry.raw = None
        else:
            document.remove(entry)
        write(project, config, document, today)
    _after_write(project, config, today)
    return entry.as_dict()
