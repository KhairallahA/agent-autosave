"""install.sh, install.ps1 and the Homebrew formula.

The installers are exercised offline: each test builds a wheel of this
checkout (with any version it needs) and installs it into a scratch HOME,
with pip's index switched off. Nothing here reaches PyPI or GitHub, so the
tests pass while the repository is private and before anything is published.
"""
import base64
import contextlib
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

from agent_autosave import __version__
from tests.helpers import ROOT

INSTALL_SH = ROOT / "install.sh"
INSTALL_PS1 = ROOT / "install.ps1"
FORMULA = ROOT / "Formula" / "agent-autosave.rb"
POSIX = pytest.mark.skipif(os.name == "nt", reason="install.sh is for macOS and Linux")
PWSH = shutil.which("pwsh")
# Tools install.sh calls, for a PATH that holds nothing else (no pipx, no python3).
# Real installs wait for one another on a machine-wide lock (see machine_lock),
# and pytest-timeout counts that wait, so this module gets more than 60 s.
pytestmark = pytest.mark.timeout(900)
TOOLS = ("sh", "sed", "head", "grep", "mkdir", "rm", "ln", "readlink", "dirname", "basename", "cat", "rmdir")


# --- builders -------------------------------------------------------------

def build_wheel(dest: Path, version: str = __version__) -> Path:
    """A wheel of this checkout's package, made with zipfile (no build backend, no network)."""
    name = "agent_autosave-{}-py3-none-any.whl".format(version)
    info = "agent_autosave-{}.dist-info/".format(version)
    files = {}
    for path in sorted((ROOT / "agent_autosave").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc", ".pyo"):
            data = path.read_bytes()
            if path == ROOT / "agent_autosave" / "__init__.py":
                data = re.sub(rb'__version__ = "[^"]+"', '__version__ = "{}"'.format(version).encode(), data)
            files[path.relative_to(ROOT).as_posix()] = data
    files[info + "METADATA"] = "Metadata-Version: 2.1\nName: agent-autosave\nVersion: {}\nRequires-Python: >=3.9\n".format(version).encode()
    files[info + "WHEEL"] = b"Wheel-Version: 1.0\nGenerator: tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    files[info + "entry_points.txt"] = b"[console_scripts]\nagent-autosave = agent_autosave.cli:main\n"
    record = []
    for path, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record.append("{},sha256={},{}".format(path, digest, len(data)))
    record.append(info + "RECORD,,")
    files[info + "RECORD"] = ("\n".join(record) + "\n").encode()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(dest / name), "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return dest / name


@pytest.fixture(scope="module")
def wheels(tmp_path_factory):
    folder = tmp_path_factory.mktemp("wheels")
    return {"current": build_wheel(folder / "a"), "next": build_wheel(folder / "b", "99.0.0")}


def only_tools(folder: Path, **extra) -> str:
    """A PATH folder holding the shell tools and whatever is passed as name=target."""
    folder.mkdir(parents=True, exist_ok=True)
    for tool in TOOLS:
        found = shutil.which(tool)
        if found:
            (folder / tool).symlink_to(found)
    for name, target in extra.items():
        (folder / name).symlink_to(target)
    return str(folder)


def fake_python(folder: Path, major_minor: str) -> Path:
    """A python3 that reports an old version and fails the version check."""
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "python3"
    major, minor = major_minor.split(".")
    script.write_text("#!/bin/sh\ncase \"$*\" in *'print('*) echo 'Python {}.{}'; exit 0 ;; esac\nexit 1\n".format(major, minor))
    script.chmod(0o755)
    return script


class Home:
    """A scratch HOME for one installer run after another."""

    def __init__(self, root: Path, path=None):
        self.root = root
        self.env = {"HOME": str(root), "PATH": path or os.environ["PATH"], "LANG": "C.UTF-8",
                    "AGENT_AUTOSAVE_PIP_ARGS": "--no-index", "AGENT_AUTOSAVE_PYTHON": sys.executable,
                    "PIPX_HOME": str(root / "pipx"), "PIPX_BIN_DIR": str(root / ".local/bin"),
                    "PIPX_MAN_DIR": str(root / ".local/share/man"), "XDG_CONFIG_HOME": str(root / ".config"),
                    "POWERSHELL_TELEMETRY_OPTOUT": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}

    @property
    def data(self) -> Path:
        return self.root / ".local/share/agent-autosave"

    @property
    def link(self) -> Path:
        return self.root / ".local/bin/agent-autosave"

    def sh(self, *args, shell="sh", **env):
        return subprocess.run([shell, str(INSTALL_SH)] + [str(a) for a in args], env=dict(self.env, **env),
                              capture_output=True, text=True, timeout=240, cwd=str(self.root))

    def ps1(self, *args, **env):
        return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(INSTALL_PS1)] + [str(a) for a in args],
                              env=dict(self.env, **env), capture_output=True, text=True, timeout=240, cwd=str(self.root))

    def version(self, command=None) -> str:
        out = subprocess.run([str(command or self.link), "--version", "--json"], capture_output=True, text=True,
                             env=self.env, timeout=60).stdout
        return re.search(r'"version": "([^"]+)"', out).group(1)


