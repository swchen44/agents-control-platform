# DESIGN_architecture — 模組架構 · HIL 生命週期 · agent↔agent 交接

> W10(2026-08-08)定案。與 `/concepts` 頁「模組架構 / 狀態機」段同源;生命週期
> 細節見 [lifecycle.md](lifecycle.md)。**本文件描述目標設計**:
> W10.1(狀態模型/圖/網頁)已實作;**W10.2(HIL 行為)與 W10.3(a2a)暫緩**,
> 待使用者審過模型/文件/網頁再接線。

## 1. 分層模組架構(資料流由上而下)

```
┌─ 輸入層 ─────────  jira_source · triggers
│                      ↓  (Jira 事件 / 排程觸發)
├─ 決策層 ─────────  poller(OuterLoop) · routing · gate(F1 額度閘)
│                      ↓  (要跑哪張票、用哪個 profile、有沒有額度)
├─ 執行層 ─────────  dispatcher · inner_runner · workspace/isolation · contract
│                      ↓  (provision → 跑 attempt → envelope 契約 → 判 outcome)
├─ 人機協作層(HIL)  approval · scoring · commands · external · sections
│                      ↓  (審批/triage · 評分 · 指令 · 離手 · description 分段)
└─ 狀態·觀測·控制層  store · control_api · detail_server · transcript · retention
```

**store 是狀態主幹**:幾乎所有模組都讀寫它(SQLite `ticket_watch`/`ticket_session`
+ append-only journal `events.jsonl`)。上圖只表達分層與資料流方向;逐模組的
trigger/輸入/輸出/上下游見下表(與 `/concepts` 頁的職責表同內容)。

## 2. 模組職責表

| 模組 | 職責 | trigger 時間 | 輸入 | 輸出 | 上游 → 下游 |
|---|---|---|---|---|---|
| **jira_source** | Jira Cloud 讀寫封裝(search/comment/transition/set_description,含 write_retry + on_write 回呼) | poller 每輪 / 各政策要寫入時 | JQL、issue_id、寫入動作 | Ticket/Comment 物件、寫入結果 | poller·dispatcher·各政策 → Jira Cloud REST |
| **triggers** | 內部排程觸發源(scheduled agent / script) | poller 每輪查 due | trigger 定義 + store 上次執行時間 | 到期 trigger → 派工/跑 script | poller → dispatcher·script |
| **poller(OuterLoop)** | 外圈輪詢:diff 變更→journal→協調派工/指令/政策/評分 | run_poller 定時迴圈(interval 秒) | JQL 搜到的票 + store watch 狀態 | journal 事件流 + 派工決策 | run_poller → routing·gate·dispatcher·commands·external·scoring·store |
| **routing** | 票 → route/profile 比對(when 條件式) | poller 每票 | Ticket 欄位 + routes.yaml | Route(profile, on_match) | poller → poller·dispatcher |
| **gate(F1)** | 分層並發額度閘(global / per-engine / per-profile) | poller 有候選待派時 | 候選清單 + in-flight 計數 | selected / queued 劃分 | poller → dispatcher |
| **dispatcher** | 派工:審批門→provision workspace→跑 attempt→寫 envelope→更新 session | poller 選中候選(create_or_resume) | Ticket + profile + store session | attempt 執行 + envelope + session/journal 更新 | poller·gate → approval·workspace·inner_runner·contract·store |
| **inner_runner** | 實跑 claude -p / codex exec 一個 attempt(看門狗/killpg/native resume) | dispatcher 呼叫 | prompt、workspace、session_id | raw 結果 + cost + session_id | dispatcher → claude/codex CLI |
| **workspace/isolation** | template→workspace instance provision(不變 id 綁 cwd)+ 隔離 | dispatcher 首次 fork | profile.template + issue_id | workspace 路徑 | dispatcher → 檔案系統 |
| **contract** | envelope 結構化契約 + grader 三態判定(證據型停止) | attempt 結束 | raw agent 輸出 | envelope + outcome(SUCCESS/FAILURE/UNKNOWN) | dispatcher·inner_runner → store |
| **approval** | 起點審批 / triage 閘(寫 plan 進 description、等 human 段 agent_name;可 decline) | dispatcher fork 前(require_approval / 全域 triage) | Ticket.description + profile | proceed/awaiting/reprompt + description 寫入 | dispatcher → sections·jira_source·store |
| **scoring(ScoreGate)** | HIL(End) 人評分(seed score 佔位、讀 score、催評) | poller 每輪對終態未評票 | description human 段 + session | human_score + journal | poller → sections·jira_source·store |
| **commands** | @agent 留言指令(run/retry/stop/cancel/next/handoff) | poller 偵測新留言 | Comment + 白名單 | 指令效果(解 pending/換手…)+ journal | poller → store·jira_source |
| **external(離手政策)** | assignee/status 政策:交人讓額度、回機器人 resume、外部關 Done=撤銷 | poller 偵測 status/assignee 變更 | Ticket 變更 + bot_id | inactive/abort/resume + journal | poller → store·jira_source |
| **sections** | description 三方分段(human/control/agent:<名>)parse/render + hash 防篡改 | approval/scoring/commands 讀寫 description 時 | description 文字 | Section 物件 / 組回 description | approval·scoring·commands → (純函式) |
| **store** | SQLite 狀態(ticket_watch/ticket_session)+ append-only journal | 各模組讀寫 | watch/session upsert、journal 事件 | 持久狀態 + 事件流 | 幾乎全部 → SQLite/檔案 |
| **control_api** | 內嵌 REST 控制面(pause/resume/reload/shutdown/evict/gen_transcript/status) | 人經 dashboard/curl POST(即時) | HTTP 請求 | poller 控制副作用 + status JSON | 人/dashboard → poller·store·transcript |
| **detail_server** | 唯讀觀測 dashboard:KPI/圖/表/狀態機/概念/REST /api/v1 | 瀏覽器請求 + 5s live 刷新 | store(runtime 目錄) | HTML/JSON | 人 → store(唯讀) |
| **transcript** | session → final HTML/打包(換手/交人/evict/close/被動按鈕) | finalize 事件 或 control gen_transcript | session 原始 log | final.html / transcript.tgz | dispatcher·commands·control_api → 檔案系統 |
| **retention** | workspace 回收(過期/終態,釋放磁碟) | poller 週期(約每 240 輪 ≈ 每小時) | store session + profile 保留策略 | 回收 workspace + journal | poller → 檔案系統/store |

