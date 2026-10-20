#!/usr/bin/env python3
"""Point Formula/agent-autosave.rb at an sdist: set its url and sha256.

    scripts/update-formula.py dist/agent_autosave-0.1.0.tar.gz
        url = the sdist's PyPI file URL (packages/<blake2b-256 split 2/2/60>/...), sha256 of the file
    scripts/update-formula.py dist/agent_autosave-0.1.0.tar.gz --url file:///path/to/it
        for trying the formula from a local tap before anything is published
    --out PATH  write there (e.g. a tap checkout's Formula/agent-autosave.rb)
                instead of editing Formula/agent-autosave.rb in place

Run it at release time, after the sdist is on PyPI (docs/releasing.md).
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORMULA = ROOT / "Formula" / "agent-autosave.rb"
SDIST = re.compile(r"^agent_autosave-(\d+\.\d+\.\d+(?:[a-z]+\d+)?(?:\.dev\d+)?)\.tar\.gz$")


# Homebrew's FormulaAudit/PyPiUrls cop rejects pypi.python.org and the short
# files.pythonhosted.org/packages/source/... form and asks for the URL on PyPI's
# downloads page (Homebrew/brew Library/Homebrew/rubocops/urls.rb, class
# PyPiUrls). PyPI stores each file under its blake2b-256 digest, split
# 2/2/60 hex characters (pypi/warehouse warehouse/forklift/legacy.py, PATH_HASHER
# "blake2_256"), so the accepted URL is computed from the sdist bytes, offline.
def pypi_url(name: str, blake2b_256: str) -> str:
    b2 = blake2b_256
    return "https://files.pythonhosted.org/packages/{}/{}/{}/{}".format(b2[:2], b2[2:4], b2[4:], name)


def update(text: str, url: str, sha256: str) -> str:
    text, urls = re.subn(r'^(  url ")[^"]*(")$', lambda m: m.group(1) + url + m.group(2), text, flags=re.M)
    text, shas = re.subn(r'^(  sha256 ")[0-9a-f]{64}(")$', lambda m: m.group(1) + sha256 + m.group(2), text, flags=re.M)
    if urls != 1 or shas != 1:
        raise SystemExit("update-formula: expected one url and one sha256 line in the formula")
    return text


def main(argv) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("sdist", type=Path)
    parser.add_argument("--url", help="where Homebrew downloads it (default: the PyPI file URL computed from the sdist)")
    parser.add_argument("--out", type=Path, help="write the formula here instead of in place")
    args = parser.parse_args(argv)
    match = SDIST.match(args.sdist.name)
    if not match:
        parser.error("not an agent-autosave sdist: " + args.sdist.name)
    data = args.sdist.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    url = args.url or pypi_url(args.sdist.name, hashlib.blake2b(data, digest_size=32).hexdigest())
    text = update(FORMULA.read_text(encoding="utf-8"), url, sha256)
    out = args.out or FORMULA
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print("{}: version {}, sha256 {}".format(out, match.group(1), sha256))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