@pytest.fixture
def home(tmp_path, request):
    (tmp_path / "home").mkdir()
    if request.node.get_closest_marker("slow") is None:
        yield Home(tmp_path / "home")
        return
    # scripts/check.sh runs six Python versions at once, each with xdist
    # workers; one real install at a time on the machine keeps the other
    # tests' subprocess timeouts from tripping under that load.
    with machine_lock():
        yield Home(tmp_path / "home")


@contextlib.contextmanager
def machine_lock():
    try:
        import fcntl
    except ImportError:  # Windows: no lock, nothing runs six matrices at once there
        yield
        return
    with open(os.path.join(tempfile.gettempdir(), "agent-autosave-installer-tests.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def pipx_home(home):
    """pipx with its own default Python for every venv.

    pipx keeps one shared pip, built for its default Python, and runs it in
    each venv. Handing install.sh the test interpreter (or the python3 that
    scripts/check.sh puts first on PATH) mixes interpreters: a 3.9 venv can't
    run a pip that needs 3.10, and another 3.13 build lacks the C modules.
    """
    if not shutil.which("pipx"):
        pytest.skip("pipx is not installed")
    default = subprocess.run(["pipx", "environment", "--value", "PIPX_DEFAULT_PYTHON"], env=home.env,
                             capture_output=True, text=True, timeout=60).stdout.strip()
    home.env["AGENT_AUTOSAVE_PYTHON"] = default or "python3"
    return home


# --- install.sh: static checks ---------------------------------------------

@POSIX
def test_install_sh_passes_shellcheck():
    shellcheck = shutil.which("shellcheck")
    if not shellcheck:
        pytest.skip("shellcheck is not installed")
    result = subprocess.run([shellcheck, "--shell=sh", "--severity=style", str(INSTALL_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout


@POSIX
@pytest.mark.parametrize("shell", ["sh", "dash", "bash", "zsh"])
def test_install_sh_parses_in_every_shell(shell):
    if not shutil.which(shell):
        pytest.skip(shell + " is not installed")
    assert subprocess.run([shell, "-n", str(INSTALL_SH)]).returncode == 0


def test_install_sh_is_posix_and_safe():
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert "set -eu" in text
    assert not re.search(r"^\s*sudo ", text, re.M)  # only ever named in a hint
    # Shell profiles are only ever named in a printed suggestion, never written.
    assert not re.search(r'>>\s*"?\$HOME/\.(zshrc|bashrc|profile)', text)
    assert "[[" not in text and "function " not in text  # no bashisms
    assert os.access(str(INSTALL_SH), os.X_OK) or os.name == "nt"


@POSIX
def test_install_sh_help_and_usage_errors(home):
    shown = home.sh("--help")
    assert shown.returncode == 0 and "--uninstall" in shown.stdout and "--from" in shown.stdout
    piped = subprocess.run(["sh", "-s", "--", "--help"], input=INSTALL_SH.read_text(), capture_output=True, text=True)
    assert piped.returncode == 0 and "--uninstall" in piped.stdout  # curl | sh -s -- --help
    for args, words in ((["--bogus"], "unknown option --bogus"), (["--method", "brew"], "auto, pipx or venv"),
                        (["--from"], "--from needs a value"), (["--from", "--method", "venv"], "--from needs a value")):
        result = home.sh(*args)
        assert result.returncode == 2 and words in result.stderr, (args, result.stderr)
    both = home.sh("--from", "x", "--version", "1")
    assert both.returncode == 1 and "not both" in both.stderr


# --- install.sh: Python checks ---------------------------------------------

@POSIX
def test_install_sh_refuses_an_old_python(home, tmp_path):
    old = fake_python(tmp_path / "old", "3.8")
    result = home.sh("--from", "x", AGENT_AUTOSAVE_PYTHON=str(old))
    assert result.returncode == 1
    assert "is Python 3.8; agent-autosave needs Python 3.9 or newer" in result.stderr
    found = Home(home.root, path=only_tools(tmp_path / "bin", python3=str(old)))
    found.env.pop("AGENT_AUTOSAVE_PYTHON")
    result = found.sh("--from", "x")
    assert result.returncode == 1 and "found only: python3 (Python 3.8)" in result.stderr and "hint:" in result.stderr
    assert not home.data.exists() and not home.link.exists()


@POSIX
def test_install_sh_without_python(home, tmp_path):
    bare = Home(home.root, path=only_tools(tmp_path / "bin"))
    bare.env.pop("AGENT_AUTOSAVE_PYTHON")
    result = bare.sh()
    assert result.returncode == 1 and "python3 was not found" in result.stderr
    missing = home.sh(AGENT_AUTOSAVE_PYTHON="no-such-python")
    assert missing.returncode == 1 and "was not found" in missing.stderr


# --- install.sh: install, re-run, upgrade, uninstall -----------------------

@POSIX
@pytest.mark.slow
def test_install_sh_venv_install_rerun_upgrade_uninstall(home, wheels, tmp_path):
    path = only_tools(tmp_path / "bin")  # no pipx on PATH: auto picks the venv
    home = Home(home.root, path=path)
    first = home.sh("--from", wheels["current"])
    assert first.returncode == 0, first.stderr
    assert "with venv" in first.stdout and "Installed agent-autosave " + __version__ in first.stdout
    assert "is not on PATH" in first.stdout and "~/.zshrc" in first.stdout
    assert os.readlink(str(home.link)) == str(home.data / "venv/bin/agent-autosave")
    assert home.version() == __version__
    marker = (home.data / "installed-by-script").read_text()
    assert marker.startswith("method=venv\n")

    again = home.sh("--from", wheels["current"])  # idempotent
    assert again.returncode == 0, again.stderr
    assert (home.data / "installed-by-script").read_text() == marker
    assert sorted(p.name for p in (home.root / ".local/bin").iterdir()) == ["agent-autosave"]

    upgraded = home.sh("--from", wheels["next"])  # a re-run upgrades in place
    assert upgraded.returncode == 0, upgraded.stderr
    assert "Installed agent-autosave 99.0.0" in upgraded.stdout and home.version() == "99.0.0"

    switch = home.sh("--from", wheels["next"], "--method", "pipx")
    assert switch.returncode == 1 and "installed with venv" in switch.stderr and "--uninstall" in switch.stderr

    gone = home.sh("--uninstall")
    assert gone.returncode == 0 and "Uninstalled agent-autosave (venv)" in gone.stdout
    assert not home.link.exists() and not os.path.lexists(str(home.link)) and not home.data.exists()
    twice = home.sh("--uninstall")
    assert twice.returncode == 1 and "nothing to uninstall" in twice.stderr and "brew uninstall" in twice.stderr


@POSIX
@pytest.mark.slow
def test_install_sh_rebuilds_a_venv_whose_python_is_gone(home, wheels):
    assert home.sh("--from", wheels["current"], "--method", "venv").returncode == 0
    python = home.data / "venv/bin/python"
    for name in ("python", "python3"):
        target = home.data / "venv/bin" / name
        if os.path.lexists(str(target)):
            target.unlink()
    python.symlink_to("/nonexistent/python3")
    result = home.sh("--from", wheels["current"], "--method", "venv")
    assert result.returncode == 0, result.stderr
    assert home.version() == __version__


@POSIX
@pytest.mark.slow
def test_install_sh_never_overwrites_a_command_it_did_not_install(home, wheels):
    home.link.parent.mkdir(parents=True)
    home.link.write_text("#!/bin/sh\necho mine\n")
    result = home.sh("--from", wheels["current"], "--method", "venv")
    assert result.returncode == 1
    assert "already exists and was not installed by this script" in result.stderr and "AGENT_AUTOSAVE_BIN_DIR" in result.stderr
    assert home.link.read_text() == "#!/bin/sh\necho mine\n"
    elsewhere = home.sh("--from", wheels["current"], "--method", "venv", AGENT_AUTOSAVE_BIN_DIR=str(home.root / "bin"))
    assert elsewhere.returncode == 0, elsewhere.stderr
    assert home.version(home.root / "bin/agent-autosave") == __version__


@POSIX
@pytest.mark.slow
def test_install_sh_warns_about_set_up_folders_on_uninstall(home, wheels):
    assert home.sh("--from", wheels["current"], "--method", "venv").returncode == 0
    registry = home.root / ".config/agent-autosave/installs.json"
    registry.parent.mkdir(parents=True)
    registry.write_text('{"installs": [{"root": "/somewhere"}]}')
    result = home.sh("--uninstall")
    assert result.returncode == 0 and "agent-autosave remove" in result.stdout and str(registry) in result.stdout


@POSIX
@pytest.mark.slow
def test_install_sh_installs_from_a_checkout(home, tmp_path):
    """sh install.sh --from . in a clone: the private-repo path. Building it needs
    setuptools (pyproject's build-system), which Python 3.12+ venvs no longer
    bundle, so fetch it into a local wheelhouse first (pip's cache, else the
    network) and let install.sh's pip take it from there, offline."""
    wheels = tmp_path / "wheelhouse"
    probe = tmp_path / "probe"
    made = subprocess.run([sys.executable, "-m", "venv", str(probe)], capture_output=True, text=True, timeout=120)
    if made.returncode != 0:
        pytest.skip("cannot make a venv to fetch setuptools: " + (made.stderr.strip() or made.stdout.strip())[-300:])
    fetched = subprocess.run([str(probe / "bin/python"), "-m", "pip", "download", "--quiet", "--disable-pip-version-check",
                              "--only-binary", ":all:", "--no-deps", "-d", str(wheels), "setuptools>=77"],
                             capture_output=True, text=True, timeout=180)
    if fetched.returncode != 0 or not list(wheels.glob("setuptools-*.whl")):
        pytest.skip("setuptools cannot be fetched for install.sh's venv: " + (fetched.stderr.strip() or fetched.stdout.strip())[-300:])
    checkout = tmp_path / "checkout"
    shutil.copytree(str(ROOT), str(checkout), ignore=shutil.ignore_patterns(".git", ".check", "*.egg-info", "__pycache__", "docs", "examples", "tests"))
    result = home.sh("--from", checkout, "--method", "venv", AGENT_AUTOSAVE_PIP_ARGS="--no-index --find-links " + str(wheels))
    assert result.returncode == 0, result.stdout + result.stderr


@POSIX
@pytest.mark.slow
def test_install_sh_with_pipx_install_rerun_upgrade_uninstall(home, wheels):
    home = pipx_home(home)
    first = home.sh("--from", wheels["current"])
    assert first.returncode == 0, first.stdout + first.stderr
    assert "with pipx" in first.stdout and home.version() == __version__
    upgraded = home.sh("--from", wheels["next"])
    assert upgraded.returncode == 0 and home.version() == "99.0.0"
    gone = home.sh("--uninstall")
    assert gone.returncode == 0 and "Uninstalled agent-autosave (pipx)" in gone.stdout
    assert not os.path.lexists(str(home.link))


@POSIX
@pytest.mark.slow
def test_install_sh_points_a_pipx_user_at_pipx_on_uninstall(home, wheels):
    home = pipx_home(home)
    installed = subprocess.run(["pipx", "install", "--pip-args=--no-index", str(wheels["current"])],
                               env=home.env, capture_output=True, text=True, timeout=240)
    assert installed.returncode == 0, installed.stderr
    result = home.sh("--uninstall")
    assert result.returncode == 1 and "pipx uninstall agent-autosave" in result.stderr
    rerun = home.sh("--from", wheels["next"])  # an existing pipx install is upgraded in place
    assert rerun.returncode == 0 and home.version() == "99.0.0"


# --- install.ps1 ------------------------------------------------------------

def test_install_ps1_structure():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    for parameter in ("[string]$From", "[string]$Version", "[string]$Method", "[switch]$Uninstall"):
        assert parameter in text
    for variable in ("AGENT_AUTOSAVE_SOURCE", "AGENT_AUTOSAVE_PYTHON", "AGENT_AUTOSAVE_HOME", "AGENT_AUTOSAVE_PIP_ARGS"):
        assert variable in text
    assert "$ErrorActionPreference = 'Stop'" in text and "Set-StrictMode" in text
    assert "Invoke-Expression" not in text and "iex " not in text.replace("| iex\n", "\n")
    assert "sys.version_info >= (3, 9)" in text  # the same floor as install.sh and pyproject.toml
    assert text.count("{") == text.count("}") and text.count("(") == text.count(")")
    assert "RunAs" not in text  # never asks for an administrator shell


@pytest.fixture
def pwsh():
    if not PWSH:
        pytest.skip("pwsh is not installed: only the structural check of install.ps1 ran")
    return PWSH


def test_install_ps1_parses_without_errors(pwsh):
    script = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile('{}', [ref]$null, [ref]$e); "
              "if ($e) {{ $e | ForEach-Object {{ $_.Message }}; exit 1 }}").format(INSTALL_PS1)
    result = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_install_ps1_passes_script_analyzer(pwsh):
    check = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command",
                            "if (Get-Module -ListAvailable PSScriptAnalyzer) { exit 0 } else { exit 3 }"], timeout=120)
    if check.returncode == 3:
        pytest.skip("PSScriptAnalyzer is not installed")
    script = ("$r = Invoke-ScriptAnalyzer -Path '{}' -Severity Warning,Error; "
              "$r | ForEach-Object {{ '{{0}}:{{1}} {{2}}' -f $_.RuleName, $_.Line, $_.Message }}; exit $r.Count").format(INSTALL_PS1)
    result = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.slow
def test_install_ps1_install_rerun_upgrade_uninstall(pwsh, home, wheels):
    """Runs the script for real under pwsh (on Linux or macOS here; it takes the same path on Windows)."""
    data = home.root / "data"
    first = home.ps1("-From", wheels["current"], "-Method", "venv", AGENT_AUTOSAVE_HOME=str(data))
    assert first.returncode == 0, first.stdout + first.stderr
    assert "Installed agent-autosave " + __version__ in first.stdout
    shim = data / "bin" / ("agent-autosave.cmd" if os.name == "nt" else "agent-autosave")
    assert home.version(shim) == __version__
    assert home.ps1("-From", wheels["current"], AGENT_AUTOSAVE_HOME=str(data)).returncode == 0  # idempotent, venv kept
    upgraded = home.ps1("-From", wheels["next"], AGENT_AUTOSAVE_HOME=str(data))
    assert upgraded.returncode == 0 and home.version(shim) == "99.0.0"
    gone = home.ps1("-Uninstall", AGENT_AUTOSAVE_HOME=str(data))
    assert gone.returncode == 0 and "Uninstalled agent-autosave (venv)" in gone.stdout
    assert not data.exists()
    twice = home.ps1("-Uninstall", AGENT_AUTOSAVE_HOME=str(data))
    assert twice.returncode == 1 and "nothing to uninstall" in twice.stderr


def test_install_ps1_refuses_an_old_python(pwsh, home, tmp_path):
    if os.name == "nt":
        pytest.skip("the fake interpreter is a POSIX shell script")
    old = fake_python(tmp_path / "old", "3.8")
    result = home.ps1("-From", "x", AGENT_AUTOSAVE_PYTHON=str(old))
    assert result.returncode == 1 and "is Python 3.8; agent-autosave needs Python 3.9 or newer" in result.stderr


# --- Homebrew formula -------------------------------------------------------

# Homebrew's FormulaAudit/PyPiUrls (Library/Homebrew/rubocops/urls.rb) rejects
# pypi.python.org and files.pythonhosted.org/packages/source/ URLs; PyPI's real
# file URL is packages/<b2[:2]>/<b2[2:4]>/<b2[4:]>/<file>, b2 = blake2b-256 of the file.
PYPI_HASHED = re.compile(r"^https://files\.pythonhosted\.org/packages/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{60}/[^/]+$")
PYPI_REJECTED = re.compile(r"^https?://(pypi\.python\.org/|files\.pythonhosted\.org/packages/source/)")

def test_formula_structure():
    text = FORMULA.read_text(encoding="utf-8")
    assert re.search(r"^class AgentAutosave < Formula$", text, re.M)
    assert "include Language::Python::Virtualenv" in text and "virtualenv_install_with_resources" in text
    url = re.search(r'^  url "([^"]+)"$', text, re.M).group(1)
    assert PYPI_HASHED.match(url) and url.endswith("/agent_autosave-{}.tar.gz".format(__version__)), url
    assert re.search(r'^  sha256 "[0-9a-f]{64}"$', text, re.M)
    assert 'license "Apache-2.0"' in text and re.search(r'^  depends_on "python@3\.\d+"$', text, re.M)
    assert "resource " not in text  # standard library only: nothing to vendor
    assert "test do" in text and "--version" in text and '"init"' in text
    # Order Homebrew's style check expects for these components.
    order = ["\n  desc ", "\n  homepage ", "\n  url ", "\n  sha256 ", "\n  license ", "\n  depends_on ", "\n  def install", "\n  test do"]
    positions = [text.index(item) for item in order]
    assert positions == sorted(positions)


def test_formula_is_valid_ruby():
    ruby = shutil.which("ruby")
    if not ruby:
        pytest.skip("ruby is not installed")
    result = subprocess.run([ruby, "-c", str(FORMULA)], capture_output=True, text=True)
    assert result.returncode == 0 and "Syntax OK" in result.stdout, result.stderr


def test_formula_passes_brew_style():
    brew = shutil.which("brew")
    if not brew:
        pytest.skip("Homebrew is not installed")
    result = subprocess.run([brew, "style", str(FORMULA)], capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr


def test_update_formula_defaults_to_the_hashed_pypi_url(tmp_path):
    data = b"not really an sdist"
    sdist = tmp_path / "agent_autosave-{}.tar.gz".format(__version__)
    sdist.write_bytes(data)
    out = tmp_path / "agent-autosave.rb"
    subprocess.run([sys.executable, str(ROOT / "scripts/update-formula.py"), str(sdist), "--out", str(out)],
                   check=True, capture_output=True)
    url = re.search(r'^  url "([^"]+)"$', out.read_text(), re.M).group(1)
    b2 = hashlib.blake2b(data, digest_size=32).hexdigest()
    assert url == "https://files.pythonhosted.org/packages/{}/{}/{}/{}".format(b2[:2], b2[2:4], b2[4:], sdist.name)
    assert PYPI_HASHED.match(url) and not PYPI_REJECTED.match(url)


def test_hashed_url_matches_a_real_pypi_file():
    """Recorded from PyPI's JSON API for six 1.16.0: its blake2b_256 digest and URL."""
    b2 = "7139171f1c67cd00715f190ba0b100d606d440a28c93c7714febeca8b79af85e"
    real = "https://files.pythonhosted.org/packages/71/39/171f1c67cd00715f190ba0b100d606d440a28c93c7714febeca8b79af85e/six-1.16.0.tar.gz"
    spec = __import__("importlib.util").util.spec_from_file_location("update_formula", ROOT / "scripts/update-formula.py")
    module = __import__("importlib.util").util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.pypi_url("six-1.16.0.tar.gz", b2) == real and PYPI_HASHED.match(real)


def test_update_formula_sets_url_and_sha(tmp_path):
    sdist = tmp_path / "agent_autosave-{}.tar.gz".format(__version__)
    sdist.write_bytes(b"not really an sdist")
    out = tmp_path / "tap/Formula/agent-autosave.rb"
    subprocess.run([sys.executable, str(ROOT / "scripts/update-formula.py"), str(sdist), "--url", "file://" + str(sdist),
                    "--out", str(out)], check=True, capture_output=True)
    text = out.read_text()
    assert 'url "file://{}"'.format(sdist) in text
    assert 'sha256 "{}"'.format(hashlib.sha256(b"not really an sdist").hexdigest()) in text
    assert text.replace('url "file://{}"'.format(sdist), "").count("url ") == FORMULA.read_text().count("url ") - 1
    bad = subprocess.run([sys.executable, str(ROOT / "scripts/update-formula.py"), str(tmp_path / "other.tar.gz")],
                         capture_output=True, text=True)
    assert bad.returncode == 2 and "not an agent-autosave sdist" in bad.stderr


# --- the release files ---------------------------------------------------------

def test_release_workflow_cannot_publish_by_accident():
    text = (ROOT / "packaging/github-actions/release.yml").read_text(encoding="utf-8")
    on = text[text.index("\non:"):text.index("\npermissions:")]
    assert re.findall(r"^  (\w+):", on, re.M) == ["push", "workflow_dispatch"]
    assert 'tags: ["v*"]' in on and "branches" not in on and "inputs" not in on
    publish = text[text.index("\n  pypi:"):text.index("\n  github-release:")]
    assert "if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')" in publish
    assert "id-token: write" in publish and "environment:" in publish
    assert "pypa/gh-action-pypi-publish" in publish and "password" not in text and "secrets." not in text
    assert "needs: pypi" in text  # no GitHub release without the PyPI upload


def test_release_notes_exist_for_this_version():
    notes = ROOT / "docs/releases/v{}.md".format(__version__)
    assert notes.is_file()
    assert "# agent-autosave v{}".format(__version__) in notes.read_text(encoding="utf-8")


def test_sdist_manifest_ships_the_installers():
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "include install.sh install.ps1" in manifest and "recursive-include Formula *.rb" in manifest
