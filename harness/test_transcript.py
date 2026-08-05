#!/usr/bin/env python3
"""W4.2 — transcript 快照/打包 單元測(fake renderer 注入;pytest-compatible,亦自跑)。

涵蓋:snapshot→latest*.html、finalize→final*.html、pack→transcript.tgz
(gzip -9,含 jsonl 原檔+HTML)、renderer 缺席優雅降級、dispatcher close
接線(journal transcript_packed)、list_artifacts。
"""
from __future__ import annotations

import os
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp_harness import dispatcher as dmod  # noqa: E402
from arcp_harness import transcript as tmod  # noqa: E402
from arcp_harness.dispatcher import Dispatcher  # noqa: E402
from arcp_harness.inner_runner import AttemptResult  # noqa: E402
from arcp_harness.profiles import Profile  # noqa: E402
from arcp_harness.store import Store  # noqa: E402
from arcp_harness.ticket import Ticket  # noqa: E402
from arcp_harness.transcript import (  # noqa: E402
    finalize,
    list_artifacts,
    snapshot,
    transcript_dir,
)


def _fake_renderers(tmp):
    """注入 fake:render 產固定 HTML;find 回假 jsonl 原檔。"""
    main_jsonl = os.path.join(tmp, "sess.jsonl")
    sub_jsonl = os.path.join(tmp, "agent-x.jsonl")
    open(main_jsonl, "w").write('{"m":1}\n')
    open(sub_jsonl, "w").write('{"s":1}\n')

    def render_claude(session, out_dir, subagents=False):
        os.makedirs(out_dir, exist_ok=True)
        outs = []
        names = ["main.html"] + (["sub-agent-x.html"] if subagents else [])
        for n in names:
            p = os.path.join(out_dir, n)
            open(p, "w").write(f"<html>{n}</html>")
            outs.append(p)
        return outs

    def render_codex(session, out_dir):
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, "main.html")
        open(p, "w").write("<html>codex</html>")
        return [p]

    tmod._render_claude = render_claude
    tmod._render_codex = render_codex
    tmod._find_claude = lambda sid: main_jsonl
    tmod._find_subs = lambda j: [sub_jsonl]
    tmod._find_codex = lambda tid: main_jsonl


def _ws(tmp):
    ws = os.path.join(tmp, "tickets", "p__K-1__1", "ws")
    os.makedirs(ws, exist_ok=True)
    return ws


def test_snapshot_latest():
    tmp = tempfile.mkdtemp()
    _fake_renderers(tmp)
    ws = _ws(tmp)
    outs = snapshot("sid-1", "claude", ws)
    names = sorted(os.path.basename(p) for p in outs)
    assert names == ["latest-sub-agent-x.html", "latest.html"]
    assert os.path.dirname(outs[0]) == transcript_dir(ws)


def test_finalize_and_pack():
    tmp = tempfile.mkdtemp()
    _fake_renderers(tmp)
    ws = _ws(tmp)
    outs = finalize("sid-1", "claude", ws, pack=True)
    names = sorted(os.path.basename(p) for p in outs)
    assert "final.html" in names and "transcript.tgz" in names
    tgz = os.path.join(transcript_dir(ws), "transcript.tgz")
    with tarfile.open(tgz) as tf:
        members = sorted(m.name for m in tf.getmembers())
    assert "sess.jsonl" in members            # 主 session 原檔
    assert "agent-x.jsonl" in members         # 子代理原檔
    assert "final.html" in members            # 定格 HTML


def test_finalize_codex():
    tmp = tempfile.mkdtemp()
    _fake_renderers(tmp)
    ws = _ws(tmp)
    outs = finalize("thread-1", "codex", ws, pack=True)
    assert any(p.endswith("final.html") for p in outs)
    assert any(p.endswith("transcript.tgz") for p in outs)


def test_degrade_without_renderer():
    tmp = tempfile.mkdtemp()
    tmod._render_claude = None                # 重置注入
    old = tmod._CCLOG_DIR
    tmod._CCLOG_DIR = os.path.join(tmp, "nonexistent")
    try:
        assert snapshot("sid", "claude", _ws(tmp)) == []      # 不炸
        assert finalize("sid", "claude", _ws(tmp), pack=True) == []
    finally:
        tmod._CCLOG_DIR = old


def test_no_session_id_noop():
    tmp = tempfile.mkdtemp()
    _fake_renderers(tmp)
    assert snapshot(None, "claude", _ws(tmp)) == []
    assert finalize(None, "claude", _ws(tmp), pack=True) == []


def test_dispatcher_close_packs():
    tmp = tempfile.mkdtemp()
    _fake_renderers(tmp)

    def _fork(agent_cfg, ws, prompt, artifacts, attempt,
              resume_session_id=None):
        return AttemptResult(raw_outcome="completed", session_id="sid-1",
                             truly_resumed=False, cost_usd=0.0, error=None,
                             events_path="", envelope_path="",
                             error_kind=None)
    dmod.run_attempt = _fork

    class Src:
        def add_comment(self, iid, text):
            pass

    prof = Profile(name="p", workspace_template="empty",
                   workspace_folder="tickets/{issue_id}", skills=[],
                   agent={"backend": "rawcli", "engine": "claude"},
                   verify=[], max_attempts=1, on_unknown="pending")
    d = Dispatcher(Src(), Store(os.path.join(tmp, "s")), {"p": prof},
                   root=tmp)
    ev = d.handle(Ticket(id=1, key="K-1", summary="s", state="x",
                         assignee=None, assignee_id=None, description="d"),
                  "p")
    packed = [e for e in ev if e["type"] == "transcript_packed"]
    assert packed and "transcript.tgz" in packed[0]["files"]
    ws = d.store.get_session(1).workspace
    assert list_artifacts(ws) == sorted(packed[0]["files"])


if __name__ == "__main__":
    ok = True
    for _name, _fn in list(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"  PASS  {_name}")
            except AssertionError as e:
                ok = False
                print(f"  FAIL  {_name}: {e}")
            except Exception as e:  # noqa: BLE001
                ok = False
                print(f"  ERROR {_name}: {type(e).__name__}: {e}")
    print("test-transcript:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
