#!/usr/bin/env python3
"""內部觸發源(J1 統一 job):load 驗證(trigger_type/script/labels)、cron/every 排程、
_run_logged_script(cwd 進 config/scripts/subfolder、存 log、路徑穿越擋)。pytest 相容。"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp import triggers as tmod  # noqa: E402
from arcp.routing import ConfigError  # noqa: E402
from arcp.store import Store  # noqa: E402
from arcp.triggers import (  # noqa: E402
    Trigger,
    _run_logged_script,
    due,
    load_triggers,
)


def _yaml(body: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        f.write(body)
    return path


# ── load 驗證 ─────────────────────────────────────────────────────────── #
def test_load_valid_both_types():
    p = _yaml("""
outer_loop:
  triggers:
    - {name: a, run_name: scan, trigger_type: agent-job, script: cq/scan.sh,
       labels: ['cr'], count: 0, cron: '*/10 * * * *'}
    - {name: b, run_name: clean, trigger_type: script-job,
       script: 'maint/clean.sh', every: 2h}
""")
    ts = load_triggers(p)
    assert ts[0].trigger_type == "agent-job" and ts[0].script == ["cq/scan.sh"]
    assert ts[0].labels == ["cr"] and ts[0].every_sec is None  # cron 優先
    assert ts[1].trigger_type == "script-job" and ts[1].every_sec == 7200


def test_load_rejects_bad():
    for body, why in [
        ("outer_loop:\n  triggers:\n    - {name: a, run_name: r, "
         "script: x.sh}", "缺 trigger_type"),
        ("outer_loop:\n  triggers:\n    - {name: a, run_name: r, "
         "trigger_type: agent-job, labels: ['x']}", "缺 script"),
        ("outer_loop:\n  triggers:\n    - {name: a, run_name: r, "
         "trigger_type: agent-job, script: x.sh}", "agent-job 缺 labels"),
        ("outer_loop:\n  triggers:\n    - {name: a, run_name: 'Bad Name', "
         "trigger_type: script-job, script: x.sh}", "run_name"),
        ("outer_loop:\n  triggers:\n    - {name: a, run_name: r, "
         "trigger_type: bogus, script: x.sh}", "trigger_type 值"),
    ]:
        try:
            load_triggers(_yaml(body))
            raise AssertionError(f"應拒絕({why})")
        except ConfigError:
            pass


def test_due_and_oneshot():
    store = Store(tempfile.mkdtemp())
    tr = Trigger("a", "nightly", "script-job", ["x.sh"], every_sec=3600)
    now = time.time()
    assert due(tr, store, now) is True
    store.set_trigger_last_run("a", now)
    assert due(tr, store, now + 100) is False
    assert due(tr, store, now + 3600) is True
    oneshot = Trigger("b", "once", "script-job", ["x.sh"], every_sec=None)
    assert due(oneshot, store, now) is False


# ── _run_logged_script:cwd 進 subfolder、存 log、註冊 session ──────────── #
def _script(body, rel="job/run.sh"):
    base = tempfile.mkdtemp()
    tmod.job_scripts_dir = lambda: base            # monkeypatch config/scripts
    full = os.path.join(base, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(body)
    os.chmod(full, 0o755)
    return base, rel


def test_run_logged_script_cwd_and_logs():
    base, rel = _script("#!/bin/bash\necho hi; pwd 1>&2\n")
    tr = Trigger("j", "run", "script-job", [rel], every_sec=None)
    root = tempfile.mkdtemp()
    rc, out, ev = _run_logged_script(tr, Store(root), root, now=1000.0)
    assert rc == 0 and out.strip() == "hi"
    rd = f"{root}/runs/j__run__1000/transcript"
    assert os.path.isfile(f"{rd}/stdout.log") and os.path.isfile(f"{rd}/run.tgz")
    # cwd = 腳本 subfolder(pwd 印到 stderr)
    assert os.path.dirname(os.path.join(base, rel)) in \
        open(f"{rd}/stderr.log").read()
    assert [e["type"] for e in ev] == ["script_run_started",
                                       "script_run_finished"]
    assert ev[-1]["outcome"] == "SUCCESS"


def test_run_logged_script_traversal_rejected():
    _script("echo x")                              # 設好 job_scripts_dir
    tr = Trigger("j", "run", "script-job", ["../../etc/passwd"], every_sec=None)
    root = tempfile.mkdtemp()
    rc, _out, ev = _run_logged_script(tr, Store(root), root, now=1.0)
    assert rc is None and any(e["type"] == "trigger_error" for e in ev)


# -- cron(不變;沿用)------------------------------------------------------ #
def test_cron_parse_and_due():
    import datetime

    from arcp.triggers import _cron_due, parse_cron
    c = parse_cron("*/15 3 1,15 * 1-5")
    assert c["min"] == {0, 15, 30, 45} and c["hour"] == {3}
    day = datetime.datetime(2026, 8, 6, 3, 0)      # 週四、非 1/15
    at_3am = day.timestamp()
    assert _cron_due(parse_cron("0 3 * * *"), at_3am - 86400, at_3am + 5) is True
    for bad in ("0 3 * *", "61 * * * *", "a b c d e"):
        try:
            parse_cron(bad)
            raise AssertionError(f"應拒絕 {bad!r}")
        except ConfigError:
            pass


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
