"""Q 波(2026-08-13 定案)— 過程存證 + 結案回寫(離開 ARCP,Jira 上也看得懂全程)。

- attach_ticket_md_if_changed:TICKET.md 內容真變(hash 比對)才上傳 Jira 附件
  `TICKET_<key>_<時戳>.md` —— 在 Jira 上可回放「agent 當時看到什麼」。
- finalize_provenance:close/cancel 收尾時
  (1) description 置頂 `[ARCP owner=result]` yaml 結果區(完成度/人評+自評/
      成本/執行+等人時長/crid/evidence 清單 + server 路徑與 dashboard 連結)
  (2) 附存證:transcript(final.html)、timeline.jsonl(journal 該票切片)、
      SESSION.md(session 全欄位快照)。>6MB 不附,結果區列 server 路徑。

全程 best-effort:任何一步失敗只 log+journal,不擋收尾(附件是證據,不是閘門)。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import tempfile
import time

from .logutil import get_logger
from .sections import Section, parse, render
from .transcript import transcript_dir

log = get_logger("provenance")

_HASH_SIDECAR = ".arcp_ticket_hash"       # 上次已上傳的 TICKET.md hash(單一 writer)
_MAX_ATTACH = 6 * 1024 * 1024             # 與 deliverables 同門檻


def _ts(now: float | None = None) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))


def attach_ticket_md_if_changed(source, store, issue_id: int, key: str,
                                ws: str, now: float | None = None) -> list[dict]:
    """TICKET.md 內容真變才上傳(2A 定案)。無 ws/檔 → no-op。回 journal 事件。"""
    path = os.path.join(ws or "", "TICKET.md")
    if not ws or not os.path.isfile(path):
        return []
    text = open(path, encoding="utf-8").read()
    h = hashlib.sha256(text.encode()).hexdigest()[:16]
    sidecar = os.path.join(ws, _HASH_SIDECAR)
    prev = (open(sidecar).read().strip()
            if os.path.isfile(sidecar) else "")
    if h == prev:
        return []
    fname = f"TICKET_{key}_{_ts(now)}.md"
    try:
        tmp = os.path.join(tempfile.mkdtemp(prefix="arcp-prov-"), fname)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        source.add_attachment(issue_id, tmp)
        os.remove(tmp)
    except Exception as e:  # noqa: BLE001  best-effort
        log.warning("%s TICKET.md 存證上傳失敗(不擋):%s", key, e)
        return []
    with open(sidecar, "w") as f:
        f.write(h)
    log.info("%s TICKET.md 存證 → %s", key, fname)
    return [store.journal("ticket_md_attached", issue_id, key,
                          filename=fname, hash=h)]


# ---------------------------------------------------------------- 結案 ----- #

def _journal_slice(store, issue_id: int) -> list[dict]:
    """events.jsonl 該票全事件切片(帶時戳的過程 log;L4 未來也讀這個)。"""
    out: list[dict] = []
    try:
        with open(store.journal_path, encoding="utf-8") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if o.get("issue_id") == issue_id:
                    out.append(o)
    except FileNotFoundError:
        pass
    return out

def _durations(evs: list[dict]) -> tuple[float, float]:
    """(執行秒, 等人秒)。執行=attempt_started→attempt_finished 配對累計;
    等人=pending→下一個 attempt_started/closed/aborted。粗算(分鐘級呈現)。"""
    run = wait = 0.0
    run_t0 = wait_t0 = None
    for e in evs:
        t, typ = e.get("ts") or 0, e.get("type")
        if typ == "attempt_started":
            run_t0 = t
            if wait_t0 is not None:
                wait += max(0, t - wait_t0)
                wait_t0 = None
        elif typ == "attempt_finished" and run_t0 is not None:
            run += max(0, t - run_t0)
            run_t0 = None
        elif typ in ("pending", "hil_requested", "score_requested"):
            if wait_t0 is None:
                wait_t0 = t
        elif typ in ("closed", "aborted") and wait_t0 is not None:
            wait += max(0, t - wait_t0)
            wait_t0 = None
    return run, wait


def _fmt_min(sec: float) -> str:
    m = int(sec // 60)
    return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m"


def _session_md(sess) -> str:
    rows = [f"| {k} | {v} |" for k, v in
            sorted(dataclasses.asdict(sess).items()) if v not in (None, "")]
    return ("# SESSION 快照(結案時)\n\n| 欄位 | 值 |\n|---|---|\n"
            + "\n".join(rows) + "\n")


def _attach(source, issue_id, key, path, skipped: list[str]) -> str | None:
    """附一檔;>6MB 或失敗 → 記 skipped 回 None,否則回檔名。"""
    try:
        if os.path.getsize(path) > _MAX_ATTACH:
            skipped.append(f"{os.path.basename(path)}(>6MB,見 server 路徑)")
            return None
        source.add_attachment(issue_id, path)
        return os.path.basename(path)
    except Exception as e:  # noqa: BLE001
        log.warning("%s 存證附件 %s 失敗(不擋):%s", key, path, e)
        skipped.append(os.path.basename(path))
        return None


def finalize_provenance(source, store, sess, issue_id: int, key: str, *,
                        dashboard_url: str = "",
                        now: float | None = None) -> list[dict]:
    """close/cancel 收尾(2B/2C 定案):附存證三件套 + description 置頂結果區。
    冪等:結果區已存在同 result 值就不重寫;附件重上傳由 Jira 同名容忍。"""
    events: list[dict] = []
    now = now or time.time()
    ws = sess.workspace if os.path.isdir(sess.workspace or "") else ""
    evs = _journal_slice(store, issue_id)
    attached: list[str] = []
    skipped: list[str] = []
    tmpdir = tempfile.mkdtemp(prefix="arcp-prov-")

    # (1) timeline.jsonl — 帶時戳的全過程事件
    tl = os.path.join(tmpdir, f"timeline_{key}.jsonl")
    with open(tl, "w", encoding="utf-8") as f:
        for e in evs:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    if evs and (n := _attach(source, issue_id, key, tl, skipped)):
        attached.append(n)

    # (2) SESSION.md — 駕駛艙欄位快照
    sm = os.path.join(tmpdir, f"SESSION_{key}.md")
    with open(sm, "w", encoding="utf-8") as f:
        f.write(_session_md(sess))
    if n := _attach(source, issue_id, key, sm, skipped):
        attached.append(n)

    # (3) transcript final.html(finalize 已定格;沒有就算了——2C 定案)
    if ws:
        fh = os.path.join(transcript_dir(ws), "final.html")
        if os.path.isfile(fh):
            if n := _attach(source, issue_id, key, fh, skipped):
                attached.append(n)
    if attached or skipped:
        events.append(store.journal("provenance_attached", issue_id, key,
                                    attached=attached, skipped=skipped))

    # (4) description 置頂 yaml 結果區(owner=result,排最前)
    outcome = sess.outcome or "UNKNOWN"
    result = (f"ABORTED(reason={sess.abort_reason})"
              if outcome == "ABORTED" and getattr(sess, "abort_reason", None)
              else outcome)
    run_s, wait_s = _durations(evs)
    hs = getattr(sess, "human_score", None)
    ag = getattr(sess, "agent_score", None)
    lines = [f"result: {result}"]
    if hs is not None or ag is not None:
        lines.append(f"score: 人評 {hs if hs is not None else '-'}/10 · "
                     f"agent 自評 {ag if ag is not None else '-'}/10")
    lines.append(f"cost: ${sess.cost_usd or 0:.4f} · "
                 f"{getattr(sess, 'tokens', 0) or 0:,} tokens · "
                 f"{sess.attempts} attempts")
    lines.append(f"time: 執行 {_fmt_min(run_s)} · 等人 {_fmt_min(wait_s)}")
    if getattr(sess, "clearquest_id", None):
        lines.append(f"crid: {sess.clearquest_id}")
    ev_names = attached + skipped
    if ev_names:
        lines.append("evidence: " + " · ".join(ev_names))
    if sess.workspace:
        lines.append(f"server: {sess.workspace}")
    if dashboard_url:
        lines.append(f"dashboard: {dashboard_url.rstrip('/')}/ticket/{key}")
    lines.append("closed_at: "
                 + time.strftime("%Y-%m-%dT%H:%M", time.localtime(now)))
    try:
        t = source.get_ticket(issue_id)
        before, secs, after = parse(t.description or "")
        by = {s.owner: s for s in secs}
        by["result"] = Section("result", "\n".join(lines))
        source.set_description(issue_id, render(before, list(by.values()), after))
        events.append(store.journal("result_written", issue_id, key,
                                    result=result))
        log.info("%s 結案回寫 result=%s 附件=%s", key, result, attached)
    except Exception as e:  # noqa: BLE001
        log.warning("%s 結果區回寫失敗(不擋收尾):%s", key, e)
    return events
