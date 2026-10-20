# Sharing with a team

**Who it's for:** Teams who want project memory in git, or who hand a task to a teammate.

## Commit project memory

<!-- run: share-on -->
```console
$ agent-autosave share on
Sharing on: commit .autosave/MEMORY.md and .autosave/journal/ (the ledger and saves stay private).
```
<!-- end run -->

```sh
git add .gitignore .gitattributes .autosave/MEMORY.md .autosave/journal
```

`share on` changes our `.gitignore` block from `.autosave/` to
`.autosave/*` with `!.autosave/MEMORY.md` and `!.autosave/journal/`, so git
tracks project memory and the journal while the ledger, saves, branch slots
and memory candidates stay private. It adds `MEMORY.md merge=union` to
`.gitattributes`, so two teammates' new entries both survive a merge.

Before anything changes, every line of `MEMORY.md` and the journal is
checked with the same patterns that redact the ledger. If one matches,
`share on` refuses (exit 1, `secrets_found`) and lists each file and line.

<!-- run: share-off -->
```console
$ agent-autosave share off
Sharing off: .autosave/ is ignored again (files already committed stay in git).
```
<!-- end run -->

`share off` ignores `.autosave/` again; files you already committed stay in
git history. Sharing needs the in-repo mode: with `vault.path` set, share
the vault instead.

## Hand a task to a teammate

<!-- run: handoff-export -->
```console
$ agent-autosave handoff export
Wrote .autosave-handoff.md: commit it (or send it) for your teammate.
```
<!-- end run -->

On the teammate's machine, on the same branch:

<!-- run: handoff-import -->
```console
$ agent-autosave handoff import
Imported 6 events: Add a failing check for hello.py in tests/test_hello.py (interrupted). The next agent session picks it up.
```
<!-- end run -->

The file has frontmatter (task, branch, status, goal), the current
`SAVE.md`, and the task's ledger events. `import` replays the events into a
new task on the teammate's branch, so the save is rebuilt exactly, and the
teammate's next agent session is handed the task as usual. It refuses a
file exported from another branch.

## See also

- [Project memory](memory.md)
- [Security](security.md)
- [Contracts](contracts.md)
