"""The 10 scripted benchmark tasks and the throwaway repos they are built in.

Each task is a small stdlib Python project with a test per plan step. Every
step's test fails until the step is done, so a script can tell which steps
an interrupted agent finished (`finished_steps`) without trusting what the
agent says about itself. The acceptance test is the whole suite. `k` is the
interruption point: the runner stops agent A after this many recorded tool
events. `solution` is the finished file for each step; only the fake agent
reads it, so the runner can be tested offline.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

def step(name: str, path: str, start: str, solution: str, test: str) -> dict[str, Any]:
    """One plan step: the file it changes, that file before and after, and its test."""
    module = Path(path).stem
    return {"name": name, "file": path, "start": start, "solution": solution,
            "test_file": "tests/test_{}.py".format(module), "test": test}


def task(name: str, kind: str, goal: str, k: int, steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {"name": name, "kind": kind, "goal": goal, "k": k, "steps": steps}


TASKS = [
    task("fix-median-and-mode", "bug fix", "Fix the failing statistics tests: median of an even list and mode ties.", 3, [
        step("Fix median for even-length lists", "app/median.py",
             "def median(xs):\n    xs = sorted(xs)\n    return xs[len(xs) // 2]\n",
             "def median(xs):\n    xs = sorted(xs)\n    mid = len(xs) // 2\n"
             "    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2\n",
             "from app.median import median\n\n\ndef test():\n    assert median([1, 2, 3, 4]) == 2.5 and median([3, 1, 2]) == 2\n"),
        step("Break mode ties by the smallest value", "app/mode.py",
             "def mode(xs):\n    return max(xs, key=xs.count)\n",
             "def mode(xs):\n    return min(sorted(set(xs)), key=lambda x: (-xs.count(x), x))\n",
             "from app.mode import mode\n\n\ndef test():\n    assert mode([3, 3, 1, 1, 2]) == 1 and mode([5, 5, 2]) == 5\n"),
    ]),
    task("fix-durations", "bug fix", "Fix parsing \"1h30m\" durations and rounding when they are formatted.", 3, [
        step("Parse hours and minutes together", "app/parse.py",
             "def parse(text):\n    return int(text.rstrip('m'))\n",
             "import re\n\n\ndef parse(text):\n    hours, minutes = re.fullmatch(r'(?:(\\d+)h)?(?:(\\d+)m)?', text).groups()\n"
             "    return int(hours or 0) * 60 + int(minutes or 0)\n",
             "from app.parse import parse\n\n\ndef test():\n    assert parse('1h30m') == 90 and parse('45m') == 45 and parse('2h') == 120\n"),
        step("Round seconds to the nearest minute", "app/fmt.py",
             "def fmt(seconds):\n    return '{}m'.format(seconds // 60)\n",
             "def fmt(seconds):\n    return '{}m'.format(int(seconds / 60 + 0.5))\n",
             "from app.fmt import fmt\n\n\ndef test():\n    assert fmt(89) == '1m' and fmt(90) == '2m' and fmt(0) == '0m'\n"),
    ]),
    task("fix-csv-quoting", "bug fix", "Fix splitting and joining CSV lines that contain quoted commas.", 3, [
        step("Split quoted fields correctly", "app/split.py",
             "def split(line):\n    return line.split(',')\n",
             "import csv\n\n\ndef split(line):\n    return next(csv.reader([line]))\n",
             "from app.split import split\n\n\ndef test():\n    assert split('a,\"b,c\",d') == ['a', 'b,c', 'd']\n"),
        step("Quote fields that contain commas when joining", "app/join.py",
             "def join(fields):\n    return ','.join(fields)\n",
             "def join(fields):\n    return ','.join('\"{}\"'.format(f) if ',' in f else f for f in fields)\n",
             "from app.join import join\n\n\ndef test():\n    assert join(['a', 'b,c']) == 'a,\"b,c\"'\n"),
    ]),
    task("fix-roman-numerals", "bug fix", "Fix Roman numeral conversion in both directions for subtractive forms like IV and XC.", 3, [
        step("Write subtractive numerals", "app/to_roman.py",
             "def to_roman(n):\n    return 'I' * n\n",
             "PAIRS = [(1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'), (90, 'XC'), (50, 'L'),\n"
             "         (40, 'XL'), (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')]\n\n\n"
             "def to_roman(n):\n    out = ''\n    for value, letters in PAIRS:\n        while n >= value:\n"
             "            out, n = out + letters, n - value\n    return out\n",
             "from app.to_roman import to_roman\n\n\ndef test():\n    assert to_roman(1994) == 'MCMXCIV' and to_roman(4) == 'IV'\n"),
        step("Read subtractive numerals", "app/from_roman.py",
             "def from_roman(s):\n    return len(s)\n",
             "VALUES = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}\n\n\n"
             "def from_roman(s):\n    total = 0\n    for here, after in zip(s, s[1:] + ' '):\n"
             "        value = VALUES[here]\n        total += -value if after != ' ' and VALUES[after] > value else value\n"
             "    return total\n",
             "from app.from_roman import from_roman\n\n\ndef test():\n    assert from_roman('MCMXCIV') == 1994 and from_roman('IV') == 4\n"),
    ]),
    task("add-word-count", "feature", "Add a word counter: a tokenizer, a counter and a top-N report.", 5, [
        step("Add the tokenizer", "app/tokens.py", "",
             "import re\n\n\ndef tokens(text):\n    return re.findall(r\"[a-z']+\", text.lower())\n",
             "from app.tokens import tokens\n\n\ndef test():\n    assert tokens('Hello, hello world!') == ['hello', 'hello', 'world']\n"),
        step("Count the words", "app/count.py", "",
             "from collections import Counter\n\nfrom app.tokens import tokens\n\n\ndef count(text):\n"
             "    return Counter(tokens(text))\n",
             "from app.count import count\n\n\ndef test():\n    assert count('a b a')['a'] == 2\n"),
        step("Report the top N words", "app/top.py", "",
             "from app.count import count\n\n\ndef top(text, n):\n"
             "    return sorted(count(text).items(), key=lambda kv: (-kv[1], kv[0]))[:n]\n",
             "from app.top import top\n\n\ndef test():\n    assert top('b a b c a b', 2) == [('b', 3), ('a', 2)]\n"),
    ]),
    task("add-inventory", "feature", "Add an inventory: items, stock changes that never go negative, and a low-stock report.", 5, [
        step("Add the item record", "app/item.py", "",
             "from collections import namedtuple\n\nItem = namedtuple('Item', 'sku name qty')\n",
             "from app.item import Item\n\n\ndef test():\n    assert Item('a1', 'Bolt', 3).qty == 3\n"),
        step("Change stock without going negative", "app/stock.py", "",
             "def change(item, delta):\n    if item.qty + delta < 0:\n        raise ValueError('not enough stock')\n"
             "    return item._replace(qty=item.qty + delta)\n",
             "import pytest\n\nfrom app.item import Item\nfrom app.stock import change\n\n\ndef test():\n"
             "    assert change(Item('a', 'b', 2), -2).qty == 0\n    with pytest.raises(ValueError):\n"
             "        change(Item('a', 'b', 1), -2)\n"),
        step("Report items below a threshold", "app/low.py", "",
             "def low(items, threshold):\n    return sorted(i.sku for i in items if i.qty < threshold)\n",
             "from app.item import Item\nfrom app.low import low\n\n\ndef test():\n"
             "    assert low([Item('b', '', 1), Item('a', '', 0), Item('c', '', 9)], 2) == ['a', 'b']\n"),
    ]),
    task("add-temperatures", "feature", "Add temperature conversion both ways and a printed conversion table.", 5, [
        step("Convert Celsius to Fahrenheit", "app/c2f.py", "",
             "def c2f(c):\n    return c * 9 / 5 + 32\n",
             "from app.c2f import c2f\n\n\ndef test():\n    assert c2f(100) == 212 and c2f(-40) == -40\n"),
        step("Convert Fahrenheit to Celsius", "app/f2c.py", "",
             "def f2c(f):\n    return (f - 32) * 5 / 9\n",
             "from app.f2c import f2c\n\n\ndef test():\n    assert f2c(212) == 100 and f2c(32) == 0\n"),
        step("Print a conversion table", "app/table.py", "",
             "from app.c2f import c2f\n\n\ndef table(start, stop, step):\n"
             "    return ['{:>4} C = {:>5.1f} F'.format(c, c2f(c)) for c in range(start, stop + 1, step)]\n",
             "from app.table import table\n\n\ndef test():\n    assert table(0, 10, 10) == ['   0 C =  32.0 F', '  10 C =  50.0 F']\n"),
    ]),
    task("add-config-loader", "feature", "Add a config loader: defaults, a deep merge, and loading a JSON file over the defaults.", 5, [
        step("Add the defaults", "app/defaults.py", "",
             "DEFAULTS = {'port': 8000, 'log': {'level': 'info', 'file': None}}\n",
             "from app.defaults import DEFAULTS\n\n\ndef test():\n    assert DEFAULTS['log']['level'] == 'info'\n"),
        step("Merge nested settings", "app/merge.py", "",
             "def merge(base, over):\n    out = dict(base)\n    for key, value in over.items():\n"
             "        out[key] = merge(base[key], value) if isinstance(value, dict) and isinstance(base.get(key), dict) else value\n"
             "    return out\n",
             "from app.merge import merge\n\n\ndef test():\n"
             "    assert merge({'a': {'b': 1, 'c': 2}}, {'a': {'b': 3}}) == {'a': {'b': 3, 'c': 2}}\n"),
        step("Load a JSON file over the defaults", "app/load.py", "",
             "import json\n\nfrom app.defaults import DEFAULTS\nfrom app.merge import merge\n\n\ndef load(path):\n"
             "    with open(path) as handle:\n        return merge(DEFAULTS, json.load(handle))\n",
             "import json\n\nfrom app.load import load\n\n\ndef test(tmp_path):\n    path = tmp_path / 'c.json'\n"
             "    path.write_text(json.dumps({'log': {'level': 'debug'}}))\n"
             "    assert load(str(path)) == {'port': 8000, 'log': {'level': 'debug', 'file': None}}\n"),
    ]),
    task("add-todo-store", "feature", "Add a todo list: todo items, a JSON store, and filtering by done state.", 5, [
        step("Add the todo item", "app/todo.py", "",
             "def todo(title, done=False):\n    return {'title': title, 'done': done}\n",
             "from app.todo import todo\n\n\ndef test():\n    assert todo('x') == {'title': 'x', 'done': False}\n"),
        step("Save and load the list as JSON", "app/store.py", "",
             "import json\n\n\ndef save(path, items):\n    with open(path, 'w') as handle:\n        json.dump(items, handle)\n\n\n"
             "def load(path):\n    with open(path) as handle:\n        return json.load(handle)\n",
             "from app.store import load, save\n\n\ndef test(tmp_path):\n    path = str(tmp_path / 't.json')\n"
             "    save(path, [{'title': 'a', 'done': True}])\n    assert load(path)[0]['done'] is True\n"),
        step("Filter by done state", "app/pending.py", "",
             "def pending(items):\n    return [i['title'] for i in items if not i['done']]\n",
             "from app.pending import pending\n\n\ndef test():\n"
             "    assert pending([{'title': 'a', 'done': True}, {'title': 'b', 'done': False}]) == ['b']\n"),
    ]),
    task("split-utils", "refactor", "Split app/utils.py into app/text.py and app/numbers.py; app/utils.py only re-exports them.", 4, [
        step("Move the text helpers to app/text.py", "app/text.py", "",
             "def shout(s):\n    return s.upper() + '!'\n",
             "from app.text import shout\n\n\ndef test():\n    assert shout('hi') == 'HI!'\n"),
        step("Move the number helpers to app/numbers.py", "app/numbers.py", "",
             "def clamp(x, low, high):\n    return max(low, min(high, x))\n",
             "from app.numbers import clamp\n\n\ndef test():\n    assert clamp(5, 0, 3) == 3\n"),
        step("Make app/utils.py re-export only", "app/utils.py",
             "def shout(s):\n    return s.upper() + '!'\n\n\ndef clamp(x, low, high):\n    return max(low, min(high, x))\n",
             "from app.numbers import clamp\nfrom app.text import shout\n\n__all__ = ['clamp', 'shout']\n",
             "import inspect\n\nimport app.utils\nfrom app.utils import clamp, shout\n\n\ndef test():\n"
             "    assert shout('a') == 'A!' and clamp(-1, 0, 2) == 0\n    assert 'def ' not in inspect.getsource(app.utils)\n"),
    ]),
]


def by_name(name: str) -> dict[str, Any]:
    for item in TASKS:
        if item["name"] == name:
            return item
    raise KeyError(name)


def prompt(item: dict[str, Any]) -> str:
    """Agent A's first prompt: the goal and the plan, as a person would ask."""
    steps = " ".join("{}. {}.".format(n, s["name"]) for n, s in enumerate(item["steps"], 1))
    return "{} Plan: {} Run the tests with: python -m pytest -q".format(item["goal"], steps)


