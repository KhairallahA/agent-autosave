#!/usr/bin/env bash
# Local test matrix: full pytest (incl. slow tests) plus the performance
# budgets (docs/contracts.md, "Performance budgets") on Python 3.9-3.14. Works with macOS bash 3.2 and Linux. Needs uv.
#   scripts/check.sh                          # all five versions, in parallel
#   CHECK_PYTHONS="3.9 3.14" scripts/check.sh
#   CHECK_VENVS=/workspace/.venvs scripts/check.sh   # where venvs live (reused)
#   AGENT_AUTOSAVE_SKIP_STARTUP_BUDGET=1 scripts/check.sh   # slow cloud machines
# Day-to-day loop instead: python -m pytest -m "not slow" -n auto <tests...>
set -u
cd "$(dirname "$0")/.." || exit 2
started=$(date +%s)
if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 2
fi
VERSIONS="${CHECK_PYTHONS:-3.9 3.10 3.11 3.12 3.13 3.14}"
VENVS="${CHECK_VENVS:-.check/venvs}"
LOGS=".check"
mkdir -p "$LOGS" "$VENVS"
# Progress lines: one printf per line, so parallel jobs never interleave mid-line.
step() {  # step <version> <what> <started-epoch|""> [result]
  if [ -z "$3" ]; then
    printf '[%s] %s: started\n' "$1" "$2"
  else
    printf '[%s] %s: finished in %ss%s\n' "$1" "$2" "$(( $(date +%s) - $3 ))" "${4:+ ($4)}"
  fi
}
stamp_value=$(cat pyproject.toml | cksum | cut -d' ' -f1)

setup_and_test() {
  version=$1
  venv="$VENVS/$version"
  python="$venv/bin/python"
  t=$(date +%s); step "$version" "python install" ""
  # Reuses an already-installed interpreter; downloads only if missing.
  if ! uv python install "$version" >"$LOGS/python-install-$version.log" 2>&1; then
    step "$version" "python install" "$t" "failed"; echo setup >"$LOGS/rc-$version"; return
  fi
  step "$version" "python install" "$t"
  t=$(date +%s); step "$version" "env setup" ""
  if [ ! -x "$python" ] || [ "$(cat "$venv/.check-stamp" 2>/dev/null)" != "$stamp_value" ]; then
    { uv venv -q --allow-existing --python "$version" "$venv" &&
      uv pip install -q --python "$python" -e ".[test]" &&
      echo "$stamp_value" >"$venv/.check-stamp"; } >"$LOGS/setup-$version.log" 2>&1 ||
      { step "$version" "env setup" "$t" "failed"; echo setup >"$LOGS/rc-$version"; return; }
    step "$version" "env setup" "$t" "installed"
  else
    step "$version" "env setup" "$t" "reused"
  fi
  t=$(date +%s); step "$version" "tests" ""
  # Timing tests run later, one version at a time (see below), not under load.
  "$python" -m pytest -q -p no:cacheprovider -n auto --ignore=tests/test_performance.py >"$LOGS/pytest-$version.log" 2>&1
  rc=$?
  echo $rc >"$LOGS/rc-$version"
  step "$version" "tests" "$t" "exit $rc"
}

for version in $VERSIONS; do
  rm -f "$LOGS/rc-$version"
  setup_and_test "$version" &
done
wait

# Timings run one version at a time, after the parallel tests, so they are not
# measured under the load of five test suites.
failures=0
summary=""
for version in $VERSIONS; do
  python="$VENVS/$version/bin/python"
  rc=$(cat "$LOGS/rc-$version" 2>/dev/null || echo missing)
  if [ "$rc" = "setup" ] || [ "$rc" = "missing" ]; then
    line="FAIL  Python $version  setup failed (see $LOGS/setup-$version.log)"
    failures=$((failures + 1))
  else
    actual=$("$python" -c 'import platform; print(platform.python_version())')
    result=$(grep -E '[0-9]+ (passed|failed|error)' "$LOGS/pytest-$version.log" | tail -1 | sed 's/=//g; s/^ *//; s/ *$//')
    t=$(date +%s); step "$version" "timings" "" >&2
    "$python" -m pytest -q -p no:cacheprovider -p no:xdist tests/test_performance.py >"$LOGS/perf-$version.log" 2>&1
    prc=$?
    [ "$prc" -eq 0 ] || rc=$prc
    presult=$(grep -E '[0-9]+ (passed|failed|error)' "$LOGS/perf-$version.log" | tail -1 | sed 's/=//g; s/^ *//; s/ *$//')
    result="$result; timing tests $presult"
    bench=$("$python" scripts/bench.py 2>&1)
    step "$version" "timings" "$t" >&2
    brc=$?
    if [ "$rc" -eq 0 ] && [ "$brc" -eq 0 ]; then status="PASS"; else status="FAIL"; failures=$((failures + 1)); fi
    line="$status  Python $actual  $result  |  $bench"
  fi
  summary="$summary$line
"
done
printf '%s' "$summary"
elapsed=$(( $(date +%s) - started ))
printf '%swall time %ss\n' "$summary" "$elapsed" >"$LOGS/summary.txt"
if [ "$failures" -ne 0 ]; then
  echo "check.sh: $failures version(s) failed in ${elapsed}s; logs in $LOGS/" >&2
  exit 1
fi
echo "check.sh: all versions passed in ${elapsed}s"
