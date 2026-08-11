#!/usr/bin/env python3
"""W4.5 — hot reload 範圍 單元測(DESIGN_hotreload;pytest-compatible,亦自跑)。

驗 make_reload 的完整 swap 範圍:routes/jql/concurrency/profiles(disp+cmds+
ext)/triggers/allowed_commenters/cancel_states;壞 config → ConfigError 且
舊設定原封續用(fail-safe)。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  (把 scripts/ 放進 sys.path)
from run_poller import make_reload  # noqa: E402

from arcp.routing import ConfigError  # noqa: E402

GOOD_V1 = """
version: 1
outer_loop:
  source: {project: X, jql: 'jql-v1'}
  concurrency: {max_running: 2, per_engine: {}, per_profile: {}}
  external_change: {cancel_states: ['Done']}
  triggers:
    - {name: t1, trigger_type: script-job, script: 'x/t.sh', run_name: r1, every: 1h}
  routes:
    - {name: r1, when: {labels: ['x']}, profile: p, on_match: create_or_resume}
inner_loop:
  profiles:
    p:
      workspace: {template: empty, folder: 'tickets/{issue_id}'}
      agent: {backend: rawcli}
      verify: [{name: v, files: {x.txt: null}}]
      loop: {max_attempts: 1, on_unknown: pending}
"""

GOOD_V2 = GOOD_V1.replace("jql-v1", "jql-v2") \
    .replace("['Done']", "['完成']") \
    .replace("max_running: 2", "max_running: 5") \
    .replace("- {name: t1, trigger_type: script-job, script: 'x/t.sh', run_name: r1, every: 1h}",
             "- {name: t1, trigger_type: script-job, script: 'x/t.sh', run_name: r1, every: 1h}\n"
             "    - {name: t2, trigger_type: script-job, script: 'x/e.sh', run_name: r2, every: 2h}")

BAD = GOOD_V1.replace("create_or_resume", "not-a-real-on-match")


class _Obj:
    pass


def _fixture(path):
    loop, disp, ext = _Obj(), _Obj(), _Obj()
    loop.routes, loop.jql, loop.concurrency, loop.triggers = [], "", {}, []
    disp.profiles = {}
    ext.profiles, ext.cancel_states = {}, []
    return make_reload(loop, disp, ext, config_path=path), loop, disp, ext


def _write(path, text):
    with open(path, "w") as f:
        f.write(text)


def test_reload_full_scope():
    path = tempfile.mkstemp(suffix=".yaml")[1]
    _write(path, GOOD_V1)
    reload_fn, loop, disp, ext = _fixture(path)
    out = reload_fn()
    assert out == {"routes": 1, "profiles": 1, "triggers": 1}
    assert loop.jql == "jql-v1"

    _write(path, GOOD_V2)                       # 改 config → 全範圍 swap
    out = reload_fn()
    assert out["triggers"] == 2                 # trigger 可 reload(W4.5 補)
    assert loop.jql == "jql-v2"
    assert loop.concurrency["max_running"] == 5
    assert [t.name for t in loop.triggers] == ["t1", "t2"]
    assert ext.cancel_states == ["完成"]         # 終止狀態可 reload
    assert disp.profiles is ext.profiles        # 兩處同一份


def test_bad_config_fail_safe():
    path = tempfile.mkstemp(suffix=".yaml")[1]
    _write(path, GOOD_V1)
    reload_fn, loop, disp, ext = _fixture(path)
    reload_fn()
    old = (loop.routes, loop.jql, loop.triggers, disp.profiles,
           ext.cancel_states)

    _write(path, BAD)                           # 壞 config
    try:
        reload_fn()
        raise AssertionError("應擲 ConfigError")
    except ConfigError:
        pass
    # 舊設定原封續用(fail-safe:引用完全沒動)
    assert (loop.routes, loop.jql, loop.triggers, disp.profiles,
            ext.cancel_states) == old


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
    print("test-hotreload:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
