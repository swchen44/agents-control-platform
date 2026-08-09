"""Agent 交付物(OUTPUT.json)—— data path 完整產出(設計:docs/design/agent-output.md)。

Agent 在 workspace 根寫 `OUTPUT.json`(格式由 inject_claude_md_end.md 指示);harness 於
終態讀取,用來貼 Jira comment + 附件 + HIL 表單頁駕駛艙。四類分明:
  summary_md   完整 markdown 敘事(表單頁渲染成 HTML)
  code[]       程式碼變更(Gerrit):{system,url,ref,note}
  attachments  要交到人手上的檔(workspace 相對路徑);<6MB 附 Jira、否則下載頁
  references[] 只給指標、不上傳:{label,path_or_url,note}

純邏輯 + 檔案讀取,零副作用、不碰 Jira/HTTP。降級不擲例外(交付物是加值)。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .logutil import get_logger

log = get_logger("output")

OUTPUT_FILE = "OUTPUT.json"
ATTACH_TOTAL_LIMIT = 6 * 1024 * 1024        # 附件總和 <此 → 附 Jira;≥此 → 下載頁


@dataclass
class Attachment:
    name: str            # 顯示名(basename)
    rel: str             # workspace 相對路徑(下載頁用)
    size: int            # bytes


@dataclass
class Output:
    summary_md: str = ""
    code: list[dict] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)   # 原始宣告(相對路徑)
    references: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)                 # 原始 JSON(存查)


def load_output(ws: str) -> Output | None:
    """讀 workspace 的 OUTPUT.json;缺/壞 → None(降級,不擲例外)。"""
    if not ws or ws.startswith("("):        # 哨值 workspace(handoff/adopted)無檔
        return None
    p = os.path.join(ws, OUTPUT_FILE)
    if not os.path.isfile(p):
        return None
    try:
        data = json.load(open(p, encoding="utf-8"))
    except (ValueError, OSError) as e:
        log.warning("OUTPUT.json 讀取/解析失敗 %s: %s", p, e)
        return None
    if not isinstance(data, dict):
        log.warning("OUTPUT.json 不是物件: %s", p)
        return None

    def _list_of_dict(v):
        return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []

    def _list_of_str(v):
        return [x for x in v if isinstance(x, str) and x.strip()] \
            if isinstance(v, list) else []

    return Output(
        summary_md=data.get("summary_md") if isinstance(
            data.get("summary_md"), str) else "",
        code=_list_of_dict(data.get("code")),
        attachments=_list_of_str(data.get("attachments")),
        references=_list_of_dict(data.get("references")),
        raw=data)


def _safe_resolve(ws: str, rel: str) -> str | None:
    """把宣告的附件相對路徑解析到 workspace 內;越界/不存在 → None(擋路徑穿越)。"""
    ws_abs = os.path.realpath(ws)
    target = os.path.realpath(os.path.join(ws_abs, rel))
    if target != ws_abs and not target.startswith(ws_abs + os.sep):
        return None                          # 逃出 workspace
    if not os.path.isfile(target):
        return None
    return target


def resolve_attachments(
        ws: str, output: Output) -> tuple[list[Attachment], int, list[str]]:
    """解析 attachments → (存在且安全的清單, 總 bytes, 被跳過的原始宣告)。
    只收 workspace 內、存在的檔;越界/不存在的記進 skipped(不擋流程)。"""
    ok: list[Attachment] = []
    skipped: list[str] = []
    total = 0
    for rel in output.attachments:
        target = _safe_resolve(ws, rel)
        if target is None:
            skipped.append(rel)
            log.warning("附件跳過(不存在或越界 workspace): %s", rel)
            continue
        size = os.path.getsize(target)
        ok.append(Attachment(name=os.path.basename(rel), rel=rel, size=size))
        total += size
    return ok, total, skipped


def attach_mode(total_bytes: int, n: int) -> str:
    """決定附件呈現方式:'none'(無檔)/ 'attach'(<6MB 附 Jira)/ 'link'(≥6MB 下載頁)。"""
    if n == 0:
        return "none"
    return "attach" if total_bytes < ATTACH_TOTAL_LIMIT else "link"
