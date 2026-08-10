"""生命週期狀態「推導」——單一真相來源(唯讀,不新增 DB state 欄)。

DB 沒有 state 欄;6 態由此純函式從 ticket_session 的正交原始欄推導。dashboard
(detail_server)、指令 console(form_server)、指令核心(commands)都用同一份,避免
各處各自判斷造成漂移。設計理由見 docs/design/architecture.md §3.1。

Model A(W10.1):success/failure/unknown 不是頂層態,收斂成 hil_end 的「結果」屬性;
舊 inactive(交人)+ 非終態 pending(審批/預算/待審視…)合併成 hil_middle。closed 是
概念終點(人關 Jira→離開 jql,不設 DB 態)。優先序:
  aborted > hil_end(終態評分)> hil_middle(pending 原因)> queued >
  hil_middle(交人 inactive)> running;無 session = todo。
"""

from __future__ import annotations


def _get(s, key):
    """讀 session 欄位;接受 dict(detail_server)或物件(TicketSession)。"""
    if s is None:
        return None
    if isinstance(s, dict):
        return s.get(key)
    return getattr(s, key, None)


def canonical_state(s) -> str:
    """(outcome, pending_reason, queued, inactive, 有無 session)→ 單一態 key。

    唯讀:只映射既有欄位,不改 runtime。s = session(dict 或物件)或 None/空。"""
    if not s:
        return "todo"
    oc = _get(s, "outcome")
    if oc == "ABORTED":
        return "aborted"
    if oc in ("SUCCESS", "FAILURE", "UNKNOWN"):
        return "hil_end"                 # 終點交人:評分 → 續跑/關票
    if _get(s, "pending_reason"):
        return "hil_middle"              # 過程中等人(審批/預算/待審視…)
    if _get(s, "queued"):
        return "queued"
    if _get(s, "inactive"):
        return "hil_middle"              # 過程中等人(交人:assignee 在人手上)
    return "running"                     # 進行中
