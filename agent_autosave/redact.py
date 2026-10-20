"""Deterministic redaction before persistence; no source contents are retained."""
from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any, Iterable

_MASK = re.compile(r"(\[redacted:[a-z_]+\])")
_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:[A-Z0-9 ]* )?PRIVATE KEY-----[\s\S]*?(?:-----END (?:[A-Z0-9 ]* )?PRIVATE KEY-----|\Z)")),
    ("url_credentials", re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/@]+@")),
    ("api_key", re.compile(r"(?<![\w-])sk-[A-Za-z0-9_-]{8,}")),
    ("github", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,})")),
    ("slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}")),
    ("aws", re.compile(r"\bAKIA[A-Z0-9]{16}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")),
    ("bearer", re.compile(r"(?i)\bbearer[ \t]+[A-Za-z0-9._~+/=-]+")),
    ("assignment", re.compile(
        r"(?i)\b[\w.-]*(?:password|passwd|secret|token|api[_-]?key)[\w.-]*[\"']?\s*[:=]\s*"
        r'''(?:"(?:[^"\\]|\\[\s\S])*"|'(?:[^'\\]|\\[\s\S])*'|[^\s,;}&\]]+)''')),
)


def _scoped(pattern):
    # Global inline flags are not allowed mid-pattern; scope them instead.
    return "(?i:" + pattern[4:] + ")" if pattern.startswith("(?i)") else "(?:" + pattern + ")"


# One pass that answers "could any built-in pattern change this text?".
# If it finds nothing, every substitution below would be the identity.
_ANY = re.compile("|".join(_scoped(pattern.pattern) for _, pattern in _PATTERNS))
_CACHE_LIMIT = 4096


def _outside_masks(text, transform):
    if "[redacted:" not in text:
        return transform(text)
    return "".join(part if index % 2 else transform(part)
                   for index, part in enumerate(_MASK.split(text)))


def env_values(project: str | Path) -> list[str]:
    """Read .env* assignments literally, including quoted multiline values.

    Never execute expansions or follow symlinks. Refuse oversized inputs instead
    of silently omitting secrets. Exclude dependency, build, git, and state trees.
    """
    values = set()
    total = 0
    for root, directories, files in os.walk(project, followlinks=False):
        directories[:] = sorted(d for d in directories
                                if d not in {".git", ".autosave", "node_modules", "dist", ".venv"}
                                and not d.startswith(".venv-")
                                and not (Path(root) / d).is_symlink())
        for name in sorted(files):
            if not name.startswith(".env"):
                continue
            path = Path(root) / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("Unsafe .env path; refusing to persist unredacted data.")
            total += path.stat().st_size
            if total > 1024 * 1024:
                raise ValueError("Environment files exceed the redaction scan limit.")
            lines = iter(path.read_text(encoding="utf-8").splitlines())
            for line in lines:
                match = re.match(r"\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*(.*)", line)
                if not match:
                    continue
                value = match.group(1).strip()
                if value.startswith(('"', "'")):
                    quote = value[0]
                    # A backslash-escaped quote belongs to a double-quoted
                    # value, not its delimiter. Single quotes remain literal.
                    pattern = r'"(?:[^"\\]|\\[\s\S])*"' if quote == '"' else r"'[^']*'"
                    closing = re.match(pattern, value)
                    while closing is None:
                        try:
                            value += "\n" + next(lines)
                        except StopIteration:
                            raise ValueError("Unterminated .env value.") from None
                        closing = re.match(pattern, value)
                    value = closing[0][1:-1]
                    if quote == '"':
                        value = re.sub(r'\\([nrt"\\])',
                                       lambda match: {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}[match[1]], value)
                else:
                    value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
                if value:
                    values.add(value)
    return sorted(values, key=lambda value: (-len(value), value))


def _home_prefixes():
    home = str(Path.home())
    prefixes = {home, os.path.realpath(home), home.replace("\\", "/")}
    return sorted((p.rstrip("/\\") for p in prefixes if p and p not in ("/", "\\")), key=len, reverse=True)


def shorten_home(value: Any) -> Any:
    """Replace the user's home directory (any spelling) with "~" in strings."""
    if isinstance(value, dict):
        return {key: shorten_home(item) for key, item in value.items()}
    if isinstance(value, list):
        return [shorten_home(item) for item in value]
    if not isinstance(value, str):
        return value
    for prefix in _home_prefixes():
        value = re.sub(re.escape(prefix) + r"(?=[/\\]|$|[\s\"'])", "~", value)
    return value


class Redactor:
    def __init__(self, *, literals: Iterable[str] = (), extra_patterns: Iterable[str] = ()) -> None:
        self.literals = tuple(sorted(set(literals) - {""}, key=lambda value: (-len(value), value)))
        self.extra = tuple(re.compile(pattern) for pattern in extra_patterns)
        self._cache = {}

    @classmethod
    def from_project(cls, project: str | Path, config: dict[str, Any]) -> Redactor:
        options = config["redact"]
        return cls(literals=env_values(project) if options["redact_env_values"] else (),
                   extra_patterns=options["extra_patterns"])

    def text(self, text: str) -> str:
        # Pure function of (text, configuration), so memoizing is deterministic.
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        result = self._text(text)
        if len(self._cache) < _CACHE_LIMIT:
            self._cache[text] = result
        return result

    def _text(self, text):
        if (not self.extra and _ANY.search(text) is None
                and not any(literal in text for literal in self.literals)):
            return text
        for literal in self.literals:
            text = _outside_masks(text, lambda part: part.replace(literal, "[redacted:env]"))
        for kind, pattern in _PATTERNS:
            if kind == "url_credentials":
                replacement = lambda match: match[0].split("://", 1)[0] + "://[redacted:url_credentials]@"
            else:
                replacement = "[redacted:" + kind + "]"
            text = _outside_masks(text, lambda part: pattern.sub(replacement, part))
        for pattern in self.extra:
            text = _outside_masks(text, lambda part: pattern.sub("[redacted:custom]", part))
        return text

    def value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {self.text(key): self.value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.value(item) for item in value]
        return value
