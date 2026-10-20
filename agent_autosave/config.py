"""Project configuration layered over user defaults, without runtime dependencies."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import re

TYPE_CHECKING = False  # the status line path never imports typing (see tests/test_statusline.py)
if TYPE_CHECKING:
    from typing import Any

DEFAULTS = {
    "v": 1,
    "inject": {"on_status": ["interrupted", "paused"], "max_chars": 4000, "resume_max_chars": 8000,
               "stale_after_hours": 24, "memory_on_start": True, "memory_max_chars": 800},
    "capture": {"prompt_max_chars": 2000, "output_tail_lines": 20, "ignore_tools": [],
                "ignore_paths": ["node_modules/", "dist/", ".git/", "__pycache__/", "*.pyc", ".DS_Store"]},
    "compile": {"save_md_max_chars": 12000, "last_actions": 10, "steering": 5, "files": 30},
    "redact": {"extra_patterns": [], "redact_env_values": True},
    "ledger": {"rotate_mb": 5},
    "history": {"keep": 50},
    "budgets_ms": {"git_call": 150},
    # Low battery: when an agent's usage limit crosses this share, the next
    # prompt asks it once to record a handoff note.
    "handoff": {"warn_at_percent": 85, "ask": True},
    # Fixture recording (agent-autosave record on/off). Codex does not pass the
    # shell environment to hooks, so AGENT_AUTOSAVE_RECORD alone cannot reach it.
    "record": {"enabled": False, "dir": "", "agent_version": ""},
    # Project memory (MEMORY.md): doctor warns above max_entries; nothing is
    # ever deleted automatically.
    "memory": {"max_entries": 60},
    # Prompt recall: at most max_items memory or journal entries scoring at
    # least min_score (matched query terms / query terms), within max_chars.
    "recall": {"enabled": True, "max_items": 3, "min_score": 0.34, "max_chars": 1200},
    # Obsidian vault: "" keeps journal and memory in .autosave/. `project` is
    # the folder name inside the vault, chosen once (see vault.py).
    "vault": {"path": "", "project": ""},
    # agentic-stack's .agent/ folder: "auto" (on when detected), "on" or "off".
    "integrations": {"agentic_stack": "auto"},
    # Claude Code status line (agent-autosave statusline): "plain" or "emoji".
    "statusline": {"style": "plain"},
    # The agents `go` and `resume --next` switch through, in order; empty is
    # the launchable agents wired here, in install order.
    "switch": {"order": []},
}

# Agents `go` can start (commands/resume.py LAUNCHERS).
SWITCH_AGENTS = ("claude-code", "codex", "gemini", "antigravity", "copilot", "opencode")
# Number fields that may be fractional; every other number is an integer.
FRACTIONS = ("ledger.rotate_mb", "recall.min_score")


# Fields that earlier versions wrote into .autosave/config.json but nothing
# reads. They are accepted and dropped so those files keep loading.
RETIRED = {"commit": None, "budgets_ms": ("hook",)}


class ConfigError(ValueError):
    """Invalid configuration. Messages contain field names, never values."""


def _merge(base, update, prefix=""):
    if not isinstance(update, dict):
        raise ConfigError("Configuration must be an object.")
    for key, value in update.items():
        if key not in base:
            raise ConfigError("Unknown configuration field.")
        name = prefix + key
        expected = base[key]
        if isinstance(expected, dict):
            _merge(expected, value, name + ".")
        elif isinstance(expected, bool):
            if type(value) is not bool:
                raise ConfigError(name + " must be a boolean.")
            base[key] = value
        elif isinstance(expected, str):
            if not isinstance(value, str) or len(value) > 4096:
                raise ConfigError(name + " must be a string.")
            base[key] = value
        elif isinstance(expected, list):
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ConfigError(name + " must be a list of strings.")
            base[key] = list(value)
        else:
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ConfigError(name + " must be positive and finite.")
            if name not in FRACTIONS and type(value) is not int:
                raise ConfigError(name + " must be an integer.")
            base[key] = value


def _drop_retired(layer):
    if not isinstance(layer, dict):
        return layer
    layer = dict(layer)
    for key, fields in RETIRED.items():
        if fields is None:
            layer.pop(key, None)
        elif isinstance(layer.get(key), dict):
            layer[key] = {k: v for k, v in layer[key].items() if k not in fields}
    return layer


def resolve(*layers: dict[str, Any] | None) -> dict[str, Any]:
    """Return fresh, validated defaults with later layers taking precedence."""
    result = deepcopy(DEFAULTS)
    for layer in layers:
        if layer is not None:
            _merge(result, _drop_retired(layer))
    if result["v"] != 1:
        raise ConfigError("Unsupported configuration version.")
    if not set(result["inject"]["on_status"]) <= {"active", "paused", "interrupted", "done"}:
        raise ConfigError("Invalid inject.on_status.")
    if not 256 <= result["compile"]["save_md_max_chars"] <= 16000:
        raise ConfigError("compile.save_md_max_chars must be between 256 and 16000.")
    if not 50 <= result["handoff"]["warn_at_percent"] <= 99:
        raise ConfigError("handoff.warn_at_percent must be between 50 and 99.")
    for key in ("max_chars", "resume_max_chars"):
        if not 500 <= result["inject"][key] <= 12000:
            raise ConfigError("inject." + key + " must be between 500 and 12000.")
    if not 0 < result["recall"]["min_score"] <= 1:
        raise ConfigError("recall.min_score must be above 0 and at most 1.")
    if not 1 <= result["recall"]["max_items"] <= 10:
        raise ConfigError("recall.max_items must be between 1 and 10.")
    for section, key in (("recall", "max_chars"), ("inject", "memory_max_chars")):
        if not 200 <= result[section][key] <= 4000:
            raise ConfigError(section + "." + key + " must be between 200 and 4000.")
    if result["integrations"]["agentic_stack"] not in ("auto", "on", "off"):
        raise ConfigError("integrations.agentic_stack must be auto, on or off.")
    if result["statusline"]["style"] not in ("plain", "emoji"):
        raise ConfigError("statusline.style must be plain or emoji.")
    if not set(result["switch"]["order"]) <= set(SWITCH_AGENTS) or \
            len(set(result["switch"]["order"])) != len(result["switch"]["order"]):
        raise ConfigError("switch.order must list agents that resume can start, each once.")
    for pattern in result["redact"]["extra_patterns"]:
        try:
            re.compile(pattern)
        except re.error:
            raise ConfigError("Invalid redact.extra_patterns expression.") from None
    return result


def _read(path):
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as stream:
            result = json.load(stream)
    except (OSError, UnicodeError, ValueError):
        raise ConfigError("Cannot read configuration JSON.") from None
    return result


NULL_VAULT_WARNING = ("vault.path is null in {} (written by an earlier init wizard); it is read as \"\", which keeps "
                      "the journal and MEMORY.md in .autosave/. Set it to \"\" or run agent-autosave init --reconfigure.")


def _forgive_null_vault(layer, name, warnings):
    # Wizards before 0.1.0 wrote {"vault": {"path": null}} for "In .autosave/ in this repo".
    vault = layer.get("vault") if isinstance(layer, dict) else None
    if isinstance(vault, dict) and "path" in vault and vault["path"] is None:
        warnings.append(NULL_VAULT_WARNING.format(name))
        return dict(layer, vault=dict(vault, path=""))
    return layer


def load_with_warnings(project: str | Path, *,
                       user_path: str | Path | None = None) -> tuple[dict[str, Any], list[str]]:
    """load(), plus a warning for each value repaired on the way (never a value, only field names)."""
    user = Path(user_path) if user_path is not None else Path.home() / ".config/agent-autosave/config.json"
    warnings: list[str] = []
    layers = [_forgive_null_vault(_read(path), shown, warnings)
              for path, shown in ((user, "the user config"), (Path(project) / ".autosave/config.json",
                                                              ".autosave/config.json"))]
    return resolve(*layers), warnings


def load(project: str | Path, *, user_path: str | Path | None = None) -> dict[str, Any]:
    """Read only: builtin defaults < user config < project config."""
    return load_with_warnings(project, user_path=user_path)[0]
