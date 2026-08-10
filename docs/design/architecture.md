# DESIGN_architecture — 模組架構 · HIL 生命週期 · agent↔agent 交接

> W10(2026-08-08)定案、W10.3(2026-08-09)實作。與 `/concepts` 頁「模組架構 /
> 狀態機」段同源;生命週期細節見 [lifecycle.md](lifecycle.md)。
> W10.1(狀態模型/圖/網頁)、W10.2(HIL 行為,於 W11+group A 落地)、
> **W10.3(a2a 交接:同票換手(next) + 跨票換手(base),由 HIL 表單驅動)皆已實作**。

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
| **routing** | 票 → route/profile 比對(when 條件式) | poller 每票 | Ticket 欄位 + config.yaml | Route(profile, on_match) | poller → poller·dispatcher |
| **gate(F1)** | 分層並發額度閘(global / per-engine / per-profile) | poller 有候選待派時 | 候選清單 + in-flight 計數 | selected / queued 劃分 | poller → dispatcher |
| **dispatcher** | 派工:審批門→provision workspace→跑 attempt→寫 envelope→更新 session | poller 選中候選(create_or_resume) | Ticket + profile + store session | attempt 執行 + envelope + session/journal 更新 | poller·gate → approval·workspace·inner_runner·contract·store |
| **inner_runner** | 實跑 claude -p / codex exec 一個 attempt(看門狗/killpg/native resume) | dispatcher 呼叫 | prompt、workspace、session_id | raw 結果 + cost + session_id | dispatcher → claude/codex CLI |
| **workspace/isolation** | template→workspace instance provision(不變 id 綁 cwd)+ 隔離 | dispatcher 首次 fork | profile.template + issue_id | workspace 路徑 | dispatcher → 檔案系統 |
| **contract** | envelope 結構化契約 + grader 三態判定(證據型停止) | attempt 結束 | raw agent 輸出 | envelope + outcome(SUCCESS/FAILURE/UNKNOWN) | dispatcher·inner_runner → store |
| **approval** | 起點審批 / triage 閘(寫 plan 進 description、等 human 段 agent_name;可 decline) | dispatcher fork 前(require_approval / 全域 triage) | Ticket.description + profile | proceed/awaiting/reprompt + description 寫入 | dispatcher → sections·jira_source·store |
| **scoring(ScoreGate)** | HIL(End) 人評分(seed score 佔位、讀 score、催評) | poller 每輪對終態未評票 | description human 段 + session | human_score + journal | poller → sections·jira_source·store |
| **commands** | 指令核心 `apply_command`(run/retry/hold/stop/cancel/next):人走指令台表單、自動化走 REST | form_server / control_api 呼叫 | issue_id + cmd + by(email) | 指令效果(解 pending/換手…)+ journal | form_server·control_api → store·jira_source |
| **external(離手政策)** | assignee/status 政策:交人讓額度、回機器人 resume、外部關 Done=撤銷 | poller 偵測 status/assignee 變更 | Ticket 變更 + bot_id | inactive/abort/resume + journal | poller → store·jira_source |
| **sections** | description 三方分段(human/control/agent:<名>)parse/render + hash 防篡改 | approval/scoring/commands 讀寫 description 時 | description 文字 | Section 物件 / 組回 description | approval·scoring·commands → (純函式) |
| **store** | SQLite 狀態(ticket_watch/ticket_session)+ append-only journal | 各模組讀寫 | watch/session upsert、journal 事件 | 持久狀態 + 事件流 | 幾乎全部 → SQLite/檔案 |
| **control_api** | 內嵌 REST 控制面(pause/resume/reload/shutdown/evict/gen_transcript/status) | 人經 dashboard/curl POST(即時) | HTTP 請求 | poller 控制副作用 + status JSON | 人/dashboard → poller·store·transcript |
| **detail_server** | 唯讀觀測 dashboard:KPI/圖/表/狀態機/概念/REST /api/v1 | 瀏覽器請求 + 5s live 刷新 | store(runtime 目錄) | HTML/JSON | 人 → store(唯讀) |
| **transcript** | session → final HTML/打包(換手/交人/evict/close/被動按鈕) | finalize 事件 或 control gen_transcript | session 原始 log | final.html / transcript.tgz | dispatcher·commands·control_api → 檔案系統 |
| **retention** | workspace 回收(過期/終態,釋放磁碟) | poller 週期(約每 240 輪 ≈ 每小時) | store session + profile 保留策略 | 回收 workspace + journal | poller → 檔案系統/store |

### 2.1 比喻:label = 入場券,profile = 該票用哪個 agent(鎖在 session)

兩件常被搞混、但屬**不同階段**的事:
- **label(Jira 票上的標籤)= 入場券**:poller 只會**撿有命中 route 的 label 的票**去派工
  (`routes[].when.labels` → `on_match: create_or_resume`)。沒有對到 route 的 label = 進不了場,
  票再有效也不會被跑。**建票的那一刻決定要不要給這張入場券**(CR→Jira bridge / job / 人)。
- **profile = 這張票要用哪個 agent**:進場後,由 routing 推導或 triage(select)決定,並**寫進
  `ticket_session.profile`(鎖定,resume 不重選)**。route 只是初步指定,session 的 profile 最終為準。

一句話:**label 管「進不進得來」,profile 管「進來後誰來做」**。(本文件與程式一律用「鎖定/
寫入 session 的 profile」描述,不用 "pin" 這個詞。)

## 3. HIL 生命週期(6 態 + 概念終點)

