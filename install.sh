#!/bin/sh
# Install agent-autosave on macOS or Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/KhairallahA/agent-autosave/main/install.sh | sh
#   curl -fsSL .../install.sh | sh -s -- --uninstall
#   sh install.sh --from .              # from a checkout, a wheel or an sdist
#
# Re-running it upgrades in place. It uses pipx when pipx is installed;
# otherwise it makes a private virtualenv and links the command into
# ~/.local/bin. It never uses sudo and never edits your shell profile.
#
# Options (each also has an environment variable):
#   --from SOURCE     what to install: a path, a URL or a pip requirement
#                     (AGENT_AUTOSAVE_SOURCE; default: agent-autosave from PyPI)
#   --version X       a release from PyPI, e.g. 0.1.0 (AGENT_AUTOSAVE_VERSION)
#   --method M        auto, pipx or venv (AGENT_AUTOSAVE_METHOD; default auto)
#   --uninstall       remove what this script installed
#   -h, --help        this text
# Other variables: AGENT_AUTOSAVE_PYTHON (interpreter to use),
#   AGENT_AUTOSAVE_HOME (default ~/.local/share/agent-autosave),
#   AGENT_AUTOSAVE_BIN_DIR (default ~/.local/bin),
#   AGENT_AUTOSAVE_PIP_ARGS (extra pip arguments, e.g. --no-index).
set -eu

NAME=agent-autosave
MIN_MAJOR=3
MIN_MINOR=9

say() { printf '%s\n' "$*"; }
fail() { printf 'agent-autosave install: %s\n' "$1" >&2; [ $# -lt 2 ] || printf '  hint: %s\n' "$2" >&2; exit 1; }
usage() {
  cat <<'USAGE'
Install agent-autosave. Re-running it upgrades in place.

  sh install.sh [--from SOURCE | --version X] [--method auto|pipx|venv]
  sh install.sh --uninstall

  --from SOURCE   a path (checkout, wheel, sdist), URL or pip requirement
                  (default: agent-autosave from PyPI)
  --version X     a PyPI release, e.g. 0.1.0
  --method M      pipx when it is installed (auto), or a private virtualenv
  --uninstall     remove what this script installed

Environment: AGENT_AUTOSAVE_SOURCE, AGENT_AUTOSAVE_VERSION,
AGENT_AUTOSAVE_METHOD, AGENT_AUTOSAVE_PYTHON, AGENT_AUTOSAVE_HOME,
AGENT_AUTOSAVE_BIN_DIR, AGENT_AUTOSAVE_PIP_ARGS.
USAGE
}

SOURCE=${AGENT_AUTOSAVE_SOURCE:-}
VERSION=${AGENT_AUTOSAVE_VERSION:-}
METHOD=${AGENT_AUTOSAVE_METHOD:-auto}
ACTION=install
value() {  # value <option> <next argument or empty>
  case ${2:-} in ''|-*) printf 'agent-autosave install: %s needs a value\n' "$1" >&2; exit 2 ;; esac
}
while [ $# -gt 0 ]; do
  case $1 in
    --from) value "$1" "${2:-}"; SOURCE=$2; shift 2 ;;
    --from=*) SOURCE=${1#*=}; shift ;;
    --version) value "$1" "${2:-}"; VERSION=$2; shift 2 ;;
    --version=*) VERSION=${1#*=}; shift ;;
    --method) value "$1" "${2:-}"; METHOD=$2; shift 2 ;;
    --method=*) METHOD=${1#*=}; shift ;;
    --uninstall) ACTION=uninstall; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'agent-autosave install: unknown option %s\n  hint: sh install.sh --help\n' "$1" >&2; exit 2 ;;
  esac
done
case $METHOD in auto|pipx|venv) ;; *) printf 'agent-autosave install: --method must be auto, pipx or venv, not %s\n' "$METHOD" >&2; exit 2 ;; esac
[ -n "${HOME:-}" ] || fail "HOME is not set"

DATA=${AGENT_AUTOSAVE_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/agent-autosave}
BIN=${AGENT_AUTOSAVE_BIN_DIR:-$HOME/.local/bin}
VENV=$DATA/venv
MARKER=$DATA/installed-by-script
PIP_ARGS=${AGENT_AUTOSAVE_PIP_ARGS:-}

