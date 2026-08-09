#!/usr/bin/env python3
"""grader 單元測:FileChecklist / Command / Json(C1)/ AllOf。確定性、免網。

C1(2026-08-09):新增 JsonGrader —— JSON 檔存在 + 可解析 + 必要鍵(點號路徑)+
選填型別。build/test/lint 已能用 CommandGrader 表達;JSON 形狀是先前缺的一塊。"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arcp.grader import (  # noqa: E402
    AllOf,
    CommandGrader,
    FileChecklistGrader,
    JsonGrader,
)

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


def _ws(files: dict) -> str:
    d = tempfile.mkdtemp()
    for rel, content in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p) or d, exist_ok=True)
        with open(p, "w") as f:
            f.write(content)
    return d


# ── FileChecklistGrader ──────────────────────────────────────────────── #
ws = _ws({"out.txt": "hello world\n"})
check("files:存在 + 內容相符 → pass",
      FileChecklistGrader({"out.txt": "hello world"}).grade(ws).passed)
check("files:缺檔 → fail",
      not FileChecklistGrader({"nope.txt": None}).grade(ws).passed)
check("files:內容不符 → fail",
      not FileChecklistGrader({"out.txt": "bye"}).grade(ws).passed)

# ── CommandGrader ────────────────────────────────────────────────────── #
check("command:rc=0 → pass", CommandGrader(["true"]).grade(ws).passed)
check("command:rc≠0 → fail", not CommandGrader(["false"]).grade(ws).passed)

# ── JsonGrader(C1)───────────────────────────────────────────────────── #
wsj = _ws({"r.json": json.dumps(
    {"status": "ok", "score": 8, "meta": {"engine": "claude"}, "flag": True})})
check("json:必要鍵齊(含點號路徑)+ 型別對 → pass",
      JsonGrader("r.json", require=["status", "meta.engine"],
                 types={"status": "str", "score": "int",
                        "meta.engine": "str"}).grade(wsj).passed)
check("json:缺鍵(點號路徑)→ fail",
      not JsonGrader("r.json", require=["meta.missing"]).grade(wsj).passed)
check("json:型別不符 → fail",
      not JsonGrader("r.json", types={"score": "str"}).grade(wsj).passed)
check("json:bool 不算數字(int)→ fail",
      not JsonGrader("r.json", types={"flag": "int"}).grade(wsj).passed)
check("json:number 接受 int → pass",
      JsonGrader("r.json", types={"score": "number"}).grade(wsj).passed)
check("json:缺檔 → fail", not JsonGrader("nope.json").grade(wsj).passed)
bad = _ws({"bad.json": "{not json"})
check("json:非合法 JSON → fail", not JsonGrader("bad.json").grade(bad).passed)

# ── AllOf(組合)──────────────────────────────────────────────────────── #
check("all-of:全過 → pass",
      AllOf(FileChecklistGrader({"r.json": None}),
            JsonGrader("r.json", require=["status"])).grade(wsj).passed)
check("all-of:一個失敗 → fail",
      not AllOf(JsonGrader("r.json", require=["status"]),
                CommandGrader(["false"])).grade(wsj).passed)
v = AllOf(JsonGrader("r.json", require=["nope"])).grade(wsj)
check("all-of:reasons 帶子 grader 標籤([json])",
      any("[json]" in r for r in v.reasons))

print(f"test-grader: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
