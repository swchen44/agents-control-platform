#!/usr/bin/env python3
"""L0–L3 trace 完整性自檢(C2 / v5 唯一 P1 硬目標)。

每個「有跑過 attempt」的票,確認四層證據齊全(docs/design/observability.md 的分層):
  L0 ticket/routing/人工事件  ← journal events.jsonl
  L1 attempt 狀態轉移/outcome ← ticket_session(DB) + journal attempt_finished
  L2 invocation envelope       ← <ws 上層>/attempts/aN.envelope.json
  L3 conversation 原生事件     ← <ws 上層>/attempts/aN.events.jsonl

判準:completed / error 的 attempt **必須**有 L2(合法 JSON、帶 completed|error 鍵)+
L3(非空);UNKNOWN(runner 死/無 envelope)**依設計可缺**,不算失敗(只記 info)。
缺任一該有的層 → 列出 + rc!=0(供 CI/審計)。

用法:uv run python scripts/trace_lint.py [runtime_dir]   (預設 arcp.paths.runtime_dir())
"""
from __future__ import annotations

import json
import os
import sys

from arcp.paths import runtime_dir
from arcp.store import Store


def _finished_raw(runtime: str) -> dict[int, dict[int, str]]:
    """讀 journal → {issue_id: {attempt: raw}}(raw=completed/error/unknown)。"""
    out: dict[int, dict[int, str]] = {}
    path = os.path.join(runtime, "events.jsonl")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if e.get("type") == "attempt_finished":
                    out.setdefault(e.get("issue_id"), {})[e.get("attempt")] = e.get("raw")
    return out


def lint(runtime: str) -> tuple[list[str], list[str], int]:
    """回 (findings=硬缺口, infos=可接受的缺, n_attempts)。"""
    findings: list[str] = []
    infos: list[str] = []
    n = 0
    store = Store(runtime)
    try:
        fin = _finished_raw(runtime)
        for s in store.all_sessions():
            if s.attempts <= 0 or not s.workspace or s.workspace == "(adopted)":
                continue                      # 沒真的派工(認養/排隊)→ 無 attempt 可檢
            adir = os.path.join(os.path.dirname(s.workspace), "attempts")
            raws = fin.get(s.issue_id, {})
            for a in range(1, s.attempts + 1):
                n += 1
                tag = f"{s.key}#a{a}"
                raw = raws.get(a)
                env = os.path.join(adir, f"a{a}.envelope.json")
                ev = os.path.join(adir, f"a{a}.events.jsonl")
                if raw is None:
                    findings.append(f"{tag}: L0/L1 缺 attempt_finished(journal 無此 attempt)")
                    continue
                if raw == "unknown":
                    infos.append(f"{tag}: UNKNOWN(envelope/events 可缺,依設計)")
                    continue
                # completed / error → 必須有 L2 + L3
                if not os.path.isfile(env):
                    findings.append(f"{tag}: L2 缺 envelope({env})")
                else:
                    try:
                        obj = json.load(open(env, encoding="utf-8"))
                        if "completed" not in obj and "error" not in obj:
                            findings.append(f"{tag}: L2 envelope 無 completed/error 鍵")
                    except (ValueError, OSError) as e:
                        findings.append(f"{tag}: L2 envelope 非合法 JSON({e})")
                if not os.path.isfile(ev):
                    findings.append(f"{tag}: L3 缺 events.jsonl({ev})")
                elif os.path.getsize(ev) == 0:
                    findings.append(f"{tag}: L3 events.jsonl 空")
    finally:
        store.close()
    return findings, infos, n


def main() -> int:
    rt = sys.argv[1] if len(sys.argv) > 1 else (runtime_dir() or "./runtime")
    if not os.path.isdir(rt):
        print(f"[trace-lint] runtime 不存在: {rt}(無資料可檢,視為通過)")
        return 0
    findings, infos, n = lint(rt)
    for i in infos:
        print("  ·", i)
    if findings:
        print(f"[trace-lint] ✗ {len(findings)} 個缺口 / {n} attempts:")
        for f in findings:
            print("  ✗", f)
        return 1
    print(f"[trace-lint] ✓ {n} attempts 四層齊全"
          + (f"({len(infos)} 個 UNKNOWN 依設計略過)" if infos else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