# --- Python ---------------------------------------------------------------
python_ok() {  # python_ok <interpreter>: a Python new enough, with venv
  "$1" -c "import sys; sys.exit(0 if sys.version_info >= ($MIN_MAJOR, $MIN_MINOR) else 1)" >/dev/null 2>&1
}
find_python() {
  if [ -n "${AGENT_AUTOSAVE_PYTHON:-}" ]; then
    command -v "$AGENT_AUTOSAVE_PYTHON" >/dev/null 2>&1 || fail "AGENT_AUTOSAVE_PYTHON=$AGENT_AUTOSAVE_PYTHON was not found"
    python_ok "$AGENT_AUTOSAVE_PYTHON" || fail "$AGENT_AUTOSAVE_PYTHON is $(python_version "$AGENT_AUTOSAVE_PYTHON"); agent-autosave needs Python $MIN_MAJOR.$MIN_MINOR or newer"
    PYTHON=$AGENT_AUTOSAVE_PYTHON; return
  fi
  seen=""
  for candidate in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 python; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if python_ok "$candidate"; then PYTHON=$candidate; return; fi
    seen="$seen $candidate ($(python_version "$candidate"))"
  done
  if [ -n "$seen" ]; then
    fail "agent-autosave needs Python $MIN_MAJOR.$MIN_MINOR or newer; found only:$seen" \
      "install a newer Python (brew install python, or your package manager), or set AGENT_AUTOSAVE_PYTHON"
  fi
  fail "python3 was not found on PATH" "install Python $MIN_MAJOR.$MIN_MINOR or newer, then run this again"
}
python_version() { "$1" -c 'import sys; print("Python %d.%d" % sys.version_info[:2])' 2>/dev/null || echo "an unknown version"; }

