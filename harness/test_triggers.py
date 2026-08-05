#!/usr/bin/env python3
"""W3.4 — 內部觸發源 單元測(DESIGN §5/W20;pytest-compatible,亦自跑)。

涵蓋:config 校驗(run_name/profile/prompt/every)、due 判定(oneshot 永不
due)、run_trigger 假 fork(folder 命名 {agent}__{run_name}__{ts}、session/
journal、先記水位=at-most-once 冪等)、失敗證據迴圈、poller 額滿跳過。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp_harness import triggers as tmod  # noqa: E402
from arcp_harness.inner_runner import AttemptResult  # noqa: E402
from arcp_harness.profiles import Profile  # noqa: E402
from arcp_harness.routing import ConfigError  # noqa: E402
from arcp_harness.store import Store, TicketSession  # noqa: E402
from arcp_harness.triggers import Trigger, due, load_triggers, run_trigger  # noqa: E402


def _profile(name="maint", **kw):
    base = dict(name=name, workspace_template="empty",
                workspace_folder="tickets/{agent}__{key}__{issue_id}",
                skills=[], agent={"backend": "rawcli"}, verify=[],
                max_attempts=2, on_unknown="pending")
    base.update(kw)
    return Profile(**base)


def _yaml(body: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        f.write(body)
    return path


PROFILES = {"maint": _profile()}


def test_load_valid_and_every_units():
    p = _yaml("""
outer_loop:
  triggers:
    - {name: a, profile: maint, run_name: nightly, every: 24h, prompt: x}
    - {name: b, profile: maint, run_name: weekly-x, every: 7d, prompt: x}
    - {name: c, profile: maint, run_name: quick, every: 30m, prompt: x}
    - {name: d, profile: maint, run_name: once, prompt: x}
