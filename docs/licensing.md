# Licensing

**Who it's for:** Anyone redistributing agent-autosave or checking where its ideas came from.

agent-autosave is licensed under the [Apache License 2.0](../LICENSE).
Copyright 2026 Khairallah and agent-autosave contributors.

## agentic-stack

The repository structure, manifest-driven adapters, file-backed state and
CLI conventions were inspired by
[agentic-stack](https://github.com/codejunkie99/agentic-stack). agent-autosave
is an independent implementation: no agentic-stack code or documentation
was copied. [NOTICE](../NOTICE) records the credit; keep it in
redistributions, as the Apache License requires.

## Contributions

Contributions are accepted under the same license (Apache-2.0, section 5),
with no separate agreement. Do not submit code copied from another project
unless its license allows it and the pull request says where it came from.

## Dependencies

The installed package has no runtime dependencies. The tests use pytest,
pytest-xdist and pytest-timeout (MIT), which are not shipped.

## Data you record

Fixtures under `tests/fixtures/` are hook payloads from real sessions, with
secrets and home-directory paths removed. Only add recordings of sessions
you ran yourself, on scratch repositories, and read them before committing.

## See also

- `LICENSE` and `NOTICE` at the repository root
- [FAQ](faq.md): the short answers
- [Contributing](../CONTRIBUTING.md): how contributions are licensed
