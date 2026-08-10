"""W3.3 — workspace retention 回收(DESIGN §3 / W19)。

終態 session(SUCCESS/ABORTED/FAILURE/UNKNOWN)保留 `retention_days` 天後
刪 **workspace instance 目錄**(含 ws/ 與 attempts/ 產物);store 記錄與
journal 留著稽核(工作區可拋、證據不可拋——journal/事件已含判定依據)。

- default 270 天(近一年,偏稽核保守);profile `retention_days: 0` = 不回收。
- 刪除後 session.workspace 置哨值 `(reclaimed)`——之後指令台 retry 之類
  讓 outcome 歸 None 時,health_check 失敗 → 重 provision(finished_at 也由
  store 歸零)。
- 安全欄:只刪「存在的目錄」;哨值(`(adopted)`/`(handoff)`/…)非目錄自然跳過。
"""

from __future__ import annotations

import os
import shutil
import time

from .logutil import get_logger

log = get_logger("retention")

TERMINAL = ("SUCCESS", "ABORTED", "FAILURE", "UNKNOWN")


def reclaim(store, profiles: dict, now: float | None = None) -> list[dict]:
    """掃一輪:終態 + 過期 → 刪 workspace instance 目錄。回 journal 事件。"""
    now = time.time() if now is None else now
    events: list[dict] = []
    for sess in store.all_sessions():
        if sess.outcome not in TERMINAL or not sess.finished_at:
            continue
        prof = profiles.get(sess.profile)
        days = prof.retention_days if prof is not None else 270
        if days <= 0:                                  # 0 = 不回收
            continue
        if now - sess.finished_at < days * 86400:
            continue
        # workspace = <base>/ws;整個 instance(base,含 attempts/)一起回收
        ws = sess.workspace
        base = os.path.dirname(ws) if os.path.basename(ws) == "ws" else ws
        if not os.path.isdir(base):                    # 哨值/已清:只標記
            if sess.workspace != "(reclaimed)":
                sess.workspace = "(reclaimed)"
                store.upsert_session(sess)
            continue
        shutil.rmtree(base, ignore_errors=True)
        sess.workspace = "(reclaimed)"
        store.upsert_session(sess)
        events.append(store.journal(
            "workspace_reclaimed", sess.issue_id, sess.key,
            path=base, outcome=sess.outcome,
            age_days=round((now - sess.finished_at) / 86400, 1)))
        log.info("%s workspace 回收(%s,%d 天)", sess.key, sess.outcome,
                 (now - sess.finished_at) // 86400)
    return events
