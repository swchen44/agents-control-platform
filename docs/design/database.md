# DB Schema(runtime/harness.db)

ARCP 的**持久記憶**:一個 SQLite 檔(`runtime/harness.db`,WAL 模式),存「當下狀態」。
與 `events.jsonl`(journal,存「發生過什麼」)分工見 [可觀測性](observability.md)。

> **真相在 code**:schema 定義在 [`src/arcp/store.py`](../../src/arcp/store.py) 的
> `CREATE TABLE`(`CREATE TABLE IF NOT EXISTS`,啟動自建、附加欄位隨版本演進)。本文是
> 對照說明,若與 code 不一致以 code 為準。**DB 是可變狀態、gitignore、絕不 wipe**
> (冪等靠它;見 [LESSONS #9](../lessons.md))。dashboard 有唯讀 **DB Browser** 頁可看。

## `ticket_watch` —— poller 水位(每票偵測差異用)

| 欄位 | 型別 | 意義 |
|---|---|---|
| `issue_id` | INTEGER PK | Jira 數字 id(**主鍵;key 會因 move 改變,不當識別**) |
| `key` | TEXT | Jira key(顯示用,如 SCRUM-36) |
| `last_comment_id` | INTEGER | 已處理到的最新 comment id(冪等水位:只反應之後的新留言) |
| `last_state` | TEXT | 上次看到的 Jira status(鏡射) |
| `last_assignee_id` | TEXT | 上次的 assignee accountId(偵測外部改 assignee) |
| `route_name` | TEXT | 命中的 route 名 |
| `first_seen_ts` | REAL | 首次看到(認養水位)的 epoch |

## `ticket_session` —— 每票的執行狀態(canonical 狀態的來源)

| 欄位 | 型別 | 意義 |
|---|---|---|
| `issue_id` | INTEGER PK | 同上 |
| `key` / `profile` / `workspace` | TEXT | Jira key / 綁定 profile / workspace 路徑 |
| `session_id` | TEXT | CLI 的 session/thread id(native resume 用;可預派) |
| `attempts` | INTEGER | 已用 attempt 數(對 max_attempts) |
| `outcome` | TEXT | `SUCCESS`/`FAILURE`/`ABORTED`/`UNKNOWN`/NULL(**內部判定,不寫回 Jira**) |
| `pending_reason` | TEXT | 非終態等待原因(budget/queue/human/external…) |
| `cost_usd` | REAL | 累計花費(對預算閘;月花費另掃 journal) |
| `queued` / `queued_at` | INTEGER/REAL | F1 額度閘排隊旗標 + 入隊時間(FIFO) |
| `inactive` | INTEGER | (歷史)assignee 離手資源開關;W11 起 assignee 恆定 |
| `approval_revisions` | INTEGER | 起點審批退回次數(對 max_revisions) |
| `finished_at` | REAL | 進終態時間(retention 回收依據) |
| `clearquest_id` | TEXT | 外部追蹤 id(選填) |
| `human_score` | INTEGER | 人類完成度評分 0–10(HIL(End)) |
| `score_reminded_at` | REAL | 上次催評時間(軟性提醒節流) |

> 上面這些欄位 + queued/inactive/有無 session,經 `canonical_state()` **唯讀映射**成
> dashboard 的 6 態(見 [生命週期](lifecycle.md));狀態機真值在 DB,不寫回 Jira。

## `trigger_state` —— 排程觸發水位

| 欄位 | 型別 | 意義 |
|---|---|---|
| `name` | TEXT PK | trigger 名 |
| `last_run` | REAL | 上次執行 epoch(對 `every` 判斷是否該再跑) |

## `interactions` —— 一次性 token 表單(W11 HIL)

| 欄位 | 型別 | 意義 |
|---|---|---|
| `request_id` | TEXT PK | 互動請求 id |
| `token` | TEXT UNIQUE | 一次性連結 token(≥128-bit;機密,勿入共用日誌) |
| `issue_id` / `key` | INTEGER/TEXT | 綁定的票 |
| `schema_id` / `schema_version` | TEXT/INTEGER | 表單型別(need_info/decision/score_and_close)+ 版本 |
| `created_at` / `expires_at` | REAL | 建立 / 到期(逾期失效) |
| `status` | TEXT | `pending`/`submitted`/… |
| `payload` / `submission` | TEXT(JSON) | 出題內容 / 人填的答案 |
| `submitted_at` / `submitted_by` | REAL/TEXT | 提交時間 / 提交者 |
| `reminders` / `reminded_at` | INTEGER/REAL | 催填次數 / 上次催填(對 stall 上限) |

索引:`ix_interactions_issue`(issue_id)。互動流程見 [互動服務](interaction.md)。

## 沒有「範例 DB」

DB 是啟動時**自動建空表、runtime 填充**;沒有、也不需要 ship 一個 `.db` 範例檔 ——
schema 的真相是 `store.py`。要看真實資料,跑起來後開 dashboard 的 DB Browser,或用
`sqlite3 runtime/harness.db .schema`。
