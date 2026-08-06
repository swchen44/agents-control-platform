"""W4.3 — transcript 快照器(B1+B3;統一快照機制的背景半邊)。

背景 daemon thread:每 `snapshot_interval_sec`(config,預設 60)掃
active sessions(有 session_id 者)→ `transcript.snapshot` 重產
latest*.html——人類可在 dashboard 肉眼監控進行中的 agent;「換手前可看」
由此保證(永遠有 ≤N 秒新的 HTML)+ 離手事件的 final 定格(dispatcher/
commands 同步觸發,見 transcript.finalize 呼叫點)。

(W5.1 起)rawcli+claude 的 sid 於 attempt 開跑前**預派並持久化**——首個
attempt 進行中即可快照。其餘 backend/codex 首跑仍要等 attempt 結束才有 sid
(codex thread id 由 CLI 自生無法預派),resume 後即時可見。
"""

from __future__ import annotations

import threading

from .logutil import get_logger
from .transcript import engine_of_agent, snapshot

log = get_logger("snapshotter")


class Snapshotter:
    def __init__(self, store, profiles_getter, interval_sec: float = 60):
        """profiles_getter:callable → dict(hot reload 後拿到新 profiles)。"""
        self.store = store
        self.profiles_getter = profiles_getter
        self.interval = max(1.0, float(interval_sec))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _tick(self) -> int:
        """掃一輪 active sessions → snapshot。回產出快照的 session 數。"""
        n = 0
        profiles = self.profiles_getter() or {}
        for s in self.store.active_sessions():
            if not s.session_id or s.workspace.startswith("("):
                continue
            prof = profiles.get(s.profile)
            engine = engine_of_agent(prof.agent) if prof is not None \
                else "claude"
            if snapshot(s.session_id, engine, s.workspace):
                n += 1
        return n

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                n = self._tick()
                if n:
                    log.debug("snapshot tick:%d session(s) 更新", n)
            except Exception as e:  # noqa: BLE001 — 快照壞不擋主流程
                log.warning("snapshot tick 失敗:%s", e)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run,
                                        name="arcp-snapshotter", daemon=True)
        self._thread.start()
        log.info("snapshotter 啟動(每 %.0fs)", self.interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
