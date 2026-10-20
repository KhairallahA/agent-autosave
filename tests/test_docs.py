"""Repository hygiene: links resolve, and no planning shorthand leaks into the product."""
import re
import subprocess
import sys

import pytest

from tests.helpers import ROOT

MILESTONE = re.compile(r"\bM[0-9]\b|\bmilestones?\b", re.I)
TICKET = re.compile(r"\bt[0-9]{1,3}u\b")
ITEM = re.compile(r"\b(?:F[1-6]|P[1-5]|X[0-8])\b")
# Build-process words: who asked for what, and where it was planned.
BUILD = re.compile(r"\bthe owner\b|\bowner's\b|PLAN\.(?:md|pdf)|original plan|\bGrok\b|Autosave Engineer"
                   r"|\bPR #[0-9]+", re.I)


def tracked(*patterns):
    try:
        result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", *patterns],
                                cwd=ROOT, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [ROOT / name for name in result.stdout.split() if (ROOT / name).exists()]


def test_every_relative_link_resolves():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/check-links.py")], capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, result.stdout


def test_no_build_process_names_anywhere():
    """No milestone, ticket or item codes, and no planning or review history, in
    anything tracked: the product, its docs, its tests and its scripts."""
    found = []
    for path in tracked("*.py", "*.md", "*.mdc", "*.toml", "*.sh", "*.ps1", "*.rb", "*.yml", "*.json", "*.txt",
                        "*.cfg", "MANIFEST.in", "NOTICE"):
        name = path.relative_to(ROOT).as_posix()
        if name.startswith("tests/fixtures/") or name == "tests/test_docs.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(pattern.search(line) for pattern in (TICKET, MILESTONE, ITEM, BUILD)):
                found.append("{}:{}: {}".format(name, number, line.strip()[:80]))
    assert not found, "\n".join(found)


def test_files_only_the_build_needed_are_gone():
    for gone in ("docs/images/reference", ".github/pr", "scripts/e2e-m7.md", "scripts/e2e-m8.md", "scripts/e2e-m9.md",
                 "docs/design/original-plan.md", ".git-blame-ignore-revs", "PLAN.md", "PLAN.pdf"):
        assert not (ROOT / gone).exists(), gone


def test_every_docs_page_says_who_it_is_for_and_links_onward():
    for page in sorted((ROOT / "docs").rglob("*.md")):
        text = page.read_text(encoding="utf-8")
        lines = text.split("\n")
        assert lines[2].startswith("**Who it's for:** "), page
        assert "\n## See also\n\n- " in text, page
        assert text.rstrip("\n").rsplit("\n## ", 1)[1].startswith("See also"), page
