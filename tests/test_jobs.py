#!/usr/bin/env python3
"""J1 agent-job:跑 script → stdout JSON 任務 → 像人 create_ticket(crid 寫進
description 最上面 yaml、不建 session、不 pin);非 JSON→trigger_error。dispatcher
建 session 時從 description 讀回 crid → clearquest_id。pytest 相容。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp import triggers as tmod  # noqa: E402
from arcp.store import Store  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402
from arcp.triggers import (  # noqa: E402
    Trigger,
    fire_agent_job,
    parse_ticket_meta,
)

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

    def create_ticket(self, project, summary, description="",
                      issue_type_id="10003", labels=None):
        self._id += 1
        self.created.append({"project": project, "summary": summary,
                             "description": description, "labels": labels or [],
                             "id": self._id})
        return Ticket(id=self._id, key=f"SCRUM-{self._id}", summary=summary,
                      state="待辦", assignee=None, assignee_id=None,
                      labels=labels or [], description=description)


def _script(body, rel="cq/scan.sh"):
    base = tempfile.mkdtemp()
    tmod.job_scripts_dir = lambda: base
    full = os.path.join(base, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(body)
    os.chmod(full, 0o755)
    return rel


def _job(rel, labels=("arcp.cr",)):
    return Trigger("scan", "scan", "agent-job", [rel], every_sec=None,
                   labels=list(labels))


# ── agent-job:script JSON 多筆 → 每筆建票(crid 寫進 description)──────── #
rel = _script(
    '#!/bin/bash\ncat <<\'J\'\n'
    '[{"summary":"CR-1","description":"修 CR-1","crid":"WCNCR0123745"},'
    '{"summary":"CR-2","description":"修 CR-2","labels":["arcp.cr","urgent"]}]\nJ\n')
st = Store(tempfile.mkdtemp()); src = FakeSource()
evs = fire_agent_job(_job(rel), src, st, tempfile.mkdtemp(), "SCRUM")
check("兩筆任務 → 兩張票", len(src.created) == 2)
check("像人建票:summary/description",
      src.created[0]["summary"] == "CR-1" and "修 CR-1" in src.created[0]["description"])
check("第一筆 crid 寫進 description 最上面 yaml",
      src.created[0]["description"].startswith("crid: WCNCR0123745"))
check("第二筆無 crid → description 無 yaml 頭",
      not src.created[1]["description"].startswith("crid:"))
check("labels:第一筆用 job 預設、第二筆用自己的",
      src.created[0]["labels"] == ["arcp.cr"]
      and src.created[1]["labels"] == ["arcp.cr", "urgent"])
check("不建 session(agent-job 不 pin)",
      all(st.get_session(c["id"]) is None for c in src.created))
check("journal job_fired 帶 crid、無 profile",
      any(e["type"] == "job_fired" and e.get("crid") == "WCNCR0123745"
          and "profile" not in e for e in evs))
check("有 script_run_started/finished(共用 log)",
      any(e["type"] == "script_run_started" for e in evs))
st.close()

# parse_ticket_meta 讀回
meta = parse_ticket_meta(src.created[0]["description"])
check("parse_ticket_meta 讀回 crid", meta.get("crid") == "WCNCR0123745")

# ── stdout 非 JSON → trigger_error(agent-job 該回 JSON)──────────────── #
rel2 = _script('#!/bin/bash\necho "not json at all"\n')
st = Store(tempfile.mkdtemp()); src = FakeSource()
evs = fire_agent_job(_job(rel2), src, st, tempfile.mkdtemp(), "SCRUM")
check("非 JSON → 0 票 + trigger_error", len(src.created) == 0
      and any(e["type"] == "trigger_error" for e in evs))

# ── script rc!=0 → trigger_error ──────────────────────────────────────── #
rel3 = _script('#!/bin/bash\nexit 3\n')
st = Store(tempfile.mkdtemp()); src = FakeSource()
evs = fire_agent_job(_job(rel3), src, st, tempfile.mkdtemp(), "SCRUM")
check("rc!=0 → 0 票 + trigger_error", len(src.created) == 0
      and any(e["type"] == "trigger_error" for e in evs))
st.close()

# ── dispatcher 建 session 時從 description 讀回 crid → clearquest_id ────── #
from arcp import dispatcher as dmod  # noqa: E402
from arcp.dispatcher import Dispatcher  # noqa: E402
from arcp.inner_runner import AttemptResult  # noqa: E402
from arcp.profiles import Profile  # noqa: E402


def _prof():
    return Profile(name="p", workspace_template="empty",
                   workspace_folder="tickets/{issue_id}", skills=[],
                   agent={"backend": "rawcli", "tag": "p"}, verify=[],
                   max_attempts=1, on_unknown="pending")


class _Src:
    def add_comment(self, iid, text): pass
    def set_description(self, iid, text): pass
    def get_ticket(self, iid): return None


dmod.run_attempt = lambda *a, **k: AttemptResult(
    raw_outcome="completed", session_id="s", truly_resumed=False,
    cost_usd=0.0, error=None, events_path="", envelope_path="",
    error_kind=None, structured={"reason": "x", "status": "done", "next": None,
                                 "summary": "ok", "score": 5})
_root = tempfile.mkdtemp()
_st = Store(os.path.join(_root, "s"))
d = Dispatcher(_Src(), _st, {"p": _prof()}, root=_root)
_tk = Ticket(id=1, key="P-1", summary="s", state="To Do", assignee=None,
             assignee_id=None, description="crid: WCNCR9\n\n真正的任務描述")
d.handle(_tk, "p")
check("dispatcher 建 session 時 crid → clearquest_id",
      _st.get_session(1).clearquest_id == "WCNCR9")
_st.close()

print(f"test-jobs: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
