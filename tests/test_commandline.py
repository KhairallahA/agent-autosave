"""Shell read-only rule and chain splitting (agent_autosave.commandline)."""
import pytest

from agent_autosave.commandline import chain, is_readonly

READONLY = [
    # Output thrown away or fds duplicated: not a write (a real macOS run).
    "ls; cat export.py 2>/dev/null || find . -name export.py", "ls 2>/dev/null", "cat a >/dev/null",
    "cat a > /dev/null", "ls &>/dev/null", "ls &>> /dev/null", "grep -rn x src 2>&1", "git log 2>&1 | head -5",
    "cat a 1>&2", "rg x 2>/dev/null >/dev/null",
    "cat README.md", "head -n 20 src/app.py", "tail -n 50 build.log", "less docs/index.md", "ls", "ls -la src",
    "tree -L 2", "pwd", "wc -l src/*.py", "file dist/app", "stat setup.cfg", "rg -n 'def export' src",
    "grep -rn TODO src", "fd -e py", "find . -name '*.py'", "find src -type f -newer setup.cfg",
    "sed -n '1,40p' src/app.py", "sed -n -e 5p file", "awk '{print $1}' data.csv", "git status",
    "git status --short", "git diff", "git diff --stat HEAD~1", "git log --oneline -5", "git show HEAD:README.md",
    "git --no-pager log -3", "git -C ../other status", "cat a | grep x", "rg foo | head -5",
    "cat a && wc -l b", "ls; pwd", "git diff || git status", "grep -c 'a > b' file",
    "cat 'quoted > not a redirect'", "rg \"x | y\" src", "head -1 a\ntail -1 b", "cat a < b",
]
WRITES = [
    # A real file next to an allowed redirect still writes.
    "cat a >/dev/null.txt", "ls 2>/dev/nullx", "ls &> /tmp/out", "cat a 2>&1 > out", "ls >&2 > f",
    "ls >&out", "ls 2>&1 >> log", "cat a > /dev/null/../x",
    # The required traps first.
    "sed -i 's/a/b/' file", "find . -delete", "echo x > f", "git branch -D x", "cat a | tee b",
    # More ways to write, delete or run something else.
    "sed -i.bak 's/a/b/' f", "sed --in-place 's/a/b/' f", "sed -ni 'p' f", "sed 's/a/b/' f",
    "find . -name '*.pyc' -exec rm {} +", "find . -execdir rm {} ;", "find . -fprint out.txt",
    "cat a > b", "cat a >> b", "ls &> out", "ls >| out", "git diff > patch.diff", "grep x f 2> err",
    "git checkout main", "git commit -m x", "git push", "git reset --hard", "git diff --output=out.diff",
    "git", "git -C", "rm -rf build", "pytest -q", "python -c 'print(1)'", "npm test", "make",
    "cat $(ls)", "cat `ls`", "diff <(ls a) <(ls b)", "ls &", "sleep 1 & cat a", "touch f", "mv a b",
    "awk -i inplace '{print}' f", "awk '{print > \"out\"}' f", "awk 'BEGIN{system(\"rm x\")}'",
    "rg --pre ./run.sh x", "fd -x rm", "fd --exec rm", "cat a; rm b", "ls && mkdir x", "cat 'unclosed",
    "", "   ", "FOO=1 cat a", "xargs cat", "env cat a", "tee out", "cat a | sh",
]


@pytest.mark.parametrize("command", READONLY)
def test_read_only_commands(command):
    assert is_readonly(command), command


@pytest.mark.parametrize("command", WRITES)
def test_commands_that_may_write_are_not_read_only(command):
    assert not is_readonly(command), command


def test_there_are_at_least_forty_cases():
    assert len(READONLY) + len(WRITES) >= 40


def test_chain_respects_quotes_and_optional_pipes():
    assert chain("a && b || c; d\ne") == (["a ", " b ", " c", " d", "e"], ["&&", "||", ";", "\n"])
    assert chain("echo 'a; b' && c") == (["echo 'a; b' ", " c"], ["&&"])
    assert chain("a | b") == (["a | b"], [])
    assert chain("a | b", pipes=True) == (["a ", " b"], ["|"])
    assert chain('echo "x \\" ; y" ; z')[0] == ['echo "x \\" ; y" ', " z"]
