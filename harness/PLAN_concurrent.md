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

**Phase conc.1 — 並行 dispatch + Store 執行緒安全**
- [ ] `store.py`:加 `threading.Lock`,包 upsert/journal/upsert_session(K2)
- [ ] `poller.py`:一輪收集 dispatch 任務 → ThreadPoolExecutor 並行(K1/K3);
      watch 狀態仍序列落庫(dispatch 前),dispatch 並行
- [ ] routes.yaml `concurrency: {max_running: 4}`
- [ ] E2E:同時開 3 張 filechain-rawcli 票 → 並行完成、store 無損、grader 全過、
      wall-clock < 串行
- [ ] commit+push

**Phase conc.2 — 長駐共享 server(openhands-server backend)**
- [ ] `server_manager.py`:懶啟動 agent-server(1 個)、健康檢查、base_url/key、收攤
- [ ] inner_runner job 傳長駐 server_url/key;inner_agentserver_runner 優先連(K5)
- [ ] OuterLoop 持有 ServerManager,openhands-server 票共用
- [ ] E2E:同時開 3 張 filechain-server 票 → 共用 1 個 server PID、並行完成
- [ ] commit+push

## 里程碑

M7 = 多張 Jira 票並行 dispatch(3 backend 通用)。
M8 = openhands-server 票共用長駐 server(harness 版的 demo_concurrent)。
