"""G1 — agent↔harness 結構化契約(docs/design/lifecycle.md §4.2)。

Agent 每次結束回一個固定形狀的物件,由 CLI 的 structured-output 強制:
  claude  --json-schema '<inline json>'   → result 事件帶 structured_output
  codex   --output-schema <file>          → 最終 agent_message 為符合 schema 的 JSON

    { reason: str,                       # 為什麼是這個 status(人看的一句話)
      status: done|failed|need_human|handoff,
      next:  null | { to: str|null, kind: agent|human },     # 下一手(驅動 W2 F3)
      summary: str }                     # 100–200 字自報:完成/未完成(進 comment/表單)

完整交付物(Gerrit / 檔案 / 敘事)另走 workspace 的 OUTPUT.json(見 arcp/output.py 與
docs/design/agent-output.md);此契約只帶「控制訊號 + 精簡自報」。

harness 解析:reason→Jira comment;status→供參(**不覆寫 grader 的證據判定**,
G2 精神——grader 仍是終審);next→W1 只 journal/comment 記錄,實際換手 W2 F3。

依賴極簡:不引 jsonschema,只做形狀 + enum 輕驗(CLI 已強制 schema,這是雙保險)。
"""

from __future__ import annotations

STATUS_VALUES = ["done", "failed", "need_human", "handoff"]
NEXT_KIND = ["agent", "human"]

# 傳給 claude --json-schema / 寫檔給 codex --output-schema 的 JSON Schema。
# 形狀遵守 OpenAI strict structured-output 規則(codex 後端會 400 拒絕否則,
# W3.1 實測):每個 object(含巢狀)都要 additionalProperties:false,且
# required 列**全部** properties——「選填」以 nullable type 表達,不是缺 key。
# claude 對此超集合也接受(實測),雙引擎共用同一份。
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
            "required": ["to", "kind"],
            "additionalProperties": False,
        },
        # 100–200 字精簡自報:完成/未完成 item。完整交付物走 OUTPUT.json。
        "summary": {"type": "string"},
    },
    "required": ["reason", "status", "next", "summary"],
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
    """Human summary for a Jira comment / 表單。summary 為 agent 100–200 字自報,
    有就附在後面(缺 summary 不算不合契約——它是資訊非控制訊號)。"""
    ok, why = validate_structured(obj)
    if not ok:
        return f"(結構化輸出不合契約:{why})"
    nx = obj.get("next")
    tail = ""
    if isinstance(nx, dict):
        tail = f";next→{nx.get('kind')}:{nx.get('to') or '-'}"
    body = f"status={obj['status']}{tail}\nreason:{obj['reason']}"
    s = obj.get("summary")
    if isinstance(s, str) and s.strip():
        body += f"\nsummary:{s.strip()}"
    return body
