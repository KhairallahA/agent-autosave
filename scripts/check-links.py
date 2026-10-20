"""Check that every relative link in the repository's Markdown resolves.

    python scripts/check-links.py

Checks files and folders, and `#anchors` against the target's headings
(GitHub's slug rules). Absolute links into this repository (github.com
blob/tree and raw.githubusercontent.com on main, as README.md uses so they
work on PyPI) are mapped to the checkout and checked the same way, images
included, offline. Relative images are checked too. Other external links are not fetched. Exits 1 and lists the
broken links if any.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_URL = re.compile(r"^https://(?:github\.com/KhairallahA/agent-autosave/(?:blob|tree)/main/"
                      r"|raw\.githubusercontent\.com/KhairallahA/agent-autosave/main/)(.*)$")
IMAGE_OR_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"^(```|~~~).*?^\1", re.M | re.S)


def markdown_files():
    try:
        listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.md"], cwd=ROOT, capture_output=True, text=True,
                                check=True).stdout.split()
        return [ROOT / name for name in listed if (ROOT / name).exists()]
    except (OSError, subprocess.CalledProcessError):
        return sorted(p for p in ROOT.rglob("*.md") if not any(part.startswith(".") for part in p.parts))


def slug(heading):
    text = re.sub(r"[`*_]|<[^>]+>", "", heading.strip().lower())
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def anchors(path):
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    found, seen = set(), {}
    for heading in re.findall(r"^#{1,6} (.+)$", text, re.M):
        base = slug(heading)
        count = seen.get(base, 0)
        seen[base] = count + 1
        found.add(base if count == 0 else "{}-{}".format(base, count))
    return found


def broken_links():
    problems = []
    for source in markdown_files():
        problems += problems_in(source, source.read_text(encoding="utf-8"))
    return problems


def problems_in(source, text):
    """Broken links and images in one Markdown text, which lives at source."""
    problems = []
    name = source.relative_to(ROOT) if source.is_relative_to(ROOT) else source
    for target in IMAGE_OR_LINK.findall(FENCE.sub("", text)):
        repo = REPO_URL.match(target)
        if repo:
            path_part, _, anchor = repo.group(1).partition("#")
            path = (ROOT / path_part).resolve() if path_part else ROOT
        elif re.match(r"[a-z][a-z0-9+.-]*:", target):
            continue  # other http(s), mailto: not fetched
        else:
            path_part, _, anchor = target.partition("#")
            path = (source.parent / path_part).resolve() if path_part else source
        if not path.exists():
            problems.append("{}: {} (no such file)".format(name, target))
        elif anchor and path.suffix == ".md" and anchor not in anchors(path):
            problems.append("{}: {} (no such heading)".format(name, target))
    return problems


def main():
    problems = broken_links()
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
