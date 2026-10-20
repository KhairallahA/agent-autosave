"""Render docs/demo.gif and the screenshots in docs/images/ from real runs.

    python docs/demo/make.py            # both
    python docs/demo/make.py --gif      # docs/demo/demo.cast and docs/demo.gif
    python docs/demo/make.py --images   # docs/images/*.png

A development tool, never imported by the package. It needs `pyte` and
`Pillow` (python -m pip install pyte pillow, in a scratch virtualenv) and,
for the GIF, the `agg` binary (https://github.com/asciinema/agg) on PATH
or in $AGG. POSIX only.

Everything agent-autosave shows is it really running in a pseudo-terminal:
an interactive bash in a scratch git repository. In the GIF, Claude Code
and Codex are the scripted stand-ins in standin.py, which speak each
agent's hook protocol (see the top of that file); the screenshots use the
stand-ins and replayed sessions of scripts/build-docs.py. The keys are
typed for real, with pauses so a person can follow.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
capture = importlib.import_module("capture")
build_docs = importlib.import_module("build-docs")
KEYS = capture.KEYS
IMAGES = ROOT / "docs/images"


def environment(tour) -> dict:
    env = {k: v for k, v in tour.env.items() if k != "NO_COLOR"}
    env.update(PS1="\\[\\e[2m\\]~/projects/hello\\[\\e[0m\\] $ ", BASH_SILENCE_DEPRECATION_WARNING="1",
               HISTFILE="/dev/null")
    return env


def shell(tour, cwd=None, columns=96, rows=30):
    return capture.Session(["bash", "--norc", "--noprofile", "-i"], cwd=cwd or tour.project, env=environment(tour),
                           columns=columns, rows=rows)


def type_line(session, text, pause=0.05):
    for char in text:
        session.keys(char)
        session.pump(pause)
    session.keys("\r")


BUGGY = 'def valid_email(address):\n    """Signup accepts any address for now."""\n    return True\n'
DECISION = "Validate emails with the standard library only (no regex package)"


def signup_project(tour) -> None:
    """Off camera: a repo with the bug, both agents installed, and one decision
    in project memory. The agents are the scripted stand-ins in standin.py."""
    standin = ROOT / "docs/demo/standin.py"
    tour._stub("claude", '[ "$1" = --version ] && {{ echo "{} (Claude Code)"; exit 0; }}\nexec "{}" "{}" claude'
               .format(build_docs.CLAUDE_VERSION, sys.executable, standin))
    tour._stub("codex", '[ "$1" = --version ] && {{ echo "codex-cli {}"; exit 0; }}\n'
                        'exec "{}" "{}" codex "$@"'.format(build_docs.CODEX_VERSION, sys.executable, standin))
    tour._stub("python3", 'exec "{}" "$@"'.format(sys.executable))  # with pytest
    config = tour.home / ".codex/config.toml"  # what accepting Codex's trust prompt writes
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('[projects."{}"]\ntrust_level = "trusted"\n'.format(tour.project.resolve()), encoding="utf-8")
    (tour.project / "signup.py").write_text(BUGGY, encoding="utf-8")
    quiet = dict(cwd=str(tour.project), env=tour.env, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], **quiet)
    subprocess.run(["git", "-c", "user.name=Ada", "-c", "user.email=ada@example.com", "commit", "-qm", "Signup"], **quiet)
    subprocess.run([str(tour.bin / "agent-autosave"), "init", "--yes"], **quiet)
    build_docs.pty_run([str(tour.bin / "agent-autosave"), "memory", "add", "--type", "decision", DECISION],
                       tour.project, tour.env)


def demo(tour, out: Path) -> None:
    """Claude Code at 88% writes a handoff note and hits its limit; `go`
    switches to Codex, which starts with the plan, the failing test and the
    remembered decision, and finishes."""
    signup_project(tour)
    s = shell(tour, columns=100, rows=32)
    s.expect(r"(?m)\$$")
    s.pump(0.8)
    type_line(s, "agent-autosave go claude-code codex")
    s.expect("Press Enter to continue in Codex", timeout=40)
    s.pump(2.5)
    s.keys("\r")
    s.expect(r"Codex exited \(0\)")
    s.pump(2.5)
    type_line(s, "clear")
    s.pump(0.4)
    type_line(s, "agent-autosave status")
    s.expect("Full save")
    s.pump(4.0)
    s.close()
    cast = out / "demo.cast"
    capture.cast([s], cast, columns=s.columns, rows=s.rows)
    agg = os.environ.get("AGG") or shutil.which("agg")
    if not agg:
        raise SystemExit("agg not found: install it or set AGG=/path/to/agg")
    subprocess.run([agg, "--font-family", "DejaVu Sans Mono", "--font-size", "15", "--theme", "asciinema",
                    "--idle-time-limit", "3", "--last-frame-duration", "4", str(cast), str(ROOT / "docs/demo.gif")],
                   check=True)


def wizard_images(tour) -> None:
    """One screenshot per wizard screen."""
    s = shell(tour, columns=96, rows=34)
    s.expect(r"(?m)\$$")
    s.keys("clear\r")
    s.pump(0.3)
    type_line(s, "agent-autosave init", 0)
    s.expect("Which agents")
    s.keys(KEYS["down"])
    capture.snapshot(s, IMAGES / "wizard-1-agents.png", title="agent-autosave init")
    s.keys(KEYS["up"])
    s.keys("\r")
    s.expect("How do you want to handle Codex")
    capture.snapshot(s, IMAGES / "wizard-2-codex.png", title="agent-autosave init")
    s.keys("\r")
    s.expect("Keep .autosave/ out of git")
    s.keys("\r")
    s.expect("Record raw hook payloads")
    s.keys("\r")
    s.expect("MCP tools")
    s.keys("\r")
    s.expect("status line")
    capture.snapshot(s, IMAGES / "wizard-3-preferences.png", title="agent-autosave init")
    s.keys("\r")
    s.expect("Where should the journal")
    s.keys("\r")
    s.expect("Write these changes")
    s.pump(0.3)
    capture.snapshot(s, IMAGES / "wizard-4-summary.png", title="agent-autosave init")
    s.keys("\r")
    s.expect("Ready.")
    s.pump(0.3)
    capture.snapshot(s, IMAGES / "wizard-5-done.png", title="agent-autosave init")
    s.close()


def command_images(tour) -> None:
    """status, doctor, history, init (--yes), remove and show, as a terminal shows them."""
    project = tour.project
    def snap(name, command, cwd=None, rows=34, plain=False):
        s = shell(tour, cwd=cwd or project, rows=rows)
        s.expect(r"(?m)\$$")
        type_line(s, ("NO_COLOR=1 " if plain else "") + command, 0)
        s.pump(1.5)
        capture.snapshot(s, IMAGES / (name + ".png"), title=command)
        s.close()
    fresh = tour.scratch / "fresh"
    fresh.mkdir()
    subprocess.run(["git", "init", "-q", str(fresh)], check=True)
    snap("init", "agent-autosave init --yes", cwd=fresh)
    tour.seed("claude")
    tour.seed("codex")
    snap("status", "agent-autosave status")
    snap("status-plain", "agent-autosave status", plain=True)
    snap("doctor", "agent-autosave doctor")
    snap("show", "agent-autosave show", rows=48)
    subprocess.run([tour.bin / "agent-autosave", "clear"], cwd=str(project), env=tour.env, check=True,
                   capture_output=True)
    snap("history", "agent-autosave history")
    snap("remove", "agent-autosave remove codex")
    snap("error", "agent-autosave status", cwd=tour.scratch)


def main(argv) -> int:
    want_gif = "--images" not in argv
    want_images = "--gif" not in argv
    IMAGES.mkdir(parents=True, exist_ok=True)
    if want_gif:
        with tempfile.TemporaryDirectory(prefix="aa-demo-") as tmp:
            tour = build_docs.Tour(Path(tmp).resolve())
            demo(tour, ROOT / "docs/demo")
        print("docs/demo.gif: {:.2f} MB".format((ROOT / "docs/demo.gif").stat().st_size / 1e6))
    if want_images:
        with tempfile.TemporaryDirectory(prefix="aa-shots-") as tmp:
            wizard_images(build_docs.Tour(Path(tmp).resolve()))
        with tempfile.TemporaryDirectory(prefix="aa-shots-") as tmp:
            tour = build_docs.Tour(Path(tmp).resolve())
            subprocess.run([tour.bin / "codex"], cwd=str(tour.project), env=tour.env, check=True, capture_output=True)
            subprocess.run([tour.bin / "agent-autosave", "init", "--yes"], cwd=str(tour.project), env=tour.env,
                           check=True, capture_output=True)
            command_images(tour)
        print("docs/images: " + ", ".join(sorted(p.name for p in IMAGES.glob("*.png"))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
