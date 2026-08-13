"""W11 互動服務核心邏輯:受控表單 schema + 一次性 token + 提交驗證(純邏輯,零副作用)。

取代「人直接編 Jira description free-text」的人機介面(見 docs/design/interaction.md)。
本模組只負責:版本化表單 schema、token 產生/綁定、Interaction Request 資料模型與狀態、
提交欄位驗證、回填摘要。**不碰 Jira / HTTP / DB**——HTTP 表單服務、poller 觸發、回寫、
持久化都在後續增量,依賴本模組的純函式。
"""

from __future__ import annotations

import re
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
    {"key": "handoff_kind", "label": "換手種類(選換手才填)", "type": "select",
     "required": False,
     # value 穩定(next/base)、label 顯示中文:同票=同一張票、跨票=系統另開新票
     "options": [["next", "同票換手(同一張票,換 profile)"],
                 ["base", "跨票換手(系統另開新票,帶脈絡)"]]},
    {"key": "next_profile", "label": "下一棒 agent profile(選換手才填)",
     "type": "select", "required": False, "options_from": "profiles"},
    {"key": "handoff_prompt", "label": "給下一棒的交接指示(選換手才填)",
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
            # handoff=改換手(再依 handoff_kind 走同票 / 跨票)。
            {"key": "next_step", "label": "續跑或改派", "type": "select",
             "required": False,
             "options": [["proceed", "照常續跑本 agent"],
                         ["handoff", "換手改派下一棒"]]},
            *_HANDOFF_FIELDS,
            {"key": "note", "label": "備註(選填)", "type": "textarea",
             "required": False},
            {"key": "human_prompt", "label": "給 agent 的補充指示(選填,寫進 TICKET.md)",
             "type": "textarea", "required": False},
        ],
    },
    # Q11:人類強制中斷(指令台 hold)→ 開此表單給新指示 → submit 後 resume 排隊。
    "hold": {
        "version": SCHEMA_VERSION, "title": "強制中斷:給 agent 新指示", "hil": "middle",
        "fields": [
            {"key": "human_prompt", "label": "給 agent 的補充指示(寫進 TICKET.md)",
             "type": "textarea", "required": True},
        ],
    },
    # W2.3 表單化(2026-08-13):起點審批走一次性表單,取代「人編 description
    # human 段 + assignee 交回」雙信號——**表單提交即放行**(hil.apply_submission
    # 驗過 → 清 pending + assignee 收回機器人)。格式錯誤表單端就地擋
    # (pattern),不再有 Jira 往返的 reprompt/escalate 迴圈。
    "approval": {
        "version": SCHEMA_VERSION, "title": "起點審批:放行這張票",
        "hil": "middle",
        "fields": [
            {"key": "agent_name", "label": "agent_name(執行名義,snake_case)",
             "type": "text", "required": True,
             "pattern": r"[a-z][a-z0-9_]*", "pattern_hint": "snake_case"},
            {"key": "human_email",
             "label": "接手人 email(選填,審批紀錄)",
             "type": "text", "required": False},
            {"key": "param", "label": "param(選填,附加參數,入審批紀錄)",
             "type": "textarea", "required": False},
        ],
    },
    # M3:TICKET.md 安全掃描命中 → 交人裁決。payload(唯讀)帶 findings(命中
    # 規則/嚴重度/片段)+ ticket_md(被掃內容)+ scan_error(掃描器異常時)。
    "security_review": {
        "version": SCHEMA_VERSION, "title": "安全審:掃描命中,請裁決",
        "hil": "middle",
        "fields": [
            {"key": "revised_text",
             "label": "修訂後的任務描述(選填:可修掉可疑內容;留空=照原文放行)",
             "type": "textarea", "required": False},
            {"key": "decision", "label": "裁決", "type": "select",
             "required": True,
             "options": [["continue", "繼續(人審放行;之後此票不再擋)"],
                         ["abort", "中止(ABORTED,理由=Security)"]]},
        ],
    },
    # budget:單票 soft 上限破 → 使用者自助調高(≤hard)。context(唯讀)放 payload:
    # 已用 token/usd、soft/hard、summary 快照、transcript/jira 連結。上限校驗(≤hard)
    # 在 hil.apply_submission(clamp 到 payload 帶的 hard)。
    "budget_increase": {
        "version": SCHEMA_VERSION, "title": "提高本票 token / 花費上限",
        "hil": "middle",
        "fields": [
            {"key": "new_soft_usd", "label": "新的本票 USD 上限(選填,≤ hard)",
             "type": "number", "required": False, "min": 0},
            {"key": "new_soft_tokens", "label": "新的本票 token 上限(選填,≤ hard)",
             "type": "int", "required": False, "min": 0},
            {"key": "note", "label": "備註(選填)", "type": "textarea",
             "required": False},
        ],
    },
    "score_and_close": {
        "version": SCHEMA_VERSION, "title": "評分與結案裁決", "hil": "end",
        # context(唯讀顯示,非欄位):grader outcome、agent 自評 —— 放 payload
        "fields": [
            {"key": "human_score", "label": "人類完成度評分(0–10)",
             "type": "int", "required": True, "min": 0, "max": 10},
            {"key": "close_decision", "label": "下一步", "type": "select",
             "required": True,
             "options": [["close", "關單(結案)"],
                         ["continue", "續跑(重置額度回進行中)"],
                         ["handoff", "換手改派下一棒"]]},
            *_HANDOFF_FIELDS,      # W10.3:選 handoff 時填(kind/下一 profile/prompt)
            # T12 修(2026-08-13):打回(continue)缺指示通道——之前提交的
            # human_prompt 不在 schema 被驗證層丟掉,agent 不知道要改什麼
            {"key": "human_prompt", "label": "打回/續跑時給 agent 的指示"
             "(選填;寫進 TICKET.md 並隨 resume 帶上)",
             "type": "textarea", "required": False},
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
    """一次互動請求:綁單一 ticket + 單一 Request ID + 單一 schema。

    kind:
      "hil"     單次:系統需要人回答(審批/補資訊/評分),submit 即失效。
      "command" 常駐:人主動下指令的表單 console,綁票、可重複用、close 才失效
                (submit 不翻 SUBMITTED)。見 docs/design/interaction.md。
    """
    request_id: str
    issue_id: int
    key: str
    schema_id: str
    schema_version: int
    token: str
    created_at: float
    kind: str = "hil"
    expires_at: float = 0.0        # 0 = 未設(一般綁票生命週期 / 短窗)
    status: str = PENDING
    # context(唯讀顯示):question / options / grader / agent_score
    payload: dict = field(default_factory=dict)
    submission: dict | None = None
    submitted_at: float = 0.0
    submitted_by: str = ""
    submitted_ip: str = ""            # K:提交來源 IP(稽核追查)
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
                  now: float | None = None,
                  kind: str = "hil") -> InteractionRequest:
    """產生一筆新請求(含 token)。ttl_sec>0 才設有效期。kind=command 為常駐指令表單
    (schema_id 可用哨值 'command',不在 FORM_SCHEMAS 內故略過 schema 檢查)。"""
    if kind != "command" and schema_id not in FORM_SCHEMAS:
        raise ValueError(f"unknown schema_id: {schema_id}")
    now = time.time() if now is None else now
    version = FORM_SCHEMAS[schema_id]["version"] if schema_id in FORM_SCHEMAS \
        else SCHEMA_VERSION
    return InteractionRequest(
        request_id=gen_request_id(), issue_id=int(issue_id), key=key,
        schema_id=schema_id, schema_version=version,
        token=gen_token(), created_at=now, kind=kind,
        expires_at=(now + ttl_sec) if ttl_sec else 0.0,
        payload=payload or {})


def opt_pairs(raw) -> list[tuple[str, str]]:
    """正規化 select options → [(value, label)]。接受純字串清單(value=label)或
    [value, label] 配對清單(顯示中文 label、送出穩定 value,如 next→同票換手)。"""
    out: list[tuple[str, str]] = []
    for o in raw or []:
        if isinstance(o, (list, tuple)) and len(o) >= 2:
            out.append((str(o[0]), str(o[1])))
        else:
            out.append((str(o), str(o)))
    return out


def _raw_options(field_def: dict, req: InteractionRequest | None):
    """取此 select 的原始 options(schema 內建 或 per-request options_from)。"""
    if "options" in field_def:
        return field_def["options"]
    src = field_def.get("options_from")
    if src and req is not None:
        return req.payload.get(src) or []
    return None


def _options_for(field_def: dict, req: InteractionRequest | None):
    """select 的合法「值」集合(驗證用);配對清單只取 value。None=無限制。"""
    raw = _raw_options(field_def, req)
    if raw is None:
        return None
    return [v for v, _ in opt_pairs(raw)]


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
        if typ in ("int", "number"):
            try:
                v = int(str(raw).strip()) if typ == "int" \
                    else float(str(raw).strip())
            except (ValueError, TypeError):
                errors.append(f"{f['label']}:需{'整數' if typ == 'int' else '數字'}")
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
            sval = str(raw).strip()
            pat = f.get("pattern")             # W2.3 表單化:格式就地擋(如
            if pat and not re.fullmatch(pat, sval):   # snake_case),不走 Jira 往返
                errors.append(f"{f['label']}:格式不符"
                              + (f"({f['pattern_hint']})"
                                 if f.get("pattern_hint") else ""))
                continue
            cleaned[k] = sval
    return (not errors), errors, cleaned


def summarize(schema_id: str, data: dict | None) -> str:
    """回填摘要(給稽核 comment / 「已提交」唯讀顯示)。select 值顯示中文 label。"""
    schema = FORM_SCHEMAS.get(schema_id) or {"fields": []}
    data = data or {}
    parts = []
    for f in schema["fields"]:
        if f["key"] not in data:
            continue
        val = data[f["key"]]
        if f.get("type") == "select" and "options" in f:   # value → 中文 label
            lbl = dict(opt_pairs(f["options"])).get(str(val))
            val = f"{lbl}({val})" if lbl and lbl != str(val) else val
        parts.append(f"{f['label']}={val}")
    return "; ".join(parts)
