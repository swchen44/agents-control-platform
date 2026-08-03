# PLAN_B+ — agent-server 模式 + 視覺化/traceability 收割

> 承 B(M1-M3 已完成)。目標:inner runner 從 SDK **in-process** 換成
> **agent-server**(REST/WS),打通 OpenHands 視覺化;投資帶進 C(見
> research/2026-08-abc-roadmap-analysis.md §3.5)。
> **單線、小步、每步 commit+push。斷線 resume:讀本檔 checklist + git log。**
> 最後更新:2026-08-03。

## 依賴鏈與收割目標

視覺化(GUI/detail page) ← agent-server ← conversation 存在 server 裡。
現在 inner runner 是 in-process,conversation 不在任何 server,GUI 看不到。

- OpenHands 給 conversation 級 trace(L1/L3);L0(ticket)/L2(envelope/驗證/
  成本)仍是 harness journal —— 互補不是替代。
- 收割形態二選一(到時決定):(a) Agent Canvas GUI 零開發;
  (b) agent-server REST/WS + 自建 detail page(v5 §4.7)拼上 L0/L2。

## 已釘死的事實(讀碼研究 openhands-acp-claude-code-lifetime.md)

- 啟動:`uvx --from openhands-agent-server==1.39.1 --with openhands-sdk==1.39.1
  --with openhands-tools==1.39.1 --with openhands-workspace==1.39.1
  --with "agent-client-protocol<0.11" agent-server --host 127.0.0.1 --port 18000`
- `/api/*` 需 `X-Session-API-Key`;事件走原生 WS `/sockets/events/{id}`
- env:`OH_SESSION_API_KEYS_0`、`OH_PERSISTENCE_DIR`、`OH_CONVERSATIONS_PATH`
- POST /api/conversations:workspace/agent_settings(acp)/secrets;lazy spawn
- startup 90s / prompt idle 1800s;閒置 20 分 Evict→rehydrate

## 前置未知數(spike 先消滅,勿直接改 runner)

- U1 agent-server 能否用現有工具鏈起來(uvx 首啟數分鐘;或本地 SDK 版本)
- U2 REST 建 conversation 的 payload 精確形狀(ACP agent_settings)
- U3 WS 事件訂閱與終止判定(對映 in-process 的 envelope 欄位)
- U4 GUI(Agent Canvas)能否連上此 server 看到 conversation

## Checklist

**Phase B+.0 — spike:agent-server 起得來 + REST 建 conversation** ✅ 2026-08-03
- [x] 裝齊四個第一方包(--no-deps,見 openhands-acp-poc/PLAN.md 陷阱補記)+ libtmux
- [x] 起 server(127.0.0.1:18010)、GET / 200(本地啟動數秒,非 uvx 數分鐘)
- [x] REST 建 ACP conversation 跑 trivial 任務、events 輪詢到產出(`spike_agentserver.py`)
- [x] **spike 答案(U1-U3 皆 PASS)**:
  - U1 起法:`python -m openhands.agent_server --host --port`,env
    `OH_SESSION_API_KEYS_0`;認證 header `X-Session-API-Key`
  - U2 建 conversation:`POST /api/conversations`,body =
    `{workspace:{kind:LocalWorkspace,working_dir}, agent:<ACPAgent.model_dump>,
    initial_message:{role,content}}` → 201 + `id`
  - U3 事件:`GET /api/conversations/{id}/events/search?limit=100` → `items[]`;
    **終止判定 = `ConversationStateUpdateEvent.execution_status`
    (running→finished)**;session_id 在 `agent_state.acp_session_id`
- [x] 事件↔envelope 對映:completed←execution_status==finished;
    session_id←agent_state;cost←需另查 conversation info(B+.1 補);
    error←ConversationErrorEvent
- [x] commit+push

**Phase B+.1 — inner runner agent-server 版(envelope 契約不變)** ✅ 2026-08-03
- [x] `inner_agentserver_runner.py`:自啟 server + REST 建 conversation +
      events 輪詢 → 同一份 envelope(completed/session_id/truly_resumed/cost/error)
