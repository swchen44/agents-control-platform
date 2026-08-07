"""Outer-loop poller: poll → diff → events → routing → journal.

Grey-rollout stance (v5 P1): every route today is expected to be
`notify_only`/`ignore` — the poller RECORDS what it would do and never acts.
Dispatch (create_or_resume) arrives in Phase 2 behind the same event stream.

Idempotency: change detection diffs against the SQLite watch state, keyed by
numeric issue_id; comments advance a per-ticket watermark (max comment id),
so a re-poll — or a crash-restart mid-poll — never replays old events.
"""

from __future__ import annotations

import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from .gate import engine_of, select_dispatchable
from .jira_source import JiraCloudSource
from .logutil import get_logger
from .retention import reclaim
from .routing import Route, match
from .store import Store, TicketWatch
from .triggers import due, run_trigger

log = get_logger("poller")


class OuterLoop:
    def __init__(self, source: JiraCloudSource, store: Store,
                 routes: list[Route], jql: str, dispatcher=None,
                 commands=None, external=None, max_running: int = 1,
                 concurrency: dict | None = None, triggers=None,
                 scoregate=None):
        self.source = source
        self.store = store
        self.routes = routes
        self.jql = jql
        self.dispatcher = dispatcher   # None = pure grey mode (Phase 1)
        self.commands = commands       # CommandHandler (Phase 3)
        self.external = external       # ExternalChangePolicy (Phase 3)
        self.scoregate = scoregate     # W7.2 ScoreGate(終態抓評分/催評)
        self.triggers = triggers or []  # W3.4 內部觸發源(scheduled)
        self.max_running = max(1, max_running)  # v5 D10 (conc.1)
        self.paused = False            # W13 graceful:只 watch 不派新工
        self.stopping = False          # W4.5 graceful shutdown:當前輪跑完就退
        self._cycles = 0               # W3.3 retention 掃描節流 / W9.1 poll 次數
        self.started_at = time.time()  # W9.1:poller 起始(control 顯示運行時間)
        self.poll_interval = 0.0       # W9.1:輪詢間隔秒(run_poller 設定)
        # F1 分層閘門;缺省退化成單層 max_running(向後相容)
        self.concurrency = concurrency or {
            "max_running": self.max_running, "per_engine": {},
            "per_profile": {}}

    def poll_once(self) -> list[dict]:
        """One reconciliation pass. Returns the events it journaled.

        Two phases: watch state updates SERIAL (watermark ordering + store),
        then dispatch runs in PARALLEL (ThreadPoolExecutor, max_running).
        Store is thread-safe (conc.1 lock); dispatch is the slow part.
        """
        events: list[dict] = []
        # W3.3 retention:首輪 + 每 240 輪(15s 間隔 ≈ 每小時)輕量掃;
        # 失敗不擋 poll(下輪再試)
        self._cycles += 1
        if self.dispatcher is not None and self._cycles % 240 == 1:
            try:
                events.extend(reclaim(self.store, self.dispatcher.profiles))
            except Exception as e:
                log.warning("retention 掃描失敗:%s", e)
        # W3.4:內部觸發源(scheduled)——due 且額度有餘才跑;paused 也不跑
        if self.triggers and self.dispatcher is not None and not self.paused:
            events.extend(self._run_due_triggers())
        to_dispatch: list = []  # (ticket, profile_name) collected serially
        for t in self.source.search(self.jql):
            prev = self.store.get(t.id)
            t.comments = self.source.get_comments(t.id)
            route = match(t, self.routes)

            if prev is None:
                events.append(self.store.journal(
                    "new_issue", t.id, t.key, state=t.state,
                    summary=t.summary[:120]))
                if route is not None:
                    events.append(self.store.journal(
                        "route_matched", t.id, t.key, route=route.name,
                        profile=route.profile, on_match=route.on_match))
            else:
                if t.state != prev.last_state:
                    events.append(self.store.journal(
                        "status_changed", t.id, t.key,
                        old=prev.last_state, new=t.state))
                    if self.external is not None:
                        events.extend(
                            self.external.on_status_changed(t, t.state))
                if (t.assignee_id or "") != prev.last_assignee_id:
                    events.append(self.store.journal(
                        "assignee_changed", t.id, t.key,
                        old=prev.last_assignee_id, new=t.assignee_id or ""))
                    if self.external is not None:
                        events.extend(self.external.on_assignee_changed(t))

            watermark = prev.last_comment_id if prev else 0
            for c in t.comments:
                if c.id > watermark:
                    events.append(self.store.journal(
                        "comment_added", t.id, t.key, comment_id=c.id,
                        author=c.author, body=c.body[:200]))
                    # 指令在 dispatch 之前處理:retry/run 解除 pending 後,
                    # 同一輪 poll 就會重新派工
                    if self.commands is not None:
                        events.extend(self.commands.handle(t, c))
            new_watermark = max([watermark] + [c.id for c in t.comments])

            self.store.upsert(TicketWatch(
                issue_id=t.id, key=t.key,
                last_comment_id=new_watermark,
                last_state=t.state,
                last_assignee_id=t.assignee_id or "",
                route_name=route.name if route else None,
                last_assignee=t.assignee or "",        # W4.1 dashboard 顯示
                summary=t.summary or "",               # W4.7 過濾用
                description=t.description or ""))

            # W7.2:終態(SUCCESS/FAILURE)未評分的票——抓 human 段 score 或催評。
            # 對所有票呼叫(內部早退非終態);終態票仍在 jql(未 Done)故看得到。
            if self.scoregate is not None:
                events.extend(self.scoregate.on_poll(
                    t, self.store.get_session(t.id)))

            # collect dispatch AFTER watch state is persisted (idempotency
            # first: a crash mid-dispatch must not replay watch events)
            if (route is not None and route.on_match == "create_or_resume"
                    and route.profile and self.dispatcher is not None):
                to_dispatch.append((t, route.profile))

        # -- F1 分層資源閘門 + 並行 dispatch (v5 D10) ---------------------- #
        if not to_dispatch:
            return events
        if self.paused:                # W13:pause 只擋新派工,watch 照常
            log.debug("paused: skip dispatch of %d candidate(s)",
                      len(to_dispatch))
            return events
        selected = self._gate(to_dispatch, events)      # 額滿標 QUEUED
        if not selected:
            return events
        max_workers = min(self.concurrency.get("max_running", 1),
                          len(selected))
        if max_workers <= 1 or len(selected) == 1:
            for t, prof in selected:
                events.extend(self.dispatcher.handle(t, prof))
            return events
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(self.dispatcher.handle, t, prof)
                       for t, prof in selected]
            for fut in as_completed(futures):
                try:
                    events.extend(fut.result())
                except Exception as e:  # one ticket failing must not kill poll
                    events.append(self.store.journal(
                        "dispatch_error", 0, "?", error=str(e)[:200]))
        return events

    def _run_due_triggers(self) -> list[dict]:
        """W3.4:due 的 scheduled trigger 與票共用 F1 額度(global+per-engine);
        額滿跳過本輪(不標 QUEUED,下輪重評——trigger 沒有票面可展示排隊)。"""
        evs: list[dict] = []
        profiles = self.dispatcher.profiles
        for tr in self.triggers:
            if not due(tr, self.store):
                continue
            if tr.script is not None:               # W4.4:script 非 agent
                try:                                # 進程,不占引擎額度
                    evs.extend(run_trigger(tr, profiles, self.store,
                                           self.dispatcher.root))
                except Exception as e:
                    evs.append(self.store.journal(
                        "trigger_error", 0, tr.name, error=str(e)[:200]))
                    log.warning("trigger %s 失敗:%s", tr.name, e)
                continue
            active = self.store.active_sessions()
            prof = profiles.get(tr.profile)
            eng = engine_of(prof) if prof is not None else "claude"
            cap = (self.concurrency.get("per_engine") or {}).get(eng)
            eng_used = sum(1 for s in active if s.profile in profiles
                           and engine_of(profiles[s.profile]) == eng)
            if (len(active) >= self.concurrency.get("max_running", 1)
                    or (cap is not None and eng_used >= cap)):
                log.debug("trigger %s due 但額滿,下輪再試", tr.name)
                continue
            try:
                evs.extend(run_trigger(tr, profiles, self.store,
                                       self.dispatcher.root))
            except Exception as e:      # 單一 trigger 壞不擋 poll
                evs.append(self.store.journal(
                    "trigger_error", 0, tr.name, error=str(e)[:200]))
                log.warning("trigger %s 失敗:%s", tr.name, e)
        return evs

    def _gate(self, to_dispatch, events):
        """F1:分層額度閘門(FIFO,to_dispatch 已是 created ASC)。

        只有「真正要跑 agent」的候選(session None 或 active)占額度、按三層額度選,
        超額標 QUEUED;已終態/pending/inactive 的直接放行(dispatcher.handle 會 skip,
        或自解除 pending:external),**不占額度**(W8)——否則 SUCCESS-未轉狀態的票仍在
        JQL 結果裡會白占額度、擠掉要跑的。回傳本輪 selected [(ticket, profile)]。"""
        profiles = self.dispatcher.profiles
        active = self.store.active_sessions()           # W8:只含 active
        inf_eng = Counter(engine_of(profiles[s.profile]) for s in active
                          if s.profile in profiles)
        inf_prof = Counter(s.profile for s in active)
        passthrough, need = [], []
        for idx, (t, prof) in enumerate(to_dispatch):
            s = self.store.get_session(t.id)
            if s is not None and (s.outcome in ("SUCCESS", "ABORTED")
                                  or s.pending_reason or s.inactive):
                passthrough.append(idx)                 # handle 秒 skip / 自解除
            else:
                need.append(idx)
        cand = []
        for i in need:                                  # F3:session pin 優先
            prof = to_dispatch[i][1]                    # (與 dispatcher 一致)
            s = self.store.get_session(to_dispatch[i][0].id)
            if s is not None and s.profile in profiles:
                prof = s.profile
            cand.append((engine_of(profiles[prof]) if prof in profiles
                         else "claude", prof))
        run_l, q_l = select_dispatchable(
            cand, self.concurrency, in_flight_engine=inf_eng,
            in_flight_profile=inf_prof, in_flight_total=len(active))
        for j in q_l:                                   # 標 QUEUED,下輪重評
            i = need[j]
            t = to_dispatch[i][0]
            sess = self.store.get_session(t.id)
            if sess is not None:                        # 只標已有 session
                sess.queued = True
                sess.queued_at = sess.queued_at or time.time()
                self.store.upsert_session(sess)
            events.append(self.store.journal(
                "queued", t.id, t.key, profile=cand[j][1], engine=cand[j][0]))
        selected = []
        for i in passthrough + [need[j] for j in run_l]:
            t, prof = to_dispatch[i]
            sess = self.store.get_session(t.id)         # 要跑了→清 queued 標記
            if sess is not None and sess.queued:
                sess.queued = False
                self.store.upsert_session(sess)
            selected.append((t, prof))
        log.debug("gate: selected=%d (run=%d passthrough=%d) queued=%d active=%d",
                  len(selected), len(run_l), len(passthrough), len(q_l),
                  len(active))
        return selected
