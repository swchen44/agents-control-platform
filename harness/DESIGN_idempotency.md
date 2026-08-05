# DESIGN_idempotency — A2 冪等分層盤點(W3.2/W18)

> 原則(使用者定調):「有些利用 agent transcript,有些要自己的機制」——分層:
> **agent 層** = native resume(transcript 重放,已完成工具不重做);
> **harness 層** = 自有機制(watermark、寫入順序、冪等 key、原子操作)。
> 本檔盤點所有「外部寫入(Jira)× store 寫入」的 crash 窗口,標防護與缺口。

## 判定語彙

- **at-most-once**:先持久化 store、再外寫 → crash 只會「漏」外寫,不會重複;
  store 是真相,漏掉的留言可從 journal/dashboard 補看。
- **at-least-once + 冪等 handler**:先外寫、後持久化 → crash 會重放一次;
  handler 對重放無害(狀態操作冪等),但外寫可能重複(可接受即標註)。

## 盤點表

| # | 路徑 | 寫入順序 | crash 重放後果 | 防護(層) | 判定 |
|---|---|---|---|---|---|
| 1 | poller watch 事件(status/assignee/comment journal) | 處理→`upsert(TicketWatch)` | 該輪事件重放一次:journal 重複行、external policy 重跑 | watermark(harness);policy 狀態操作冪等(inactive=True 再設無害) | at-least-once,可接受(journal 消費端容忍重複) |
| 2 | comment 指令(run/retry/stop/cancel/next) | 執行+ack→watermark upsert | 指令重執行 + ack 重複一則 | 狀態操作冪等(cancel 兩次仍 ABORTED、next 重置冪等);**反向排序會丟指令,比重複更糟** | at-least-once,可接受(設計選擇,本檔記錄) |
| 3 | dispatcher 終態/pending comment(SUCCESS/FAILURE/unknown/budget/external/handoff) | **upsert→comment→journal** | 只會漏 comment/journal,不重複、不重跑(終態 skip) | 寫入順序 = 冪等 key(harness);store 是真相 | **at-most-once ✓**(W1 起即如此,本檔確認) |
| 4 | approval gate(首貼 plan/退回/escalate) | (W3.2 前)外寫在 gate 內、store 在 dispatcher 返回後 | revisions/pending 丟失 → 退回計數重置,**escalate 上限跨 crash 失效** | **W3.2 修正:gate 內先 upsert 再外寫**;首貼冪等 key = description 已有 control 段(重跑走 awaiting) | **at-most-once ✓(本波修)** |
| 5 | attempt 中途 harness crash(agent 已 spawn) | attempts/session_id 在 attempt 完成後才 upsert | 重跑整個 attempt:**錢重花**、workspace 由 agent 重做(claude sid 未持久 → 無法 native resume) | agent 層 native resume 需 sid;目前 sid 於 attempt 後才知 | **缺口(記錄,W4+)**:dispatcher 預派 sid 並 pre-persist(attempt_started 標記),重啟後 aN.envelope 缺失 → 判 UNKNOWN 交人,符合「loop on evidence」 |
| 6 | agent 進程死亡(非 harness) | envelope 缺/error | — | 三態:無 envelope=UNKNOWN 不自動重試(v5 D3);infra 不耗 attempt | ✓(W1 前既有) |
| 7 | provision(template 複製) | copytree→tmp→rename | 半成品目錄 | 原子 rename;殘留 tmp 重跑先清 | ✓(W1) |
| 8 | description 分區段寫入 | 讀最新→只換區塊→寫回 | 重寫同內容 | hash 冪等(沒變不重寫);機器段 hash 防篡改 | ✓(W2.2) |
| 9 | journal(events.jsonl append) | append-only | 重複行(見 #1) | 消費端(dashboard/detail)按 type 彙總,容忍重複 | ✓ 設計即容忍 |

## 本波(W3.2)動作

1. **#4 修正**:`approval.gate` 每次 session 變更(pending/revisions)先
   `store.upsert_session` 再外寫(comment/assign/description)。
2. **#3 確認測試**:終態後重跑 handle 不重派、不重留言(test_idempotency)。
3. **#2/#5 記錄**:#2 是設計選擇(丟指令比重複糟);#5 留 W4+(sid 預派 +
   attempt_started 標記 → 缺 envelope 判 UNKNOWN)。

## 不做(本波)

- comment 內容級冪等 key(掃舊留言防重)——每輪 poll 掃 comment 成本高,
  且 #3/#4 的寫入順序已達 at-most-once,無需求場景。
- #5 的 sid 預派(牽動 rawcli agent 介面與三 backend,W4+ 單獨做)。
