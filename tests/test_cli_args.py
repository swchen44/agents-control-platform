#!/usr/bin/env python3
"""run_poller / detail_server 的 CLI argparse(2026-08-09 全 flag 化)。

守住:minutes/interval 為 -m/-i flag(非位置參數)、detail_server 全 flag 無位置參數、
不再讀 ARCP_DASH_HOST / ARCP_CONTROL_URL env。純解析、免網、免起服務。"""
from __future__ import annotations

import contextlib
import io
import os
import sys

# 先設好「應被忽略」的 env,再 import detail_server → 驗證它不讀 env
os.environ["ARCP_DASH_HOST"] = "9.9.9.9"
os.environ["ARCP_CONTROL_URL"] = "http://should-be-ignored:1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import detail_server as ds  # noqa: E402
import run_poller as rp  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


def _errors(fn, argv) -> bool:
    """argparse 對 argv 應報錯(SystemExit);吞掉 usage 輸出。"""
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            fn(argv)
        return False
    except SystemExit:
        return True


# ── run_poller:minutes/interval 為 flag,非位置參數 ─────────────────── #
a = rp._parse_args([])
check("run_poller 預設 minutes=30 / interval=15", a.minutes == 30.0
      and a.interval == 15.0)
a = rp._parse_args(["-m", "0", "-i", "5"])
check("run_poller -m 0 -i 5(短 flag)", a.minutes == 0.0 and a.interval == 5.0)
a = rp._parse_args(["--minutes", "10", "--interval", "20",
                    "--control-port", "9001", "--form-port", "9002",
                    "--log-level", "DEBUG"])
check("run_poller 長 flag + ports + log-level",
      a.minutes == 10.0 and a.interval == 20.0 and a.control_port == 9001
      and a.form_port == 9002 and a.log_level == "DEBUG")
check("run_poller 拒收舊式位置參數(30 15 → 報錯)",
      _errors(rp._parse_args, ["30", "15"]))
check("run_poller --log-level 限 choices(FOO → 報錯)",
      _errors(rp._parse_args, ["--log-level", "FOO"]))

# ── detail_server:全 flag、無位置參數、不讀 env ─────────────────────── #
d = ds._parse_args([])
check("detail_server 預設全 None(用 module 層預設)",
      d.port is None and d.host is None and d.runtime is None
      and d.control_url is None and d.log_level is None)
d = ds._parse_args(["--port", "9000", "--host", "127.0.0.1",
                    "--runtime", "/x", "--control-url", "http://h",
                    "--log-level", "WARNING"])
check("detail_server flags 解析",
      d.port == 9000 and d.host == "127.0.0.1" and d.runtime == "/x"
      and d.control_url == "http://h" and d.log_level == "WARNING")
check("detail_server 拒收舊式位置參數(/runtime 8788 → 報錯)",
      _errors(ds._parse_args, ["/runtime", "8788"]))
# 關鍵:env 已移除 → module 預設不受上面設的 env 影響
check("detail_server 不讀 ARCP_DASH_HOST(HOST 仍預設 0.0.0.0)",
      ds.HOST == "0.0.0.0")
check("detail_server 不讀 ARCP_CONTROL_URL(CONTROL 仍預設本機 8787)",
      ds.CONTROL == "http://127.0.0.1:8787")

print(f"test-cli-args: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
