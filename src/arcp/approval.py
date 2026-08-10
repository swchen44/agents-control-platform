"""W2.3 — 起點審批門(DESIGN §4)。

per-profile `require_approval`:match 後不直接 fork,先把 plan 寫進 description 分區段
(control 段 status=awaiting-approval + human 空欄段)、貼填表說明 comment(幂等)、
assignee 改審批者;等人填 human 段參數 + 把 assignee 交回機器人 → 校驗:
  通過 → proceed(dispatcher 照常 copy+fork)
  失敗 → reprompt(comment 寫 error + assignee 退回審批者,approval_revisions++)
  超 max_revisions → escalate(pending:escalated,需人工)

審批狀態在 store(pending_reason + approval_revisions);description 分區段是「展示 +
人類輸入表單」——真實決策看 store,人類誤改展示無害(機器段 hash 於重寫時還原)。

註:approver 設定值視為 accountId(email→accountId 解析是真實 Jira 細節,另補)。
"""

from __future__ import annotations

from .logutil import get_logger
from .sections import Section, parse, render, validate_keys

log = get_logger("approval")

_INSTRUCTIONS = (
    "[agent] 這張票需要人工審批才會開始。請在 description 的 "
    "`### [ARCP owner=human]` 區段填好參數(至少 agent_name,snake_case),"
    "然後把 assignee 改回機器人,系統就會開始。填錯會退回並在此說明。")


class ApprovalGate:
    def __init__(self, source, store, bot_account_id: str | None):
        self.source = source
        self.store = store
        self.bot_account_id = bot_account_id or ""

    # -- render 機器/人類區段 --------------------------------------------- #
    def _control_body(self, profile, session) -> str:
        return (f"template: {profile.workspace_template}\n"
                f"profile: {profile.name}\n"
                f"status: awaiting-approval\n"
                f"revisions: {session.approval_revisions}")

    def _human_body(self) -> str:
        return ("agent_name:            # ← 請填(snake_case)\n"
                "human_email:           # 選填:接手人 Jira email(空=審批者)\n"
                "param:")

    def _write_plan(self, ticket, profile, session) -> None:
        before, secs, after = parse(ticket.description or "")
        if not secs and not after:
            # 首次:原本無 ARCP 區塊,原始描述整段沉到區塊下方(區塊置頂)
            before, after = "", before
        by = {s.owner: s for s in secs}
        # 保留指令台連結(provision_command_link 寫在 control 段;approval 重建
        # control 時別把它蓋掉)
        prev = by.get("control")
        cc = prev.data().get("command_console") if prev else None
        body = self._control_body(profile, session)
        if cc:
            body += f"\ncommand_console: {cc}"
        by["control"] = Section("control", body)
        by.setdefault("human", Section("human", self._human_body()))
        # render 依 canonical 序(human→control→agent)自動排,不需手排
        self.source.set_description(
            ticket.id, render(before, list(by.values()), after))

    def _validate_human(self, human: Section | None) -> list[str]:
        if human is None:
            return ["缺 human 區段"]
        errs = [f"key 非 snake_case: {k}" for k in validate_keys(human)]
        if not str(human.data().get("agent_name") or "").strip():
            errs.append("agent_name 必填")
        # human_email 選填(空=fallback 審批者);有填就即時驗證是合法 Jira 帳號
        email = str(human.data().get("human_email") or "").strip()
        if email:
            find = getattr(self.source, "find_account_id", None)
            if "@" not in email:
                errs.append(f"human_email 不像 email: {email}")
            elif find is not None and find(email) is None:
                errs.append(f"human_email 不是合法 Jira 帳號: {email}")
        return errs

    def _acct(self, value: str | None) -> str | None:
        """email → accountId(assign API 收 accountId);已是 accountId /
        離線 mock(無 user-search)→ 原樣。"""
        if value and "@" in value:
            find = getattr(self.source, "find_account_id", None)
            if find is not None:
                return find(value) or value
        return value

    # -- 狀態機 ------------------------------------------------------------ #
    def gate(self, ticket, profile, session) -> str:
        """回 proceed | awaiting | reprompt | escalate;副作用:寫 description/
        comment/assignee + 改 session(pending_reason/approval_revisions)。

        A2(W3.2):session 變更**先持久化、再外寫**(comment/assign/description)
        ——crash 在外寫途中,revisions/pending 已落 store,重跑不會重置退回計數
        (escalate 上限跨 crash 有效);首貼的冪等 key = description 已有 control
        段(重跑走 awaiting 分支,不重貼)。
        """
        _before, secs, _after = parse(ticket.description or "")
        by = {s.owner: s for s in secs}

        if "control" not in by:                       # 首次:貼 plan、指派審批者
            session.pending_reason = "approval"
            self.store.upsert_session(session)        # 先持久化(A2)
            self._write_plan(ticket, profile, session)
            self.source.add_comment(ticket.id, _INSTRUCTIONS)
            self.source.assign(ticket.id, self._acct(profile.approver))
            log.info("%s 審批門:貼 plan,指派審批者 %s", ticket.key, profile.approver)
            return "awaiting"

        if (ticket.assignee_id or "") != self.bot_account_id:
            session.pending_reason = "approval"       # 還在人手上,繼續等
            self.store.upsert_session(session)
            return "awaiting"

        errs = self._validate_human(by.get("human"))  # 交回機器人 → 校驗
        if not errs:
            session.pending_reason = None
            self.store.upsert_session(session)
            log.info("%s 審批通過,放行", ticket.key)
            return "proceed"

        session.approval_revisions += 1
        if session.approval_revisions > profile.max_revisions:
            session.pending_reason = "escalated"
            self.store.upsert_session(session)        # 先持久化(A2)
            self.source.add_comment(
                ticket.id, f"[agent] 審批退回超過 {profile.max_revisions} 次,"
                           f"需人工介入(escalate)。")
            log.info("%s 審批 escalate", ticket.key)
            return "escalate"

        session.pending_reason = "approval"
        self.store.upsert_session(session)            # 先持久化(A2)
        self.source.add_comment(
            ticket.id, "[agent] 填表有誤,請修正後把 assignee 交回機器人:\n"
                       + "\n".join(f"- {e}" for e in errs))
        self.source.assign(ticket.id, self._acct(profile.approver))
        log.info("%s 審批退回(第 %d 次)", ticket.key, session.approval_revisions)
        return "reprompt"
