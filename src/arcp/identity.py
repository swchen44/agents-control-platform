"""K:負責人 email 身分門禁(選填 opt-in;K6 多負責人)。

description 契約的 `email`(可逗號分隔多個)首建存進 `session.owner_email_list`;之後
HIL 表單 / 指令台提交必填 email,與 owner 集合比對。**放行條件(任一)**:
- `∈ owners`(該票負責人們)
- `∈ 全站 admin_emails`(config,管理者豁免)
- `== 該票 profile.approver`(若 approver 是 email)

`owner_email_list` 為空 → **門禁未啟用**(選填):一律放行(表單另驗「非空供稽核」)。
比對前一律**正規化**:strip + lowercase(email 慣例大小寫不敏感)。
"""
from __future__ import annotations


def normalize_email(e) -> str:
    """strip + lowercase;None/空 → ""。"""
    return (e or "").strip().lower()


def normalize_email_list(s) -> str:
    """逗號分隔 email 正規化:每個 strip+lower、去空,逗號連接(存 session 用)。
    'Alice@X, bob@Y ' → 'alice@x,bob@y';None/空 → ''。"""
    return ",".join(e for e in (normalize_email(x)
                                for x in (s or "").split(",")) if e)


def owner_emails(session) -> set:
    """該 session 的 owner email 集合(正規化)。owner_email_list 空 → 空集合。"""
    raw = getattr(session, "owner_email_list", None) if session else None
    return {e for e in (normalize_email(x)
                        for x in (raw or "").split(",")) if e}


def resolve_user_id(email, source=None, store=None, user_map=None,
                    username_rule: str = "") -> str | None:
    """email → Jira 使用者識別碼(cloud=accountId、dc=name/username)。

    查序(主題 L6/L7):① config `source.user_map` 手動映射(user-search
    被權限擋的逃生路)② store user_dir 快取 ③ source.find_user_id /
    find_account_id(命中寫回快取)④ `username_rule` 推導('local'=email
    @ 前段;或含 {local} 的模板如 'corp-{local}')。全 miss → None。
    供 mention/watcher 解析;**approval 驗證仍走 source 直查**(推導值
    不能當「合法帳號」證據)。"""
    e = normalize_email(email)
    if not e:
        return None
    m = {normalize_email(k): v for k, v in (user_map or {}).items()}
    if m.get(e):
        return m[e]
    if store is not None:
        try:
            uid = store.get_user_uid(e)
            if uid:
                return uid
        except Exception:      # noqa: BLE001 — 快取壞不擋解析
            pass
    find = (getattr(source, "find_user_id", None)
            or getattr(source, "find_account_id", None))
    if find:
        try:
            uid = find(e)
        except Exception:      # noqa: BLE001 — 查詢失敗走 fallback
            uid = None
        if uid:
            if store is not None:
                try:
                    store.put_user_uid(e, uid)
                except Exception:  # noqa: BLE001
                    pass
            return uid
    local = e.split("@")[0]
    if username_rule == "local":
        return local
    if "{local}" in (username_rule or ""):
        return username_rule.replace("{local}", local)
    return None


def owner_gate(submitted, session, profile,
               admin_emails) -> tuple[bool, str]:
    """身分門禁。回 (放行?, 給人看的拒絕訊息)。

    session 可為 None、profile 可為 None、admin_emails 可為 None(全容錯)。
    owners 為空 → 門禁未啟用,放行(呼叫端仍應驗「有填 email」供稽核)。
    """
    owners = owner_emails(session)
    if not owners:
        return True, ""                       # 選填門禁未啟用(此票沒上鎖)
    sub = normalize_email(submitted)
    if sub and sub in owners:
        return True, ""                       # 負責人之一
    admins = {normalize_email(a) for a in (admin_emails or []) if a}
    if sub and sub in admins:
        return True, ""                       # 管理者豁免(config admin_emails)
    approver = normalize_email(getattr(profile, "approver", None)
                               if profile else None)
    if approver and sub == approver:
        return True, ""                       # 該票 profile 的審批者
    return False, "此 email 無權操作本票(需為負責人、管理者或審批者)。"
