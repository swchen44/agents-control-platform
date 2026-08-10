#!/usr/bin/env python3
"""泛化 job(P2):agent-job 開真 Jira 票 + 鎖定 profile 的 session + count 上限 + task_script。

設計見 docs/design/lifecycle.md §5.1。免網:FakeSource.create_ticket 攔建票。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arcp.routing import ConfigError  # noqa: E402
from arcp.store import Store  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402
from arcp.triggers import Trigger, fire_agent_job  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


class FakeSource:
    def __init__(self):
        self.created = []
        self._id = 100

    def create_ticket(self, project, summary, description="", issue_type_id="10003",
                      labels=None):
        self._id += 1
        self.created.append({"project": project, "summary": summary,
                             "description": description, "labels": labels or [],
                             "id": self._id})
        return Ticket(id=self._id, key=f"SCRUM-{self._id}", summary=summary,
                      state="待辦", assignee=None, assignee_id=None,
                      labels=labels or [], description=description)


def _job(**kw):
    base = dict(name="j", profile="scanner", run_name="scan", prompt="",
                every_sec=None)
    base.update(kw)
    return Trigger(**base)


PROFILES = {"scanner": object()}

# ── load_triggers 驗證(count 需排程 / agent 需 task)────────────────── #
from arcp.triggers import load_triggers  # noqa: E402


def _cfg(**job):
    import textwrap

    import yaml
    d = {"outer_loop": {"triggers": [dict(name="j", run_name="scan", **job)]},
         "inner_loop": {"profiles": {}}}
    p = os.path.join(tempfile.mkdtemp(), "c.yaml")
    open(p, "w").write(yaml.safe_dump(d, allow_unicode=True))
    _ = textwrap  # noqa
    return p


def _load_err(job, profiles):
    try:
        load_triggers(_cfg(**job), profiles)
        return False
    except ConfigError:
        return True


check("load:count=0 無 cron → ConfigError",
      _load_err({"profile": "scanner", "task": "x", "count": 0},
                {"scanner": object()}))
check("load:agent-job 無 task/task_script/prompt → ConfigError",
      _load_err({"profile": "scanner", "count": 1}, {"scanner": object()}))
_tp = load_triggers(_cfg(profile="scanner", task="巡檢", count=0,
                         cron="0 3 * * *", labels=["agent"]),
                    {"scanner": object()})
check("load:合法 job 解析(count/task/labels)",
      _tp[0].count == 0 and _tp[0].task == "巡檢" and _tp[0].labels == ["agent"])

# ── fire_agent_job:靜態 task → 一張票 + 鎖定 profile 的 session ────────────────── #
st = Store(tempfile.mkdtemp()); src = FakeSource()
evs = fire_agent_job(_job(task="每日巡檢", labels=["agent"]), src, st,
                     PROFILES, "SCRUM")
check("static:建了一張票", len(src.created) == 1
      and src.created[0]["project"] == "SCRUM")
check("static:summary 帶 [job:scan]、labels 帶入",
      "[job:scan]" in src.created[0]["summary"]
      and src.created[0]["labels"] == ["agent"])
new_id = src.created[0]["id"]
sess = st.get_session(new_id)
check("static:預建鎖定 profile 的 session(profile=scanner、(handoff) 哨值)",
      sess is not None and sess.profile == "scanner"
      and sess.workspace == "(handoff)")
check("static:journal job_fired", any(
    e["type"] == "job_fired" and e["profile"] == "scanner" for e in evs))
st.close()

# ── fire_agent_job:task_script 多筆 → 每筆一張票 ──────────────────────── #
st = Store(tempfile.mkdtemp()); src = FakeSource()
gen = os.path.join(tempfile.mkdtemp(), "gen.py")
open(gen, "w").write(
    'import json;print(json.dumps(['
    '{"summary":"CR-1","description":"修 CR-1","labels":["agent"]},'
    '{"summary":"CR-2","description":"修 CR-2"}]))')
evs = fire_agent_job(_job(task_script=[sys.executable, gen], labels=["agent"]),
                     src, st, PROFILES, "SCRUM")
check("task_script:stdout JSON 多筆 → 兩張票", len(src.created) == 2)
check("task_script:各自 summary/description",
      src.created[0]["summary"] == "CR-1"
      and src.created[1]["description"] == "修 CR-2")
check("task_script:第二筆無 labels → 用 job 預設 labels",
      src.created[1]["labels"] == ["agent"])
check("task_script:兩張都預建鎖定 profile 的 session",
      all(st.get_session(c["id"]) is not None for c in src.created))
st.close()

# ── task_script 壞掉 → 0 票(降級不擲例外)──────────────────────────── #
st = Store(tempfile.mkdtemp()); src = FakeSource()
bad = os.path.join(tempfile.mkdtemp(), "bad.py")
open(bad, "w").write('import sys;sys.exit(3)')
fire_agent_job(_job(task_script=[sys.executable, bad]), src, st, PROFILES, "SCRUM")
check("task_script rc!=0 → 0 票、不炸", len(src.created) == 0)
st.close()

# ── store run_count(count 上限記數)─────────────────────────────────── #
st = Store(tempfile.mkdtemp())
check("run_count:初始 0", st.trigger_run_count("j") == 0)
st.bump_trigger_run("j", 100.0)
st.bump_trigger_run("j", 200.0)
check("run_count:bump 兩次 → 2", st.trigger_run_count("j") == 2)
st.close()

print(f"test-jobs: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
