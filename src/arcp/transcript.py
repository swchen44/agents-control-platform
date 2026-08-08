"""W4.2 — transcript 快照/打包(B2;統一快照器的產物層)。

產物放 instance 內 `<base>/transcript/`(W24,與 instance 同生命週期,
retention 照收):
    latest.html / latest-sub-*.html   active 快照(W4.3 每 N 秒重產)
    final.html  / final-sub-*.html    離手/close 當下的定格
    transcript.tgz                    close 打包(gzip -9:主/子 session
                                      jsonl 原檔 + final HTML)

渲染 = vendored claude-code-log(tools/cclog,MIT,見 NOTICE)。renderer
缺席/失敗 → journal 記警告、不擋派工流程(優雅降級)。
"""

from __future__ import annotations

import glob
import os
import sys
import tarfile

from .logutil import get_logger
from .paths import vendor_dir

log = get_logger("transcript")

_CCLOG_DIR = os.path.join(vendor_dir() or ".", "cclog")   # vendored claude-code-log

# 注入點(測試 monkeypatch;正式 = tools/cclog/render_transcript 的函數)
_render_claude = None
_render_codex = None
_find_claude = None
_find_subs = None
_find_codex = None


def _load_renderer() -> bool:
    """延遲載入 wrapper(tools/cclog 可能不在——降級)。"""
    global _render_claude, _render_codex, _find_claude, _find_subs, _find_codex
    if _render_claude is not None:
        return True
    if not os.path.isdir(_CCLOG_DIR):
        return False
    if _CCLOG_DIR not in sys.path:
        sys.path.insert(0, _CCLOG_DIR)
    try:
        import render_transcript as rt
        _render_claude = rt.render_claude
        _render_codex = rt.render_codex
        _find_claude = rt.find_claude_session
        _find_subs = rt.find_subagent_files
        _find_codex = rt.find_codex_rollout
        return True
    except Exception as e:  # noqa: BLE001 — renderer 壞不擋流程
        log.warning("cclog renderer 載入失敗:%s", e)
        return False


def transcript_dir(workspace: str) -> str:
    base = os.path.dirname(workspace) if workspace.endswith("/ws") \
        else workspace
    return os.path.join(base, "transcript")


def engine_of_agent(agent_cfg: dict) -> str:
    return agent_cfg.get("engine", "claude")


def _render(session_id: str, engine: str, out_dir: str,
            prefix: str) -> list[str]:
    """session → HTML;產物依 prefix 改名(latest / final)。"""
    if not _load_renderer():
        return []
    os.makedirs(out_dir, exist_ok=True)
    tmp = os.path.join(out_dir, f".{prefix}-work")
    outs = (_render_claude(session_id, tmp, subagents=True)
            if engine == "claude" else _render_codex(session_id, tmp))
    final: list[str] = []
    for p in outs:
        name = os.path.basename(p)
        dst = os.path.join(
            out_dir, f"{prefix}.html" if name == "main.html"
            else f"{prefix}-{name.removesuffix('.html')}.html")
        os.replace(p, dst)
        final.append(dst)
    if os.path.isdir(tmp):
        try:
            os.rmdir(tmp)
        except OSError:
            pass
    return final


def snapshot(session_id: str | None, engine: str, workspace: str) -> list[str]:
    """active 快照 → latest*.html(W4.3 每 N 秒呼叫)。"""
    if not session_id:
        return []
    try:
        return _render(session_id, engine, transcript_dir(workspace), "latest")
    except Exception as e:  # noqa: BLE001
        log.warning("snapshot 失敗(%s):%s", session_id, e)
        return []


