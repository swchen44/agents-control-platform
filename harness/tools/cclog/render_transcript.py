#!/usr/bin/env python3
"""ARCP wrapper — claude / codex session → human-readable HTML(W4/V0)。

底層 = vendored claude-code-log(MIT,見 NOTICE.md;上游碼零修改)。
本檔是 ARCP 自寫整合層:session 定位(id→檔案)、sub-agent 枚舉
(<proj>/<session-id>/subagents/agent-*.jsonl)、輸出整理。

Usage:
  python3 render_transcript.py --claude-session <id|.jsonl> [--subagents] \
      --output-dir <dir>
  python3 render_transcript.py --codex-session <thread-id|rollout.jsonl> \
      --output-dir <dir>

被 W4.2 transcript.py(快照器)呼叫;獨立可手跑除錯。
輸出:<output-dir>/main.html(主 session)、sub-<agentid>.html(每個子代理)。
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(HERE, ".venv", "bin", "python")
CLAUDE_PROJECTS = os.path.expanduser("~/.claude/projects")
CODEX_SESSIONS = os.path.expanduser("~/.codex/sessions")


def _cli(*args: str) -> subprocess.CompletedProcess:
    """跑 vendored cli(專用 venv + PYTHONPATH 指向本目錄)。"""
    env = dict(os.environ, PYTHONPATH=HERE)
    return subprocess.run(
        [VENV_PY, "-m", "claude_code_log.cli", *args],
        capture_output=True, text=True, env=env, timeout=300)


def find_claude_session(session_id: str) -> str | None:
    """session id(或前綴)→ ~/.claude/projects 下的 .jsonl 路徑。"""
    hits = glob.glob(os.path.join(CLAUDE_PROJECTS, "*",
                                  f"{session_id}*.jsonl"))
    return hits[0] if hits else None


def find_subagent_files(session_jsonl: str) -> list[str]:
    """主 session 檔 → subagents/agent-*.jsonl 清單(新版 Claude Code 格局)。"""
    sid = os.path.basename(session_jsonl).removesuffix(".jsonl")
    subdir = os.path.join(os.path.dirname(session_jsonl), sid, "subagents")
    return sorted(glob.glob(os.path.join(subdir, "agent-*.jsonl")))


def find_codex_rollout(thread_id: str) -> str | None:
    """thread id → ~/.codex/sessions/YYYY/MM/DD/rollout-*-<thread>.jsonl。"""
    hits = glob.glob(os.path.join(CODEX_SESSIONS, "**",
                                  f"*{thread_id}*.jsonl"), recursive=True)
    return hits[0] if hits else None


# cclog 的互動時間軸會從 unpkg CDN 動態載 vis-timeline。內網/離線環境
# 不可下載外部元件 → 改指向 dashboard 本地路徑(vendor 版,見 vendor/)。
# CSP 另在 dashboard 端硬擋任何外部載入(雙保險)。
_CDN_REWRITES = (
    ("https://unpkg.com/vis-timeline/standalone/umd/"
     "vis-timeline-graph2d.min.js", "/tvendor/vis-timeline.min.js"),
    ("https://unpkg.com/vis-timeline/styles/vis-timeline-graph2d.min.css",
     "/tvendor/vis-timeline.min.css"),
)


def _delocalize(html_path: str) -> None:
    """把 cclog HTML 內的外部 CDN URL 改成 dashboard 本地路徑(離線可用)。"""
    try:
        with open(html_path, encoding="utf-8") as f:
            html = f.read()
    except OSError:
        return
    orig = html
    for cdn, local in _CDN_REWRITES:
        html = html.replace(cdn, local)
    if html != orig:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)


def _render_jsonl(jsonl: str, out_html: str) -> bool:
    """單一 claude 格式 .jsonl → HTML(cli 會在同目錄產出,再搬到目標)。"""
    r = _cli(jsonl, "-o", out_html)
    if r.returncode != 0:
        print(f"[cclog] 轉檔失敗 {jsonl}: {r.stderr[-300:]}", file=sys.stderr)
        return False
    if os.path.isfile(out_html):
        _delocalize(out_html)          # 去外部 CDN 依賴
        return True
    return False


def render_claude(session: str, output_dir: str,
                  subagents: bool = False) -> list[str]:
    """→ 產出的 HTML 路徑清單(main + sub-*)。"""
    jsonl = session if session.endswith(".jsonl") \
        else find_claude_session(session)
    if not jsonl or not os.path.isfile(jsonl):
        print(f"[cclog] 找不到 claude session: {session}", file=sys.stderr)
        return []
    os.makedirs(output_dir, exist_ok=True)
    out: list[str] = []
    main = os.path.join(output_dir, "main.html")
    if _render_jsonl(jsonl, main):
        out.append(main)
    if subagents:
        for sub in find_subagent_files(jsonl):
            agent_id = os.path.basename(sub).removesuffix(".jsonl")
            dst = os.path.join(output_dir, f"sub-{agent_id}.html")
            if _render_jsonl(sub, dst):
                out.append(dst)
    return out


def render_codex(session: str, output_dir: str) -> list[str]:
    """codex thread → HTML(走上游 --provider codex)。"""
    os.makedirs(output_dir, exist_ok=True)
    main = os.path.join(output_dir, "main.html")
    if session.endswith(".jsonl"):
        # rollout 檔名含 thread id;取出給 --session-id(provider 路徑解碼)
        base = os.path.basename(session).removesuffix(".jsonl")
        session = base.split("-", 7)[-1] if "rollout-" in base else base
    r = _cli("--provider", "codex", "--session-id", session, "-o", main)
    if r.returncode != 0:
        print(f"[cclog] codex 轉檔失敗 {session}: {r.stderr[-300:]}",
              file=sys.stderr)
        return []
    if os.path.isfile(main):
        _delocalize(main)              # 去外部 CDN 依賴
        return [main]
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--claude-session")
    ap.add_argument("--codex-session")
    ap.add_argument("--subagents", action="store_true")
    ap.add_argument("--output-dir", required=True)
    a = ap.parse_args()
    if not a.claude_session and not a.codex_session:
        ap.error("需要 --claude-session 或 --codex-session")
    outs: list[str] = []
    if a.claude_session:
        outs += render_claude(a.claude_session, a.output_dir, a.subagents)
    if a.codex_session:
        outs += render_codex(a.codex_session, a.output_dir)
    for p in outs:
        print(p)
    return 0 if outs else 1


if __name__ == "__main__":
    sys.exit(main())
