"""Rebuild this example's save and go.txt from its ledger: python replay.py [--check]."""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from _replay import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(HERE, sys.argv[1:], go=True))