def _write_meta(out_dir: str, session_id: str, reason: str,
                files: list[str]) -> None:
    """W6.4:sidecar 記產生時間 + 原因 + session + sub-session(dashboard 顯示)。"""
    import datetime
    import glob as _glob
    import json as _json
    subs = [os.path.basename(f).removesuffix(".jsonl")
            for f in _glob.glob(os.path.expanduser(
                f"~/.claude/projects/*/{session_id}/subagents/agent-*.jsonl"))]
    meta = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "reason": reason, "session_id": session_id, "subs": subs,
        "files": [os.path.basename(f) for f in files],
    }
    try:
        with open(os.path.join(out_dir, "meta.json"), "w",
                  encoding="utf-8") as f:
            _json.dump(meta, f, ensure_ascii=False)
    except OSError as e:
        log.warning("transcript meta 寫入失敗:%s", e)


def read_meta(workspace: str) -> dict | None:
    """W6.4:dashboard 讀 transcript 產生 metadata(時間/原因)。"""
    import json as _json
    p = os.path.join(transcript_dir(workspace), "meta.json")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return _json.load(f)
    except (OSError, ValueError):
        return None


def finalize(session_id: str | None, engine: str, workspace: str,
             pack: bool = False, reason: str = "") -> list[str]:
    """定格 → final*.html + meta.json(W6.4 記時間/原因);pack=True(close)
    再打 transcript.tgz(gzip -9:session jsonl 原檔 + final HTML)。回產物清單。

    W6.4:統一入口——事件觸發(state/assignee/evict/close)與手動按鈕都走它,
    reason 記入 meta(state-change/assignee-change/evict/close/manual/pending)。
    """
    if not session_id:
        return []
    out_dir = transcript_dir(workspace)
    try:
        outs = _render(session_id, engine, out_dir, "final")
    except Exception as e:  # noqa: BLE001
        log.warning("finalize 渲染失敗(%s):%s", session_id, e)
        outs = []
    if outs:                                # 有產出才記 metadata
        _write_meta(out_dir, session_id, reason or "unknown", outs)
    if not pack:
        return outs
    try:
        sources: list[str] = []
        if _load_renderer():
            if engine == "claude":
                j = _find_claude(session_id)
                if j:
                    sources.append(j)
                    sources += _find_subs(j)
            else:
                j = _find_codex(session_id)
                if j:
                    sources.append(j)
        tgz = os.path.join(out_dir, "transcript.tgz")
        os.makedirs(out_dir, exist_ok=True)
        with tarfile.open(tgz, "w:gz", compresslevel=9) as tf:
            for p in sources + outs:
                if os.path.isfile(p):
                    tf.add(p, arcname=os.path.basename(p))
        if sources or outs:
            outs.append(tgz)
            log.info("transcript 打包 %s(%d 檔,%.1fKB)", tgz,
                     len(sources) + len(outs) - 1,
                     os.path.getsize(tgz) / 1024)
        else:
            os.remove(tgz)                     # 空包不留
    except Exception as e:  # noqa: BLE001
        log.warning("transcript 打包失敗(%s):%s", session_id, e)
    return outs


def source_files(session_id: str | None, engine: str) -> list[str]:
    """W7.7:session id → 原始對話 jsonl 路徑(claude:主 + sub-session;codex:rollout)。
    給 REST /api/v1 讓 LLM 深挖用;renderer(cclog finders)缺席 → []。"""
    if not session_id or not _load_renderer():
        return []
    out: list[str] = []
    try:
        if engine == "claude":
            j = _find_claude(session_id)
            if j:
                out.append(j)
                out += _find_subs(j)
        else:
            j = _find_codex(session_id)
            if j:
                out.append(j)
    except Exception as e:  # noqa: BLE001
        log.warning("source_files 解析失敗(%s):%s", session_id, e)
    return [p for p in out if p and os.path.isfile(p)]


def list_artifacts(workspace: str) -> list[str]:
    """dashboard 用:instance 的 transcript 產物(檔名清單,依名排序)。
    meta.json 是 sidecar 中繼資料(W6.4 產生時間/原因),不列為產物。"""
    d = transcript_dir(workspace)
    return sorted(os.path.basename(p)
                  for p in glob.glob(os.path.join(d, "*"))
                  if os.path.isfile(p) and os.path.basename(p) != "meta.json")
