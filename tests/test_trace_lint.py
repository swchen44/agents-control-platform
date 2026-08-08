#!/usr/bin/env python3
"""C2 trace 完整性自檢(scripts/trace_lint.py)。合成 runtime:完整 attempt 過、
缺層失敗、UNKNOWN 依設計略過。免 token、確定性。"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  (把 scripts/ 放進 sys.path)
import trace_lint  # noqa: E402

from arcp.store import Store, TicketSession  # noqa: E402

ok = fail = 0


def check(name: str, cond: bool) -> None:
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


def _mk_session(store, runtime, issue_id, key, attempts):
    ws = os.path.join(runtime, "tickets", f"t{issue_id}", "ws")
    os.makedirs(ws, exist_ok=True)
    store.upsert_session(TicketSession(
        issue_id=issue_id, key=key, profile="p", workspace=ws,
        session_id="s", attempts=attempts, outcome="SUCCESS",
        pending_reason=None, cost_usd=0.0))
    return os.path.join(runtime, "tickets", f"t{issue_id}", "attempts")


def _attempt_files(adir, a, envelope=True, events=True):
    os.makedirs(adir, exist_ok=True)
    if envelope:
        json.dump({"completed": True, "session_id": "s", "cost_usd": 0.01},
                  open(os.path.join(adir, f"a{a}.envelope.json"), "w"))
    if events:
        open(os.path.join(adir, f"a{a}.events.jsonl"), "w").write('{"type":"x"}\n')


# ── 1. 完整 attempt → 無缺口 ──────────────────────────────────────── #
rt = tempfile.mkdtemp()
st = Store(rt)
adir = _mk_session(st, rt, 101, "SCRUM-101", 1)
_attempt_files(adir, 1)
st.journal("attempt_finished", 101, "SCRUM-101", attempt=1, raw="completed")
st.close()
findings, infos, n = trace_lint.lint(rt)
check("完整 attempt → 0 缺口", findings == [] and n == 1)

# ── 2. completed 但缺 envelope(L2)→ 缺口 ─────────────────────────── #
rt = tempfile.mkdtemp()
st = Store(rt)
adir = _mk_session(st, rt, 102, "SCRUM-102", 1)
_attempt_files(adir, 1, envelope=False)          # 缺 L2
st.journal("attempt_finished", 102, "SCRUM-102", attempt=1, raw="completed")
st.close()
findings, infos, n = trace_lint.lint(rt)
check("completed 缺 envelope → 抓到 L2 缺口",
      any("L2" in f for f in findings))

# ── 3. completed 但 events 空(L3)→ 缺口 ─────────────────────────── #
rt = tempfile.mkdtemp()
st = Store(rt)
adir = _mk_session(st, rt, 103, "SCRUM-103", 1)
_attempt_files(adir, 1)
open(os.path.join(adir, "a1.events.jsonl"), "w").close()   # 清空 L3
st.journal("attempt_finished", 103, "SCRUM-103", attempt=1, raw="completed")
st.close()
findings, infos, n = trace_lint.lint(rt)
check("completed 但 events 空 → 抓到 L3 缺口",
      any("L3" in f for f in findings))

# ── 4. UNKNOWN 缺 envelope → 依設計略過(非缺口)───────────────────── #
rt = tempfile.mkdtemp()
st = Store(rt)
adir = _mk_session(st, rt, 104, "SCRUM-104", 1)
# 完全不寫 attempt 檔(runner 死)
st.journal("attempt_finished", 104, "SCRUM-104", attempt=1, raw="unknown")
st.close()
findings, infos, n = trace_lint.lint(rt)
check("UNKNOWN 缺層 → 不算缺口(info)", findings == [] and len(infos) == 1)

# ── 5. 有 attempt 但 journal 無 attempt_finished → L0/L1 缺口 ──────── #
rt = tempfile.mkdtemp()
st = Store(rt)
adir = _mk_session(st, rt, 105, "SCRUM-105", 1)
_attempt_files(adir, 1)                          # 檔在,但 journal 沒 finished
st.close()
findings, infos, n = trace_lint.lint(rt)
check("無 attempt_finished journal → L0/L1 缺口",
      any("L0/L1" in f for f in findings))

# ── 6. adopted/0-attempt session 不檢 ─────────────────────────────── #
rt = tempfile.mkdtemp()
st = Store(rt)
st.upsert_session(TicketSession(
    issue_id=106, key="SCRUM-106", profile="p", workspace="(adopted)",
    session_id=None, attempts=0, outcome="ABORTED",
    pending_reason=None, cost_usd=0.0))
st.close()
findings, infos, n = trace_lint.lint(rt)
check("adopted/0-attempt 不檢(n=0)", n == 0 and findings == [])

print(f"test-trace-lint: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
