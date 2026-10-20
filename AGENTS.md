# agent-autosave: rules for coding agents working on this repo

- The docs describe the current behavior: [docs/contracts.md](docs/contracts.md),
  [the compiler rules](docs/compiler.md#compiler-rules) and
  [the save format](docs/spec/save-format-v1.md). Read the relevant page before
  changing behavior. If the docs are unclear or contradict each other, stop and ask.
- Follow [CONTRIBUTING.md](CONTRIBUTING.md). Its code rules, adapter rules and test
  conventions apply to every change.
- A bug fix starts with a failing test. Tests must not depend on timing, order or
  the machine's environment.
- Standard library only at runtime, for Python 3.9 to 3.14. No network calls.
- Work in small commits with conventional messages (`feat:`, `fix:`, `test:`,
  `docs:`). Add user-visible changes to CHANGELOG.md.
- Code and docs are original. Don't copy from other projects.
