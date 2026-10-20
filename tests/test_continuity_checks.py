"""Checks: recognised test, lint and type-check runs, newest per command."""
import pytest

from agent_autosave.commandline import is_check
from agent_autosave.compiler import compile_events
from agent_autosave.loader import context
from tests.helpers import tool
from tests.scenario import Repo

CHECKS = ["pytest", "pytest -q tests/billing", "py.test", "python -m pytest -x", "python3 -m unittest discover",
          "python3.12 -m pytest", "tox -e py312", "nox -s tests", "npm test", "npm t", "npm run test:unit",
          "pnpm test", "yarn lint", "bun run typecheck", "npm run type-check", "yarn test --watch=false",
          "vitest run", "jest --ci", "mocha", "go test ./...", "go vet ./...", "cargo test", "cargo clippy",
          "cargo check", "make test", "make check", "make lint", "ruff check .", "flake8 src", "mypy src",
          "pyright", "eslint .", "tsc --noEmit", "rspec", "bundle exec rspec spec/a_spec.rb", "phpunit",
          "dotnet test", "gradle test", "./gradlew test", "mvn test", "./mvnw test", "uv run pytest",
          "poetry run mypy .", "npx eslint src", "CI=1 npm test", "cd api && pytest -q", "node_modules/.bin/jest"]
NOT_CHECKS = ["python app.py", "npm install", "npm run build", "go build", "cargo build", "make", "make install",
              "git status", "ls", "pytesting", "echo pytest", "python -m http.server", "yarn add jest", "gradle build"]


@pytest.mark.parametrize("command", CHECKS)
def test_recognised(command):
    assert is_check(command)


@pytest.mark.parametrize("command", NOT_CHECKS)
def test_not_a_check(command):
    assert not is_check(command)


def test_newest_per_command_failing_first_at_most_six():
    events = [tool(0, summary="pytest -q", exit=1), tool(1, summary="npm run lint", exit=0),
              tool(2, summary="pytest -q", exit=0), tool(3, summary="mypy src", exit=2),
              tool(4, summary="python app.py", exit=1), tool(5, summary="ruff check .", exit=None)]
    events += [tool(6 + i, summary="go test ./pkg{}".format(i), exit=0) for i in range(6)]
    save = compile_events(events)
    assert [c["command"] for c in save["checks"]] == ["mypy src", "go test ./pkg5", "go test ./pkg4", "go test ./pkg3",
                                                       "go test ./pkg2", "go test ./pkg1"]
    assert save["checks"][0] == {"command": "mypy src", "exit": 2, "at": events[3]["ts"]}
    assert all(c["command"] != "ruff check ." for c in save["checks"])  # unknown exit: neither pass nor fail


def test_end_to_end_checks_in_context_and_save_md(tmp_path):
    repo = Repo(tmp_path / "repo")
    repo.prompt("rewrite the CSV exporter so it streams rows instead of loading them all")
    repo.bash("pytest -q", exit=1)
    repo.claude("PostToolUse-Edit")
    repo.bash("pytest -q")
    repo.bash("npm run lint", exit=1)
    save = repo.save()
    at = save["checks"][1]["at"][11:16]
    line = 'Checks: "npm run lint" failing (exit 1); "pytest -q" passed at {} UTC.'.format(at)
    assert line in context(save, force=True)
    markdown = repo.markdown()
    assert "## Checks\n- `npm run lint` failing (exit 1) at " in markdown and "- `pytest -q` passed at " in markdown
