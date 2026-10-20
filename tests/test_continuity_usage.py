"""Low battery (Codex): usage from the rollout tail on Stop, one ask per
crossing on the next prompt, none once a handoff note is recorded."""
from datetime import datetime, timezone
import json

from agent_autosave.compiler import compile_events
from agent_autosave.loader import context
from agent_autosave.transcripts import codex as rollout
from tests.helpers import FIXTURES, event
from tests.scenario import Repo, codex_recording

RESET_1, RESET_2 = 1792087878, 1792105878  # epoch seconds, as Codex writes resets_at


def utc(epoch, fmt="%Y-%m-%dT%H:%M:%SZ"):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime(fmt)


def token_count(used, resets, weekly=40.0, total=200000, window=258400):
    return {"timestamp": "2026-10-17T12:00:00.000Z", "type": "event_msg", "payload": {
        "type": "token_count", "info": {"last_token_usage": {"total_tokens": total}, "model_context_window": window},
        "rate_limits": {"primary": {"used_percent": used, "window_minutes": 300, "resets_at": resets},
                        "secondary": {"used_percent": weekly, "window_minutes": 10080, "resets_at": resets + 500000},
                        "rate_limit_reached_type": None}}}


def test_usage_from_the_recorded_rollout():
    rows = rollout.tail_lines(FIXTURES / "codex/rollouts/rollout-real-session.jsonl")
    usage = rollout.usage(rows)
    assert usage["limit_window_minutes"] == 300 and usage["limit_resets_at"].endswith("Z")
    assert usage["limit_pct"] is not None and 0 <= usage["context_pct"] <= 100


def test_context_pct_follows_codex_rs():
    [row] = [token_count(10, RESET_1, total=12000 + 123200, window=258400)]
    assert rollout.usage([row])["context_pct"] == 50.0
    assert rollout.usage([token_count(10, RESET_1, total=5000)])["context_pct"] == 0.0
    assert rollout.usage([token_count(10, RESET_1, window=1000)])["context_pct"] is None
    assert rollout.usage([{"type": "event_msg", "payload": {"type": "token_count"}}]) is None


class Codex:
    def __init__(self, tmp_path):
        self.repo = Repo(tmp_path / "repo", agents=("codex",))
        self.path = tmp_path / "rollout-2026-10-17T12-00-00-x.jsonl"
        self.path.write_text("")

    def prompt(self, text="continue with the exporter and keep the tests green"):
        payload = dict(codex_recording("plan-handoff/UserPromptSubmit-17be2b700d80.json"), prompt=text,
                       transcript_path=str(self.path))
        output = self.repo.send("codex", payload)
        return json.loads(output)["hookSpecificOutput"]["additionalContext"] if output else ""

    def stop(self, used, resets=RESET_1):
        with self.path.open("a") as stream:
            stream.write(json.dumps(token_count(used, resets)) + "\n")
        payload = dict(codex_recording("plan-handoff/Stop-9f956362cdb2.json"), transcript_path=str(self.path))
        self.repo.send("codex", payload)


ASK = ("agent-autosave: Codex has used {}% of its 5-hour limit. Before continuing, record a short handoff "
       '(what is done, what is next, anything risky) with: agent-autosave note --quiet --type handoff "..."')


def test_one_ask_at_86_none_at_92_after_a_handoff_note_and_a_new_ask_after_the_reset(tmp_path):
    codex = Codex(tmp_path)
    codex.prompt()
    codex.stop(40)
    assert codex.prompt() == ""
    codex.stop(86)
    assert codex.prompt() == ASK.format(86)
    assert codex.prompt() == ""  # asked once
    codex.repo.cli("note", "--quiet", "--type", "handoff", "Done: streaming. Next: wrapper. Risky: chunk=.")
    codex.stop(92)
    assert codex.prompt() == ""
    codex.stop(88, resets=RESET_2)  # the window reset and filled up again
    assert codex.prompt() == ASK.format(88)
    usage = codex.repo.save()["usage"]["codex"]
    assert usage["limit_pct"] == 88 and usage["limit_resets_at"] == utc(RESET_2)


def test_a_crossing_without_a_note_is_still_asked_only_once(tmp_path):
    codex = Codex(tmp_path)
    codex.prompt()
    codex.stop(90)
    assert codex.prompt() == ASK.format(90)
    codex.stop(95)
    assert codex.prompt() == ""


