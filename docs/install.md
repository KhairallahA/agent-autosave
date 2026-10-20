# Installing

**Who it's for:** Anyone installing, upgrading or removing agent-autosave on macOS, Linux or Windows.

agent-autosave needs Python 3.9 or newer and nothing else. Every way below
gives you the same `agent-autosave` command. macOS and Linux are supported;
Windows is experimental for v0.1.

> **Before v0.1.0 is published** on PyPI and Homebrew, only
> [from a checkout](#from-a-checkout) works.

| Way | Install | Upgrade | Uninstall |
| --- | --- | --- | --- |
| Homebrew | `brew install KhairallahA/agent-autosave/agent-autosave` | `brew upgrade agent-autosave` | `brew uninstall agent-autosave` |
| pipx | `pipx install agent-autosave` | `pipx upgrade agent-autosave` | `pipx uninstall agent-autosave` |
| Script | `curl -fsSL https://raw.githubusercontent.com/KhairallahA/agent-autosave/main/install.sh \| sh` | run it again | `curl -fsSL …/install.sh \| sh -s -- --uninstall` |
| Windows | `irm https://raw.githubusercontent.com/KhairallahA/agent-autosave/main/install.ps1 \| iex` | run it again | `.\install.ps1 -Uninstall` |

Before you uninstall, run `agent-autosave remove --all` in each folder you
set up, so your agents' config files go back to how they were. The install
scripts remind you, and list where those folders are recorded.

## The install script

`install.sh` is a POSIX `sh` script. It never uses `sudo` and never edits
your shell profile.

1. It finds a Python 3.9 or newer (`python3`, then `python3.14` down to
   `python3.9`) and stops with a clear message if there is none.
2. If pipx is installed, it runs `pipx install --force`. Otherwise it makes
   a virtualenv in `~/.local/share/agent-autosave/venv` and links
   `~/.local/bin/agent-autosave` to it.
3. It runs `agent-autosave --version` to check the install, and tells you
   if `~/.local/bin` is not on your `PATH`.

Running it again reinstalls the newest release in the same place, so a
re-run is an upgrade. It remembers which way it installed
(`~/.local/share/agent-autosave/installed-by-script`) and refuses to mix
them. If `~/.local/bin/agent-autosave` already exists and it didn't put it
there, it stops instead of overwriting it.

| Option | Variable | What it does |
| --- | --- | --- |
| `--from SOURCE` | `AGENT_AUTOSAVE_SOURCE` | Install a checkout, wheel, sdist, URL or pip requirement instead of the PyPI release |
| `--version X` | `AGENT_AUTOSAVE_VERSION` | Install release `X` from PyPI |
| `--method M` | `AGENT_AUTOSAVE_METHOD` | `auto` (default), `pipx` or `venv` |
| `--uninstall` | | Remove what the script installed |
| | `AGENT_AUTOSAVE_PYTHON` | The Python to use |
| | `AGENT_AUTOSAVE_HOME` | Where the virtualenv goes (default `~/.local/share/agent-autosave`) |
| | `AGENT_AUTOSAVE_BIN_DIR` | Where the command is linked (default `~/.local/bin`) |
| | `AGENT_AUTOSAVE_PIP_ARGS` | Extra pip arguments, e.g. `--no-index` |

Options go after `sh -s --` when the script is piped:
`curl -fsSL …/install.sh | sh -s -- --version 0.1.0`.

`install.ps1` takes the same choices as `-From`, `-Version`, `-Method` and
`-Uninstall`. It installs into `%LOCALAPPDATA%\agent-autosave` and adds its
`bin` folder to your user `PATH`.

## From a checkout

```sh
git clone https://github.com/KhairallahA/agent-autosave
sh agent-autosave/install.sh --from ./agent-autosave
```

Or, with pip alone: `python3 -m pip install --user ./agent-autosave`.

## Homebrew

The formula is `Formula/agent-autosave.rb` in this repository. It is a
Python virtualenv formula with no resources, because agent-autosave has
no dependencies. Its copy in the tap repository
(`KhairallahA/homebrew-agent-autosave`) is what `brew install` reads.
[Releasing](releasing.md) explains how the two are kept in step.

## How the installers are tested

`tests/test_installers.py` runs everything offline in a scratch `HOME`: it
builds a wheel of the checkout and installs that, with pip's index switched
off, so the tests need neither PyPI nor a public repository.

- `install.sh`: shellcheck, a parse in `sh`, `dash`, `bash` and `zsh`,
  install, re-run, upgrade (to a wheel versioned 99.0.0), uninstall, and
  rebuilding a virtualenv whose Python was removed. It also covers an
  existing pipx install, refusing Python 3.8 or a missing Python, refusing
  to overwrite a command it didn't install, and every usage error.
- `install.ps1`: a structural check always. Where `pwsh` is installed, also
  a parse, PSScriptAnalyzer, and a real install, upgrade and uninstall
  (which runs the script's non-Windows branch).
- The formula: a structural check always, plus `ruby -c` with Ruby and
  `brew style` with Homebrew. `brew install` and `brew test` from a local tap
  are a manual step before each release ([Releasing](releasing.md)).

## Check the install

Every installer finishes by running this; run it yourself to see which version is on your `PATH`:

<!-- run: version -->
```console
$ agent-autosave --version
agent-autosave 0.1.0
```
<!-- end run -->

After upgrading the package, refresh the hooks in each set-up repo:

<!-- run: upgrade -->
```console
$ agent-autosave upgrade --yes
Upgraded: claude-code
```
<!-- end run -->

## See also

- [Getting started](getting-started.md): what to do once it is installed
- [Releasing](releasing.md): how the formula and PyPI release are made
- [Troubleshooting](troubleshooting.md#hooks-do-nothing-when-the-agent-starts-from-an-ide-or-desktop-app): when an app cannot find the command
- [Install tests](testing/install.md): the manual installer checks
