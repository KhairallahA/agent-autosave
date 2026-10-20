#!/usr/bin/env python3
"""Scan every file version and commit message in a git repo for private data.

Usage: scan-history.py [--repo DIR] [--rev REV ...] [--forbid-file FILE] [--allowlist FILE]

Looks for tokens, private keys, personal paths (/Users/<name>, /home/<user>, ~/Desktop...),
macOS per-user temp folders, account/user ids, encrypted blobs and email addresses, plus any
literal listed in --forbid-file (one per line; keep that file outside the repo, so a private
name is never committed). Default revs: --all.

Output names the rule, file, line, commit and a short hash of the matched text, never the
text itself, so the log can be shared. Known-benign hits are allowed by ALLOW below or by an
--allowlist file of `rule<TAB>path-glob<TAB>sha256-prefix-or-*` lines. Exit 0 = clean,
1 = hits, 2 = usage or git error. Standard library only (Python 3.9+).
"""
import argparse
import fnmatch
import hashlib
import re
import subprocess
import sys

RULES = [
    ("token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"
                         r"|(?:AKIA|ASIA)[0-9A-Z]{16}|xox[abposr]-[A-Za-z0-9-]{10,})\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{6,}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("encrypted blob", re.compile(r"\bgAAAAAB[A-Za-z0-9_-]{40,}")),
    ("account id", re.compile(r"\buser-[A-Za-z0-9]{16,}\b|\"(?:creator_)?(?:user|account|org|organization)_id\"\s*:\s*\"[^\"]+\"")),
    ("home path", re.compile(r"/Users/[A-Za-z0-9._-]+|/home/[A-Za-z0-9._-]+|\b[A-Za-z]:\\{1,2}Users\\{1,2}[A-Za-z0-9._-]+")),
    ("personal folder", re.compile(r"~/(?:Desktop|Documents|Downloads|Projects|Library|Dropbox|iCloud)\b[^\s\"'`)\]]*")),
    ("macOS temp", re.compile(r"/(?:private/)?var/folders/[\w+-]{2}/[\w+-]{10,}")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")),
]

# Known-benign hits: (rule, path glob, regex the matched text must fully match).
ALLOW = [
    ("email", "*", r"[\w.+-]+@users\.noreply\.github\.com|noreply@[\w.-]+|[\w.+-]+@example\.(?:com|org|invalid)"),
    ("home path", "*", r"/Users/(?:k|me|you|someone|alice|bob|dev|user|name|runner)"
                       r"|/home/(?:k|me|you|someone|alice|bob|dev|user|runner|box)"),
    ("home path", "*", r"[A-Za-z]:\\{1,2}Users\\{1,2}(?:k|me|you|someone|alice|dev|user|name|runneradmin)"),
    # This repo's own checkout (as public as the repo URL), example vaults and the bare folder names:
    ("personal folder", "*", r"~/Desktop/Projects/agent-autosave(?:/[^\s]*)?"),
    ("personal folder", "*", r"~/(?:Desktop/|Documents/|Projects/)*(?:app|hello|demo|project|Obsidian)(?:/[^\s]*)?"),
    ("personal folder", "*", r"~/(?:Desktop|Documents|Downloads|Projects|Library)"),
    # Fake tokens the redactor tests feed in: obvious filler, never a real key.
    ("token", "tests/*", r".*(?:abcdef|aaaaaa|AAAAAA|012345|x{6}|XXXXXX|EXAMPLE|test).*"),
    # Compressed image bytes read as text often look like a@b.cd:
    ("email", "*.gif", r".*"), ("email", "*.png", r".*"),
    ("*", "tests/test_redact*.py", r".*"),
    # Test data that must look real so the redactor, the scrubber and this scan are exercised:
    ("*", "tests/test_scrub_rollout.py", r".*"),
    ("*", "tests/test_scan_history.py", r".*"),
    ("*", "scripts/scan-history.py", r".*"),
]


def git(repo, *args, data=None):
    return subprocess.run(["git", "-C", repo, *args], input=data, capture_output=True, check=True).stdout


def fingerprint(text):
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()[:12]


def load_allowlist(path):
    extra = []
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if line.strip() and not line.startswith("#"):
            rule, glob, digest = (line.split("\t") + ["*", "*"])[:3]
            extra.append((rule, glob, digest))
    return extra


def allowed(rule, path, text, extra):
    for r, glob, pattern in ALLOW:
        if r in ("*", rule) and fnmatch.fnmatch(path, glob) and re.fullmatch(pattern, text):
            return True
    return any(r in ("*", rule) and fnmatch.fnmatch(path, glob) and d in ("*", fingerprint(text)[:len(d)])
               for r, glob, d in extra)


def scan(repo, revs=("--all",), forbid=(), extra=()):
    """[(rule, path, line, commit, fingerprint)] for every hit not allowed."""
    rules = RULES + [("forbidden text", re.compile(re.escape(word), re.I)) for word in forbid]
    commits = git(repo, "rev-list", *revs).decode().split()
    first_seen = {}  # blob -> (commit, path)
    for commit in commits:
        for entry in git(repo, "ls-tree", "-r", "-z", commit).decode("utf-8", "surrogateescape").split("\0"):
            if entry:
                meta, path = entry.split("\t", 1)
                _, kind, sha = meta.split()
                if kind == "blob":
                    first_seen.setdefault((sha, path), commit)
    hits = set()

    def look(text, path, commit):
        for number, line in enumerate(text.splitlines(), 1):
            for rule, rx in rules:
                for match in rx.finditer(line):
                    if not allowed(rule, path, match.group(0), extra):
                        hits.add((rule, path, number, commit[:12], fingerprint(match.group(0))))

    blobs = sorted({sha for sha, _ in first_seen})
    if blobs:
        out = git(repo, "cat-file", "--batch", data="".join(b + "\n" for b in blobs).encode())
        contents, i = {}, 0
        for sha in blobs:
            header_end = out.index(b"\n", i)
            size = int(out[i:header_end].split()[2])
            contents[sha] = out[header_end + 1:header_end + 1 + size]
            i = header_end + 2 + size
        for (sha, path), commit in sorted(first_seen.items()):
            raw = contents[sha]
            text = raw.decode("utf-8", "surrogateescape") if b"\0" not in raw[:8000] else \
                "\n".join(s.decode("latin-1") for s in re.findall(rb"[\x20-\x7e]{6,}", raw))
            look(text, path, commit)
    for commit in commits:
        look(git(repo, "show", "-s", "--format=%B", commit).decode("utf-8", "surrogateescape"), "<commit message>", commit)
    return sorted(hits)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", default=".")
    parser.add_argument("--rev", action="append", help="revisions to scan (default --all)")
    parser.add_argument("--forbid-file", help="literals that must not appear, one per line (never printed)")
    parser.add_argument("--allowlist", help="extra allowlist: rule<TAB>path-glob<TAB>sha256-prefix")
    args = parser.parse_args(argv)
    forbid = [w.strip() for w in open(args.forbid_file, encoding="utf-8") if w.strip()] if args.forbid_file else []
    extra = load_allowlist(args.allowlist) if args.allowlist else []
    try:
        hits = scan(args.repo, args.rev or ["--all"], forbid, extra)
    except subprocess.CalledProcessError as error:
        print("scan-history: git failed: " + (error.stderr or b"").decode(errors="replace").strip(), file=sys.stderr)
        return 2
    for rule, path, line, commit, digest in hits:
        print("{}\t{}:{}\tcommit {}\tsha256 {}".format(rule, path, line, commit, digest))
    print("scan-history: {} hit(s)".format(len(hits)))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
