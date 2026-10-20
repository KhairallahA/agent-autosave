# Security and privacy

**Who it's for:** Anyone deciding whether agent-autosave is safe to run on their code, and anyone auditing what it keeps.

The runtime uses only the Python standard library and makes no network calls;
a test rejects network modules in the package. `init` adds `.autosave/` to
`.gitignore`, so captured state is never committed by accident.

Redaction covers API key shapes, GitHub and Slack tokens, AWS access-key IDs,
private key blocks, JWTs, bearer tokens, credential assignments, authenticated
URLs, custom patterns, and literal values from project `.env*` files. Values are
parsed as data, never evaluated. Symlinks and oversized or malformed environment
inputs fail closed. Dependency, git, virtualenv, build, and autosave directories
are excluded from the environment scan.

Retained text is redacted before capture limits are applied, so truncation cannot
leave the beginning of a complete secret. Redaction runs before temporary writes,
archives, and diagnostic serialization. Successful tool output is discarded;
failure output retains only the last 20 lines by default. Source file contents
are never read by the core. Pattern matching cannot recognize arbitrary secrets
absent from the configured patterns or environment values.

State directories use 0700 and files 0600 on POSIX. Windows uses the current
user's filesystem ACLs. State-file symlinks are rejected. Advisory locks coordinate
cooperating processes; they are not protection against a hostile local process
with the same user permissions.

Snapshots use private temporary files, flush/fsync, then same-directory replace.
Failed replacement preserves the previous snapshot and cleans the temporary.
Diagnostics are best effort with a separate short lock: a broken disk or busy
logger cannot replace the original exception. Error logs may begin with a partial
old line after size capping.

Run `python -m pytest tests/test_redact.py -q` for the planted-secret tests. They
inspect every persisted file, including rotation archives and Unicode diagnostics.

## Memory, the journal and the vault

Notes become memory only when a person accepts them: agents can propose
(`note`, `note --keep`, `memory add` without a terminal), never accept. A
note is redacted before its candidate is written, and again before any
`MEMORY.md`, journal or vault write. Recall and the session-start memory
block give agents every recorded value as one JSON-quoted string under a
line that says it is data, not instructions, and recall never runs on text
that is one of our own blocks pasted back.

The vault is written only from background work or a command, never inside
a hook's reply. Every write stays inside `<vault>/agent-autosave/<project>/`:
`..`, absolute paths and any symlink on the way are refused, and a file
agent-autosave did not write (it is not in `.agent-autosave.json`, the
folder's manifest) is never overwritten. `remove` never deletes vault
content.

With agentic-stack, agent-autosave writes only its marked block in
`.agent/memory/working/WORKSPACE.md` and runs `.agent/tools/track.py add
case` with redacted arguments and a 2-second limit. It reads
`lessons.jsonl`; it never edits agentic-stack's files otherwise.

## Redaction, for real

After `agent-autosave note "The staging key is sk-ant-api03-…" --type warning`, the ledger holds:

<!-- run: redacted -->
```console
$ grep -o 'staging key is [^"]*' .autosave/events.jsonl
staging key is [redacted:api_key]
```
<!-- end run -->

## Fixtures recorded from real sessions

Run every fixture recorded from a real agent session through
`scripts/scrub-rollout.py` before you commit it. It removes the following:
- account and user ids;
- paths outside the scratch repo (each one becomes its basename);
- encrypted content;
- base, developer and system instructions;
- plugin and skill names;
- every prompt except the scripted test prompts passed with
  `--keep-prompt`.

`tests/test_scrub_rollout.py` runs `--check` over all of them.

`scripts/scan-history.py` scans every version of every file and every commit
message for tokens, keys, personal paths, account ids and email addresses.
It prints where each hit is, never the hit itself
([contributing](../CONTRIBUTING.md#privacy-check)).

## See also

- [Configuration](configuration.md): `redact.extra_patterns` and `redact.redact_env_values`
- [Memory](memory.md): what is proposed, accepted and recalled
- [Local format](format.md): every file that is written
- [FAQ](faq.md#does-it-send-my-code-or-prompts-anywhere): the short answer
