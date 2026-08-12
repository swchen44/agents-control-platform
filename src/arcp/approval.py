"""W2.3 — 起點審批門(DESIGN §4;2026-08-13 表單化)。

per-profile `require_approval`:match 後不直接 fork——貼 plan 進 description
分區段(control 段 status=awaiting-approval,**含一次性審批表單連結,在 hash
範圍內**)、發審批表單 + comment @mention 審批者、assignee 指審批者(通知/
看板顯示用,**不是放行信號**)。

**放行 = 表單提交**(2026-08-13 定案,取代「人編 description human 段 +
assignee 交回機器人」雙信號——人不再手編 description 的 ARCP 內容):
`hil.apply_submission` 的 approval 分支驗過 → 清 pending、assignee 收回機器人、
審批紀錄(agent_name/human_email/param)由表單回寫進 human 段(archive)。
格式錯誤(agent_name 非 snake_case)表單端就地擋,**不再有 Jira 往返的
reprompt/escalate 迴圈**;`max_revisions` 設定保留相容但不再使用。

審批狀態在 store(pending_reason);description 分區段是展示——真實決策看
store,人類誤改展示無害(機器段 hash 於重寫時還原)。
"""

from __future__ import annotations

from .logutil import get_logger
from .sections import Section, parse, render

log = get_logger("approval")


class ApprovalGate:
    def __init__(self, source, store, bot_account_id: str | None,
                 form_base_url: str = ""):
        self.source = source
        self.store = store
        self.bot_account_id = bot_account_id or ""
        self.form_base_url = form_base_url

    # -- render 機器區段 --------------------------------------------------- #
    def _control_body(self, profile, session, form_url: str = "") -> str:
        body = (f"template: {profile.workspace_template}\n"
                f"profile: {profile.name}\n"
                f"status: awaiting-approval\n"
                f"revisions: {session.approval_revisions}")
        if form_url:                       # 審批表單連結(control 段=hash 範圍)
            body += f"\napproval_form: {form_url}"
        return body

    def _write_plan(self, ticket, profile, session, form_url: str) -> None:
        before, secs, after = parse(ticket.description or "")
        if not secs and not after:
            # 首次:原本無 ARCP 區塊,原始描述整段沉到區塊下方(區塊置頂)
            before, after = "", before
        by = {s.owner: s for s in secs}
        # 保留指令台連結(provision_command_link 寫在 control 段;approval 重建
        # control 時別把它蓋掉)
        prev = by.get("control")
        cc = prev.data().get("command_console") if prev else None
        body = self._control_body(profile, session, form_url)
        if cc:
            body += f"\ncommand_console: {cc}"
        by["control"] = Section("control", body)
        # 表單化:human 段不再渲染「請填」欄位(人不編 description);審批
        # 紀錄由表單提交後回寫(hil._write_human_section)。
        self.source.set_description(
            ticket.id, render(before, list(by.values()), after))

    def _acct(self, value: str | None) -> str | None:
        """email → accountId/username(assign API 用);已是識別碼 /
        離線 mock(無 user-search)→ 原樣。"""
        if value and "@" in value:
            find = getattr(self.source, "find_account_id", None)
            if find is not None:
                return find(value) or value
        return value

    def _open_approval_form(self, ticket_id: int):
        """該票是否已有待填的審批表單(冪等/自癒鍵)。"""
        for r in self.store.open_interactions_for_ticket(ticket_id):
            if getattr(r, "schema_id", "") == "approval":
                return r
        return None

    def _issue_form(self, ticket, profile) -> str:
        """發一次性審批表單(comment @mention 審批者)→ 回表單 URL。"""
        from .hil import form_link, request_human
        req = request_human(
            self.source, self.store, ticket.id, ticket.key, "approval",
            question=(f"這張票需要審批才會開始(profile={profile.name})。"
                      "請填此表單放行;格式錯誤表單會直接提示。"),
            payload_extra={"title": f"審批 {ticket.key}",
                           "profile": profile.name},
            base_url=self.form_base_url,
            mention=self._acct(profile.approver) or "")
        return form_link(self.form_base_url, req.token)

    # -- 狀態機 ------------------------------------------------------------ #
    def gate(self, ticket, profile, session) -> str:
        """回 proceed | awaiting;副作用:寫 description/comment/assignee +
        session.pending_reason。放行由表單提交事件驅動(hil.apply_submission
        清 pending)——session 不在 pending:approval 即視為已放行。

        A2(W3.2):session 變更**先持久化、再外寫**;首貼冪等 key =
        description 已有 control 段 + 已有待填審批表單(缺表單會自癒補發)。
        """
        _before, secs, _after = parse(ticket.description or "")
        by = {s.owner: s for s in secs}

        if "control" not in by:                   # 首次:貼 plan + 發表單
            session.pending_reason = "approval"
            self.store.upsert_session(session)    # 先持久化(A2)
            form_url = ""
            try:
                form_url = self._issue_form(ticket, profile)
            except Exception as e:  # noqa: BLE001 — 表單發失敗下輪自癒補發
                log.warning("%s 審批表單發送失敗(下輪補發):%s",
                            ticket.key, e)
            self._write_plan(ticket, profile, session, form_url)
            self.source.assign(ticket.id, self._acct(profile.approver))
            log.info("%s 審批門:貼 plan+發表單,指派審批者 %s",
                     ticket.key, profile.approver)
            return "awaiting"

        if session.pending_reason == "approval":  # 等表單提交(事件驅動)
            if self._open_approval_form(ticket.id) is None:
                try:                              # 自癒:crash 在發表單前
                    form_url = self._issue_form(ticket, profile)
                    self._write_plan(ticket, profile, session, form_url)
                except Exception as e:  # noqa: BLE001
                    log.warning("%s 審批表單補發失敗:%s", ticket.key, e)
            return "awaiting"

        return "proceed"                          # 表單已提交放行(pending 清)
