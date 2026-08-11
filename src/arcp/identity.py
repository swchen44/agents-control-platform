"""K:負責人 email 身分門禁(選填 opt-in)。

description 契約的 `email` 首建存進 `session.owner_email`;之後 HIL 表單 / 指令台
提交必填 email,與 owner_email 比對。**放行條件(任一)**:
- `== owner_email`(該票負責人)
- `∈ 全站 admin_emails`(config,管理者豁免)
- `== 該票 profile.approver`(若 approver 是 email)

`owner_email` 為空 → **門禁未啟用**(選填):一律放行(表單另驗「非空供稽核」)。
比對前一律**正規化**:strip + lowercase(email 慣例大小寫不敏感)。
"""
from __future__ import annotations


def normalize_email(e) -> str:
    """strip + lowercase;None/空 → ""。"""
    return (e or "").strip().lower()


def owner_gate(submitted, session, profile,
               admin_emails) -> tuple[bool, str]:
    """身分門禁。回 (放行?, 給人看的拒絕訊息)。

    session 可為 None、profile 可為 None、admin_emails 可為 None(全容錯)。
    owner_email 為空 → 門禁未啟用,放行(呼叫端仍應驗「有填 email」供稽核)。
    """
    owner = normalize_email(getattr(session, "owner_email", None)
                            if session else None)
    if not owner:
        return True, ""                       # 選填門禁未啟用(此票沒上鎖)
    sub = normalize_email(submitted)
    if sub and sub == owner:
        return True, ""                       # 負責人本人
    admins = {normalize_email(a) for a in (admin_emails or []) if a}
    if sub and sub in admins:
        return True, ""                       # 管理者豁免(config admin_emails)
    approver = normalize_email(getattr(profile, "approver", None)
                               if profile else None)
    if approver and sub == approver:
        return True, ""                       # 該票 profile 的審批者
    return False, "此 email 無權操作本票(需為負責人、管理者或審批者)。"
