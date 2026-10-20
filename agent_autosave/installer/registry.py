"""Reference-counted registry of user-level hook files shared by installs.

One user-level file (today: Codex's ~/.codex/hooks.json) can serve many
repos. Each repo that relies on it is listed here; `remove` takes the hooks
out only with the last live install. An install whose repo no longer has
.autosave/install.json counts as gone (stale): doctor lists those.

Location: $XDG_CONFIG_HOME/agent-autosave/installs.json, else
~/.config/agent-autosave/installs.json. Writes are atomic and locked.
Format: {"v": 1, "files": {hooks_file: {"agent", "change", "installs":
[{"repo", "agent", "date"}]}}}. `change` is the recorded insertion (spans and
digests) so whichever repo removes last can revert it byte for byte.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from ..locking import exclusive
from ..storage import atomic_write, private_dir, read_json


def path(env: Mapping[str, str] | None = None, home: str | Path | None = None) -> Path:
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else (Path.home() if home is None else Path(home)) / ".config"
    return root / "agent-autosave" / "installs.json"


def _key(hooks_file):
    return str(Path(hooks_file).expanduser().resolve())


def load(registry: Path | None = None) -> dict[str, Any]:
    data = read_json(registry or path())
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return {"v": 1, "files": {}}
    return data


def is_live(install: dict[str, Any]) -> bool:
    try:
        return (Path(install["repo"]) / ".autosave" / "install.json").is_file()
    except (KeyError, TypeError, OSError):
        return False


def _locked(registry):
    private_dir(registry.parent)
    return exclusive(registry.parent / ".installs.lock", timeout=2.0)


def register(hooks_file: str | Path, repo: str | Path, agent: str, change: dict[str, Any] | None = None,
             registry: Path | None = None) -> None:
    registry = Path(registry or path())
    with _locked(registry):
        data = load(registry)
        record = data["files"].setdefault(_key(hooks_file), {"agent": agent, "change": None, "installs": []})
        if change is not None and record.get("change") is None:
            record["change"] = {k: change[k] for k in ("before", "after", "spans", "created", "policy")}
        repo = str(Path(repo).resolve())
        record["installs"] = [i for i in record["installs"] if i.get("repo") != repo]
        record["installs"].append({"repo": repo, "agent": agent,
                                   "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        atomic_write(registry, data)


def unregister(hooks_file: str | Path, repo: str | Path, registry: Path | None = None, *,
               live: Callable[[dict[str, Any]], bool] = is_live) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Drop `repo`; return (other live installs, recorded change or None).
    The record (with its change) is deleted once no live install is left."""
    registry = Path(registry or path())
    with _locked(registry):
        data = load(registry)
        key = _key(hooks_file)
        record = data["files"].get(key)
        if record is None:
            return [], None
        repo = str(Path(repo).resolve())
        others = [i for i in record["installs"] if i.get("repo") != repo and live(i)]
        record["installs"] = [i for i in record["installs"] if i.get("repo") != repo]
        change = record.get("change")
        if not others:
            del data["files"][key]
        atomic_write(registry, data)
        return others, change


def users(hooks_file: str | Path, registry: Path | None = None, *,
          live: Callable[[dict[str, Any]], bool] = is_live) -> list[dict[str, Any]]:
    record = load(registry).get("files", {}).get(_key(hooks_file)) or {}
    return [i for i in record.get("installs", []) if live(i)]


def stale(registry: Path | None = None, *,
          live: Callable[[dict[str, Any]], bool] = is_live) -> list[tuple[str, dict[str, Any]]]:
    """[(hooks_file, install)] whose repo has no .autosave/install.json."""
    return [(key, install) for key, record in load(registry)["files"].items()
            for install in record.get("installs", []) if not live(install)]


def prune(registry: Path | None = None, *,
          live: Callable[[dict[str, Any]], bool] = is_live) -> list[tuple[str, dict[str, Any]]]:
    registry = Path(registry or path())
    with _locked(registry):
        data = load(registry)
        dropped = []
        for key, record in data["files"].items():
            dropped += [(key, i) for i in record.get("installs", []) if not live(i)]
            record["installs"] = [i for i in record.get("installs", []) if live(i)]
        if dropped:
            atomic_write(registry, data)
        return dropped