- [x] profile `backend: openhands-server`(filechain-server;與 openhands-acp 並存)
- [x] inner_runner.py 依 backend 分派(RUNNERS 表);job 加 server_port/key/persist
- [x] E2E 4/4 PASS(`e2e_agentserver.py`):server 版 completed + session_id +
      A 路 grader 通過;in-process 對照同契約 —— **backend 切換 = 只改 profile 一行**
- [x] commit+push
- **真實發現(入 COMPARISON)**:
  - ⚠️ **cost gap**:ACP-over-agent-server 的 UsageUpdate 常在拆除時尚未到達
    (server.log "UsageUpdate not received"),`metrics.accumulated_cost` 回 $0;
    in-process 版能拿到 $0.045 → 成本可控性是 in-process/raw 的又一優勢。
  - 教訓:events search 端點 `limit<=100` 硬上限(assert);spike 用 100 貼邊
    沒踩、runner 隨手寫 200 就 500 → **spike 參數要與正式碼一致**。長任務
    (>100 事件)需分頁,列 B+.2 精修。
  - 回寫 Jira 未測(直接跑 run_attempt 聚焦 envelope 契約);走 dispatcher
    的完整 Jira E2E 與 in-process 版同路徑,低風險。

**Phase B+.2 — 視覺化收割** ✅ 2026-08-03(M4 達成)
- [x] **detail page**(`detail_server.py`,v5 §4.7 雛形,stdlib http.server):
      一張 ticket 的**四層 trace 一頁**——L0/L1 harness journal + L2 envelope +
      **L3 agent-server conversation 原生事件**;Claude in Chrome 實地打開驗證
      (SCRUM-9,19 事件:MessageEvent/ACPToolCall×8/StateUpdate running→finished)
- [x] **收割核心價值證實**:L3 是 OpenHands 給的(conversation 視角);
      **L0(ticket)/L1(attempt/outcome)/L2(grader/cost)是 GUI 給不了的**——
      detail page 對齊兩者 = 完整 traceability(v5 §4.5 四層對齊落地)
- [x] **cost gap 有解(收割副產品)**:L3 底部 `stats` 事件帶
      `usage_to_metrics.haiku` → 用量在事件流裡,只是沒進 metrics.accumulated_cost;
      改從 stats 事件讀即可修(非硬限制)。列 B+.3 精修。
- [ ] resume 對照(agent-server 閒置 Evict→rehydrate)未做 —— 低優先,
      機制已由讀碼研究釐清(§3.5)+ B+.1 session_id 續用實證。
- [x] commit+push

## B+ 總結(M4)

同一張 Jira 票的 agent 執行,現在可在 detail page 看到完整四層 trace。
backend 切換(in-process↔agent-server)只改 profile 一行,envelope 契約不變,
dispatcher/grader/三態邏輯零改動 —— 為 C 期(換 RawCLIAgent)鋪好路。
收割品(detail page、四層對齊)帶進 C 且事件更細(RawCLIAgent 發 248 級)。
**Phase B+.3 — 精修** ✅ 2026-08-03
- [x] **cost 修正**:改從 `stats.usage_to_metrics.*.accumulated_cost` 讀
      (fallback metrics 端點)→ SCRUM-10 拿到 $0.0257(之前 $0)。
      B+.2 記的 cost gap 消除 —— agent-server 成本可查,只是位置不同。
- [x] **events 分頁**:`_fetch_all_events` 用 `next_page_id` 翻頁,破 100 硬上限
      (B+.1 教訓);長任務(>100 事件)不再漏事件/誤判終止。
- [x] **detail page live 刷新**:每 5s 自動重載,進行中的 conversation 事件
      逐步可見。
- [x] commit+push

**B+.3 後續(未排程,低優先)**:detail page 拼 Jira 深連結、resume 對照
(閒置 Evict→rehydrate)、長駐共享 server(避免每 attempt 重啟)。

## 里程碑

M4 = 同一張 Jira 票的 agent 執行在 OpenHands GUI 裡看得到 conversation。
C 期待驗點(spike 2,半天):agent-server 由 server 端實例化自訂 RawCLIAgent。
