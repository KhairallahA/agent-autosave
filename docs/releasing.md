# Releasing

**Who it's for:** The maintainer cutting a release.

Nothing is published until the maintainer says so. This page is the checklist
for that moment. Until the repository is public, GitHub Actions is
disabled, so steps marked *(Actions)* are done by hand.

## Once, before the first release

- **PyPI.** Reserve `agent-autosave`, then add a trusted publisher: GitHub
  repository owner `KhairallahA`, repository `agent-autosave`, workflow `release.yml`,
  environment `pypi`.
- **Workflow.** The release workflow stays in
  `packaging/github-actions/release.yml` in the repository. At publish time
  the maintainer copies it to `.github/workflows/release.yml` and commits it,
  with an account that may push workflow files. It is kept out of
  `.github/workflows/` until then, so that nothing can run before you choose.
- **GitHub.** Create the `pypi` environment and require your approval for it,
  so a pushed tag waits for you before anything is uploaded.
- **Tap.** Create the public repository `KhairallahA/homebrew-agent-autosave`
  with `packaging/homebrew-tap/README.md` as its README and an empty
  `Formula/` folder.

## Going public (the first release only)

The public repository starts from a fresh export of `main` as a single
commit, so none of the development history is published. In this order,
each by the maintainer:

1. Merge the release branch into `main`. `scripts/scrub-rollout.py --check`
   must pass on every recorded fixture.
2. Export the tree at `main` into a new folder and make it one commit:

   ```sh
   mkdir /tmp/agent-autosave-public
   git archive main | tar -x -C /tmp/agent-autosave-public
   cd /tmp/agent-autosave-public
   git init -b main && git add -A && git commit -m "agent-autosave 0.1.0"
   ```

3. Run the privacy scan on it, with any private names in a file kept outside
   the repo, one per line. It must print `scan-history: 0 hit(s)`:

   ```sh
   python3 scripts/scan-history.py --forbid-file ~/public-forbid.txt
   ```

4. Create the new, empty public repository on GitHub, add it as `origin`
   and push `main` to it.
5. In the new repository, copy `packaging/github-actions/release.yml` to
   `.github/workflows/release.yml` and commit it.
6. Make the new repository public (Settings, General, Danger Zone), then turn
   on GitHub Actions for it. Point the README, the formula and
   `pyproject.toml` URLs at it if the name changed.
7. Do the steps of "Each release" below; step 4 is the moment you say
   "Publish".

## Each release

1. Set `__version__` in `agent_autosave/__init__.py`, give the CHANGELOG
   section the version and today's date (for 0.1.0, replace "unreleased"),
   and write `docs/releases/vX.Y.Z.md`.
2. Run `scripts/check.sh`. It must print PASS for every Python version.
   Then work through the [release checklist](testing/release-checklist.md) by hand.
3. Try the formula from a local tap (macOS, zsh):

   ```sh
   uv build --out-dir dist
   brew tap-new --no-git local/agent-autosave
   python3 scripts/update-formula.py dist/agent_autosave-X.Y.Z.tar.gz \
     --url "file://$PWD/dist/agent_autosave-X.Y.Z.tar.gz" \
     --out "$(brew --repository)/Library/Taps/local/homebrew-agent-autosave/Formula/agent-autosave.rb"
   brew install --build-from-source local/agent-autosave/agent-autosave
   brew test local/agent-autosave/agent-autosave
   brew audit --strict --formula local/agent-autosave/agent-autosave
   brew style local/agent-autosave/agent-autosave
   brew uninstall agent-autosave && brew untap local/agent-autosave
   ```

4. **Publish:** tag and push, `git tag vX.Y.Z && git push origin vX.Y.Z`.
   *(Actions)* `release.yml` checks the tag against the version, tests,
   builds, and waits for your approval. It then uploads to PyPI and creates
   the GitHub release from `docs/releases/vX.Y.Z.md`. By hand instead:
   `uv build`, `uvx twine upload dist/*`, and
   `gh release create vX.Y.Z dist/* --notes-file docs/releases/vX.Y.Z.md`.
5. **Tap:** download the sdist from PyPI, then run
   `scripts/update-formula.py agent_autosave-X.Y.Z.tar.gz --out <tap>/Formula/agent-autosave.rb`
   and commit and push the tap.
6. Check it from nothing:
   `brew install KhairallahA/agent-autosave/agent-autosave`,
   `pipx install agent-autosave` and `curl … install.sh | sh` each give
   `agent-autosave X.Y.Z`.
7. After the first release, remove the "Before v0.1.0 is published"
   notes from README.md, docs/install.md and docs/getting-started.md.

### The formula's URL (Homebrew's PyPiUrls rule)

`brew style` and `brew audit` run Homebrew's `FormulaAudit/PyPiUrls` rule.
It rejects a formula `url` on `pypi.python.org` and the short form
`https://files.pythonhosted.org/packages/source/a/agent-autosave/agent_autosave-X.Y.Z.tar.gz`,
and asks for the "Source" link on the PyPI downloads page instead. That link
names the file by its BLAKE2b-256 digest, split into 2, 2 and 60 hex
characters:

```text
https://files.pythonhosted.org/packages/<b2[0:2]>/<b2[2:4]>/<b2[4:64]>/agent_autosave-X.Y.Z.tar.gz
```

`scripts/update-formula.py` computes it from the sdist's bytes, with no
network, so run it on the exact file uploaded to PyPI (step 5). `--url`
still overrides it, for example with a `file://` URL for a local tap. The
committed formula holds an all-zero placeholder in this form until release.

`release.yml` runs only on a pushed `v*` tag or by hand. A manual run
builds and checks but never publishes.

README.md links and images use absolute URLs on `main`
(`github.com/.../blob/main/...` and `raw.githubusercontent.com/.../main/...`),
so they also work in PyPI's long description. `scripts/check-links.py` maps
them back to the checkout and checks them offline. They only load for
visitors once the repository is public.

## See also

- [Installing](install.md): what users run once a release is out
- [Release notes](releases/v0.1.0.md): the first release
- [Testing](testing/README.md): the checks to run before tagging
