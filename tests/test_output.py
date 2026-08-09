#!/usr/bin/env python3
"""Agent 交付物 OUTPUT.json 讀取 + 附件解析(arcp/output.py)。確定性、免網。

守住:四類解析、缺/壞檔降級 None、附件路徑穿越防護、大小分類(<6MB 附 / ≥6MB 下載頁)。
設計見 docs/design/agent-output.md。"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arcp.output import (  # noqa: E402
    ATTACH_TOTAL_LIMIT,
    attach_mode,
    load_output,
    resolve_attachments,
)

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


def _ws(output=None, files=None):
    d = tempfile.mkdtemp()
    if output is not None:
        with open(os.path.join(d, "OUTPUT.json"), "w", encoding="utf-8") as f:
            json.dump(output, f)
    for rel, size in (files or {}).items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p) or d, exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"x" * size)
    return d


# ── load_output ──────────────────────────────────────────────────────── #
o = load_output(_ws({
    "summary_md": "# 成果\n做完了",
    "code": [{"system": "gerrit", "url": "http://g/+/1", "ref": "r", "note": "n"}],
    "attachments": ["a.md"],
    "references": [{"label": "L", "path_or_url": "/x", "note": "n"}]}))
check("load:四類解析", o is not None and o.summary_md.startswith("# 成果")
      and len(o.code) == 1 and o.attachments == ["a.md"] and len(o.references) == 1)
check("load:缺檔 → None", load_output(_ws()) is None)
check("load:哨值 workspace → None", load_output("(handoff)") is None)
bad = tempfile.mkdtemp()
open(os.path.join(bad, "OUTPUT.json"), "w").write("{not json")
check("load:壞 JSON → None(降級)", load_output(bad) is None)
notobj = tempfile.mkdtemp()
open(os.path.join(notobj, "OUTPUT.json"), "w").write("[1,2]")
check("load:非物件 → None", load_output(notobj) is None)
# 髒欄位型別:非 list / 非 dict 元素被濾掉,不炸
o2 = load_output(_ws({"summary_md": 123, "code": "x",
                      "attachments": ["ok", 5, ""], "references": [1, {"a": 1}]}))
check("load:髒型別容錯", o2 is not None and o2.summary_md == ""
      and o2.code == [] and o2.attachments == ["ok"] and o2.references == [{"a": 1}])

# ── resolve_attachments:路徑安全 + 大小 ──────────────────────────────── #
ws = _ws({"attachments": ["a.md", "sub/b.png", "missing.txt", "../evil.sh"]},
         files={"a.md": 100, "sub/b.png": 200})
open(os.path.join(os.path.dirname(ws), "evil.sh"), "w").write("pwn")  # workspace 外
o = load_output(ws)
atts, total, skipped = resolve_attachments(ws, o)
check("resolve:只收存在且在 workspace 內的檔",
      {a.name for a in atts} == {"a.md", "b.png"})
check("resolve:總大小正確", total == 300)
check("resolve:缺檔 + 越界(../evil.sh)被跳過",
      "missing.txt" in skipped and "../evil.sh" in skipped)
check("resolve:相對路徑保留(下載頁用)",
      any(a.rel == "sub/b.png" for a in atts))

# ── attach_mode:6MB 門檻 ─────────────────────────────────────────────── #
check("mode:無檔 → none", attach_mode(0, 0) == "none")
check("mode:<6MB → attach", attach_mode(ATTACH_TOTAL_LIMIT - 1, 2) == "attach")
check("mode:≥6MB → link(下載頁)", attach_mode(ATTACH_TOTAL_LIMIT, 1) == "link")

print(f"test-output: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
