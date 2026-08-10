#!/usr/bin/env python3
"""W10.3 a2a handoff(2026-08-09 定案):HIL 表單改派下一棒。

覆蓋:①同票 next(reset+鎖定 profile+哨值)②跨票 base(建新票+預建鎖定 profile 的 session+base_ref+
本票 ABORTED)③資料不完整 fail-safe 降級續跑 ④workspace 注入 base 脈絡到子票。
(W2.5 的指令式 @agent next 換手另見 test_handoff.py)免 token/免真 Jira/agent,確定性。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arcp.hil import apply_submission  # noqa: E402
from arcp.interaction import (  # noqa: E402
    FORM_SCHEMAS,
    build_request,
    opt_pairs,
    validate_submission,
)
from arcp.store import Store, TicketSession  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402
from arcp.workspace import _read_human_notes, inject_base_context  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


class FakeSource:
    """記錄 create_ticket / add_comment;get_ticket 回帶 labels 的票。"""
    base_url = "https://x.atlassian.net"

    def __init__(self):
        self.comments = []
        self.descs = {}
        self.created = []
        self._next_id = 100

    def add_comment(self, iid, body):
        self.comments.append((iid, body))

    def get_ticket(self, iid):
        return Ticket(id=iid, key=f"SCRUM-{iid}", summary="原任務", state="進行中",
                      assignee=None, assignee_id=None, labels=["agent", "team-x"],
                      description=self.descs.get(iid, "做 X"), comments=[])

    def set_description(self, iid, d):
        self.descs[iid] = d

    def transition(self, iid, cat):
        return True

    def create_ticket(self, project_key, summary, description="",
                      issue_type_id="10003", labels=None):
        self._next_id += 1
        nid = self._next_id
        self.created.append({"project": project_key, "summary": summary,
                             "description": description, "labels": labels or [],
                             "id": nid})
        self.descs[nid] = description
        return Ticket(id=nid, key=f"SCRUM-{nid}", summary=summary, state="待辦",
                      assignee=None, assignee_id=None, labels=labels or [],
                      description=description, comments=[])


PROFILES = {"p": object(), "q2": object()}


def _sess(iid, ws, profile="p"):
    return TicketSession(issue_id=iid, key=f"SCRUM-{iid}", profile=profile,
                         workspace=ws, session_id="s1", attempts=2,
                         outcome="SUCCESS", pending_reason=None, cost_usd=0.5)


def _score_req(iid, decision, kind="", prof="", prompt="", score=7):
    req = build_request(iid, f"SCRUM-{iid}", "score_and_close",
                        payload={"profiles": list(PROFILES)})
    req.submission = {"human_score": score, "close_decision": decision,
                      "handoff_kind": kind, "next_profile": prof,
                      "handoff_prompt": prompt}
    return req


# ── schema:score_and_close/decision 內嵌 handoff 欄位 ────────────────── #
sc_keys = {f["key"] for f in FORM_SCHEMAS["score_and_close"]["fields"]}
check("schema:score_and_close 有 handoff 欄位",
      {"handoff_kind", "next_profile", "handoff_prompt"} <= sc_keys)
_cd_vals = [v for v, _ in opt_pairs(
    next(f for f in FORM_SCHEMAS["score_and_close"]["fields"]
         if f["key"] == "close_decision")["options"])]
check("schema:close_decision 含 handoff(value 穩定)", "handoff" in _cd_vals)
dc_keys = {f["key"] for f in FORM_SCHEMAS["decision"]["fields"]}
check("schema:decision 有 next_step + handoff 欄位",
      {"next_step", "handoff_kind", "next_profile"} <= dc_keys)

# ── validate:next_profile 下拉吃 payload['profiles'];非法選項擋掉 ────── #
req = _score_req(1, "handoff", "next", "q2", "接著做 Z")
okv, errs, cleaned = validate_submission("score_and_close", req.submission, req)
check("validate:合法 handoff 提交過驗證",
      okv and cleaned.get("next_profile") == "q2")
bad = _score_req(1, "handoff", "next", "不存在", "x")
okb, errsb, _ = validate_submission("score_and_close", bad.submission, bad)
check("validate:next_profile 非候選 → 擋", not okb)

# ── ① 同票 next:reset + 鎖定 profile + workspace 哨值 ───────────────────────── #
rt = tempfile.mkdtemp(); st = Store(rt); src = FakeSource()
ws = os.path.join(rt, "tickets", "1", "ws"); os.makedirs(ws)
st.upsert_session(_sess(1, ws))
req = _score_req(1, "handoff", "next", "q2", "接著做 Z")
st.upsert_interaction(req)
evs = apply_submission(src, st, req, profiles=PROFILES)
s = st.get_session(1)
check("next:profile 換成 q2", s.profile == "q2")
check("next:session/attempts/outcome 全 reset",
      s.session_id is None and s.attempts == 0 and s.outcome is None
      and s.pending_reason is None)
check("next:workspace = (handoff) 哨值", s.workspace == "(handoff)")
check("next:human_score 記入(=7)", s.human_score == 7)
check("next:journal handoff kind=next via=hil",
      any(e["type"] == "handoff" and e.get("kind") == "next"
          and e.get("via") == "hil" and e.get("to") == "q2" for e in evs))
check("next:handoff_prompt 寫進 description human 段",
      "接著做 Z" in src.descs.get(1, ""))
st.close()

# ── ② 跨票 base:建新票 + 預建鎖定 profile 的 session(base_ref)+ 本票 ABORTED ─ #
rt = tempfile.mkdtemp(); st = Store(rt); src = FakeSource()
ws = os.path.join(rt, "tickets", "1", "ws"); os.makedirs(ws)
st.upsert_session(_sess(1, ws))
req = _score_req(1, "handoff", "base", "q2", "用新方向重做")
st.upsert_interaction(req)
evs = apply_submission(src, st, req, profiles=PROFILES)
old = st.get_session(1)
check("base:本票 outcome=ABORTED(交接非失敗)", old.outcome == "ABORTED")
check("base:create_ticket 被呼叫一次", len(src.created) == 1)
ct = src.created[0]
check("base:新票同 project=SCRUM", ct["project"] == "SCRUM")
check("base:新票沿用本票 labels", ct["labels"] == ["agent", "team-x"])
check("base:新票 description 含 base: 標記與交接指示",
      "base: SCRUM-1" in ct["description"] and "用新方向重做" in ct["description"])
new_id = ct["id"]
child = st.get_session(new_id)
check("base:預建新票 session 鎖定 q2", child is not None and child.profile == "q2")
check("base:新票 base_ref 指回本票 issue_id",
      child.base_ref == "1" and child.workspace == "(handoff)")
check("base:journal handoff kind=base 帶 new_ticket",
      any(e["type"] == "handoff" and e.get("kind") == "base"
          and e.get("new_ticket") == f"SCRUM-{new_id}" for e in evs))
st.close()

# ── ③ fail-safe:handoff 但 kind 空 → 降級續跑原 agent(不硬失敗)──────── #
rt = tempfile.mkdtemp(); st = Store(rt); src = FakeSource()
ws = os.path.join(rt, "tickets", "1", "ws"); os.makedirs(ws)
st.upsert_session(_sess(1, ws))
req = _score_req(1, "handoff", "", "", "")   # kind/profile 空
st.upsert_interaction(req)
evs = apply_submission(src, st, req, profiles=PROFILES)
s = st.get_session(1)
check("fail-safe:未建新票", len(src.created) == 0)
check("fail-safe:profile 不變(續跑原 agent)", s.profile == "p")
check("fail-safe:解終態(outcome/pending 清)→ 下輪 resume",
      s.outcome is None and s.pending_reason is None)
check("fail-safe:journal handoff_invalid via=hil",
      any(e["type"] == "handoff_invalid" and e.get("via") == "hil" for e in evs))
st.close()

# ── ④ 注入 base 脈絡:BASE_<key>/ 內含 TICKET.md + envelope + 指路 ─────── #
rt = tempfile.mkdtemp()
base_ws = os.path.join(rt, "tickets", "1", "ws"); os.makedirs(base_ws)
with open(os.path.join(base_ws, "TICKET.md"), "w") as f:
    f.write("# SCRUM-1 原任務\n做 X 的脈絡\n")
att = os.path.join(rt, "tickets", "1", "attempts"); os.makedirs(att)
for n in ("a1.envelope.json", "a2.envelope.json"):
    with open(os.path.join(att, n), "w") as f:
        f.write('{"outcome":"ok"}')
child_ws = os.path.join(rt, "tickets", "101", "ws"); os.makedirs(child_ws)
dest = inject_base_context(child_ws, base_ws, "SCRUM-1", "https://x.atlassian.net")
check("inject:BASE_SCRUM-1/ 目錄建立", os.path.basename(dest) == "BASE_SCRUM-1")
check("inject:複製了 base TICKET.md",
      os.path.isfile(os.path.join(dest, "TICKET.md")))
check("inject:只複製最後一個 envelope(a2)",
      os.path.isfile(os.path.join(dest, "a2.envelope.json"))
      and not os.path.isfile(os.path.join(dest, "a1.envelope.json")))
check("inject:寫了 HANDOFF.md 指路(含來源票連結)",
      os.path.isfile(os.path.join(dest, "HANDOFF.md"))
      and "browse/SCRUM-1" in open(os.path.join(dest, "HANDOFF.md")).read())
check("inject:human sidecar 指向 BASE_ 目錄(進 TICKET.md 人類指示段)",
      "BASE_SCRUM-1" in _read_human_notes(child_ws))

# ── ⑤ dispatcher:base 子票首次派工 → provision 後注入脈絡 + 清 base_ref ── #
from arcp import dispatcher as dmod  # noqa: E402
from arcp.dispatcher import Dispatcher  # noqa: E402
from arcp.inner_runner import AttemptResult  # noqa: E402
from arcp.profiles import Profile  # noqa: E402


def _profile(name):
    return Profile(name=name, workspace_template="empty",
                   workspace_folder=f"tickets/{name}-{{issue_id}}", skills=[],
                   agent={"backend": "rawcli", "tag": name}, verify=[],
                   max_attempts=2, on_unknown="pending")


def _fork_ok(*a, **kw):
    return AttemptResult(raw_outcome="completed", session_id="s-new",
                         truly_resumed=False, cost_usd=0.0, error=None,
                         events_path="", envelope_path="", error_kind=None,
                         structured=None)


dmod.run_attempt = _fork_ok
rt = tempfile.mkdtemp()
st = Store(os.path.join(rt, "s")); src = FakeSource()
# base(來源)session:issue_id=1、真實 ws 帶 TICKET.md + envelope
base_ws = os.path.join(rt, "tickets", "p-1", "ws"); os.makedirs(base_ws)
with open(os.path.join(base_ws, "TICKET.md"), "w") as f:
    f.write("# SCRUM-1\n來源脈絡\n")
batt = os.path.join(rt, "tickets", "p-1", "attempts"); os.makedirs(batt)
with open(os.path.join(batt, "a1.envelope.json"), "w") as f:
    f.write("{}")
bs = _sess(1, base_ws); bs.outcome = "ABORTED"; st.upsert_session(bs)
# base 子票 session:issue_id=2、鎖定 q2、workspace 哨值、base_ref 指回 1
st.upsert_session(TicketSession(
    issue_id=2, key="SCRUM-2", profile="q2", workspace="(handoff)",
    session_id=None, attempts=0, outcome=None, pending_reason=None,
    cost_usd=0.0, base_ref="1"))
child_tk = Ticket(id=2, key="SCRUM-2", summary="子票", state="待辦",
                  assignee=None, assignee_id=None, labels=["agent"],
                  description="base: SCRUM-1\n用新方向重做", comments=[])
d = Dispatcher(src, st, {"p": _profile("p"), "q2": _profile("q2")}, root=rt)
evs = d.handle(child_tk, "q2")
child = st.get_session(2)
check("dispatcher:base_ref 注入後清為 None(一次性)", child.base_ref is None)
check("dispatcher:發了 base_injected 事件",
      any(e["type"] == "base_injected" and e.get("base") == "SCRUM-1"
          for e in evs))
check("dispatcher:子票 ws 有 BASE_SCRUM-1/(含來源 TICKET.md)",
      os.path.isfile(os.path.join(child.workspace, "BASE_SCRUM-1", "TICKET.md")))
check("dispatcher:子票 TICKET.md 人類指示段指向 BASE_(佈建刷新後)",
      "BASE_SCRUM-1" in open(os.path.join(child.workspace, "TICKET.md")).read())
st.close()

print(f"test-handoff-hil: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
