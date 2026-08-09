"""W11 互動服務核心邏輯:受控表單 schema + 一次性 token + 提交驗證(純邏輯,零副作用)。

取代「人直接編 Jira description free-text」的人機介面(見 docs/design/interaction.md)。
本模組只負責:版本化表單 schema、token 產生/綁定、Interaction Request 資料模型與狀態、
提交欄位驗證、回填摘要。**不碰 Jira / HTTP / DB**——HTTP 表單服務、poller 觸發、回寫、
持久化都在後續增量,依賴本模組的純函式。
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

SCHEMA_VERSION = 1
TOKEN_BYTES = 32          # token_urlsafe(32) ≈ 256 bit(遠超 ≥128 底線)

# Interaction Request 狀態
PENDING = "pending"
SUBMITTED = "submitted"
EXPIRED = "expired"
INVALIDATED = "invalidated"

# W10.3 a2a handoff 共用欄位:decision(HIL Middle)與 score_and_close(End)都內嵌。
# 這些欄位「選 handoff 才有意義」——schema 一律非必填,實際校驗在 hil._do_handoff
#(kind/profile 不完整則 fail-safe 降級續跑)。next_profile 下拉吃 payload["profiles"]
#(載入的全部 profile 名,由 ScoreGate 產表時注入),讓人選下一棒。
_HANDOFF_FIELDS: list[dict] = [
    {"key": "handoff_kind", "label": "handoff 種類(選 handoff 才填)", "type": "select",
     "required": False, "options": ["next", "base"]},
    {"key": "next_profile", "label": "下一棒 agent profile(選 handoff 才填)",
     "type": "select", "required": False, "options_from": "profiles"},
    {"key": "handoff_prompt", "label": "給下一棒的交接指示(選 handoff 才填)",
     "type": "textarea", "required": False},
]

# 表單型別(對應 HIL):need_info/decision=HIL(Middle);score_and_close=HIL(End)。
# select 的 options 可 per-request(options_from 指向 request.payload 的 key)。
FORM_SCHEMAS: dict[str, dict] = {
    "need_info": {
        "version": SCHEMA_VERSION, "title": "補充資訊", "hil": "middle",
        "fields": [
            {"key": "answer", "label": "請補充 agent 需要的資訊",
             "type": "textarea", "required": True},
            # Q10:給 agent 的補充指示 → 寫進 workspace 的人類指示段(data path)
            {"key": "human_prompt", "label": "給 agent 的補充指示(選填,寫進 TICKET.md)",
             "type": "textarea", "required": False},
        ],
    },
    "decision": {
        "version": SCHEMA_VERSION, "title": "決策 / 核可", "hil": "middle",
        "fields": [
            {"key": "choice", "label": "請擇一", "type": "select",
             "required": True, "options_from": "options"},
            # W10.3:HIL(Middle) 也能改派下一棒。proceed=照常續跑本 agent;
            # handoff=改交接(再依 handoff_kind 走同票 next / 跨票 base)。
            {"key": "next_step", "label": "續跑或改派", "type": "select",
             "required": False, "options": ["proceed", "handoff"]},
            *_HANDOFF_FIELDS,
            {"key": "note", "label": "備註(選填)", "type": "textarea",
             "required": False},
            {"key": "human_prompt", "label": "給 agent 的補充指示(選填,寫進 TICKET.md)",
             "type": "textarea", "required": False},
        ],
    },
    # Q11:人類強制中斷(@agent hold)→ 開此表單給新指示 → submit 後 resume 排隊。
    "hold": {
        "version": SCHEMA_VERSION, "title": "強制中斷:給 agent 新指示", "hil": "middle",
        "fields": [
            {"key": "human_prompt", "label": "給 agent 的補充指示(寫進 TICKET.md)",
             "type": "textarea", "required": True},
        ],
    },
    "score_and_close": {
        "version": SCHEMA_VERSION, "title": "評分與結案裁決", "hil": "end",
        # context(唯讀顯示,非欄位):grader outcome、agent 自評 —— 放 payload
        "fields": [
            {"key": "human_score", "label": "人類完成度評分(0–10)",
             "type": "int", "required": True, "min": 0, "max": 10},
            {"key": "close_decision", "label": "下一步", "type": "select",
             "required": True, "options": ["close", "continue", "handoff"]},
            *_HANDOFF_FIELDS,      # W10.3:選 handoff 時填(kind/下一 profile/prompt)
            {"key": "note", "label": "備註(選填)", "type": "textarea",
             "required": False},
        ],
    },
}


def gen_token() -> str:
    """一次性、不可預測 token(capability URL 用)。"""
    return secrets.token_urlsafe(TOKEN_BYTES)


def gen_request_id() -> str:
    return "req-" + secrets.token_hex(8)


@dataclass
class InteractionRequest:
    """一次互動請求:綁單一 ticket + 單一 Request ID + 單一 schema。"""
    request_id: str
    issue_id: int
    key: str
    schema_id: str
    schema_version: int
    token: str
    created_at: float
    expires_at: float = 0.0        # 0 = 未設(一般綁票生命週期 / 短窗)
    status: str = PENDING
    # context(唯讀顯示):question / options / grader / agent_score
    payload: dict = field(default_factory=dict)
    submission: dict | None = None
    submitted_at: float = 0.0
    submitted_by: str = ""
    reminders: int = 0
    reminded_at: float = 0.0

    def is_expired(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return bool(self.expires_at) and now >= self.expires_at

    def is_open(self, now: float | None = None) -> bool:
        """可開表單填寫作答(pending 且未逾期)。"""
        return self.status == PENDING and not self.is_expired(now)


def build_request(issue_id: int, key: str, schema_id: str,
                  payload: dict | None = None, ttl_sec: float = 0.0,
                  now: float | None = None) -> InteractionRequest:
    """產生一筆新請求(含 token)。ttl_sec>0 才設有效期。"""
    if schema_id not in FORM_SCHEMAS:
        raise ValueError(f"unknown schema_id: {schema_id}")
    now = time.time() if now is None else now
    return InteractionRequest(
        request_id=gen_request_id(), issue_id=int(issue_id), key=key,
        schema_id=schema_id, schema_version=FORM_SCHEMAS[schema_id]["version"],
        token=gen_token(), created_at=now,
        expires_at=(now + ttl_sec) if ttl_sec else 0.0,
        payload=payload or {})


def _options_for(field_def: dict, req: InteractionRequest | None):
    """select 的合法選項:schema 內建 options,或 per-request(options_from)。"""
    if "options" in field_def:
        return list(field_def["options"])
    src = field_def.get("options_from")
    if src and req is not None:
        return list(req.payload.get(src) or [])
    return None


def validate_submission(schema_id: str, data: dict | None,
                        req: InteractionRequest | None = None
                        ) -> tuple[bool, list[str], dict]:
    """後端驗證(前端另驗一次):→ (ok, errors, cleaned)。
    檢查必填 / 型別(int)/ 值域(min-max)/ select 成員。"""
    schema = FORM_SCHEMAS.get(schema_id)
    if schema is None:
        return False, [f"unknown schema_id: {schema_id}"], {}
    errors: list[str] = []
    cleaned: dict = {}
    data = data or {}
    for f in schema["fields"]:
        k, typ = f["key"], f["type"]
        raw = data.get(k)
        empty = raw is None or (isinstance(raw, str) and not raw.strip())
        if empty:
            if f.get("required"):
                errors.append(f"{f['label']}:必填")
            continue
        if typ == "int":
            try:
                v = int(str(raw).strip())
            except (ValueError, TypeError):
                errors.append(f"{f['label']}:需整數")
                continue
            lo, hi = f.get("min"), f.get("max")
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                errors.append(f"{f['label']}:需在 {lo}–{hi}")
                continue
            cleaned[k] = v
        elif typ == "select":
            opts = _options_for(f, req)
            sval = str(raw).strip()
            if opts is not None and sval not in opts:
                errors.append(f"{f['label']}:非合法選項")
                continue
            cleaned[k] = sval
        else:                                  # text / textarea
            cleaned[k] = str(raw).strip()
    return (not errors), errors, cleaned


def summarize(schema_id: str, data: dict | None) -> str:
    """回填摘要(給稽核 comment / 「已提交」唯讀顯示)。"""
    schema = FORM_SCHEMAS.get(schema_id) or {"fields": []}
    data = data or {}
    parts = [f"{f['label']}={data[f['key']]}"
             for f in schema["fields"] if f["key"] in data]
    return "; ".join(parts)
