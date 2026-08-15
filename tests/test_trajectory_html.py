#!/usr/bin/env python3
"""VIZ — trajectory.html 產生器(抄 DeepSeek Trajectory 排版)。免網。
情境:collect 泳道/時長推導、category fallback(舊檔 emoji)、多 attempt
turn、render 自足單檔、無事件回 None、</script> 注入防護。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
from arcp.trajectory_html import collect, render_trajectory  # noqa: E402

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}  {detail}")


def ev(ts, source, text, category=None):
    e = {"kind": "MessageEvent", "timestamp": f"2026-08-15T10:00:{ts:06.3f}",
         "source": source,
         "llm_message": {"role": "assistant" if source == "agent" else "user",
                         "content": [{"type": "text", "text": text}]}}
    if category:
        e["category"] = category
    return e


root = tempfile.mkdtemp(prefix="arcp-test-tj-")
att = os.path.join(root, "attempts")
os.makedirs(att)
# a1:新式(帶 category);a2:舊式(emoji fallback)
with open(os.path.join(att, "a1.events.jsonl"), "w") as f:
    for e in [ev(1, "user", "做任務", "user"),
              ev(3, "agent", "💭 想一下", "thinking"),
              ev(4, "agent", "🔧 Write ARTICLE.md", "tool"),
              ev(6, "agent", "好了", "text")]:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
with open(os.path.join(att, "a2.events.jsonl"), "w") as f:
    for e in [ev(10, "user", "再修一下"),
              ev(12, "agent", "🔧 Edit ARTICLE.md"),        # 無 category→emoji
              ev(13, "agent", "📋 ok"),
              ev(14, "agent", "完成 </script> 測試")]:      # 注入樣本
        f.write(json.dumps(e, ensure_ascii=False) + "\n")

rs = collect(att)
check("collect:8 事件、兩 attempt", len(rs) == 8
      and {r["attempt"] for r in rs} == {1, 2})
check("泳道:user=0 / text·thinking=1 / tool·result=2",
      rs[0]["lane"] == 0 and rs[1]["lane"] == 1 and rs[2]["lane"] == 2)
check("category fallback:舊檔 🔧→tool、📋→tool_result",
      rs[5]["cat"] == "tool" and rs[6]["cat"] == "tool_result")
check("時長=到下一事件;末事件=最小寬(不捏造)",
      abs((rs[0]["end"] - rs[0]["start"]) - 2.0) < 0.01
      and abs((rs[7]["end"] - rs[7]["start"]) - 0.35) < 0.01)

out = os.path.join(root, "transcript", "trajectory.html")
p = render_trajectory(att, out, title="T-1")
doc = open(out, encoding="utf-8").read()
check("render:自足單檔(無外部 src/href 資源)",
      p == out and "http" not in doc.split("<script>")[0].split("hint")[0]
      and "<script>" in doc)
check("資料嵌入+標題", '"records"' in doc and "T-1 · trajectory" in doc)
check("</script> 注入防護(資料內轉義)", "完成 <\\/script> 測試" in doc)
check("三件套結構:Overview 泳道標籤/ledger/details 頁籤",
      all(k in doc for k in ("ovLabels", "id=\"rows\"", "id=\"dtabs\"",
                             "Timing", "sequence")))
check("token 配色(明暗 alias)", "--tj-user" in doc
      and "prefers-color-scheme: dark" in doc)
check("無事件 → None(不產空檔)",
      render_trajectory(os.path.join(root, "nothing"),
                        os.path.join(root, "x.html")) is None)

shutil.rmtree(root, ignore_errors=True)
print(f"test-trajectory-html: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
