#!/usr/bin/env python3
"""profile 選擇(A/B / 泛化 triage,J4):config 驗證(random 限同族、script 放寬)、
random/script、軸 B(script 回任何已定義 profile)、遞歸(鏈/回自己/繞圈/第 10 層截斷)、
fail-safe/notfound。免 token、確定性。"""
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


def _err(sel):
    try:
        _parse_select("fc", sel)
        return False
    except ConfigError:
        return True


# ── config 驗證(J4:random 限同族、script 放寬)──────────────────────── #
check("驗證:無 select → None", _parse_select("fc", None) is None)
check("驗證:random 候選 prefix ≠ main → ConfigError",
      _err({"candidates": ["other_v2"], "method": "random"}))
check("驗證:random 缺 candidates → ConfigError", _err({"method": "random"}))
check("驗證:method 非 random|script → ConfigError",
      _err({"candidates": ["fc_v2"], "method": "bogus"}))
check("驗證:script 缺 script 命令 → ConfigError", _err({"method": "script"}))
check("驗證:script 候選免 prefix、免 candidates → OK(軸 B)",
      _parse_select("fc", {"method": "script", "script": "x"})["candidates"] == []
      and _parse_select("fc", {"method": "script", "script": "x",
                               "candidates": ["anything"]}) is not None)

# ── 無 select → 回 main;random 在 pool ─────────────────────────────── #
chosen, meta = select_profile(_tk(), _prof("fc"), {"fc": _prof("fc")})
check("無 select → 回 main", chosen == "fc" and meta == {})
main = _prof("fc", {"candidates": ["fc_v2"], "method": "random"})
picks = {select_profile(_tk(), main,
                        {"fc": main, "fc_v2": _prof("fc_v2")})[0]
         for _ in range(30)}
check("random:選出的都在 pool", picks <= {"fc", "fc_v2"})

# ── script 單層 + 軸 B(回任何已定義 profile)+ notfound/fail-safe ─────── #
d = tempfile.mkdtemp()


def _script(name, target_json):
    p = os.path.join(d, name)
    open(p, "w").write("#!/bin/sh\ncat >/dev/null\necho '" + target_json + "'\n")
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return p


def _pscript(name, target):        # profile `name`:select.script 回 {profile:target}
    return _prof(name, {"method": "script", "candidates": [],
                        "script": _script(f"{name}.sh",
                                          '{"profile":"%s"}' % target)})


def _sel_raw(name, body):          # 用原始 body(測 notfound/壞輸出)
    m = _prof("fc", {"method": "script", "candidates": [],
                     "script": _script(name, body)})
    return select_profile(_tk(), m, {"fc": m, "fc_v2": _prof("fc_v2"),
                                     "other": _prof("other")})


chosen, meta = _sel_raw("nf.sh", '{"profile":"notfound","reason":"無適用"}')
check("script:notfound → 哨值(dispatcher 中止)+ reason",
      chosen == "notfound" and meta.get("untriageable"))
# 軸 B:回一個非候選、但已定義的 profile → 鎖定它
chosen, _ = _sel_raw("axb.sh", '{"profile":"other"}')
check("軸 B:script 回非候選的已定義 profile(other)→ 鎖定它", chosen == "other")
# 未定義名 / 非 JSON / rc≠0 → fail-safe 回 main
chosen, _ = _sel_raw("undef.sh", '{"profile":"nonexistent"}')
check("script:未定義名 → fallback main", chosen == "fc")
m = _prof("fc", {"method": "script", "candidates": [],
                 "script": os.path.join(d, "rc.sh")})
open(os.path.join(d, "rc.sh"), "w").write("#!/bin/sh\nexit 5\n")
os.chmod(os.path.join(d, "rc.sh"), 0o755)
check("script:rc!=0 → fallback main",
      select_profile(_tk(), m, {"fc": m})[0] == "fc")

# ── 遞歸(軸 A):鏈 / 回自己 / 繞圈 / 第 10 層截斷 ────────────────────── #
a = _pscript("a", "b"); b = _pscript("b", "c"); c = _prof("c")   # c=葉
chosen, meta = select_profile(_tk(), a, {"a": a, "b": b, "c": c})
check("遞歸:a→b→c(葉)→ 鎖定 c、chain 記錄",
      chosen == "c" and meta["chain"] == ["a", "b", "c"])

s = _pscript("s", "s")             # 回自己
chosen, _ = select_profile(_tk(), s, {"s": s})
check("遞歸:回自己 → 停(鎖定自己)", chosen == "s")

x = _pscript("x", "y"); y = _pscript("y", "x")    # 繞圈
chosen, _ = select_profile(_tk(), x, {"x": x, "y": y})
check("遞歸:繞圈(x→y→x)→ 停在 y", chosen == "y")

N = 12                             # p0→p1→…→p10(每層 select)→ 第 10 層截斷
chainp = {f"p{i}": (_pscript(f"p{i}", f"p{i+1}") if i < N - 1 else _prof(f"p{i}"))
          for i in range(N)}
chosen, meta = select_profile(_tk(), chainp["p0"], chainp)
check("遞歸:超過 10 層 → 截斷(不無限;停在 p10)",
      chosen == "p10" and len(meta["chain"]) == 11 and "p11" not in meta["chain"])

print(f"test-selection: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
