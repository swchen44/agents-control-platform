# PLAN_concurrent — harness 併發:並行 dispatch + 長駐共享 server

> 承 B/B+/C + demo_concurrent(已證 1 server 管 4 conversation)。把併發接進
> harness 主線:多張 Jira 票並行 dispatch;openhands-server backend 共用 1 個
> 長駐 server(現每 attempt 自起,慢且無共享)。設計約束沿用 v5 D10。
> **單線、小步、每步 commit+push。斷線 resume:讀本檔 checklist + git log。**
> 最後更新:2026-08-03。

## 兩個正交能力

1. **並行 dispatch(harness 層,通用)**:outer loop 一輪收集多張 create_or_resume
   票 → 執行緒池並行 `dispatcher.handle`,`max_running` 限流(v5 D10=8)。
   對三個 backend 都有效(各自子進程/conversation 並發)。
2. **長駐共享 server(openhands-server backend 優化)**:harness 起 1 個長駐
   agent-server,openhands-server 票共用(避免每 attempt 90s 自起 + 享 demo 的
   1-server-N-conversation 併發)。rawcli/acp 不受影響(各自子進程)。

## 設計決策(沿用 v5 D10;新的標註)

| # | 決策 | 理由 |
|---|---|---|
| K1 | 並行用 `ThreadPoolExecutor(max_workers=max_running)` | dispatch 是 IO-bound(等 CLI/HTTP),執行緒夠;GIL 不礙(子進程/HTTP 在外) |
| K2 | **Store 加 `threading.Lock`** 包 DB 寫 + journal append | ⚠️ SQLite 連接非執行緒安全;dispatch 慢(數十秒)、store 操作快(ms),串行化 store 不影響併發 |
| K3 | max_running 從 routes.yaml `concurrency:` 讀(預設 4) | v5 D10;超過排隊(執行緒池自然 queue) |
| K4 | 長駐 server 由 `ServerManager` 懶啟動 + 健康檢查 + 收攤關閉 | 一個 server 服務所有 openhands-server 票 |
| K5 | inner_agentserver_runner 優先連 job 給的長駐 server,否則自起 | 向後相容(單票仍可自起) |

## Checklist

**Phase conc.1 — 並行 dispatch + Store 執行緒安全** ✅ 2026-08-03 **M7**
- [x] `store.py`:`threading.Lock` 包 get/upsert/journal/get_session/
      upsert_session;`check_same_thread=False`(K2)
- [x] `poller.py`:兩階段(watch 序列 + dispatch 並行 ThreadPoolExecutor);
      一票拋異常不殺其它(as_completed try/except→dispatch_error)
- [x] routes.yaml `concurrency: {max_running: 4}`;load_config 透出 max_running
- [x] E2E(`e2e_parallel.py`)3 張 filechain-rawcli 並行:3/3 SUCCESS、store 無損
      (costs 各自正確)、**wall-clock 27.5s vs 串行 ~75s**、selftest 17/17
- [x] commit+push

**Phase conc.2 — 長駐共享 server(openhands-server backend)**
- [ ] `server_manager.py`:懶啟動 agent-server(1 個)、健康檢查、base_url/key、收攤
- [ ] inner_runner job 傳長駐 server_url/key;inner_agentserver_runner 優先連(K5)
- [ ] OuterLoop 持有 ServerManager,openhands-server 票共用
- [ ] E2E:同時開 3 張 filechain-server 票 → 共用 1 個 server PID、並行完成
- [ ] commit+push

## 健壯性:non-normal cases 分析(2026-08-03,使用者提)

核心原則(承 v5 + Hermes 三態):**store 是 source of truth,不是 server 記憶體**。
任何票只要 outcome 非終態(SUCCESS/ABORTED),下次 poll 就重新評估→resume/retry。
**「不漏掉」= 持久化 + 非終態必重評 + 基礎設施故障不消耗 attempt。**

| # | 異常 | 現況 | 防護設計(conc.2) |
|---|---|---|---|
| N1 | **長駐 server 中途掛掉**(crash/OOM/kill) | 每 attempt 自起,無此問題但無共享 | ServerManager 健康檢查→**重起(同 `OH_PERSISTENCE_DIR`)→ OpenHands rehydrate conversation**;掛時未完成的票 envelope completed=False |
| N2 | **掛掉的票怎麼續、不漏** | store 記 session_id+outcome | outcome 非 SUCCESS→下次 poll 重新 dispatch→`acp_resume_session_id`/`--resume` 續原 conversation(session_id 持久) |
| N3 | **區分基礎設施 vs 任務故障**(關鍵) | 現在都算 error→消耗 attempt | envelope 加 `error_kind`:**infra(server 連不上/掛)→`pending:external`**(不消耗 attempt,server 回來下次 poll 續);task(agent 做不對)→FAILURE(消耗 attempt);無證據(kill)→UNKNOWN |
| N4 | **server 啟動慢/失敗** | 自起 90s timeout | ServerManager 懶啟動+就緒探測;連不上=infra=pending:external |
| N5 | **poll 週期重疊**(dispatch 慢於 interval) | run_poller **串行 poll**(一輪返回才 sleep)→不重疊 ✅ | 標注:未來 webhook/多 poller 需 in-flight 鎖(issue_id 正在跑就跳過) |
| N6 | **harness 自己崩**(並行 dispatch 中) | store 持久 | 重起後非終態票重評續;in-flight 只在記憶體(崩了自然丟=重評,正確) |
| N7 | **一張票 dispatch 拋異常** | ✅ as_completed try/except→dispatch_error,不殺其它 | 已處理 |
| N8 | **Jira rate limit**(並行多 add_comment) | 未防 | 並行度限流(max_running)+ comment 退避重試;write_policy coarse(v5) |
| N9 | **斷網**(poll 中) | ✅ run_poller retry next cycle | 已處理 |
| N10 | **store 執行緒競爭** | ✅ conc.1 加鎖 | 已處理 |
| N11 | **並行票 workspace 衝突** | ✅ 各自 tickets/{issue_id}/ws | 已隔離 |
| N12 | **resume 找不到原 conversation**(session store 被清) | rawcli 有三段梯度(transcript);openhands load_session 失敗 fallback new_session | 既有機制;transcript 降級可補 openhands 側 |

**conc.2 據此的必做**:① ServerManager(健康檢查+重起+同 persistence)② envelope
`error_kind` 區分 infra/task/unknown → infra 走 pending:external(不消耗 attempt)。

## 里程碑

M7 = 多張 Jira 票並行 dispatch(3 backend 通用)。
M8 = openhands-server 票共用長駐 server + N1-N4 健壯(掛了能重起續、不漏、
基礎設施故障不消耗 attempt)。
