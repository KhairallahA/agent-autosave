# FAQ

**Who it's for:** Anyone deciding whether to install agent-autosave, or wondering about a behaviour before reading the details.

### Does it send my code or prompts anywhere?

No. agent-autosave makes no network calls. Everything it records stays in
`.autosave/` in your repository, which `init` adds to `.gitignore`. Secrets
are masked before anything is written; see [security](security.md).

### Does it slow my agent down?

A hook takes about 70 ms, under a 300 ms budget, and always exits 0, so a
problem in agent-autosave can never fail or block the agent. Successful tool
calls only append a line; the save is rebuilt at turn ends and failures.

### Is the save written by an LLM?

No. The compiler is deterministic: the same events give the same bytes. A
save is a list of facts the hooks saw (goal, plan, last actions, the failing
command, files), not a summary. [How a save works](how-a-save-works.md).

### What if the save is wrong or out of date?

The block an agent gets ends with "Git is the source of truth if the two
differ", and says when commits landed since the save or it is more than a
day old. You can rebuild it (`agent-autosave save`), add a decision
(`agent-autosave note`), or start fresh (`agent-autosave clear`).

### Which agents does it support?

Claude Code and Codex are tested on real installs. Antigravity is tested
through `resume`. Cursor and Gemini CLI are built from their docs and not
yet tested on a real install. Any agent that reads `AGENTS.md` can use the
generic block. See the [adapters](adapters/).

### Will it overwrite my CLAUDE.md, AGENTS.md or hook settings?

No. JSON settings are merged (your hooks stay first), and Markdown files get
a marked block. `install.json` records every change, and `agent-autosave
remove` restores each file byte for byte, or deletes it if agent-autosave
created it.

### Why did init skip Codex?

Codex runs project hooks only in folders it trusts, and agent-autosave will
not put hooks in your user-level Codex config unless you ask with
`--user-level`. Trust the folder, then `agent-autosave add codex`.
[Troubleshooting](troubleshooting.md#codex-records-nothing).

### Can I commit the save so a teammate's agent picks it up?

Partly, on purpose. `agent-autosave share on` commits project memory and the
journal, so a teammate's agents are given what yours learned; the ledger and
the save stay private, because they hold your prompts. To hand one task to
a teammate, `agent-autosave handoff export` writes `.autosave-handoff.md`
for them to `import` on the same branch. See [sharing](sharing.md).

### How do I uninstall it?

`agent-autosave remove --all` restores every file it changed. Then delete
`.autosave/` and `pip uninstall agent-autosave`.

### Does it work on Windows?

The code is written to run there (the wizard falls back to `msvcrt` and
line prompts), but the test suite is not verified on Windows yet. macOS and
Linux are supported.

## See also

- [Getting started](getting-started.md): a whole handoff with real output
- [Troubleshooting](troubleshooting.md): problems and their fixes
- [Security and privacy](security.md): what is stored and masked
- [Adapters](adapters/claude-code.md): what each agent captures
