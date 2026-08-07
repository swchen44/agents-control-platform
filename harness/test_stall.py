#!/usr/bin/env python3
"""conc.2 — stall watchdog 單元測(免 token,確定;不依賴 claude 行為)。

用假卡住進程(sleep)驗證 watchdog 機制(exit 部分):
  W1 無進展 → stall_seconds 後 killpg(_stalled=True)   —— stalled is not legal
  W2 持續進展 → 不 kill(slow is legal)                  —— reset-on-progress

exit→resume 的 resume 部分由 C.4(e2e_c4)已證(completed=False→dispatcher
resume);組合成立。真 claude sleep 不可靠制造 stall(haiku 常不 sleep),故機制
用單元測、不燒 token。

Usage: .venv/python test_stall.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp_rawcli import RawCLIAgent  # noqa: E402

ok = True


def check(name, cond):
    global ok
    ok = ok and cond
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


# W1: 無進展 → 3s 後偵測 stall + killpg
a1 = RawCLIAgent(stall_seconds=3)
a1._last_progress = time.time()
p1 = subprocess.Popen(["sleep", "60"], start_new_session=True)
t0 = time.time()
a1._stall_watchdog(p1)          # blocks until it kills the hung child
dur = round(time.time() - t0, 1)
# watchdog 只送 SIGKILL 不回收(production 由主迴圈 proc.wait() 收屍);
# 單元測直呼 watchdog 故自行 reap,否則 poll() 會撞 kill→reap 的 race。
try:
    p1.wait(timeout=2)
except subprocess.TimeoutExpired:
    pass
check(f"W1 無進展→stall 偵測+killpg (dur={dur}s)",
      a1._stalled and p1.poll() is not None and 2.5 <= dur <= 6)

# W2: 持續進展 → watchdog 不 kill(slow is legal)
a2 = RawCLIAgent(stall_seconds=3)
a2._last_progress = time.time()
p2 = subprocess.Popen(["sleep", "60"], start_new_session=True)
stop = threading.Event()


def keep_progress():
    while not stop.is_set():
        a2._last_progress = time.time()   # simulate streaming lines
        time.sleep(0.5)


threading.Thread(target=keep_progress, daemon=True).start()
threading.Thread(target=a2._stall_watchdog, args=(p2,), daemon=True).start()
time.sleep(6)                    # 6s > 2×stall,但 progress 每 0.5s 更新
survived = p2.poll() is None
check("W2 持續進展→不 kill(slow is legal)", survived and not a2._stalled)
stop.set()
try:
    os.killpg(os.getpgid(p2.pid), 9)
except (ProcessLookupError, OSError):
    pass

print("test-stall:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
