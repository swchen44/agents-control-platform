#!/usr/bin/env python3
"""progress 診斷 + timeout_kind 分類 單元測(免 spawn)。

  P1 progress_snapshot:bytes/行數/首輸出延遲/最長靜默(content-free)
  P2 timeout_kind:非 stall=None;stall+零輸出=no_output;stall+有輸出=stalled
  P3 _tail_last_event:行數+最後 category;缺檔=(0,"")
  P4 _timeout_kind_from_disk:raw 有內容=stalled_output、無檔=no_output

Usage: <venv>/python test_progress_heartbeat.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time as _t

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
from arcp.inner_runner import _tail_last_event, _timeout_kind_from_disk  # noqa: E402
from arcp.rawcli.agent import RawCLIAgent  # noqa: E402

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}  {detail}")


# P1:progress_snapshot 計算(直接設內部欄位,模擬 run() 迴圈統計後的狀態)
a = RawCLIAgent(engine="claude")
a._t0 = 100.0
a._first_output_at = 102.5
a._last_progress = 130.0
a._max_silence = 8.0
a._raw_count, a._raw_bytes, a._event_count = 12, 3456, 7
a._last_type = "assistant"
_real = _t.time
_t.time = lambda: 133.0     # 固定 now,驗 idle/max_silence 併入尾段靜默
snap = a.progress_snapshot()
_t.time = _real
check("P1 snapshot:行數/bytes/事件/last_event",
      snap["raw_lines"] == 12 and snap["raw_bytes"] == 3456
      and snap["events"] == 7 and snap["last_event"] == "assistant", snap)
check("P1 snapshot:首輸出 2.5s、idle 3.0s、max_silence 取尾段前最大 8.0",
      snap["first_output_after_sec"] == 2.5
      and snap["last_output_idle_sec"] == 3.0
      and snap["max_silence_sec"] == 8.0, snap)

# P2:timeout_kind
check("P2 非 stall → None", a.timeout_kind() is None)
a._stalled = True
check("P2 stall+有輸出 → stalled_output_timeout",
      a.timeout_kind() == "stalled_output_timeout")
a._first_output_at = None
check("P2 stall+零輸出 → no_output_timeout",
      a.timeout_kind() == "no_output_timeout")

# P3:_tail_last_event
root = tempfile.mkdtemp(prefix="arcp-test-hb-")
ev = os.path.join(root, "a1.events.jsonl")
with open(ev, "w") as f:
    f.write(json.dumps({"category": "user"}) + "\n")
    f.write(json.dumps({"category": "tool"}) + "\n")
check("P3 events 檔 → (2, 'tool')", _tail_last_event(ev) == (2, "tool"))
check("P3 缺檔 → (0, '')", _tail_last_event(ev + ".nope") == (0, ""))

# P4:_timeout_kind_from_disk
raw = os.path.join(root, "a1.raw.jsonl")
check("P4 raw 無檔 → no_output_timeout",
      _timeout_kind_from_disk(raw) == "no_output_timeout")
open(raw, "w").write('{"type":"system"}\n')
check("P4 raw 有內容 → stalled_output_timeout",
      _timeout_kind_from_disk(raw) == "stalled_output_timeout")

print(f"test-progress-heartbeat: {'PASS' if fail == 0 else 'FAIL'} "
      f"({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
