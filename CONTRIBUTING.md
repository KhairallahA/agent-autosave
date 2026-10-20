# Contributing

Thanks for helping. Bug reports with the agent and its version, the command
you ran and `.autosave/errors.log` (it is redacted, but read it first) are the
most useful thing you can send.

## Set up

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[test]'
python -m pytest -q -m "not slow"   # quick run
python -m pytest -q                 # everything, including timing and fuzz tests
scripts/check.sh                    # every Python from 3.9 to 3.14 (needs uv)
```

## Rules the code keeps

- Python 3.9 or newer, standard library only at runtime. pytest is for tests.
- A hook never breaks the agent: it catches every error, logs it to
  `.autosave/errors.log`, exits 0, and stays under 300 ms.
- A user's config file is merged, never overwritten; `install.json` records
  what was added and `remove` undoes exactly that.
- Secrets are redacted before anything is written. No network calls.
- The compiler is deterministic: the same events give the same bytes.
- Every command supports `--json` and keeps the [contracts](docs/contracts.md).
- Strings use double quotes.

## Adapters

Read the agent's current hook documentation first, and follow it. Record
anything it leaves open, and every choice the adapter makes, in
`docs/adapters/<agent>.md`. Write the normalizer against recorded payloads:
[docs/testing/](docs/testing/README.md) explains how to record them, and
`tests/fixtures/<agent>/README.md` where they go. `adapters/<agent>/` and
`agent_autosave/resources/<agent>/` must stay identical (a test checks).

## Tests

Tests are pytest functions in files named by topic (`test_doctor.py`,
`test_codex_adapter.py`). Mark benchmarks, fuzzing and whole-process timing
`@pytest.mark.slow`. The compiler goldens in `tests/fixtures/compiler/`
change only on purpose: run `python scripts/update-goldens.py` and review
the whole diff. Every fixture must be read by a test.

## Privacy check

Before you open a pull request, scan your commits for tokens, keys,
personal paths, account ids and email addresses:

```sh
python3 scripts/scan-history.py --rev origin/main..HEAD
```

It prints the rule, file, line, commit and a hash of each hit, never the
text. Add `--forbid-file FILE` to also look for names of your own, one per
line; keep that file outside the repo so the names are never committed.
Known-benign hits go in `ALLOW` in the script or in an `--allowlist` file of
`rule<TAB>path-glob<TAB>sha256-prefix` lines. Recorded agent sessions also
need `scripts/scrub-rollout.py --check`.

## Pull requests

- One topic per pull request, with tests.
- Add a line under an "Unreleased" heading in [CHANGELOG.md](CHANGELOG.md) for anything
  a user would notice.
- Update the docs page the change affects; `tests/test_docs.py` checks that
  links resolve.
- Code is original. Do not paste code from another project, including
  agentic-stack, which this project credits as inspiration (see
  [licensing](docs/licensing.md)).