def generate(item: dict[str, Any], dest: Path) -> Path:
    """Write the task's repo at `dest` and commit it, so every arm starts equal."""
    files = {"app/__init__.py": "", "tests/__init__.py": "",
             "README.md": "# {}\n\n{}\n".format(item["name"], item["goal"])}
    for s in item["steps"]:
        if s["start"]:
            files[s["file"]] = s["start"]
        files[s["test_file"]] = s["test"]
    for name, text in files.items():
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    env = dict(os.environ, GIT_AUTHOR_NAME="bench", GIT_AUTHOR_EMAIL="bench@example.invalid",
               GIT_COMMITTER_NAME="bench", GIT_COMMITTER_EMAIL="bench@example.invalid")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "Task start"]):
        subprocess.run(["git"] + args, cwd=str(dest), env=env, check=True)
    return dest


def step_passes(repo: Path, item_step: dict[str, Any]) -> bool:
    return _pytest(repo, [item_step["test_file"]])


def finished_steps(repo: Path, item: dict[str, Any]) -> list[int]:
    """1-based numbers of the steps whose test passes in `repo` now."""
    return [n for n, s in enumerate(item["steps"], 1) if step_passes(repo, s)]


def accepted(repo: Path) -> bool:
    """The acceptance test: the whole suite passes."""
    return _pytest(repo, ["tests"])


def _pytest(repo, targets):
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"] + targets,
                            cwd=str(repo), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0