`todo → running ⇄ queued`,人介入時進 `HIL(Middle)`(過程中等人)或 `HIL(End)`
(終點評分),外部撤銷/交接/**triage 判不出**進 `aborted`,人關 Jira 後 `closed`(概念終點,
離開 jql)。`success/failure/unknown` 是 **HIL(End) 的結果屬性**,不是頂層狀態。

### 3.1 狀態是「推導」的,沒有 state 欄(重要)

**DB 沒有 `state` 欄**;6 態由 `detail_server.canonical_state()` 從**互相正交的原始欄**
唯讀映射:`outcome`(SUCCESS/FAILURE/**ABORTED**/UNKNOWN/None)、`pending_reason`、
`queued`、`inactive`、有無 session。優先序:**aborted > hil_end(終態評分)> hil_middle
(pending 原因)> queued > hil_middle(inactive/交人)> running**;無 session = todo。

**為何不加 state 欄**:行為邏輯(dispatcher / gate / ScoreGate)讀的是**原始欄**(如
`outcome in (SUCCESS,ABORTED)` 判終態、gate 用 outcome/pending/queued/inactive 算 in-flight);
若再加一個權威 `state` 字串 = **兩個真相來源** → 雙寫漂移,正是 D6/單一 writer 要避免的
race。`canonical_state` 只是**讀模型**(dashboard / `/api/v1/tickets` 用),不參與行為。要看
state 就查 API/dashboard,不必動 DB。範例:triage 判不出 → 寫 `outcome=ABORTED` +
`profile=notfound` → 推導成 `aborted`(理由由 `profile=notfound` + journal `aborted` 得知)。

完整轉移圖見 `/concepts` 頁與 [lifecycle.md](lifecycle.md);開發細節見 [開發者手冊](../developer-guide.md)。

## 4. agent↔agent 交接:兩種機制,怎麼選?

**觸發點(W10.3)**:人在 **HIL(End) `score_and_close`** 或 **HIL(Middle) `decision`**
一次性表單選「改派下一棒」(`close_decision=handoff` / `next_step=handoff`),再選
`handoff_kind`(next / base)+ 下一棒 profile(下拉,候選=載入的全部 profile)+ 交接
prompt。也保留人在指令台下 `next` / agent 自發(envelope `status=handoff`)的同票換手。

| | **同票換手(next)** | **跨票換手(base)** |
|---|---|---|
| 做法 | 同一張 Jira,重置 session、鎖定新 profile | **系統**在 agent 自己 project 建新票(`create_ticket`,一步完成)、預建其 session(鎖定新 profile + `base_ref` 指回本票),本票收成 ABORTED(交接) |
| 脈絡 | 全留在同一票(留言/description/人類指示 → 新 TICKET.md) | dispatcher 於新票首次佈建後複製 base 的 `TICKET.md`+最後 envelope 進 `ws/BASE_<key>/` + human 指示段前置指路 |
| 舊工作 | 同票由新 profile 重新開始(session 重置=session_id None/attempts 0,**非** native resume);workspace 重新 provision 為新 profile 的 template | 新票重新開始,但帶 base 脈絡 |
| 開新票? | 否 | 是(**系統建**,非人手建;沿用本票 labels → 新票走同 route) |
| **適合場景** | 小幅換手、**同一件事繼續**、換 profile 但引擎/脈絡相容 | 換**引擎**、重開、跨專案、人策展重啟、任何要「乾淨重來但保留前輪脈絡」的泛化場景 |

**怎麼選(給人的判斷準則)**:
- 想「就地把這件事交給另一個 agent/profile 繼續」→ **同票換手**。
- 想「這條路走不下去了,換個引擎/從頭來過,但別讓新 agent 從零摸索」→ **跨票換手**。
- 跨引擎(claude↔codex)因 session 格式不同無法 native resume,通常走 **跨票換手**。
- 資料不完整(沒選 kind/profile)→ fail-safe **降級為續跑原 agent**,不硬失敗(見 `handoff_invalid`)。

### 4.1 跨票換手(base)spec(W10.3,`hil._do_handoff` + `dispatcher._inject_base`)

- **觸發**:HIL 表單選 `handoff_kind=base` + 下一棒 profile + 交接 prompt。源票術語稱
  **base(基底票)**。人的選擇同時寫進本票 description human 段(hash 保護)供稽核。
- **系統建新票 + 預建 session**(`hil._do_handoff`,不需人手建 Jira):
  1. `create_ticket` 在同 project(= 本票 key 前綴)建新票,summary=`[base:<key>] …`、
     description 含 `base: <key>` + 交接指示、**沿用本票 labels**(→ 新票走同 route 被撿);
  2. 預建新票 `ticket_session`:鎖定選定 profile、`workspace="(handoff)"` 哨值、
     `base_ref=<本票 issue_id>`(標記待注入 base 脈絡);
  3. 本票 session 收成 **ABORTED**(交接出去=終態,**不算 failure**,失敗率 KPI 才誠實;
     HIL 時本票非執行中,不需 killpg)。兩票互貼留言可於 dashboard 對照。
- **脈絡注入**(`dispatcher._inject_base`,新票下一輪首次佈建後,一次性):
  1. 解析 `base_ref` → 複製 base 的 `TICKET.md` + 最後一個 envelope 進 `ws/BASE_<key>/`,
     寫 `HANDOFF.md` 指路(含 base 觀測頁連結);
  2. 往 human 指示 sidecar 追一行指向 `BASE_<key>/`,並立刻刷新 TICKET.md → agent 首跑
     即在「人類指示」段看到「先讀 BASE_ 前輪脈絡再續做」;
  3. 注入後清 `base_ref`(之後 resume 不重注)+ journal `base_injected`。
- **觀測**:journal `handoff(kind=base, new_ticket, via=hil)` + `base_injected(base, dest)`;
  事件語意見 [observability.md](observability.md)。