""")
    ts = load_triggers(p, PROFILES)
    assert [t.every_sec for t in ts] == [24 * 3600, 7 * 86400, 1800, None]


def test_load_rejects_bad_config():
    for body, why in [
        ("outer_loop:\n  triggers:\n    - {name: a, profile: maint, "
         "run_name: 'Bad Name', every: 1h, prompt: x}", "run_name"),
        ("outer_loop:\n  triggers:\n    - {name: a, profile: nope, "
         "run_name: ok, every: 1h, prompt: x}", "profile"),
        ("outer_loop:\n  triggers:\n    - {name: a, profile: maint, "
         "run_name: ok, every: fortnight, prompt: x}", "every"),
        ("outer_loop:\n  triggers:\n    - {name: a, profile: maint, "
         "run_name: ok, every: 1h}", "prompt"),
    ]:
        try:
            load_triggers(_yaml(body), PROFILES)
            raise AssertionError(f"應拒絕({why})")
        except ConfigError:
            pass


def test_due_and_oneshot():
    store = Store(tempfile.mkdtemp())
    tr = Trigger("a", "maint", "nightly", "x", every_sec=3600)
    now = time.time()
    assert due(tr, store, now) is True              # 沒跑過 → due
    store.set_trigger_last_run("a", now)
    assert due(tr, store, now + 100) is False       # 間隔未到
    assert due(tr, store, now + 3600) is True       # 到了
    oneshot = Trigger("b", "maint", "once", "x", every_sec=None)
    assert due(oneshot, store, now) is False        # oneshot 永不自動 due


def _fake_fork(results):
    it = iter(results)

    def _f(agent_cfg, ws, prompt, artifacts, attempt, resume_session_id=None):
        raw = next(it)
        return AttemptResult(raw_outcome=raw, session_id="s1",
                             truly_resumed=resume_session_id is not None,
                             cost_usd=0.01, error=None, events_path="",
                             envelope_path="", error_kind=None)
    return _f


def test_run_trigger_success_naming_and_watermark():
    tmod.run_attempt = _fake_fork(["completed"])
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    tr = Trigger("nightly-maintain", "maint", "nightly", "打掃",
                 every_sec=3600)
    now = time.time()
    ev = run_trigger(tr, PROFILES, store, root, now=now)
    assert [e["type"] for e in ev] == ["trigger_started", "attempt_finished",
                                      "trigger_finished"]
    assert ev[-1]["outcome"] == "SUCCESS"
    ts = int(now)
    ws = ev[0]["workspace"]
    assert f"maint__nightly__{ts}" in ws            # DESIGN §2 無票命名
    assert os.path.isfile(os.path.join(ws, "TICKET.md"))
    assert "打掃" in open(os.path.join(ws, "TICKET.md")).read()
    sess = store.get_session(ts)
    assert sess is not None and sess.outcome == "SUCCESS"
    assert store.trigger_last_run("nightly-maintain") == now   # 水位已記
    assert due(tr, store, now + 100) is False       # 冪等:同輪不重跑


def test_run_trigger_failure_evidence_loop():
    calls = []

    def _f(agent_cfg, ws, prompt, artifacts, attempt, resume_session_id=None):
        calls.append(prompt)
        return AttemptResult(raw_outcome="completed", session_id="s1",
                             truly_resumed=False, cost_usd=0.0, error=None,
                             events_path="", envelope_path="",
                             error_kind=None)
    tmod.run_attempt = _f
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    prof = {"maint": _profile(verify=[__import__(
        "arcp_harness.profiles", fromlist=["VerifyStep"]).VerifyStep(
        name="v", files={"nope.txt": None})])}
    tr = Trigger("t", "maint", "job", "x", every_sec=None)
    ev = run_trigger(tr, prof, store, root)
    assert ev[-1]["outcome"] == "FAILURE"           # verify 不過 → FAILURE
    assert len(calls) == 2                          # max_attempts=2
    assert "失敗證據" in calls[1]                    # 證據餵回第二輪


def test_run_trigger_unknown_stops():
    tmod.run_attempt = _fake_fork(["unknown"])
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    tr = Trigger("t", "maint", "job", "x", every_sec=None)
    ev = run_trigger(tr, PROFILES, store, root)
    assert ev[-1]["outcome"] == "UNKNOWN"           # 不自動重試(v5 D3)
    sess = store.get_session(ev[0]["issue_id"])
    assert sess.pending_reason == "unknown"


# -- W4.6 cron 排程 --------------------------------------------------------- #
def test_cron_parse_and_validate():
    from arcp_harness.triggers import parse_cron
    c = parse_cron("*/15 3 1,15 * 1-5")
    assert c["min"] == {0, 15, 30, 45} and c["hour"] == {3}
    assert c["dom"] == {1, 15} and c["dow"] == {1, 2, 3, 4, 5}
    assert parse_cron("0 0 * * 7")["dow"] >= {0, 7}   # 週日 7≡0
    for bad in ("0 3 * *", "61 * * * *", "* * 0 * *", "*/0 * * * *",
                "a b c d e"):
        try:
            parse_cron(bad)
            raise AssertionError(f"應拒絕 {bad!r}")
        except ConfigError:
            pass


def test_cron_due_wall_clock():
    import datetime

    from arcp_harness.triggers import _cron_due, parse_cron
    c = parse_cron("0 3 * * *")                       # 每天 03:00
    day = datetime.datetime(2026, 8, 6, 3, 0)
    at_3am = day.timestamp()
    # 昨天跑過 → 今天 03:00:05 due;同分鐘跑完後不重複 due
    assert _cron_due(c, at_3am - 86400, at_3am + 5) is True
    assert _cron_due(c, at_3am + 5, at_3am + 30) is False
    # 02:59 還不 due
    assert _cron_due(c, at_3am - 86400, at_3am - 60) is False
    # 首跑(last_run=0):不回溯,只看當下分鐘
    assert _cron_due(c, 0, at_3am + 7200) is False    # 05:00 啟動 → 不補跑
    assert _cron_due(c, 0, at_3am + 10) is True       # 03:00 當下啟動 → due
    # 停機跨過 03:00 → 補跑一次(回溯 2 天內)
    assert _cron_due(c, at_3am - 3600, at_3am + 7200) is True


def test_cron_dow_dom_vixie_or():
    import datetime

    from arcp_harness.triggers import _cron_matches, parse_cron
    c = parse_cron("0 0 13 * 5")                      # 13 號「或」週五
    fri = datetime.datetime(2026, 8, 7, 0, 0)         # 週五、非 13 號
    d13 = datetime.datetime(2026, 8, 13, 0, 0)        # 13 號、週四
    other = datetime.datetime(2026, 8, 12, 0, 0)      # 都不是
    assert _cron_matches(c, fri) and _cron_matches(c, d13)
    assert not _cron_matches(c, other)


def test_cron_wins_over_every_with_warning():
    p = _yaml("outer_loop:\n  triggers:\n"
              "    - {name: a, script: '/bin/true', run_name: ok,"
              " every: 1h, cron: '0 3 * * *'}")
    t = load_triggers(p, PROFILES)[0]
    assert t.cron == "0 3 * * *" and t.cron_spec is not None
    assert t.every_sec is None                        # cron 優先,every 忽略


def test_poller_skips_when_quota_full():
    from arcp_harness.poller import OuterLoop

    class FakeSource:
        def search(self, jql, max_results=50):
            return []

        def get_comments(self, iid):
            return []

    class FakeDispatcher:
        profiles = PROFILES
        root = tempfile.mkdtemp()

    store = Store(tempfile.mkdtemp())
    for i in (1, 2):                                # 佔滿 max_running=2
        store.upsert_session(TicketSession(
            issue_id=i, key=f"P-{i}", profile="maint", workspace="ws",
            session_id="s", attempts=1, outcome=None, pending_reason=None,
            cost_usd=0.0))
    tr = Trigger("t", "maint", "job", "x", every_sec=60)
    loop = OuterLoop(FakeSource(), store, [], "jql",
                     dispatcher=FakeDispatcher(),
                     concurrency={"max_running": 2, "per_engine": {},
                                  "per_profile": {}}, triggers=[tr])
    ev = loop.poll_once()
    assert all(e["type"] != "trigger_started" for e in ev)   # 額滿跳過
    assert store.trigger_last_run("t") == 0.0                # 水位沒動


if __name__ == "__main__":
    ok = True
    for _name, _fn in list(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"  PASS  {_name}")
            except AssertionError as e:
                ok = False
                print(f"  FAIL  {_name}: {e}")
            except Exception as e:  # noqa: BLE001
                ok = False
                print(f"  ERROR {_name}: {type(e).__name__}: {e}")
    print("test-triggers:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
