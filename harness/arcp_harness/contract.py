"""G1 — agent↔harness 結構化契約(DESIGN_lifecycle §4.2)。

Agent 每次結束回一個固定形狀的物件,由 CLI 的 structured-output 強制:
  claude  --json-schema '<inline json>'   → result 事件帶 structured_output
  codex   --output-schema <file>          → 最終 agent_message 為符合 schema 的 JSON

    { reason: str,                       # 為什麼是這個 status(人看的一句話)
      status: done|failed|need_human|handoff,
      next:  null | { to: str|null, kind: agent|human } }   # 下一手(驅動 W2 F3)

harness 解析:reason→Jira comment;status→供參(**不覆寫 grader 的證據判定**,
G2 精神——grader 仍是終審);next→W1 只 journal/comment 記錄,實際換手 W2 F3。

依賴極簡:不引 jsonschema,只做形狀 + enum 輕驗(CLI 已強制 schema,這是雙保險)。
"""

from __future__ import annotations

STATUS_VALUES = ["done", "failed", "need_human", "handoff"]
NEXT_KIND = ["agent", "human"]

# 傳給 claude --json-schema / 寫檔給 codex --output-schema 的 JSON Schema。
CONTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "status": {"type": "string", "enum": STATUS_VALUES},
        "next": {
            "type": ["object", "null"],
            "properties": {
                "to": {"type": ["string", "null"]},
                "kind": {"type": "string", "enum": NEXT_KIND},
            },
        },
    },
    "required": ["reason", "status"],
    "additionalProperties": False,
}


def validate_structured(obj) -> tuple[bool, str]:
    """(ok, reason). Light shape + enum check — CLI already enforces schema."""
    if not isinstance(obj, dict):
        return False, "not an object"
    if not isinstance(obj.get("reason"), str) or not obj["reason"].strip():
        return False, "reason missing / not a non-empty string"
    if obj.get("status") not in STATUS_VALUES:
        return False, f"status not in {STATUS_VALUES}"
    nx = obj.get("next")
    if nx is not None:
        if not isinstance(nx, dict):
            return False, "next must be null or object"
        if nx.get("kind") not in NEXT_KIND:
            return False, f"next.kind not in {NEXT_KIND}"
    return True, "ok"


def summarize(obj) -> str:
    """One-line human summary for a Jira comment."""
    ok, why = validate_structured(obj)
    if not ok:
        return f"(結構化輸出不合契約:{why})"
    nx = obj.get("next")
    tail = ""
    if isinstance(nx, dict):
        tail = f";next→{nx.get('kind')}:{nx.get('to') or '-'}"
    return f"status={obj['status']}{tail}\nreason:{obj['reason']}"
