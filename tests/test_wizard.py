"""The init wizard, driven through a real pseudo-terminal with keystrokes."""
import json
import os
import re
import select
import subprocess
import sys
import time

import pytest

import itertools

from agent_autosave.config import resolve
from agent_autosave.wizard import render, write
from tests.helpers import LENIENT_CLI, ROOT

pytestmark = [pytest.mark.skipif(os.name == "nt", reason="drives a POSIX pty"),
              pytest.mark.filterwarnings("ignore:.*fork.*:DeprecationWarning")]

UP, DOWN, ENTER, SPACE = "\x1b[A", "\x1b[B", "\r", " "
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class Wizard:
    """`agent-autosave init` in a pty: type keys, read the screen text."""

    def __init__(self, project, home, *args, env=None):
        import pty
        self.home = home
        environment = dict(os.environ, HOME=str(home), USERPROFILE=str(home), CODEX_HOME=str(home / ".codex"),
                           PATH=str(home / "bin") + os.pathsep + os.environ["PATH"], TERM="xterm-256color",
                           PYTHONPATH=str(ROOT), COLUMNS="100")
        for name in ("NO_COLOR", "FORCE_COLOR"):
            environment.pop(name, None)
        environment.update(env or {})
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(str(project))
            os.execve(sys.executable, [*LENIENT_CLI, "init", *args], environment)
        self.raw = b""

    def read(self, seconds=0.2):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            ready, _, _ = select.select([self.fd], [], [], 0.05)
            if ready:
                try:
                    data = os.read(self.fd, 65536)
                except OSError:
                    return
                if not data:
                    return
                self.raw += data

    @property
    def text(self):
        return ANSI.sub("", self.raw.decode("utf-8", "replace")).replace("\r\n", "\n")

    def expect(self, needle, timeout=15):
        end = time.monotonic() + timeout
        while needle not in self.text:
            if time.monotonic() > end:
                raise AssertionError("{!r} never appeared:\n{}".format(needle, self.text[-3000:]))
            self.read(0.05)

    def keys(self, *presses):
        for press in presses:
            os.write(self.fd, press.encode())
            self.read(0.08)

    def wait(self, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.read(0.05)
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.read(0.1)
                return os.waitstatus_to_exitcode(status)
        os.kill(self.pid, 9)
        raise AssertionError("wizard did not exit:\n" + self.text[-3000:])


@pytest.fixture
def machine(tmp_path):
    """A project, and a home with stand-in claude and codex binaries."""
    project, home = tmp_path / "project", tmp_path / "home"
    (home / "bin").mkdir(parents=True)
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (home / "bin/claude").write_text("#!/bin/sh\necho '2.1.4 (Claude Code)'\n")
    # `codex` with no arguments stands in for accepting Codex's trust prompt.
    (home / "bin/codex").write_text(
        "#!/bin/sh\n[ \"$1\" = --version ] && { echo 'codex-cli 0.130.0'; exit 0; }\n"
        "mkdir -p \"$CODEX_HOME\"\n"
        "printf '[projects.\"%s\"]\\ntrust_level = \"trusted\"\\n' \"$(pwd -P)\" >> \"$CODEX_HOME/config.toml\"\n")
    for name in ("claude", "codex"):
        (home / "bin" / name).chmod(0o755)
    return project, home


def test_defaults_wire_both_agents_after_trusting_codex(machine):
    project, home = machine
    wizard = Wizard(project, home)
    wizard.expect("Which agents should autosave here?")
    wizard.expect("Codex  0.130.0")  # the rows arrive after the question; a busy machine splits the read
    assert "Claude Code  2.1.4" in wizard.text
    assert "Not found on this machine: Cursor, Gemini CLI, Antigravity" in wizard.text
    wizard.keys(ENTER)
    wizard.expect("How do you want to handle Codex?")
    wizard.keys(ENTER)  # open Codex here
    wizard.expect("Codex trusts this folder now")
    wizard.expect("Keep .autosave/ out of git?")
    wizard.keys(ENTER)
    wizard.expect("Record raw hook payloads too?")
    wizard.keys(ENTER)  # default: no
    wizard.expect("Give agents the agent-autosave MCP tools too?")
    wizard.keys(ENTER)  # default: yes
    wizard.expect("Show the save in Claude Code\'s status line?")
    wizard.keys(ENTER)  # default: yes
    wizard.expect("Where should the journal and project memory live?")
    wizard.keys(ENTER)  # default: in .autosave/
    wizard.expect("Write these changes?")
    summary = wizard.text.split("Summary:")[1]
    for path in (".claude/settings.json", "CLAUDE.md", ".mcp.json", ".codex/hooks.json", "AGENTS.md",
                 ".codex/config.toml", ".gitignore", ".autosave/config.json"):
        assert path in summary
    assert not (project / ".autosave").exists()  # nothing written before the confirmation
    wizard.keys(ENTER)
    assert wizard.wait() == 0
    assert "Next steps" in wizard.text and "Ready." in wizard.text
    assert (project / ".codex/hooks.json").is_file() and (project / ".claude/settings.json").is_file()
    assert ".autosave/" in (project / ".gitignore").read_text()
    assert not (home / ".codex/hooks.json").exists()  # never user-level without --user-level
    config = json.loads((project / ".autosave/config.json").read_text())
    assert config["record"]["enabled"] is False
    # "In .autosave/ in this repo" is the default answer: an empty vault path, never null.
    assert config["vault"]["path"] == "" and resolve(config)["vault"]["path"] == ""


def test_space_unchecks_an_agent_and_preferences_are_written(machine):
    project, home = machine
    wizard = Wizard(project, home)
    wizard.expect("Which agents should autosave here?")
    wizard.keys(DOWN, SPACE, ENTER)  # uncheck Codex
    wizard.expect("Keep .autosave/ out of git?")
    assert "How do you want to handle Codex?" not in wizard.text
    wizard.keys(DOWN, ENTER)  # I manage .gitignore myself
    wizard.expect("Record raw hook payloads too?")
    wizard.keys("y", ENTER)
    wizard.expect("Give agents the agent-autosave MCP tools too?")
    wizard.keys(ENTER)  # default: yes
    wizard.expect("Show the save in Claude Code\'s status line?")
    wizard.keys(ENTER)  # default: yes
    wizard.expect("Where should the journal and project memory live?")
    wizard.keys(ENTER)  # default: in .autosave/
    wizard.expect("Write these changes?")
    wizard.keys(ENTER)
    assert wizard.wait() == 0
    assert not (project / ".codex").exists() and not (project / ".gitignore").exists()
    assert json.loads((project / ".autosave/config.json").read_text())["record"]["enabled"] is True
    assert json.loads((project / ".autosave/install.json").read_text())["gitignore"] is False


def test_skipping_codex_prints_the_steps_and_writes_no_codex_hooks(machine):
    project, home = machine
    wizard = Wizard(project, home)
    wizard.expect("Which agents should autosave here?")
    wizard.keys(ENTER)
    wizard.expect("How do you want to handle Codex?")
    wizard.keys(DOWN, ENTER)  # skip Codex for now
    wizard.expect("Then run: agent-autosave add codex")
    assert 'trust_level = "trusted"' in wizard.text
    wizard.expect("Keep .autosave/ out of git?")
    wizard.keys(ENTER, ENTER)
    wizard.expect("Give agents the agent-autosave MCP tools too?")
    wizard.keys(ENTER)  # default: yes
    wizard.expect("Show the save in Claude Code\'s status line?")
    wizard.keys(ENTER)  # default: yes
    wizard.expect("Where should the journal and project memory live?")
    wizard.keys(ENTER)  # default: in .autosave/
    wizard.expect("Write these changes?")
    wizard.keys(ENTER)
    assert wizard.wait() == 0
    assert not (project / ".codex").exists() and not (home / ".codex/hooks.json").exists()
    assert json.loads((project / ".autosave/install.json").read_text())["agents"] == ["claude-code"]


@pytest.mark.parametrize("keys", [("q",), (ENTER, ENTER, ENTER, ENTER, ENTER, ENTER, ENTER, "n", ENTER)], ids=["quit", "decline"])
def test_cancel_or_declining_the_summary_writes_nothing(machine, keys):
    project, home = machine
    wizard = Wizard(project, home)
    wizard.expect("Which agents should autosave here?")
    wizard.keys(*keys)
    assert wizard.wait() == 0
    assert "Nothing was written." in wizard.text
    assert sorted(p.name for p in project.iterdir()) == [".git"]


def test_reconfigure_adds_an_agent_and_keeps_the_rest(machine):
    project, home = machine
    env = dict(os.environ, HOME=str(home), CODEX_HOME=str(home / ".codex"), PYTHONPATH=str(ROOT),
               PATH=str(home / "bin") + os.pathsep + os.environ["PATH"])
    subprocess.run([*LENIENT_CLI, "init", "--agents", "claude-code"], cwd=str(project),
                   env=env, check=True, capture_output=True)
    again = Wizard(project, home)
    assert again.wait() == 0
    assert "Already set up here" in again.text and "init --reconfigure" in again.text
    wizard = Wizard(project, home, "--reconfigure")
    wizard.expect("Which agents should autosave here?")
    wizard.expect("already set up")
    wizard.keys(ENTER)
    wizard.expect("How do you want to handle Codex?")
    wizard.keys(ENTER)
    wizard.expect("Record raw hook payloads too?")
    assert "Keep .autosave/ out of git?" not in wizard.text.split("Reconfigure")[1]
    wizard.keys(ENTER)
    wizard.expect("Give agents the agent-autosave MCP tools too?")
    wizard.keys(ENTER)  # default: yes
    wizard.expect("Where should the journal and project memory live?")
    assert "status line?" not in wizard.text  # ours is already there
    wizard.keys(ENTER)  # default: in .autosave/
    wizard.expect("Write these changes?")
    wizard.keys(ENTER)
    assert wizard.wait() == 0
    assert json.loads((project / ".autosave/install.json").read_text())["agents"] == ["claude-code", "codex"]
    assert (project / ".codex/hooks.json").is_file()


def test_memory_can_go_to_a_vault_and_agentic_stack_is_asked_about_when_found(machine, tmp_path):
    project, home = machine
    (project / ".agent/memory").mkdir(parents=True)
    (project / ".agent/AGENTS.md").write_text("# agentic-stack\n")
    vault = tmp_path / "vault"
    wizard = Wizard(project, home)
    wizard.expect("Which agents should autosave here?")
    wizard.keys(SPACE, DOWN, SPACE, DOWN, SPACE, ENTER)  # only AGENTS.md: nothing about Codex
    wizard.expect("Keep .autosave/ out of git?")
    wizard.keys(ENTER, ENTER)
    wizard.expect("Give agents the agent-autosave MCP tools too?")
    wizard.keys(ENTER)  # default: yes
    wizard.expect("Where should the journal and project memory live?")
    wizard.keys(DOWN, ENTER)
    wizard.expect("Vault folder")
    wizard.keys(str(vault), ENTER)
    wizard.expect("agentic-stack is here (.agent/).")
    wizard.keys("n", ENTER)
    wizard.expect("Write these changes?")
    assert "memory in the vault " + str(vault) in wizard.text and "agentic-stack off" in wizard.text
    wizard.keys(ENTER)
    assert wizard.wait() == 0
    config = json.loads((project / ".autosave/config.json").read_text())
    assert config["vault"]["path"] == str(vault) and config["integrations"]["agentic_stack"] == "off"


def test_a_vault_chosen_in_the_wizard_gets_the_home_note_and_current_md_at_once(machine, tmp_path):
    project, home = machine
    vault = tmp_path / "vault"
    vault.mkdir()
    wizard = Wizard(project, home)
    wizard.expect("Which agents should autosave here?")
    wizard.keys(SPACE, DOWN, SPACE, DOWN, SPACE, ENTER)
    wizard.expect("Keep .autosave/ out of git?")
    wizard.keys(ENTER, ENTER)
    wizard.expect("Give agents the agent-autosave MCP tools too?")
    wizard.keys(ENTER)  # default: yes
    wizard.expect("Where should the journal and project memory live?")
    wizard.keys(DOWN, ENTER)
    wizard.expect("Vault folder")
    wizard.keys(str(vault), ENTER)
    wizard.expect("Write these changes?")
    wizard.keys(ENTER)
    assert wizard.wait() == 0
    name = json.loads((project / ".autosave/config.json").read_text())["vault"]["project"]
    assert (vault / "agent-autosave" / name / "CURRENT.md").is_file()
    assert "[[{}/CURRENT\\|save]]".format(name) in (vault / "agent-autosave/README.md").read_text()


def test_yes_and_agents_flags_never_prompt(machine):
    project, home = machine
    wizard = Wizard(project, home, "--yes")
    assert wizard.wait() == 0
    assert "Which agents" not in wizard.text and "agent-autosave init" in wizard.text
    assert "skipped: this folder is not trusted in Codex" in wizard.text  # --yes never opens Codex


def test_line_prompts_when_the_terminal_cannot_redraw(machine):
    project, home = machine
    wizard = Wizard(project, home, env={"TERM": "dumb"})
    wizard.expect("Numbers, comma-separated [1,2]: ")
    wizard.keys("1\n")
    wizard.expect("Number [1]: ")
    wizard.keys("\n")
    wizard.expect("[y/N]")
    wizard.keys("\n")
    wizard.expect("Give agents the agent-autosave MCP tools too?")
    wizard.keys(ENTER)  # default: yes
    wizard.expect("Show the save in Claude Code\'s status line?")
    wizard.keys(ENTER)  # default: yes
    wizard.expect("Where should the journal and project memory live?")
    wizard.keys(ENTER)  # default: in .autosave/
    wizard.expect("Write these changes? [Y/n]")
    wizard.keys("\n")
    assert wizard.wait() == 0
    assert json.loads((project / ".autosave/install.json").read_text())["agents"] == ["claude-code"]
    assert "\x1b[" not in wizard.raw.decode()  # TERM=dumb: no colour, no cursor movement


def test_render_lists_every_change_before_writing(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# mine\n")
    answers = render.Answers(("claude-code",), None, True, True, False)
    changes = render.changes(tmp_path, answers)
    assert [(c.path, c.action) for c in changes] == [
        (".claude/settings.json", "create"), ("CLAUDE.md", "change"), (".mcp.json", "create"), (".gitignore", "create"),
        (".autosave/", "create"), (".autosave/config.json", "create")]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["CLAUDE.md"]


def test_write_is_atomic_and_backs_up_the_replaced_file(tmp_path):
    path = tmp_path / "config.json"
    assert write.write_json(path, {"v": 1}) is None and json.loads(path.read_text()) == {"v": 1}
    assert write.write_json(path, {"v": 1}) is None  # unchanged: no backup
    backup = write.write_json(path, {"v": 1, "record": {"enabled": True}})
    assert backup == tmp_path / "config.json.bak" and json.loads(backup.read_text()) == {"v": 1}
    assert not list(tmp_path.glob("*.tmp"))


def test_summary_names_hook_and_plugin_files_for_the_newer_agents(tmp_path):
    """The summary called OpenCode's plugin file
    "instructions": it labelled by merge policy, not by what the file is."""
    answers = render.Answers(("antigravity", "copilot", "opencode"), None, True, True, False, mcp=False)
    what = {c.path: c.why for c in render.changes(tmp_path, answers)}
    assert what[".agents/hooks.json"].startswith("hooks for Antigravity")
    assert what[".github/hooks/agent-autosave.json"].startswith("hooks for Copilot CLI")
    assert what[".opencode/plugins/agent-autosave.js"] == "plugin for OpenCode"
    assert what["AGENTS.md"] == "instructions for Copilot CLI"


ANSWER_CHOICES = list(itertools.product(
    (False, True),                      # record raw hook payloads
    (None, "/tmp/some-vault"),          # memory location: in .autosave/, or a vault folder
    (None, "auto", "off"),              # agentic-stack: not found, keep on, turn off
    (True, False),                      # MCP tools
    ("auto", "wrap", "off"),            # status line
))


@pytest.mark.parametrize("record,vault,agentic,mcp,statusline", ANSWER_CHOICES)
def test_every_wizard_answer_writes_a_config_that_loads(record, vault, agentic, mcp, statusline):
    answers = render.Answers(("claude-code",), None, True, True, record, vault, agentic, mcp, statusline)
    for current in (None, {"v": 1, "vault": {"path": None, "project": ""}}):
        config = render.config(current, answers)
        assert config["vault"]["path"] == (vault or "")
        loaded = resolve(config)
        assert loaded["record"]["enabled"] is record and loaded["vault"]["path"] == (vault or "")
        if agentic is not None:
            assert loaded["integrations"]["agentic_stack"] == agentic
