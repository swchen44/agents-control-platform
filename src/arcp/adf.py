"""精簡 ADF(Atlassian Document Format)builder —— 零依賴,建 Jira comment 用。

Jira Cloud comment body 是 ADF(不是 markdown/html);貼 md/html 不會被渲染。這裡只提供
交付物 comment 需要的節點:doc / heading / paragraph / text(+strong)/ bulletList / link /
codeBlock。設計見 docs/design/agent-output.md §3。
"""

from __future__ import annotations


def text(s: str) -> dict:
    return {"type": "text", "text": str(s)}


def strong(s: str) -> dict:
    return {"type": "text", "text": str(s), "marks": [{"type": "strong"}]}


def link(s: str, url: str) -> dict:
    return {"type": "text", "text": str(s),
            "marks": [{"type": "link", "attrs": {"href": url}}]}


def paragraph(*inline) -> dict:
    """段落;inline 傳 text/strong/link 節點,或純字串(自動轉 text)。"""
    content = [text(x) if isinstance(x, str) else x for x in inline if x is not None]
    return {"type": "paragraph", "content": content or [text("")]}


def heading(s: str, level: int = 3) -> dict:
    return {"type": "heading", "attrs": {"level": max(1, min(6, level))},
            "content": [text(s)]}


def code_block(s: str, language: str = "") -> dict:
    node = {"type": "codeBlock", "content": [text(s)]}
    if language:
        node["attrs"] = {"language": language}
    return node


def bullet_list(items: list) -> dict:
    """items:每項是 inline 節點清單(或單一節點/字串)→ 各成一個 listItem/paragraph。"""
    li = []
    for it in items:
        inly = it if isinstance(it, list) else [it]
        inly = [text(x) if isinstance(x, str) else x for x in inly]
        li.append({"type": "listItem",
                   "content": [{"type": "paragraph", "content": inly}]})
    return {"type": "bulletList", "content": li}


def doc(*blocks) -> dict:
    """頂層 ADF 文件。blocks 為 heading/paragraph/bulletList/codeBlock 節點。"""
    return {"version": 1, "type": "doc",
            "content": [b for b in blocks if b is not None]}
