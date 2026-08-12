"""M3:TICKET.md 安全掃描(prompt injection 防線)。設計:docs/design/security-scan.md。

TICKET.md 是 agent 開工第一份讀物,內容含外部輸入(Jira description、agent-job
script 回傳的 prompt、HIL 人類指示)——spawn 前用**外部靜態掃描器**掃一遍
(建議 cisco skill-scanner:pattern/YARA 靜態引擎,不用 LLM)。

策略(2026-08-12 定案):config `outer_loop.security_scan` **選配**——沒配=功能關
(現行行為零變);配了但掃描器執行失敗(未裝/timeout/壞輸出)= **fail-closed**
(當命中處理,表單標明「掃描器異常」非威脅)。命中 >= fail_on → dispatcher 擋派工
→ HIL security_review 表單交人裁決(可修描述;繼續=人審蓋章之後不再擋 / abort,
reason=security)。內容 hash 快取:同內容掃過且通過 → 不重掃。
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

from .logutil import get_logger

log = get_logger("secscan")

_SEV_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


@dataclass
class ScanOutcome:
    ok: bool                       # True=通過(無 >= fail_on 的命中且掃描成功)
    findings: list = field(default_factory=list)   # [{severity,rule_id,title,…}]
    error: str = ""                # 掃描器異常描述(fail-closed 時 ok=False)
    content_hash: str = ""


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _parse_findings(doc) -> list[dict]:
    """skill-scanner JSON(ScanResult.to_dict)→ 精簡 findings。寬鬆解析:
    只取表單/journal 需要的欄位,缺欄容錯。"""
    out = []
    for f in (doc or {}).get("findings") or []:
        out.append({
            "severity": str(f.get("severity") or "?").lower(),
            "rule_id": f.get("rule_id") or f.get("id") or "?",
            "title": f.get("title") or "",
            "description": (f.get("description") or "")[:300],
            "snippet": (f.get("snippet") or "")[:200],
        })
    return out


def scan_text(text: str, cfg: dict) -> ScanOutcome | None:
    """掃一段 markdown 文本(TICKET.md)。cfg 空 → None(功能關)。

    做法:文本存暫存目錄 SKILL.md → `<command> scan <dir> --lenient
    --format json --output out.json` → 解析 findings、依 fail_on 門檻判定。
    任何執行問題 → ok=False + error(fail-closed,呼叫端交人審)。"""
    if not cfg or not cfg.get("command"):
        return None
    h = content_hash(text)
    fail_on = _SEV_ORDER.get(str(cfg.get("fail_on") or "high").lower(), 3)
    tmp = tempfile.mkdtemp(prefix="arcp-secscan-")
    try:
        with open(os.path.join(tmp, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(text)
        out_json = os.path.join(tmp, "out.json")
        argv = [*shlex.split(cfg["command"]), "scan", tmp,
                "--lenient", "--format", "json", "--output", out_json]
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True,
                stdin=subprocess.DEVNULL,
                timeout=float(cfg.get("timeout_sec") or 180))
        except (subprocess.TimeoutExpired, OSError) as e:
            log.warning("secscan 執行失敗:%s", e)
            return ScanOutcome(ok=False, error=f"掃描器執行失敗:{e}",
                               content_hash=h)
        if not os.path.isfile(out_json):
            err = (proc.stderr or proc.stdout or "")[-300:]
            log.warning("secscan 無輸出(rc=%s):%s", proc.returncode, err)
            return ScanOutcome(ok=False, content_hash=h,
                               error=f"掃描器無輸出(rc={proc.returncode}):{err}")
        try:
            with open(out_json, encoding="utf-8") as f:
                findings = _parse_findings(json.load(f))
        except (OSError, ValueError) as e:
            return ScanOutcome(ok=False, content_hash=h,
                               error=f"掃描器輸出解析失敗:{e}")
        hits = [x for x in findings
                if _SEV_ORDER.get(x["severity"], 0) >= fail_on]
        return ScanOutcome(ok=not hits, findings=findings, content_hash=h)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
