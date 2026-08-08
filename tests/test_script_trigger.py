#!/usr/bin/env python3
"""W4.4 — 萬用 script trigger 單元測(本機真跑 .py/.sh;pytest-compatible,亦自跑)。

涵蓋:config 校驗(script/profile 互斥、字串→argv)、真跑 stdout/stderr 保存、
run.tgz(gzip -9)內容、rc=0→SUCCESS session、rc≠0→FAILURE、timeout、
找不到執行檔不炸、run dir 命名、水位冪等。
"""
from __future__ import annotations

import os
import sys
import tarfile
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp.profiles import Profile  # noqa: E402
from arcp.routing import ConfigError  # noqa: E402
from arcp.store import Store  # noqa: E402
from arcp.triggers import (  # noqa: E402
    Trigger,
    due,
    load_triggers,
    run_trigger,
)

PROFILES = {"maint": Profile(
    name="maint", workspace_template="empty",
    workspace_folder="tickets/{issue_id}", skills=[],
    agent={"backend": "rawcli"}, verify=[], max_attempts=1,
    on_unknown="pending")}


def _yaml(body: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        f.write(body)
    return path


def _script_trigger(argv, timeout=30.0, run_name="job"):
    return Trigger(name="t", profile=None, run_name=run_name, prompt="",
                   every_sec=None, script=argv, timeout_sec=timeout)


def test_config_script_or_profile_exclusive():
    for body, why in [
        ("outer_loop:\n  triggers:\n    - {name: a, run_name: ok, "
         "every: 1h}", "都缺"),
        ("outer_loop:\n  triggers:\n    - {name: a, profile: maint, "
         "script: 'x', run_name: ok, prompt: p}", "都給"),
    ]:
        try:
            load_triggers(_yaml(body), PROFILES)
            raise AssertionError(f"應拒絕({why})")
        except ConfigError:
            pass


def test_config_script_string_to_argv():
    p = _yaml("outer_loop:\n  triggers:\n"
              "    - {name: a, script: 'uvx some-tool --fast', "
              "run_name: ok, every: 1h}")
    t = load_triggers(p, PROFILES)[0]
    assert t.script == ["uvx", "some-tool", "--fast"]
    assert t.profile is None and t.timeout_sec == 600


def test_run_py_success_logs_and_tgz():
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    tr = _script_trigger([sys.executable, "-c",
                          "print('HELLO-OUT'); import sys;"
                          "sys.stderr.write('HELLO-ERR');"
                          "open('made.txt','w').write('x')"])
    now = time.time()
    ev = run_trigger(tr, PROFILES, store, root, now=now)
    assert [e["type"] for e in ev] == ["script_run_started",
                                      "script_run_finished"]
    fin = ev[-1]
    assert fin["outcome"] == "SUCCESS" and fin["rc"] == 0
    ts = int(now)
    base = os.path.join(root, "runs", f"t__job__{ts}")
    assert "HELLO-OUT" in open(f"{base}/transcript/stdout.log").read()
    assert "HELLO-ERR" in open(f"{base}/transcript/stderr.log").read()
    assert os.path.isfile(f"{base}/ws/made.txt")     # cwd = ws
    with tarfile.open(f"{base}/transcript/run.tgz") as tf:
        assert sorted(m.name for m in tf.getmembers()) == \
            ["stderr.log", "stdout.log"]
    sess = store.get_session(ts)
    assert sess.outcome == "SUCCESS"
    assert sess.profile == "script:t"                # dashboard 徽章/列表重用
    assert store.trigger_last_run("t") == now        # 水位冪等


def test_run_sh_nonzero_rc_failure():
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    tr = _script_trigger(["/bin/sh", "-c", "echo oops >&2; exit 3"])
    ev = run_trigger(tr, PROFILES, store, root)
    assert ev[-1]["outcome"] == "FAILURE" and ev[-1]["rc"] == 3
    assert store.get_session(ev[-1]["issue_id"]).outcome == "FAILURE"


def test_run_timeout_failure():
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    tr = _script_trigger([sys.executable, "-c",
                          "import time; time.sleep(30)"], timeout=1.0)
    ev = run_trigger(tr, PROFILES, store, root)
    assert ev[-1]["outcome"] == "FAILURE"
    assert ev[-1]["timeout"] is True and ev[-1]["rc"] is None


def test_missing_executable_no_crash():
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    tr = _script_trigger(["/no/such/binary-xyz"])
    ev = run_trigger(tr, PROFILES, store, root)
    assert ev[-1]["outcome"] == "FAILURE"
    ts = ev[-1]["issue_id"]
    err = open(os.path.join(root, "runs", f"t__job__{ts}",
                            "transcript", "stderr.log")).read()
    assert "無法執行" in err


def test_oneshot_not_auto_due():
    store = Store(tempfile.mkdtemp())
    tr = _script_trigger(["/bin/true"])              # every_sec=None
    assert due(tr, store) is False


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
    print("test-script-trigger:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