## 3. HIL 生命週期(6 態 + 概念終點)

`todo → running ⇄ queued`,人介入時進 `HIL(Middle)`(過程中等人)或 `HIL(End)`
(終點評分),外部撤銷/交接進 `aborted`,人關 Jira 後 `closed`(概念終點,離開
jql)。`success/failure/unknown` 是 **HIL(End) 的結果屬性**,不是頂層狀態。完整
轉移圖與 DB 推導見 `/concepts` 頁與 [lifecycle.md](lifecycle.md)。

## 4. agent↔agent 交接:兩種機制,怎麼選?

| | **同票換手** `@agent next <profile>` | **跨票 base 繼承** |
|---|---|---|
| 做法 | 同一張 Jira,重置 session、pin 新 profile | 人**自建**新 Jira,宣告 `base:<ref>`,harness 登記+注入脈絡,舊票收成 ABORTED(交接) |
| 脈絡 | 全留在同一票(留言/description/transcript 都在) | 複製 base 的 transcript/TICKET.md 進新 workspace(`ws/BASE_<key>/`)+ prompt 前置 + 貼 Jira 連結 |
| 舊工作 | 就地接續(可 native resume) | 新票重新開始,但帶 base 脈絡 |
| 開新票? | 否 | 是(人在 Jira 建,harness 只登記) |
| **適合場景** | 小幅換手、**同一件事繼續**、換 profile 但引擎/脈絡相容 | 換**引擎**、重開、跨專案、人策展重啟、任何要「乾淨重來但保留前輪脈絡」的泛化場景 |

**怎麼選(給人的判斷準則)**:
- 想「就地把這件事交給另一個 agent/profile 繼續」→ **同票 next**。
- 想「這條路走不下去了,換個引擎/從頭來過,但別讓新 agent 從零摸索」→ **跨票 base**。
- 跨引擎(claude↔codex)因 session 格式不同無法 native resume,通常走 **跨票 base**。

### 4.1 base(基底票)spec(W10.3 實作)

- **宣告**:新票 description human 段 `base: <ref>`(3 合 1 ref:Jira key / 內部 id
  / CQ id),或在 Control 頁登記「新票=N,base=M」。源票術語稱 **base(基底票)**。
- **登記 + 脈絡注入**(harness,不建 Jira):
  1. 解析 base → 複製 base 的 `final.html` transcript + `TICKET.md` + 最後 envelope
     進新 workspace `ws/BASE_<key>/`;
  2. agent prompt **前置**一段「請先讀 `ws/BASE_<key>/` 的前輪脈絡,確認做到哪、
     再續做」;
  3. 在新票貼 Jira 留言,連到 base 的觀測頁(dashboard `/ticket/<base>`)供人追。
- **舊票收尾**:交接時 base 票 session 收成 **ABORTED(reason=handoff→新票)**,
  **不算 failure**(交接不是 agent 失敗,失敗率 KPI 才誠實);若還在跑則 killpg 釋放。
- **Control 頁**:輸入 Jira ID 做設定;「開新票、繼承自哪張 base」→ 登記連結。
