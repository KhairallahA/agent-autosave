"""README.md: the launch order, and every command it shows is really run.

scripts/build-docs.py runs every command shown in the README (its tour);
tests/test_docs_output.py checks the pasted output is current.
"""
import importlib
import os
import re
import sys

import pytest

from tests.helpers import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
build_docs = importlib.import_module("build-docs")


@pytest.fixture(scope="module")
def tour(tmp_path_factory):
    if os.name == "nt":
        pytest.skip("the tour runs commands in a POSIX pty")
    return build_docs.tour(tmp_path_factory.mktemp("tour").resolve())


@pytest.mark.filterwarnings("ignore:.*fork.*:DeprecationWarning")
def test_every_command_in_the_readme_is_run_by_the_tour(tour):
    ran = {output.split("\n")[0][2:] for output in tour.values()}
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    shown = []
    for kind, block in re.findall(r"^```(sh|console)\n(.*?)^```", text, re.S | re.M):
        for line in block.split("\n"):
            if kind == "console" and not line.startswith("$ "):
                continue  # output, not a command
            command = re.sub(r"\s+#.*$", "", line[2:] if line.startswith("$ ") else line).strip()
            if command.startswith("agent-autosave"):
                shown.append(command)
    assert len(shown) >= 6
    missing = [command for command in shown if command not in ran]
    assert not missing, missing


def test_readme_follows_the_launch_order():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    order = ["# agent-autosave", "DEMO VIDEO PLACEHOLDER", "docs/demo.gif", "docs/diagram.svg", "docs/handoff.svg",
             "## New in v0.1.0", "img.shields.io", "x.com/eng_khairallah1", "## Quickstart", "### macOS / Linux (brew)",
             "### curl | sh", "### pipx", "### Windows (experimental)", "### Already installed?", "### Clone instead?",
             "### Once installed: manage what's wired", "## The setup wizard", "## How a handoff works",
             "## How it compares",
             "## What gets saved", "## Supported agents", "## Commands", "## The `.autosave/` folder", "## Memory",
             "## Works with agentic-stack", "## Examples",
             "## Configuration, privacy and security", "## Troubleshooting", "## FAQ", "## What's next",
             "## Contributing",
             "## Repo layout", "## License and credits", "## Star History"]
    positions = [text.index(item) for item in order]
    assert positions == sorted(positions)
    for install in ("brew install KhairallahA/agent-autosave/agent-autosave", "pipx install agent-autosave",
                    "main/install.sh | sh", "install.sh --from", "docs/install.md"):
        assert install in text, install


def _check_links():
    sys.path.insert(0, str(ROOT / "scripts"))
    return importlib.import_module("check-links")


def test_readme_links_are_absolute_for_pypi():
    """PyPI renders README.md as the long description, where relative links and images break."""
    check_links = _check_links()
    text = check_links.FENCE.sub("", (ROOT / "README.md").read_text(encoding="utf-8"))
    relative = [target for target in check_links.IMAGE_OR_LINK.findall(text)
                if not target.startswith(("https://", "http://", "mailto:", "#"))]
    assert not relative, relative
    images = re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", text)
    ours = [url for url in images if not url.startswith(("https://img.shields.io/", "https://api.star-history.com/"))]
    assert ours and all(url.startswith("https://raw.githubusercontent.com/KhairallahA/agent-autosave/main/") for url in ours)
    assert not check_links.problems_in(ROOT / "README.md", (ROOT / "README.md").read_text(encoding="utf-8"))


def test_absolute_repo_links_are_checked_against_the_checkout(tmp_path):
    check_links = _check_links()
    base = "https://github.com/KhairallahA/agent-autosave/"
    raw = "https://raw.githubusercontent.com/KhairallahA/agent-autosave/main/"
    good = ("[a](" + base + "blob/main/docs/faq.md) [b](" + base + "tree/main/docs/adapters) "
            "![c](" + raw + "docs/diagram.svg) [d](" + base + "blob/main/docs/install.md#the-install-script)")
    assert check_links.problems_in(tmp_path / "x.md", good) == []
    bad = "[a](" + base + "blob/main/docs/nope.md) ![b](" + raw + "docs/nope.png) [c](" + base + "blob/main/docs/faq.md#nope)"
    found = check_links.problems_in(tmp_path / "x.md", bad)
    assert len(found) == 3 and "no such heading" in found[2]