def test_handoff_ask_off_and_threshold_from_config(tmp_path):
    codex = Codex(tmp_path)
    path = codex.repo.root / ".autosave/config.json"
    config = json.loads(path.read_text())
    config["handoff"] = {"warn_at_percent": 60, "ask": True}
    path.write_text(json.dumps(config))
    codex.prompt()
    codex.stop(61)
    assert codex.prompt() == ASK.format(61)
    config["handoff"]["ask"] = False
    path.write_text(json.dumps(config))
    codex.stop(70, resets=RESET_2)
    assert codex.prompt() == ""


def test_crossing_rule_in_the_compiler():
    def usage(n, pct, resets="2026-10-17T14:20:00Z"):
        return dict(event("usage", n, limit_pct=pct, limit_window_minutes=300, limit_resets_at=resets,
                          weekly_pct=None, context_pct=None), agent="codex")
    save = compile_events([usage(0, 40), usage(1, 86), usage(2, 92)])
    assert save["usage"]["codex"]["crossed_at"] == usage(1, 86)["ts"] and save["usage"]["codex"]["handoff_at"] is None
    below = compile_events([usage(0, 86), usage(1, 40)])
    assert below["usage"]["codex"]["crossed_at"] is None
    reset = compile_events([usage(0, 86), usage(1, 90, resets="2026-10-17T19:20:00Z")])
    assert reset["usage"]["codex"]["crossed_at"] == usage(1, 90)["ts"]
    noted = compile_events([usage(0, 86), dict(event("note", 1, text="handoff", type="handoff"), agent="cli")])
    assert noted["usage"]["codex"]["handoff_at"] == event("note", 1)["ts"]


def test_usage_and_handoff_notes_in_the_context(tmp_path):
    codex = Codex(tmp_path)
    codex.prompt()
    codex.stop(91)
    codex.repo.cli("note", "--quiet", "--type", "handoff", "Done: streaming. Next: wrapper.")
    text = context(codex.repo.save(), force=True)
    assert "Usage: Codex 91% of 5-hour limit (resets {} UTC), weekly 40%, context ".format(utc(RESET_1, "%H:%M")) in text
    assert 'Handoff notes: "Done: streaming. Next: wrapper."' in text


def test_status_has_a_usage_row(tmp_path):
    from agent_autosave.commands.status import render
    codex = Codex(tmp_path)
    codex.prompt()
    codex.stop(91)
    code, result = codex.repo.cli("status")
    local = datetime.fromtimestamp(RESET_1, timezone.utc).astimezone().strftime("%H:%M")
    assert code == 0 and result["data"]["usage"] == ["Codex 91% of 5-hour limit (resets {}), weekly 40%, context 76%".format(local)]
    assert "Usage" in render(result["data"]) and "Codex 91% of 5-hour limit" in render(result["data"])


def bucket(row, limit_id):
    row = json.loads(json.dumps(row))
    row["payload"]["rate_limits"]["limit_id"] = limit_id
    return row


def test_a_model_specific_bucket_never_hides_the_codex_limit():
    """codex-rs tui status/rate_limits.rs shows the "codex" bucket first and
    model-specific quotas after it; a newer token_count for another bucket
    (0% used) must not replace the plan's own reading."""
    rows = [bucket(token_count(42.0, RESET_1), "codex"), bucket(token_count(0.0, RESET_1, weekly=0.0), "other-model")]
    usage = rollout.usage(rows)
    assert usage["limit_pct"] == 42.0 and usage["weekly_pct"] == 40.0
    only_other = rollout.usage([bucket(token_count(0.0, RESET_1, weekly=0.0), "other-model")])
    assert only_other is None or (only_other["limit_pct"] is None and only_other["weekly_pct"] is None)
    assert rollout.usage([token_count(42.0, RESET_1)])["limit_pct"] == 42.0  # no limit_id: older Codex


def test_a_fraction_of_a_percent_is_rounded_not_shown_as_zero():
    from agent_autosave.loader import usage_text
    text = usage_text("codex", {"limit_pct": 0.6, "limit_window_minutes": 300, "weekly_pct": 0.7, "context_pct": 0.6})
    assert "Codex 1% of 5-hour limit" in text and "weekly 1%" in text and "context 1%" in text
