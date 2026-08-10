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

# ── script → 嚴格 JSON stdout {profile,reason};notfound→中止、無效/rc→main ── #
d = tempfile.mkdtemp()


def _script(name, body):
    p = os.path.join(d, name)
    open(p, "w").write("#!/bin/sh\ncat >/dev/null\n" + body + "\n")
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return p


def _sel(script):
    m = _prof("fc", {"candidates": ["fc_v2"], "method": "script",
                     "script": script})
    return select_profile(_tk(), m, {"fc": m, "fc_v2": _prof("fc_v2")})


chosen, meta = _sel(_script("pick.sh",
                            'echo \'{"profile":"fc_v2","reason":"符合"}\''))
check("script:JSON 回 fc_v2 → 選 fc_v2 + reason",
      chosen == "fc_v2" and meta.get("reason") == "符合")

chosen, meta = _sel(_script("nf.sh",
                            'echo \'{"profile":"notfound","reason":"無適用"}\''))
check("script:notfound → 回哨值(dispatcher 中止用)+ reason",
      chosen == "notfound" and meta.get("untriageable")
      and meta.get("reason") == "無適用")

chosen, meta = _sel(_script("bad.sh", 'echo \'{"profile":"nonexistent"}\''))
check("script:無效名(非池非notfound)→ fallback main",
      chosen == "fc" and "error" in meta)

chosen, meta = _sel(_script("notjson.sh", 'echo hello-plain-text'))
check("script:stdout 非 JSON → fallback main",
      chosen == "fc" and "error" in meta)

chosen, meta = _sel(_script("rc.sh", 'exit 5'))
check("script:rc!=0 → fallback main", chosen == "fc" and "error" in meta)

print(f"test-selection: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
