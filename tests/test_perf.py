#!/usr/bin/env python3
"""Q5 效能監控:perf_metrics 純函式(合成 journal/sessions/watch/sysinfo → 紅黃綠燈
+ per-profile 細節)。免 token、確定性。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  (把 scripts/ 放進 sys.path)
from detail_server import perf_metrics  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


def light(res, key):
    return next(i["light"] for i in res["indicators"] if i["key"] == key)


NOW = 1_000_000.0
HR = 3600.0

# journal:profile fc 3 attempt(1 error)、profile fc_v2 1 attempt(completed);
# 1 evict、2 dispatch_error(近 1h);cost 近 1h 合計 $6(→ 紅)
journal = [
    {"type": "attempt_started", "issue_id": 1, "attempt": 1, "ts": NOW - 100},
    {"type": "attempt_finished", "issue_id": 1, "attempt": 1, "ts": NOW - 40,
     "raw": "completed", "profile": "fc", "cost": 3.0},
    {"type": "attempt_started", "issue_id": 1, "attempt": 2, "ts": NOW - 30},
    {"type": "attempt_finished", "issue_id": 1, "attempt": 2, "ts": NOW - 10,
     "raw": "error", "profile": "fc", "cost": 2.0},
    {"type": "attempt_finished", "issue_id": 2, "attempt": 1, "ts": NOW - 5,
     "raw": "completed", "profile": "fc_v2", "cost": 1.0},
    {"type": "evicted", "issue_id": 1, "ts": NOW - 20, "count": 1},
    {"type": "dispatch_error", "issue_id": 3, "ts": NOW - 50, "error": "x"},
    {"type": "trigger_error", "issue_id": 0, "ts": NOW - 60, "error": "y"},
]
sessions = {
    1: {"issue_id": 1, "profile": "fc", "outcome": None, "queued": 0, "cost_usd": 5.0},
    2: {"issue_id": 2, "profile": "fc_v2", "outcome": "SUCCESS", "queued": 0},
    3: {"issue_id": 3, "profile": "fc", "outcome": None, "queued": 1},  # 排隊
}
watch = {
    1: {"first_seen_ts": NOW - 30 * HR},   # 開著 30h → 紅
    2: {"first_seen_ts": NOW - 1 * HR},    # 已 SUCCESS,不計
    3: {"first_seen_ts": NOW - 0.5 * HR},
}
sysinfo = {"resources": {"mem": {"total": 100, "used": 95},   # 95% → 紅
                         "disk": {"total": 100, "used": 30},
                         "loadavg": [1.0], "cpus": 8}}

res = perf_metrics(journal, sessions, watch, sysinfo, journal_bytes=10 * 1_000_000,
                   now=NOW)

check("失敗率 1/3≈33% → 紅", light(res, "fail_rate") == "red")
check("排隊深度 1 → 黃", light(res, "queue") == "yellow")
check("最舊未終態 30h → 紅", light(res, "oldest") == "red")
check("evict 1(近1h)→ 黃", light(res, "evict") == "yellow")
check("花費 $6(近1h)→ 紅", light(res, "cost") == "red")
check("錯誤事件 2(近1h)→ 黃", light(res, "errors") == "yellow")
check("系統資源 95% → 紅", light(res, "sysres") == "red")
check("journal 10MB → 綠", light(res, "journal") == "green")

profs = {p["profile"]: p for p in res["profiles"]}
check("per-profile:fc 2 attempts、失敗率 50%",
      profs["fc"]["attempts"] == 2 and profs["fc"]["fail_rate"] == 50)
check("per-profile:fc 平均時長有值(有配對 started)", profs["fc"]["avg_sec"] is not None)
check("per-profile:fc 累計 $5", abs(profs["fc"]["cost"] - 5.0) < 1e-6)
check("per-profile:fc_v2 1 attempt、失敗率 0%",
      profs["fc_v2"]["attempts"] == 1 and profs["fc_v2"]["fail_rate"] == 0)

# 全空 → 不炸、燈預設綠/gray
empty = perf_metrics([], {}, {}, None, 0, now=NOW)
check("空資料不炸;系統資源無 sysinfo → gray",
      light(empty, "sysres") == "gray" and light(empty, "fail_rate") == "green")

print(f"test-perf: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
