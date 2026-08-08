#!/usr/bin/env python3
"""掃 code 裡的 `*.journal("event", issue_id, key, **fields)` 呼叫,列出所有 journal
事件名與其欄位 —— 供 docs/design/observability.md 的「事件字典」自動部分(防漂移)。

用法:
    python3 scripts/gen_event_dict.py            # 印 Markdown 表格到 stdout
    python3 scripts/gen_event_dict.py --check     # 與 observability.md 內嵌區塊比對,
                                                  # 不一致回非 0(給 CI/pre-commit 用)

事件語意(何時發、正常 vs 異常、該連看哪個證據)是**手寫**在 observability.md;本工具
只保證「有哪些事件 + 欄位」這份清單永遠對得上 code。設計背景見 BACKLOG 主題 H。
"""
from __future__ import annotations

import argparse
import ast
import os
import sys

from arcp.paths import repo_root

# 掃描範圍:產生事件的正式來源(套件 + 入口腳本);測試/demo 不算。
SCAN_DIRS = ("src/arcp", "scripts")
MARK_BEGIN = "<!-- BEGIN gen_event_dict -->"
MARK_END = "<!-- END gen_event_dict -->"


def _iter_py(root: str):
    for base in SCAN_DIRS:
        d = os.path.join(root, base)
        for dirpath, _, files in os.walk(d):
            for fn in files:
                if fn.endswith(".py") and fn != "gen_event_dict.py":
                    yield os.path.join(dirpath, fn)


def collect(root: str) -> dict[str, dict]:
    """event 名 → {fields:set[str], sites:set[str]}。只收第一參數是字串字面量的呼叫。"""
    events: dict[str, dict] = {}
    for path in _iter_py(root):
        try:
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        except SyntaxError:
            continue
        rel = os.path.relpath(path, root)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "journal"):
                continue
            if not (node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                continue
            name = node.args[0].value
            e = events.setdefault(name, {"fields": set(), "sites": set()})
            for kw in node.keywords:
                if kw.arg:                       # 略過 **kwargs 展開
                    e["fields"].add(kw.arg)
            e["sites"].add(f"{rel}:{node.lineno}")
    return events


def render(events: dict[str, dict]) -> str:
    lines = ["| 事件 | 欄位(kwargs) | 產生點 |", "|---|---|---|"]
    for name in sorted(events):
        e = events[name]
        fields = ", ".join(f"`{f}`" for f in sorted(e["fields"])) or "—"
        sites = ", ".join(f"`{s}`" for s in sorted(e["sites"]))
        lines.append(f"| `{name}` | {fields} | {sites} |")
    lines.append(f"\n> 共 {len(events)} 種事件。本表由 "
                 "`scripts/gen_event_dict.py` 掃 code 產生,勿手改。")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="與 observability.md 內嵌區塊比對,不一致回非 0")
    args = ap.parse_args()
    root = repo_root() or "."
    table = render(collect(root))

    if not args.check:
        print(table)
        return 0

    doc = os.path.join(root, "docs", "design", "observability.md")
    try:
        text = open(doc, encoding="utf-8").read()
    except FileNotFoundError:
        print(f"[check] 找不到 {doc}", file=sys.stderr)
        return 2
    if MARK_BEGIN not in text or MARK_END not in text:
        print(f"[check] {doc} 缺 {MARK_BEGIN} / {MARK_END} 標記", file=sys.stderr)
        return 2
    embedded = text.split(MARK_BEGIN, 1)[1].split(MARK_END, 1)[0].strip()
    if embedded == table.strip():
        print("[check] 事件字典與 code 一致 ✓")
        return 0
    print("[check] 事件字典與 code 不一致 —— 請重跑 gen_event_dict.py 更新內嵌區塊",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
