"""Installed-agent detection: read-only, bounded, never raises."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from typing import Any, Callable, Mapping

from ..timeouts import bound


def claude_code(*, which: Callable[[str], str | None] = shutil.which, home: str | Path | None = None,
                timeout: float = 2.0) -> dict[str, Any]:
    """Return {'installed', 'version', 'path'} for Claude Code."""
    binary = which("claude")
    home = Path.home() if home is None else Path(home)
    installed = bool(binary) or (home / ".claude").is_dir()
    version = None
    if binary:
        try:
            result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=bound(timeout))
            version = (result.stdout.strip().split() or [None])[0] if result.returncode == 0 else None
        except Exception:
            version = None
    return {"installed": installed, "version": version, "path": binary}


def hook_command(*, which: Callable[[str], str | None] = shutil.which) -> str:
    """Absolute path of the console script, so GUI-launched agents find it."""
    return which("agent-autosave") or "agent-autosave"


def codex_home(home: str | Path | None = None, env: Mapping[str, str] | None = None) -> Path:
    """$CODEX_HOME, else ~/.codex (same rule as Codex)."""
    import os
    env = os.environ if env is None else env
    if env.get("CODEX_HOME"):
        return Path(env["CODEX_HOME"]).expanduser()
    return (Path.home() if home is None else Path(home)) / ".codex"


def codex(*, which: Callable[[str], str | None] = shutil.which, home: str | Path | None = None,
          env: Mapping[str, str] | None = None, timeout: float = 2.0) -> dict[str, Any]:
    """Return {'installed', 'version', 'path'} for the Codex CLI."""
    binary = which("codex")
    installed = bool(binary) or codex_home(home, env).is_dir()
    version = None
    if binary:
        try:
            result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=bound(timeout))
            # "codex-cli 0.130.0" -> "0.130.0"
            words = result.stdout.strip().split()
            version = words[-1] if result.returncode == 0 and words else None
        except Exception:
            version = None
    return {"installed": installed, "version": version, "path": binary}


def _trust_entries(text):
    """{project path: trust_level} from config.toml `[projects."<path>"]` tables."""
    try:
        import tomllib  # Python 3.11+
        projects = tomllib.loads(text).get("projects") or {}
        return {str(k): str((v or {}).get("trust_level", "")) for k, v in projects.items() if isinstance(v, dict)}
    except ImportError:
        pass
    except ValueError:
        return {}
    import re
    entries, current = {}, None
    for line in text.splitlines():
        line = line.strip()
        header = re.match(r'^\[projects\.(?:"((?:\\.|[^"\\])*)"|\'([^\']*)\')\]$', line)
        if header:
            current = header[1].replace("\\\\", "\\").replace('\\"', '"') if header[1] is not None else header[2]
            entries.setdefault(current, "")
            continue
        if line.startswith("["):
            current = None
            continue
        value = re.match(r'^trust_level\s*=\s*["\']([^"\']*)["\']', line)
        if current is not None and value:
            entries[current] = value[1]
    return entries


def codex_trusted(project: str | Path, *, home: str | Path | None = None, env: Mapping[str, str] | None = None) -> bool:
    """True if Codex config marks `project` (or a parent) trusted. Read-only."""
    path = codex_home(home, env) / "config.toml"
    try:
        entries = _trust_entries(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return False
    project = Path(project).resolve()
    for candidate in (project, *project.parents):
        for key, level in entries.items():
            try:
                same = Path(key).expanduser().resolve() == candidate
            except (OSError, RuntimeError, ValueError):
                same = False
            if same:
                return level == "trusted"
    return False


def _binary(names, *, which=shutil.which, timeout=2.0):
    for name in names:
        binary = which(name)
        if binary:
            version = None
            try:
                result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=bound(timeout))
                words = result.stdout.strip().split()
                version = words[-1] if result.returncode == 0 and words else None
            except Exception:
                version = None
            return binary, version
    return None, None


def cursor(*, which: Callable[[str], str | None] = shutil.which, home: str | Path | None = None,
           timeout: float = 2.0) -> dict[str, Any]:
    """Cursor: the `cursor` or `cursor-agent` binary, or a ~/.cursor directory."""
    binary, version = _binary(("cursor", "cursor-agent"), which=which, timeout=timeout)
    home = Path.home() if home is None else Path(home)
    return {"installed": bool(binary) or (home / ".cursor").is_dir(), "version": version, "path": binary}


def gemini(*, which: Callable[[str], str | None] = shutil.which, timeout: float = 2.0) -> dict[str, Any]:
    """Gemini CLI: binary only (~/.gemini is shared with Antigravity)."""
    binary, version = _binary(("gemini",), which=which, timeout=timeout)
    return {"installed": bool(binary), "version": version, "path": binary}


def antigravity(*, which: Callable[[str], str | None] = shutil.which, timeout: float = 2.0) -> dict[str, Any]:
    """Antigravity CLI: the `agy` binary only."""
    binary, version = _binary(("agy",), which=which, timeout=timeout)
    return {"installed": bool(binary), "version": version, "path": binary}


def copilot(*, which: Callable[[str], str | None] = shutil.which, timeout: float = 2.0) -> dict[str, Any]:
    """GitHub Copilot CLI: the `copilot` binary."""
    binary, version = _binary(("copilot",), which=which, timeout=timeout)
    return {"installed": bool(binary), "version": version, "path": binary}


def opencode(*, which: Callable[[str], str | None] = shutil.which, timeout: float = 2.0) -> dict[str, Any]:
    """OpenCode: the `opencode` binary."""
    binary, version = _binary(("opencode",), which=which, timeout=timeout)
    return {"installed": bool(binary), "version": version, "path": binary}


def all_agents() -> dict[str, dict[str, Any]]:
    """Detection for every adapter; generic is never auto-detected."""
    return {"claude-code": claude_code(), "codex": codex(), "cursor": cursor(), "gemini": gemini(),
            "antigravity": antigravity(), "copilot": copilot(), "opencode": opencode(),
            "generic": {"installed": False, "version": None, "path": None}}
