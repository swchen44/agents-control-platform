#!/usr/bin/env python3
"""Q16 profile 選擇(A/B / 泛化 triage):random / script / fail-safe + config 驗證。
免 token、確定性。"""
from __future__ import annotations

import os
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arcp.profiles import Profile, _parse_select  # noqa: E402
from arcp.routing import ConfigError  # noqa: E402
from arcp.selection import select_profile  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


def _prof(name, select=None):
    return Profile(name=name, workspace_template="empty",
                   workspace_folder="tickets/{issue_id}", skills=[],
                   agent={"backend": "rawcli"}, verify=[], max_attempts=2,
                   on_unknown="pending", select=select)


def _tk():
    return Ticket(id=1, key="SCRUM-1", summary="s", state="待辦", assignee=None,
                  assignee_id=None, labels=["agent"], description="做 X")


# ── config 驗證(_parse_select)────────────────────────────────────── #
check("驗證:無 select → None", _parse_select("fc", None) is None)
try:
    _parse_select("fc", {"candidates": ["other_v2"]}); r = False
except ConfigError:
    r = True
check("驗證:候選 prefix ≠ main 名 → ConfigError", r)
try:
    _parse_select("fc", {"candidates": ["fc_v2"], "method": "bogus"}); r = False
except ConfigError:
    r = True
check("驗證:method 非 random|script → ConfigError", r)
try:
    _parse_select("fc", {"candidates": ["fc_v2"], "method": "script"}); r = False
except ConfigError:
    r = True
check("驗證:method=script 缺 script → ConfigError", r)
good = _parse_select("fc", {"candidates": ["fc_v2"], "method": "random"})
check("驗證:合法 select 解析出 candidates/method", good["candidates"] == ["fc_v2"])

# ── 無 select → 回 main ────────────────────────────────────────────── #
profiles = {"fc": _prof("fc"), "fc_v2": _prof("fc_v2")}
chosen, meta = select_profile(_tk(), profiles["fc"], profiles)
check("無 select → 回 main", chosen == "fc" and meta == {})

# ── random → 一定在 pool [fc, fc_v2] ──────────────────────────────── #
main = _prof("fc", {"candidates": ["fc_v2"], "method": "random"})
profiles = {"fc": main, "fc_v2": _prof("fc_v2")}
picks = {select_profile(_tk(), main, profiles)[0] for _ in range(30)}
check("random:選出的都在 pool", picks <= {"fc", "fc_v2"})
check("random:pool 兩個都可能被選到(30 次)", len(picks) >= 1)

# ── script → 依 stdout 回名;無效/rc!=0 → fallback main ────────────── #
d = tempfile.mkdtemp()
good_sh = os.path.join(d, "pick.sh")
open(good_sh, "w").write('#!/bin/sh\ncat >/dev/null\necho fc_v2\n')
os.chmod(good_sh, os.stat(good_sh).st_mode | stat.S_IEXEC)
main_s = _prof("fc", {"candidates": ["fc_v2"], "method": "script",
                      "script": f"{good_sh}"})
profiles = {"fc": main_s, "fc_v2": _prof("fc_v2")}
chosen, meta = select_profile(_tk(), main_s, profiles)
check("script:stdout 回 fc_v2 → 選 fc_v2", chosen == "fc_v2")

bad_sh = os.path.join(d, "bad.sh")
open(bad_sh, "w").write('#!/bin/sh\ncat >/dev/null\necho nonexistent_profile\n')
os.chmod(bad_sh, os.stat(bad_sh).st_mode | stat.S_IEXEC)
main_b = _prof("fc", {"candidates": ["fc_v2"], "method": "script",
                      "script": f"{bad_sh}"})
profiles = {"fc": main_b, "fc_v2": _prof("fc_v2")}
chosen, meta = select_profile(_tk(), main_b, profiles)
check("script:回不在 pool 的名 → fallback main", chosen == "fc" and "error" in meta)

rc_sh = os.path.join(d, "rc.sh")
open(rc_sh, "w").write('#!/bin/sh\ncat >/dev/null\nexit 5\n')
os.chmod(rc_sh, os.stat(rc_sh).st_mode | stat.S_IEXEC)
main_r = _prof("fc", {"candidates": ["fc_v2"], "method": "script",
                      "script": f"{rc_sh}"})
profiles = {"fc": main_r, "fc_v2": _prof("fc_v2")}
chosen, meta = select_profile(_tk(), main_r, profiles)
check("script:rc!=0 → fallback main", chosen == "fc")

print(f"test-selection: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
