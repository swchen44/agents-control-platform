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
| 2 | 指令台 / REST `apply_command`(run/retry/hold/stop/cancel/next) | 驗身分/可用性→`apply_command`→journal | REST 同步呼叫,**無 poll 重放窗口**;僅呼叫端逾時重送才會重執行 | 狀態操作冪等(cancel 兩次仍 ABORTED、next 重置冪等);指令台依**當前狀態**擋不合法指令 | 冪等(狀態操作)+ 呼叫端負責重試 |
| 3 | dispatcher 終態/pending comment(SUCCESS/FAILURE/unknown/budget/external/handoff) | **upsert→comment→journal** | 只會漏 comment/journal,不重複、不重跑(終態 skip) | 寫入順序 = 冪等 key(harness);store 是真相 | **at-most-once ✓**(W1 起即如此,本檔確認) |
| 4 | approval gate(首貼 plan/退回/escalate) | (W3.2 前)外寫在 gate 內、store 在 dispatcher 返回後 | revisions/pending 丟失 → 退回計數重置,**escalate 上限跨 crash 失效** | **W3.2 修正:gate 內先 upsert 再外寫**;首貼冪等 key = description 已有 control 段(重跑走 awaiting) | **at-most-once ✓(本波修)** |
| 5 | attempt 中途 harness crash(agent 已 spawn) | (W5.1 前)attempts/sid 在 attempt 完成後才 upsert | (修前)重跑整個 attempt、錢重花 | **W5.1 已修**:attempt 開跑前先持久化 attempts+預派 sid(rawcli+claude `--session-id`)+ journal `attempt_started`;重啟偵測 `a{N}.envelope` 缺 → 有 sid(任一引擎)退還 attempt + native resume(transcript 重放不重工);無 sid(codex 首跑)→ UNKNOWN 交人 | **✓(W5.1)**;殘邊角:persist 與 spawn 間 crash → resume 不存在的 session → 該 attempt 以 error 收場(機率極低,接受) |
| 6 | agent 進程死亡(非 harness) | envelope 缺/error | — | 三態:無 envelope=UNKNOWN 不自動重試(v5 D3);infra 不耗 attempt | ✓(W1 前既有) |
| 7 | provision(template 複製) | copytree→tmp→rename | 半成品目錄 | 原子 rename;殘留 tmp 重跑先清 | ✓(W1) |
| 8 | description 分區段寫入 | 讀最新→只換區塊→寫回 | 重寫同內容 | hash 冪等(沒變不重寫);機器段 hash 防篡改 | ✓(W2.2) |
| 9 | journal(events.jsonl append) | append-only | 重複行(見 #1) | 消費端(dashboard/detail)按 type 彙總,容忍重複 | ✓ 設計即容忍 |
| 10 | HIL 一次性表單提交(W11:human 段/稽核 comment/transition/resume) | 提交→回寫→`upsert_interaction(status=submitted)` | 同一 token 重放 | **一次性 token**:`is_open()`=status==PENDING;提交後轉 submitted,form_server 擋二次提交(= 內建 dedup key) | at-least-once + 冪等(token 去重),可接受(稽核 comment 極端窗口可能重一則) |
| 11 | workspace 佈建(W15:install 腳本 / copytree / skills / inject) | 佈建全成功才寫 `.arcp_provisioned` marker | install 中途 crash → 半殘 ws | **W18 修**:marker=commit;provision 進入時「不完整(無 marker 且無 TICKET.md)」→ rmtree 重建;grandfather 既有 ws(有 TICKET.md 者視為完整) | **at-most-once / all-or-nothing ✓(W18)** |

## 本波(W3.2)動作

1. **#4 修正**:`approval.gate` 每次 session 變更(pending/revisions)先
   `store.upsert_session` 再外寫(comment/assign/description)。
2. **#3 確認測試**:終態後重跑 handle 不重派、不重留言(test_idempotency)。
3. **#2/#5 記錄**:#2 原為 comment 留言指令通道(靠 watermark、有排序丟指令風險);
   後已改**指令台/REST `apply_command`**(狀態操作冪等、不再靠 comment watermark)。
   #5 留 W4+(sid 預派 + attempt_started 標記 → 缺 envelope 判 UNKNOWN)。

## 不做(本波)

- comment 內容級冪等 key(掃舊留言防重)——每輪 poll 掃 comment 成本高,
  且 #3/#4 的寫入順序已達 at-most-once,無需求場景。
- #5 的 sid 預派(牽動 rawcli agent 介面與三 backend,W4+ 單獨做)。

## A2 結論(W18):不建 qm 式 tool-output ledger

BACKLOG A2 原意 = 抄 qm 的 tool-output ledger 避免 resume 重複副作用。**釐清邊界後判定
在 ARCP 屬重工、不建**:

- **agent 工具調用**:靠 native resume(CLI 自己重放 session,已完成工具不重跑)——
  ARCP 根本不重呼叫工具(#5/#6)。
- **harness 副作用**:靠「先持久化 store 再外寫」的 **at-most-once**(#1–#4/#8/#9)——
  **構造上不會重複**(只會漏,可從 journal 補看)。ARCP 本就不重試副作用,沒有 qm 那種
  「at-least-once 需 ledger 去重」的場景。
- **HIL 表單**:一次性 token 狀態即 dedup(#10)。

qm 需要 ledger 是因為它 at-least-once + 重試;ARCP 走 at-most-once + native resume + 一次性
token,A2 的目標(不產生重複副作用)已達成。真正的殘缺是 **#11 install 原子性**(W15 引入),
已於 W18 用 `.arcp_provisioned` commit marker 補上。