# --- What to install -------------------------------------------------------
requirement() {
  if [ -n "$SOURCE" ]; then
    [ -z "$VERSION" ] || fail "use --from or --version, not both"
    if [ -e "$SOURCE" ]; then  # a local path: make it absolute
      case $SOURCE in /*) ;; *) SOURCE=$(cd "$(dirname "$SOURCE")" && pwd)/$(basename "$SOURCE") ;; esac
    fi
    printf '%s' "$SOURCE"
  elif [ -n "$VERSION" ]; then
    printf '%s==%s' "$NAME" "${VERSION#v}"
  else
    printf '%s' "$NAME"
  fi
}

recorded_method() {
  if [ -f "$MARKER" ]; then sed -n 's/^method=//p' "$MARKER" | head -n 1; fi
}

pipx_has() { command -v pipx >/dev/null 2>&1 && pipx list --short 2>/dev/null | grep -q "^$NAME "; }

# --- Install ---------------------------------------------------------------
install_pipx() {
  command -v pipx >/dev/null 2>&1 || fail "--method pipx was asked for, but pipx is not installed" "drop --method, or install pipx first"
  # --force reinstalls, so a re-run upgrades, and a local source is picked up.
  if [ -n "$PIP_ARGS" ]; then
    pipx install --force --python "$PYTHON" --pip-args="$PIP_ARGS" "$REQ" >"$DATA/install.log" 2>&1 || { cat "$DATA/install.log" >&2; fail "pipx install failed (log: $DATA/install.log)"; }
  else
    pipx install --force --python "$PYTHON" "$REQ" >"$DATA/install.log" 2>&1 || { cat "$DATA/install.log" >&2; fail "pipx install failed (log: $DATA/install.log)"; }
  fi
  COMMAND=$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || echo "$HOME/.local/bin")/$NAME
}

install_venv() {
  if [ -d "$VENV" ] && ! "$VENV/bin/python" -c "" >/dev/null 2>&1; then
    rm -rf "$VENV"  # its Python was removed or upgraded away: rebuild it
  fi
  if [ ! -x "$VENV/bin/python" ]; then
    "$PYTHON" -m venv "$VENV" >"$DATA/install.log" 2>&1 || { rm -rf "$VENV"; cat "$DATA/install.log" >&2
      fail "could not create a virtualenv with $PYTHON" "on Debian or Ubuntu: sudo apt install python3-venv"; }
  fi
  # shellcheck disable=SC2086  # PIP_ARGS is a list of words on purpose
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade --force-reinstall $PIP_ARGS "$REQ" >>"$DATA/install.log" 2>&1 ||
    { cat "$DATA/install.log" >&2; fail "pip could not install $REQ (log: $DATA/install.log)"; }
  link=$BIN/$NAME
  if [ -e "$link" ] || [ -L "$link" ]; then
    if [ "$(readlink "$link" 2>/dev/null || true)" != "$VENV/bin/$NAME" ]; then
      fail "$link already exists and was not installed by this script" \
        "remove it (pip uninstall agent-autosave, or pipx uninstall agent-autosave), or set AGENT_AUTOSAVE_BIN_DIR"
    fi
  fi
  mkdir -p "$BIN"
  ln -sf "$VENV/bin/$NAME" "$link"
  COMMAND=$link
}

do_install() {
  find_python
  REQ=$(requirement)
  mkdir -p "$DATA"
  method=$METHOD
  if [ "$method" = auto ]; then
    method=$(recorded_method)
    if [ -z "$method" ]; then
      if command -v pipx >/dev/null 2>&1; then method=pipx; else method=venv; fi
    fi
  fi
  previous=$(recorded_method)
  if [ -n "$previous" ] && [ "$previous" != "$method" ]; then
    fail "agent-autosave was installed with $previous by this script" "run it with --uninstall first, then install with --method $method"
  fi
  say "Installing $REQ with $method ($(python_version "$PYTHON"))..."
  "install_$method"
  printf 'method=%s\nbin=%s\n' "$method" "$COMMAND" >"$MARKER"
  version=$("$COMMAND" --version </dev/null 2>/dev/null | sed -n 's/.*"version": *"\([^"]*\)".*/agent-autosave \1/p') || fail "installed, but $COMMAND --version failed" "see $DATA/install.log"
  [ -n "$version" ] || fail "installed, but $COMMAND --version printed nothing" "see $DATA/install.log"
  say "Installed $version at $COMMAND"
  dir=$(dirname "$COMMAND")
  case ":$PATH:" in
    *":$dir:"*)
      found=$(command -v "$NAME" 2>/dev/null || true)
      if [ -n "$found" ] && [ "$found" != "$COMMAND" ]; then
        say "Note: another $NAME comes first on PATH: $found"
      fi ;;
    *)
      say "Note: $dir is not on PATH. Add it, e.g. for zsh:"
      say "  echo 'export PATH=\"$dir:\$PATH\"' >> ~/.zshrc && exec zsh" ;;
  esac
  say "Next: cd into a repository and run: $NAME init"
}

# --- Uninstall -------------------------------------------------------------
do_uninstall() {
  method=$(recorded_method)
  if [ -z "$method" ]; then
    if pipx_has; then
      fail "agent-autosave was installed with pipx, not by this script" "pipx uninstall agent-autosave"
    fi
    fail "nothing to uninstall: this script did not install agent-autosave (no $MARKER)" \
      "if you used Homebrew: brew uninstall agent-autosave; pip: python3 -m pip uninstall agent-autosave"
  fi
  registry=${XDG_CONFIG_HOME:-$HOME/.config}/agent-autosave/installs.json
  if [ -s "$registry" ]; then
    say "Note: folders set up with '$NAME init' keep their hooks. Run '$NAME remove' in each first;"
    say "      they are listed in $registry"
  fi
  case $method in
    pipx) if pipx_has; then pipx uninstall "$NAME" >/dev/null || fail "pipx uninstall failed"; fi ;;
    venv)
      link=$BIN/$NAME
      if [ "$(readlink "$link" 2>/dev/null || true)" = "$VENV/bin/$NAME" ]; then rm -f "$link"; fi
      rm -rf "$VENV" ;;
    *) fail "unrecognised install record in $MARKER" ;;
  esac
  rm -f "$MARKER" "$DATA/install.log"
  rmdir "$DATA" 2>/dev/null || true
  say "Uninstalled agent-autosave ($method)."
}

"do_$ACTION"
