<!--
出處:使用者原創研究,Google Docs
「Jira 驅動的 Agent Harness — 選型研究報告與設計 Prompt」v5(2026-08-02)
原文: https://docs.google.com/document/d/1rMtWcCFY5gEWFQdbyogirG49iqP4Fmj4sPGQ6jF1aHk/
本副本 2026-08-03 匯入(markdown export),並做最小去識別化:
  內部 git host → <internal-git>;內部網域 → <internal-host>;
  Jira project key PROJ1/PROJ2 → PROJ1/PROJ2;人名 → <lead>。
  其餘內容(含工具名稱)未動。與 Google Docs 原文有出入時以原文為準。
整合分析(× ARCP 實測對照): 2026-08-jira-harness-integration.md
-->
# Jira 驅動的 Agent Harness — 選型研究報告與設計 Prompt

> **版本**：v5（2026-08-02）— 新增 §10 KPI（效率／效益／debuggability／traceability \+ Goodhart 防護） **文件用途**：本文同時是研究結論與下一次 design/code session 的起手 prompt **時效警告**：所有引用的 issue 與版本狀態變動極快，動工前請逐條重新確認

---

## 0\. 一句話結論

**用 OpenHands SDK 當 harness 骨架，用 `claude -p --bare` / `codex exec` 當執行單元，不走 ACP。** Outer loop 管 ticket 生命週期與人工介入，inner loop 管單次 workspace+skill 任務，兩者都用 YAML 設定。 Session 的 source of truth 永遠是自己的 event log，不是 CLI 的 session 檔。

---

## 0.1 決策紀錄（C1–C4 已定案）

| \# | 張力 | 決策 | 剩餘風險 |
| :---- | :---- | :---- | :---- |
| **C1** | Graph 形式化程度 | Route 監看 **new ticket \+ comment 變動**；在 Jira 上新增 **`Agent Status` 自訂欄位**與 **`Agent Link`**（指向 detail 網頁）。Routing 只選 profile、不描述步驟 | YAML 出現 `steps:` 就是越界，需 code review 護欄 |
| **C2** | 狀態機歸屬 | **欄位所有權模型**：workflow state 由 assignee 寫（agent 在工作時就是 assignee）；`Agent Status` **只有 harness 能寫**；人透過 **comment** 下指令。→ 不再需要雙狀態機對帳 | 這是**慣例**不是技術保證，Jira permission scheme 必須實際設定 |
| **C3** | workspace 與 session 的建立/續用 | 自建 **`issue_id → (workspace, cli_session_id)` 對映表**（主鍵用 Jira **數字 issue id**，跨 project move 不變；`ticket_key` 僅供顯示）；查得到就 resume，查不到就新建 workspace。skill 由 YAML 決定，worktree 由 agent 決定 | workspace 存在但髒掉/session 死掉 → 需 health check \+ 三層 fallback |
| **C4** | AionUi 的角色 | **列為 ACP 路徑的對照實作**，不作為生產依賴。用來驗證「ACP 買到什麼 / print mode 少了什麼」 | 對照歸對照，別讓它變成 production 相依 |

### C1 補充：滑坡風險仍在

v1 引用的決策表第 7 條說的是「**任務分解圖**」——「這種 ticket 要先做 A 再做 B 再平行做 C」那種。那是對工作內容的假設，工作類型多樣時必然過早。

Outer loop 的 ticket 生命週期不同：**它是既有系統（Jira）的鏡像，不是我們對工作的猜測**。Jira 狀態機已經存在、已經穩定、與任務類型無關。形式化一個外部既有事實是安全的。

**但滑坡風險是真的**：YAML regex 路由很容易從「路由到哪個 profile」長成「這類 ticket 要跑哪幾個步驟」。一旦 YAML 裡出現 `steps:` 或 `then:` 這種欄位，你就在寫任務分解圖了。

> **護欄（建議寫進 code review checklist）** Outer loop 的 YAML 只能決定 **(a) 這張 ticket 歸哪個 profile、(b) 什麼時候該接管、(c) 什麼時候該交還給人**。 一旦出現「先做 X 再做 Y」的序列描述，就是越界，該退回 inner loop 由模型自己決定。

---

## 1\. 目標架構

### 1.1 雙層迴圈

╔═══════════════════════════════════════════════════════════════╗

║ OUTER LOOP  (Graph 層 — ticket 生命週期)                       ║

║                                                               ║

║  Jira poll/webhook                                            ║

║      │                                                        ║

║      ▼                                                        ║

║  比對 routing rules (YAML regex)                              ║

║    · summary  · assignee  · comments  · state                 ║

║      │                                                        ║

║      ▼                                                        ║

║  決策：create session / resume session / 交還人工 / 忽略        ║

║      │                                                        ║

║      ▼                                                        ║

║  ┌─────────────────────────────────────────────────────────┐ ║

║  │ INNER LOOP  (Harness 層 — 單次 workspace+skill 任務)     │ ║

║  │                                                         │ ║

║  │   建 workspace (YAML template) → 注入 skills (YAML)      │ ║

║  │        │                                                │ ║

║  │        ▼                                                │ ║

║  │   claude \-p \--bare ...  (agent 自決是否開 worktree)      │ ║

║  │        │                                                │ ║

║  │        ▼                                                │ ║

║  │   確定性驗證 (build / test / lint / schema)             │ ║

║  │        │                                                │ ║

║  │   ┌────┴────┬──────────┐                                │ ║

║  │  PASS     FAIL      UNKNOWN                             │ ║

║  │   │         │          │                                │ ║

║  │   │    有界重試      立即上報                             │ ║

║  │   ▼         └──▶ (回到 CLI 呼叫)                         │ ║

║  └───┼─────────────────────────────────────────────────────┘ ║

║      │                                                        ║

║      ▼                                                        ║

║  回寫 Jira 狀態 → 判斷是否 close / pending / 續下一輪          ║

╚═══════════════════════════════════════════════════════════════╝

**分工鐵律**

|  | Outer Loop | Inner Loop |
| :---- | :---- | :---- |
| 對應層 | Graph（流程） | Harness \+ Loop（環境 \+ 回饋） |
| 週期 | 分鐘～天（跟著 ticket 走） | 秒～分鐘（跟著一次 CLI 呼叫走） |
| 觸發 | Jira 事件、排程輪詢、人工留言 | outer loop 派工、驗證失敗重試 |
| 終止 | Ticket Closed / Cancelled | 證據通過、達重試上限、預算耗盡、UNKNOWN |
| 誰能中斷 | **人**（Jira 上任何動作） | orchestrator（timeout / budget） |
| 設定檔 | `outer_loop.routes[]` | `inner_loop.profiles[]` |
| **禁止** | 描述任務步驟序列 | 改寫 Jira 狀態 |

### 1.2 三層對應（修訂）

| 層 | 本系統中是什麼 | 誰負責 |
| :---- | :---- | :---- |
| **Harness**（管環境） | workspace 建立、skills 注入、CLI 呼叫封裝、權限與密鑰、worktree 能力、trace 落地、預算控制 | inner loop |
| **Loop**（管回饋） | **兩層**：inner \= act→verify→retry（證據驅動）；outer \= ticket 級的推進與交還 | 兩者 |
| **Graph**（管流程） | ticket 生命週期狀態機 \+ routing 規則 | outer loop |

> 三層架構原文說 loop 是 **"a stack of loops"**，不是一個魔法 while——雙層迴圈正是這個意思，與 v1 沒有矛盾，只是把 v1 一句帶過的部分展開了。

### 1.3 Jira 欄位所有權模型（C2 決策）

**核心原則：每個欄位只有一個 writer。** 這樣就不需要雙狀態機對帳。

| 欄位 | Writer | 更新頻率 | 用途 |
| :---- | :---- | :---- | :---- |
| **Workflow state**（New/Backlog/In Progress/Pending/Resolve/Close/Cancelled） | **當下的 assignee**（agent 工作時 agent 就是 assignee） | 低（每 ticket 數次） | 人類可見的粗粒度進度 |
| **`Agent Status`**（自訂欄位） | **只有 harness** | 中（每 attempt 一次） | `IDLE` / `PREPARING` / `RUNNING` / `VERIFYING` / `PENDING:<reason>` / `DONE` / `ABORTED` |
| **`Agent Link`**（自訂欄位，URL） | **只有 harness** | 一次（建 session 時） | 指向 agent detail page，見 §4.7 |
| **Comments** | 人 **與** harness 皆可 | 依需要 | **人 → agent 的唯一指令通道** |
| **Assignee** | 人 | 罕見 | **改走 \= 隱含撤銷授權**，見 §6-10 |

**Workflow state 對映**

| Jira 狀態 | Agent Status | 誰觸發轉移 |
| :---- | :---- | :---- |
| **New** | *(不碰)* | 人建立 |
| **Backlog / To Do** | `IDLE` | 人；harness 命中 routing 後才認領 |
| **In Progress** | `PREPARING` → `RUNNING` ⇄ `VERIFYING` | harness（認領時同步設 assignee \= agent） |
| **Pending** | `PENDING:<reason>` | harness 或人（透過 comment） |
| **Resolve** | `DONE` | harness（證據全通過） |
| **Close** | `DONE` | 人 |
| **Cancelled** | `ABORTED` | 人 → harness 必須立即中斷 inner loop |

> **重要提醒**：「人不能改 state」是**流程慣例**，不是技術保證。 要真的擋住，得在 Jira 設定 **workflow transition conditions**（限 assignee）與 **field configuration**（`Agent Status` / `Agent Link` 設為 read-only 或限特定角色）。 **沒設定 \= 只是口頭約定**，第一次有人手動拖看板就破功。§6-10 的偵測邏輯仍然必須實作，當作最後一道防線。

**Pending 的三種語意**（解除條件不同，必須分開）：

| 觸發原因 | 誰能解除 | 建議標記 |
| :---- | :---- | :---- |
| 需要人工決策（設計選擇、風險核可） | 人 | `pending:human-decision` |
| 外部阻塞（等 CI、等別的 ticket、等權限） | 自動偵測或人 | `pending:external` |
| **UNKNOWN**（無法證明副作用是否發生） | **只能人** | `pending:unknown` |

> `pending:unknown` 絕對不可自動解除。這是抄 Hermes 的三態設計：無法證明就不猜。

---

## 2\. 候選比較（v2：含 Debug/Trace 與 AionUi 重評）

### 2.1 總表

| 維度 | OpenHands SDK | Hermes Agent | Goose | AionUi |
| :---- | :---- | :---- | :---- | :---- |
| 定位 | SWE agent 平台 / SDK | 持久個人 agent | vendor-neutral 終端 agent | **桌面 GUI 前端** |
| 語言 / 授權 | Python / MIT | Python / MIT | Rust / Apache-2.0 | TS+Electron / Apache-2.0 |
| **執行隔離** | **Workspace 抽象：Local / Remote / Docker，agent 程式碼不變** | in-process thread；Kanban 有 worktree-per-task | 本機 | 直接在桌面上跑，無沙箱 |
| **Resume** | event-sourced：`base_state.json` \+ 重放 events，自動偵測未完成對話續跑（persist \<1ms，recovery \<20ms） | profile-local `state.db`，`--continue` / `--resume <id>` | SQLite 具名 session，`--resume --name`（不存在時警告並新建） | client 端 history；ACP context 在 CLI 那邊 |
| **Handoff** | 有 spawn sub-agent 原語，協調機制論文自承未完成 | **最完整**：`delegate_task(_async)`，spawn/check/steer/collect/cancel/list | `summon` extension，隔離 Agent 實例 | 無文件化原語 |
| **Debug / Trace** | **決定性重放**；REST `/trajectory`；trajectory-visualizer（**無 LICENSE**） | **每子代一份即時 transcript（`tail -f`）**；超時 dump 全執行緒堆疊；stall monitor | TUI diff viewer、chat history search | 對話歷史、檔案預覽 |
| 故障語意 | success / error | **SUCCESS / FAILURE / UNKNOWN 三態** | 具名 session 缺失→新建 | — |
| **人工介入 (HITL)** | 有 approval 機制，需自建 UI | 逐工具核可 \+ 多平台 gateway（Telegram/Slack/…） | 逐步核可 | **權限閘門 \+ 檔案預覽 \+ IM 遙控（現成 GUI）** |
| 適配「harness 骨架」 | ★★★★★ | ★★★ | ★★ | ★ |
| 適配「HITL 前端」 | ★★（Agent Canvas 可用但為對話設計） | ★★★★（IM 通道很適合 pending 通知） | ★★ | ★★★★（GUI 最完整）**但綁 ACP** |

### 2.2 AionUi 重新評估（v1 評 ★1，v2 分角色重評）

**v1 的結論在「當 harness 骨架」這個角色下仍然成立**：擴充點是給非工程使用者的 markdown assistant 與 skills 目錄，深度客製會直接撞到 Electron 主程序；535 open issues / 138 open PRs；無沙箱抽象。**不要 fork 它當骨架。**

**但 outer loop 引入 human-in-the-loop 之後，它多了一個合理角色**：作為 pending 狀態的操作台。它現成提供對話歷史、專案（工作目錄）管理、**權限閘門**、檔案預覽。

**C4 決策：AionUi 作為 ACP 路徑的對照實作，不作為生產依賴。**

它**用 ACP 驅動 Claude Code / Codex CLI**，正好是我們選擇不走的那條路。與其純靠 issue 推論，不如裝一套實測，把「ACP 買到什麼、print mode 少了什麼」變成可驗證的事實。

**值得對照的四個問題**

| 問題 | 為什麼要看 | 對我們的意義 |
| :---- | :---- | :---- |
| 執行中的權限閘門 UX 長什麼樣 | ACP 唯一真正買到的能力是 `session/request_permission` | 如果 pending 流程需要「執行中批准」，print mode 做不到，得改設計 |
| session 與 workspace 怎麼綁 | ACP 以 cwd 為 key，AionUi 的專案管理如何處理 | 對照我們的 `ticket_key` 對映表是否漏了什麼 |
| **CLI 子行程死掉時它怎麼表現** | Zed \#55501 那種「session 活著但壞掉、無錯誤訊號」 | **最有價值的一項**——直接驗證我們的 liveness probe 設計是否必要且足夠 |
| 多 workspace 併行時的行為 | 我們一次會有多張 ticket 在跑 | 併發模型的參考 |

**驗證方式（P4 的一個 spike，1–2 天）**：裝 AionUi，接一份 sandbox repo，故意觸發 rate limit 與 kill 子行程，記錄它的表現與我們 print mode 的差異。**結論寫回本報告，不引入相依。**

**HITL 前端則走另一條路**：不做獨立 GUI，改用 **Jira comment \+ `Agent Link` 指向自建 detail page**（§4.7）。理由是 Jira 本來就是 ticket 的家，人不該為了看 agent 狀態換一個介面。

---

## 3\. 選型結論

### 3.1 骨架：OpenHands Software Agent SDK

1. **Workspace 抽象不可妥協**——agent 生成的程式碼必須能進 container，且本地開發與遠端隔離同一份程式碼  
2. **event-sourced \+ 決定性重放**是唯一能支撐「跨重啟 resume 到 ticket close」的架構  
3. Agent Server 提供 REST/WebSocket，接 dashboard 不用重寫  
4. MIT，企業內部使用無授權障礙

**限制**：多 agent 協調未完成（我們不做多 agent，不衝突）；`OH_SECRET_KEY` 未設定時重啟後 secrets 解密失敗變 None（部署清單必列）。

### 3.2 執行單元：headless CLI，不走 ACP

ACP 存在的理由是「借用訂閱制 CLI 的登入態」。我們走公司 API 合約，用不到好處，卻要承擔全部故障面：

| \# | ACP 故障模式 | 佐證 |
| :---- | :---- | :---- |
| 1 | `session/load` 不是逐字稿傳輸通道，而是請求 ACP server 串流**它自己儲存裡**的 session；協定**沒有 `session/save`**，也沒有 client 提供 history 的機制。每個 server 以 cwd 為 key 存 session | OpenHands \#14260 |
| 2 | 用量上限觸發 → CLI 以 143/SIGTERM 退出 → **ACP session 維持在壞掉但「活著」的狀態**；後續 prompt 被接受、輸入框清空、無串流、**無錯誤訊號**，永久卡死 | Zed \#55501 |
| 3 | Client 在斷線/閒置回收/重啟後呼叫 `newSession` 而非 `resumeSession`，context 全失 | T3 Code \#2838 |
| 4 | Agent 宣告 `loadSession` capability 但 `session/load` 後不送回放通知 → 還原不出歷史 | Cursor CLI |
| 5 | ACP **沒有 session 列舉 API**，client 得自己追蹤 session ID（仍是 RFD） | ACP RFD: Session List |

**補充**：Hermes 的 ACP 是 **server 方向**（等 IDE 連進來）；一般化 ACP client 的提案已被標為 **P4「盡力而為，不做承諾」+ `needs-decision`**，短期不會通。

### 3.3 不引入 Hermes 依賴，但抄它三個設計

Hermes bundled 的 `skills/autonomous-ai-agents/claude-code`（v2.2.0, MIT）是一份**寫得很好的 SKILL.md，不是程式碼整合**——實際執行靠通用 `terminal()` 工具。**當文件讀，把 flag 組合抄走。**

要抄的三個設計（OpenHands 沒有現成的）：

1. **UNKNOWN 三態**——擁有者行程消失時記為 UNKNOWN，不猜  
2. **先落地再投遞**——完成事件先寫入持久層再發布佇列；重啟後待決事件還原並走同一套 ownership 檢查  
3. **超時 dump 全執行緒堆疊**——因為「卡在巢狀 helper thread」與「慢速 provider」從外面看完全一樣

---

## 4\. 實作規格

### 4.1 Outer Loop：YAML 路由設定

version: 1

outer\_loop:

  source:

    type: jira

    poll\_interval\_sec: 60

    jql: 'project in (PROJ1, PROJ2) AND status in ("Backlog", "In Progress", "Pending")'

    watch:                                     \# 監看哪些變動（C1 決策）

      \- new\_issue                              \# 新建 ticket

      \- comment\_added                          \# 留言新增 → 人機指令通道

      \- comment\_edited

      \- assignee\_changed                       \# 改走 \= 隱含撤銷，見 §6-10

      \- status\_changed                         \# 偵測 out-of-band（尤其 Cancelled）

    comment\_watermark: true                    \# 必開：記錄每張 ticket 最後處理的 comment id

                                               \# 否則每次輪詢都會重放舊留言

  \# harness 專屬欄位（只有 harness 能寫）

  agent\_fields:

    status\_field: 'customfield\_10101'          \# Agent Status

    link\_field:   'customfield\_10102'          \# Agent Link

    detail\_url\_template: 'https://<internal-host>/tickets/{ticket\_key}'

    write\_policy: coarse                       \# coarse | verbose

                                               \# coarse \= 只在狀態轉移時寫，不是每 attempt

                                               \# 細節一律留在 detail page，避免 Jira rate limit

  \# 併發控制（D10）——兩個獨立的閘門

  concurrency:

    max\_running: 8                           \# 同時執行中的 ticket（資源綁定）

    max\_awaiting\_close: 3                    \# Resolve 但未 Close 的張數（審查頻寬綁定）

                                             \# 達上限 → 新 ticket 排隊，不派工

    per\_profile:

      fuzzing: 4

      triage: 8                              \# 唯讀、便宜、快，可以開高

    queue\_policy: fifo                       \# 超過上限一律排隊，不拒絕

  \# 對映表（D9）

  store:

    type: sqlite

    path: /var/lib/agent-harness/mapping.db

    journal\_mode: WAL                        \# \-wal / \-shm 必須與主檔同一持久 volume

    txn\_mode: IMMEDIATE                      \# 「查不到就建立」必須原子化，deferred 會建出重複 workspace

    backup:

      cron: '\*/15 \* \* \* \*'

      dest: /backup/agent-harness/

  \# 人 → agent 的指令（comment 語法）

  commands:

    \- pattern: '(?i)^@agent\\s+run\\b'

      action: create\_or\_resume

    \- pattern: '(?i)^@agent\\s+(stop|hold|pause)\\b'

      action: handoff\_to\_human

    \- pattern: '(?i)^@agent\\s+reject\\b\\s\*(?P\<reason\>.\*)'

      action: reject\_and\_rework            \# D7 配套：Resolve 被打回

                                           \# → Jira 回 In Progress、Agent Status 回 RUNNING

                                           \# → reason 當作下一輪 feedback 餵進 CLI

    \- pattern: '(?i)^@agent\\s+retry\\b'

      action: reset\_attempts\_and\_resume

    \- pattern: '(?i)^@agent\\s+cancel\\b'

      action: abort

    \- pattern: '(?i)^@agent\\s+profile\\s+(?P\<profile\>\\w+)'

      action: override\_profile

    require\_commenter\_in: \['<lead>', 'team-leads'\]   \# 誰的留言算數

    ack\_with\_comment: true                        \# 執行後回一則確認，避免人不知道有沒有生效

  \# 規則由上而下第一個 match 者勝出

  routes:

    \- name: fuzz-harness-generation

      when:                                    \# 所有列出的條件皆須成立 (AND)

        summary:  '(?i)\\b(fuzz|harness|libfuzzer)\\b'

        assignee: \['agent-bot'\]

        state:    \['Backlog', 'In Progress'\]

      profile: fuzzing

      on\_match: create\_or\_resume               \# create\_or\_resume | resume\_only | notify\_only

    \- name: human-invoked-triage

      when:

        comments: '(?i)@agent\\s+triage'        \# 只看最新 N 則，見 lookback

        state:    \['New', 'Backlog', 'Pending'\]

      comments\_lookback: 5

      profile: triage

      on\_match: create\_or\_resume

    \- name: hands-off

      when:

        labels: \['no-agent'\]

      on\_match: ignore

  \# 交還人工的條件（任一成立即 → Jira: Pending）

  handoff\_to\_human:

    on\_unknown: true                           \# 永遠成立，不可關閉

    on\_max\_attempts: true

    on\_budget\_exceeded: true

    on\_comment: '(?i)@agent\\s+(stop|hold|pause)'

    notify:

      \- type: jira\_comment

      \- type: slack

        channel: '\#agent-pending'

  \# 人在 Jira 上 out-of-band 改狀態時的處理

  external\_change\_policy:

    on\_cancelled: abort\_immediately            \# 立即殺掉 inner loop

    on\_state\_moved\_back: pause\_and\_reconcile

    reconcile\_interval\_sec: 30

**設計要點**

- `when:` 底下所有欄位是 **AND**；同一欄位的陣列值是 **OR**  
- regex 一律用 Python `re` 語法並在載入時預編譯，設定錯誤要在啟動時就炸，不要等到 runtime  
- `on_match: notify_only` 提供「先觀察不接管」的灰度上線路徑  
- **`external_change_policy` 是 v2 新增的必要項**——人隨時可能在 Jira 上動手，harness 必須讓步

### 4.2 Inner Loop：YAML workspace \+ skill 設定

inner\_loop:

  profiles:

    fuzzing:

      workspace:

        template: repo-checkout                \# repo-checkout | empty | copy-from

        repo: 'git@<internal-git>:proj/core.git'

        ref: main

        folder: 'tickets/{ticket\_key}'         \# 相對於 harness root，路徑穩定不變

        worktree\_policy: agent\_decides         \# agent\_decides | always | never

      skills:

        \- path: skills/libfuzzer-harness.md

        \- path: skills/gtest-conventions.md

        \- path: rules/security.md

        inject\_as: claude\_skills               \# → .claude/skills/ \+ .claude/rules/

      context\_files:

        \- render: templates/TICKET.md.j2       \# 由 ticket 欄位渲染

          to: TICKET.md

      cli:

        engine: claude                          \# claude | codex

        bare: true                              \# 跳過 hooks/plugins/MCP/CLAUDE.md，要求 ANTHROPIC\_API\_KEY

        model: sonnet

        max\_turns: 25

        max\_budget\_usd: 5.00

        allowed\_tools: \[Read, Edit, Write, Bash\]

        fallback\_model: haiku

        json\_schema: schemas/task\_result.json   \# 強制回報 worktree 路徑、變更檔案、自評結論

      verify:                                   \# 確定性檢查，依序執行，全過才算 PASS

        \- name: build

          cmd: 'cmake \--build build \-j'

          timeout\_sec: 900

        \- name: harness-compiles

          cmd: './scripts/check\_harness.sh'

        \- name: no-new-warnings

          cmd: './scripts/diff\_warnings.sh'

      loop:

        max\_attempts: 4

        feedback\_mode: evidence\_only            \# 只回傳失敗證據摘要，不貼整份 log

        on\_unknown: pending                     \# 不可改為 retry

    triage:

      workspace:

        template: empty

        folder: 'tickets/{ticket\_key}'

        worktree\_policy: never

      skills:

        \- path: skills/triage.md

      cli:

        engine: claude

        model: haiku

        max\_turns: 8

        max\_budget\_usd: 0.50

        allowed\_tools: \[Read\]                   \# 唯讀

      verify:

        \- name: schema-valid

          cmd: './scripts/validate\_triage.sh'

      loop:

        max\_attempts: 2

        on\_unknown: pending

**`worktree_policy` 三態解決了 C3 的邊界問題**：

- `agent_decides`（預設，維持 v1 立場）— YAML 只給能力，SKILL.md 裡寫判準（例如「會改超過 N 個檔案或需跑破壞性測試 → 開 worktree」），agent 自決並在結果 JSON 回報實際路徑  
- `always` / `never` — 特定 profile 明確已知時才用，屬於例外

### 4.3 CLI 呼叫契約

claude \-p "\<task prompt\>" \\

  \--bare \\                                 \# 跳過 hooks/plugins/MCP discovery/CLAUDE.md；要求 ANTHROPIC\_API\_KEY，跳過 OAuth

  \--append-system-prompt-file ./TICKET.md \\

  \--output-format json \\

  \--json-schema "$SCHEMA" \\

  \--allowedTools 'Read,Edit,Write,Bash' \\

  \--max-turns 25 \\

  \--max-budget-usd 5.00 \\                  \# 最低約 $0.05（system prompt 快取建立成本）

  \--fallback-model haiku                   \# 主模型過載自動降級（print mode only）

  \# cwd \= workspace folder

> ⚠️ `--bare` 跳過 CLAUDE.md 載入。要保留專案慣例就用 `--append-system-prompt-file` 顯式帶入，或不要 `--bare`。兩者擇一，別假設。

同時開一份 stream trace：

claude \-p "..." \--output-format stream-json \--verbose \--include-partial-messages \\

  \> traces/{ticket\_key}/{attempt}.ndjson

**Codex** 走 `codex exec`，旗標名不同——實作前跑 `codex --help` 逐一對齊，不要照抄 Claude 的旗標。

### 4.4 Session 狀態與 Resume

@dataclass

class TicketSession:

    issue\_id: int                     \# 主鍵：Jira 數字 id，跨 project move 不變

    ticket\_key: str                   \# 顯示用（PROJ2-123），會變，不可當鍵

    external\_ref: str | None          \# ClearQuest record id（bridge 用）

    jira\_state: str                   \# Jira 擁有，只鏡寫不推導

    harness\_state: HarnessState       \# 我們擁有

    profile: str                      \# 命中的 inner\_loop profile

    route\_name: str                   \# 命中的 outer\_loop route

    workspace: Path                   \# 絕對路徑，建立後永不變動

    worktree\_path: Path | None        \# agent 回報

    cli\_session\_id: str | None        \# 從 CLI JSON 結果鏡寫

    attempt: int

    max\_attempts: int

    budget\_usd\_spent: float

    outcome: Literal\["SUCCESS", "FAILURE", "UNKNOWN"\] | None

    pending\_reason: str | None        \# human-decision | external | unknown

    last\_event\_seq: int

    jira\_state\_seen\_at: datetime      \# 用於偵測 out-of-band 變更

**C3 決策：`issue_id`（Jira 數字 id）是主鍵，對映表決定 create 還是 resume**

> ⚠️ **不要用 `ticket_key` 當主鍵**。Jira 跨 project move 會改變 key（`PROJ2-123` → `AGENT-45`），數字 issue id 則不變。用 key 當鍵的話，任何一次 move 都會讓所有 workspace 與 session 失聯。

收到 ticket 事件

      │

      ▼

查 mapping\[issue\_id\]

      │

      ├── 查無 ──▶ 建 workspace → 注入 skills → 新 CLI session

      │              └─▶ 寫入 mapping，設 Agent Link

      │

      └── 查到 ──▶ workspace health check

                      ├── 健康 ──▶ resume（三層 fallback，見下）

                      └── 不健康 ─▶ 依 policy：repair / recreate / pending:human-decision

**Workspace health check（resume 前必做）**

- 路徑存在且可寫  
- git 狀態可讀（非 detached/衝突中/rebase 未完成）  
- 未提交變更量在門檻內（超過表示上次跑到一半死掉，可能需要人看）  
- `TICKET.md` 與目前 ticket 內容一致（ticket 被改寫過就要重新渲染）

>   
> 對映表是 **harness 自己的持久層**，不是 Jira 欄位。Jira 上只放 `Agent Link` 指過來。

**Resume 三層 fallback**

1\. 熱路徑：claude \-p \--resume \<cli\_session\_id\>      \# cwd 必須相同

      ↓ 失敗 / session 不存在

2\. 分叉：  claude \-p \--resume \<id\> \--fork-session    \# 保留歷史、新 ID

      ↓ 失敗

3\. 冷路徑：從自己的 event log 重建 bootstrap prompt

      \<\<RESUMED {ticket\_key} / attempt {n}\>\>

      \+ 先前輪次的角色標記訊息

      \+ 工具呼叫的壓縮摘要

      \+ 目前 workspace / git 狀態快照

**鐵律**

- CLI 的 session 檔**永遠不是 source of truth**；event log 才是  
- 每個 attempt 邊界鏡寫 `cli_session_id` \+ `workspace` 進持久層  
- 要保留 CLI 端 session，把資料目錄導到持久 volume：`CLAUDE_CONFIG_DIR` / `CODEX_HOME`

### 4.5 Trace 模型（四層對齊）

| 層級 | 內容 | 落地位置 |
| :---- | :---- | :---- |
| **L0 Ticket** | Jira 狀態轉移、routing 命中記錄、人工介入事件 | event log（`route_matched`、`handoff_to_human`、`external_change`） |
| **L1 Attempt** | harness 狀態轉移、預算累計、outcome 三態 | OpenHands event log（可重放） |
| **L2 Invocation** | CLI JSON 結果封套：`session_id` / `num_turns` / `total_cost_usd` / `subtype` | `traces/{ticket}/{attempt}.result.json` |
| **L3 Turn** | CLI 內部逐事件 NDJSON，含 `api_retry`（`rate_limit` / `billing_error`） | `traces/{ticket}/{attempt}.ndjson` |

**每個 L0/L1 狀態轉移必須帶上對應的 L2 檔案路徑。** 這是決策表第 6 條要的「與節點及轉移對齊的有狀態 trace」，也是區分「深度任務」與「卡死」的唯一方法。

**必做的可觀測性**（抄 Hermes）

- 每個執行中的 attempt 有一份可 `tail -f` 的人類可讀 log  
- liveness probe：偵測子行程已死但狀態未更新  
- 超時且零 API 呼叫時 dump **全執行緒**堆疊

### 4.6 Loop 規格：七要素 × 兩層

| 要素 | Inner Loop | Outer Loop |
| :---- | :---- | :---- |
| Trigger | outer loop 派工、前次 verify 失敗 | Jira webhook / 輪詢 / 人工留言 |
| Goal | 該 profile 的 `verify` 全部通過 | Ticket 進入 Closed 或 Cancelled |
| State & memory | event log \+ workspace 的 git 歷史 | `TicketSession` \+ Jira 狀態鏡像 |
| Action policy | `allowed_tools` \+ `max_turns` \+ `max_budget_usd` | routing 規則 \+ 允許的 Jira 狀態轉移集合 |
| **Evidence** | build / test / lint / diff / schema 驗證 | Jira 狀態本身 \+ 人工確認 |
| Feedback | 失敗證據的精簡可行動描述（`evidence_only`） | Jira comment |
| **Stopping rule** | 證據全過 / 達 `max_attempts` / 預算耗盡 / UNKNOWN | Closed / Cancelled / 人工 hold |

**Resolve 時的 comment 內容（D7 配套，必要）**

既然人只負責 Close，他打開 ticket 時需要的東西必須已經在 comment 裡：各項 `verify` 的通過結果、變更檔案清單、attempt 次數與累計成本、`Agent Link`。只寫「已完成」會讓 Close 變成無腦動作。

**要追蹤的指標**：Resolve → Close 的直接通過率。打回率偏高代表 `verify` 不夠強——該補確定性檢查，不是換模型。

> **Do not loop on confidence. Loop on evidence.** Agent 自稱「做完了」不是停止條件。`subtype: success` 也只代表 CLI 正常退出，不代表工作正確。

### 4.7 Agent Detail Page（`Agent Link` 的落點）

每張被接管的 ticket 有一個唯讀網頁，Jira 的 `Agent Link` 欄位指過來。**這取代了獨立 GUI 的需求。**

**最小內容（P2 就要有）**

| 區塊 | 內容 | 資料來源 |
| :---- | :---- | :---- |
| 摘要 | ticket\_key、profile、route\_name、Agent Status、目前 attempt / 上限、累計花費 / 預算 | `TicketSession` |
| 時間軸 | L0/L1 事件流：認領、每次 attempt 起訖、verify 結果、pending 觸發、人工指令 | event log |
| 每次 attempt | `subtype`、`num_turns`、成本、耗時、verify 各項通過與否 | L2 `.result.json` |
| Live log | 執行中的 attempt 可即時追（抄 Hermes 的 `tail -f` transcript） | L3 NDJSON |
| Workspace | 路徑、git 狀態、worktree 是否啟用、變更檔案清單 | health check \+ agent 回報 |
| Pending 詳情 | `pending_reason` 分類、觸發時的證據、解除條件 | event log |

**實作建議**：讀 OpenHands Agent Server 的 REST/WebSocket \+ 我們自己的 trace 檔案即可，不需要另一套資料庫。先做靜態渲染，live log 之後再加。

**認證與邊界（D8 決策）**

- **內網限制**，無登入  
- **純唯讀**——所有指令一律走 Jira comment。這頁不做任何會觸發動作的介面，讓認證強度只需對應「讀取」風險  
- ⚠️ **不要監聽 `0.0.0.0`**。內網保護常常是靠部署位置隱性達成的，多一張網卡或改一條防火牆規則就會無聲失效。綁明確網段或前置顯式 IP allowlist，讓限制是可 review 的  
- ⚠️ **無登入 \= 無稽核軌跡**。唯一來源是反向代理 access log——現在就打開並保留  
- 注意 trace 內含程式碼片段，**能看這頁 ≈ 能讀該 repo**；而 `Agent Link` 會隨 Jira 通知、匯出、截圖擴散

---

## 5\. 故障定位表（v3 客製化版）

| \# | 症狀 | 先查哪層 | 本系統的修法 |
| :---- | :---- | :---- | :---- |
| 1 | Agent 存取不到正確的檔案或工具 | **Harness / Inner** | `allowed_tools` 白名單、`--add-dir`、`workspace.template` |
| 2 | 跨 attempt / 跨重啟忘記進度 | **Harness / Inner** | event log 持久化、§4.4 三層 resume、`CLAUDE_CONFIG_DIR` |
| 3 | 第一次嘗試常常接近但不可靠 | **Loop / Inner** | 加強 `verify` 的確定性檢查，**不要**加 LLM 評審 |
| 4 | 宣稱完成但沒有證據 / 成功後還在跑 | **Loop / Inner** | 證據型停止規則 \+ `max_turns` \+ `max_budget_usd` |
| 5 | 多個步驟必須按受控順序執行 | **Graph / Outer** | ⚠️ 先確認真的需要，見第 7 條 |
| 6 | 多步流程中的失敗難以定位 | **Graph \+ Harness** | §4.5 L0↔L1↔L2↔L3 四層對齊的 trace |
| 7 | 工作類型太多樣，不適合固定流程 | **更簡單的 Harness** | 保持模型驅動控制；**routing 只選 profile，不描述步驟** |
| **8** | **ticket 被人改了狀態但 agent 還在跑** | **Graph / Outer** | `external_change_policy` \+ `status_changed` 監看 \+ 定期對帳 |
| **9** | **Pending 堆積、沒人處理** | **Graph / Outer** | `pending_reason` 分類 \+ 各自的 SLA 與通知路徑 |
| **10** | **YAML 改了但行為沒變 / 行為變了但沒人知道** | **Harness / Outer** | 設定版本化 \+ 啟動時 schema 驗證 \+ `route_matched` 事件記錄命中的規則名 |
| **11** | **同一則舊留言被反覆執行** | **Graph / Outer** | `comment_watermark` \+ comment id 去重；指令執行必須冪等 |
| **12** | **人下了指令但不知道有沒有生效** | **Graph / Outer** | `ack_with_comment` 回覆確認；不認得的 `@agent` 指令也要回一則說明 |
| **13** | **Agent 被改走 assignee 後仍在跑** | **Graph / Outer** | 監看 `assignee_changed`，改走視為隱含撤銷 → 立即 pending |
| **14** | **Jira 欄位更新太頻繁被 rate limit** | **Harness / Outer** | `write_policy: coarse`——只在狀態轉移時寫 Jira，細節留 detail page |
| **15** | **workspace 存在但上次跑到一半死掉** | **Harness / Inner** | resume 前的 workspace health check（§4.4） |

---

## 6\. 已知陷阱清單

**工作目錄**

1. **workspace 路徑一旦建立永不變動**。`--continue` 依 cwd 找最近 session。用臨時目錄 \= resume 必壞。

**Claude Code 旗標** 2\. `--max-turns` 僅 print mode 有效 3\. `--max-budget-usd` 最低約 $0.05，更低會直接錯誤 4\. `--bare` 跳過 OAuth，**要求 `ANTHROPIC_API_KEY`** 5\. `--json-schema` 需要足夠 `--max-turns`——Claude 得先讀檔才能產出結構化輸出 6\. Context 使用率 \>70% 品質下降、\>85% 幻覺風險大增 → 在 orchestrator 層控制單次任務範圍

**YAML / Outer loop** 7\. **regex 在載入時預編譯並驗證**，設定錯誤要在啟動時炸 8\. **`comment_watermark` 是必要的，不是最佳化**——沒有它，每次輪詢都會重放全部舊留言並重複執行指令 9\. **routing 與指令執行都必須冪等**——用 `issue_id` \+ `comment_id` 做去重鍵 10\. **「人不能改 state」是慣例不是保證**。要真的擋，得設 Jira workflow transition conditions（限 assignee）＋ field configuration（`Agent Status` / `Agent Link` 唯讀）。**即使設了，偵測邏輯仍要實作**——admin 權限、自動化規則、批次操作都可能繞過 11\. **assignee 被改走 \= 隱含撤銷授權**，必須當成停止訊號處理 12\. **`Agent Status` 用 coarse 寫入**——每個 attempt 都寫 Jira 會撞 rate limit 且製造噪音；細節放 detail page 13\. **`@agent` 指令要限制 commenter 白名單**，否則任何人都能指揮 agent 動公司程式碼 14\. **不認得的 `@agent` 指令也要回覆**，否則人以為下了指令其實沒生效

**Workspace / Session** 15\. **workspace 路徑穩定是 resume 的前提**，見第 1 條；對映表遺失等於全部 session 失聯，要納入備份 16\. **resume 前一定要跑 health check**，不要假設上次是乾淨結束的

**部署** 17\. **`OH_SECRET_KEY` 必須設定且不可變更**，否則重啟後 secrets 解密失敗 18\. Jira API rate limit——輪詢間隔、JQL 範圍、`write_policy` 三者要一起調 19\. Jira 自訂欄位 ID（`customfield_xxxxx`）在不同環境不同，**不要 hardcode**，用欄位名稱查詢後快取

**授權** 20\. `trajectory-visualizer`、`eval-monitor`、`agent-analysis` 等 repo **沒有 LICENSE 檔** \= all rights reserved 21\. 跑 SBOM / license scan（`pip-licenses`、ScanCode、FOSSA）交法務——MIT 是頂層，傳遞依賴才是風險

**時效** 22\. 引用的 issue 狀態變動極快（Hermes 兩個 patch 版之間約 2,789 commits / 44 萬行新增）。動工前逐條重新確認。

---

## 7\. 實作優先序

| 階段 | 內容 | 完成判準 |
| :---- | :---- | :---- |
| **P0** | Inner loop 單獨跑通：YAML profile → workspace → `claude -p --output-format json` → verify → 三態 outcome | 手動指定 profile 與 ticket 資料，能跑到 SUCCESS |
| **P0** | L2 trace 落地 \+ UNKNOWN 正確觸發 | 每次執行有 `.result.json`；kill 子行程會得到 UNKNOWN 而非 FAILURE |
| **P0** | Trace completeness CI 檢查（§10.5） | 每個結束的 attempt 四層 trace 齊全，缺任一層告警 |
| **P1** | `issue_id → (workspace, session_id)` 對映表（SQLite）+ health check \+ create/resume 分流 | 同一 ticket 第二次觸發會 resume 而非重建 |
| **P1** | Outer loop 讀 Jira：輪詢 \+ `watch` \+ routing regex \+ `notify_only` 灰度模式 | 真實 ticket 能被正確路由，但**不接管**，只記 `route_matched` 事件 |
| **P1** | Resume 三層 fallback \+ event log 持久化 | 中途 kill orchestrator，重啟後續跑同一 ticket |
| **P2** | Jira 欄位寫入：`Agent Status` \+ `Agent Link`（coarse policy） | Jira 上看得到粗粒度進度與連結 |
| **P2** | Comment 指令通道：watermark \+ 白名單 \+ 冪等 \+ ack 回覆 | 重複輪詢不會重放舊指令；下指令有回應 |
| **P2** | **Agent Detail Page**（靜態渲染即可） | `Agent Link` 點得進去，看得到 attempt 歷史與 verify 結果 |
| **P2** | `external_change_policy`：Cancelled / assignee 改走 → 立即中斷 | 人手動 Cancel 時 inner loop 在一個對帳週期內停止 |
| **P2** | Pending 三分類 \+ 通知路徑 | 三種 pending 各自有正確的解除條件 |
| **P3** | L3 stream trace \+ live log \+ liveness probe \+ 全執行緒超時 dump | 能區分「深度任務」與「卡死」 |
| **P3** | Docker Workspace 隔離 | agent 生成的程式碼在 container 內執行 |
| **P3** | Codex 第二執行單元 | 同一份 orchestrator 可切換 CLI |
| **P4** | **AionUi 對照 spike**（1–2 天） | 產出「ACP vs print mode」的實測差異報告，寫回 §2.2 |

**先不要做**：多 agent 編排、任務分解 graph、LLM 評審器、獨立 GUI。

> **Jira 設定是 P1 的前置作業，不是程式碼**：建 `Agent Status` / `Agent Link` 兩個自訂欄位、設 workflow transition conditions（限 assignee）、設 field configuration（兩個欄位對人類唯讀）。這件事要先跟 Jira admin 排期，不然 P2 會卡住。

---

## 8\. 給下一個 design/code session 的起手 Prompt

你要幫我實作一個 Jira 驅動的 agent harness。請嚴格遵守附帶的研究報告

（agent-harness-research-report.md）中的規格，特別是：

架構立場（不可推翻，除非我明確同意）：

\- 骨架用 OpenHands Software Agent SDK（Python, MIT）

\- 執行單元用 headless CLI print mode（claude \-p / codex exec），不使用 ACP

\- 雙層迴圈：outer loop 管 ticket 生命週期與人工介入，inner loop 管單次

  workspace+skill 任務。兩者都由 YAML 設定驅動

\- Jira 採欄位所有權模型：workflow state 由 assignee 寫、Agent Status 與

  Agent Link 只有 harness 能寫、comment 是人 → agent 的唯一指令通道。

  每個欄位只有一個 writer

\- 主鍵是 Jira 數字 issue id（不是 ticket key，key 會因 project move 改變）：

  查 mapping 表，查得到就 resume，查不到就建 workspace

\- Session 的 source of truth 是我們自己的 event log，不是 CLI 的 session 檔

\- outcome 是 SUCCESS / FAILURE / UNKNOWN 三態；UNKNOWN 一律進 pending:unknown

  且只能由人解除

\- Outer loop 的 YAML 只能決定「歸哪個 profile / 何時接管 / 何時交還」，

  不得出現任務步驟序列

從 P0 開始，一次只做一個階段，做完停下來讓我驗收：

P0 \= inner loop 單獨跑通 \+ L2 trace 落地 \+ 三態 outcome

實作時：

\- 先讀 §4.3 的 CLI 呼叫契約，用 \`claude \--help\` 與 \`codex \--help\` 逐一驗證

  旗標是否仍存在且語意相同，不要照抄報告裡的旗標名

\- 對映表用獨立 SQLite，所有 DB 存取包在 repository 介面後面（之後換 Postgres 只改一個檔案）；

  WAL 模式、\`BEGIN IMMEDIATE\` 交易、獨立備份

\- Source 層抽一個正規化的 Ticket 模型，Jira 是唯一實作（未來要接 ClearQuest bridge）

\- YAML 用 pydantic 定義 schema，啟動時驗證並預編譯所有 regex，設定錯誤要立即失敗

\- comment 指令必須有 watermark（記錄最後處理的 comment id）且執行冪等，

  用 issue\_id \+ comment\_id 做去重鍵

\- Jira 自訂欄位 ID 不要 hardcode，用欄位名稱查詢後快取

\- workspace 路徑必須絕對且穩定，寫成設定不要 hardcode

\- resume 前一定要跑 workspace health check，不要假設上次乾淨結束

\- 每個狀態轉移都要寫 event，且帶上對應的 trace 檔案路徑

\- 不要加 LLM 評審器；驗證一律用確定性檢查

\- 不要加 retry 之外的任何 loop

寫完 P0 後，用 §5 的故障定位表自檢一遍，告訴我哪幾條目前沒有覆蓋。

---

## 9\. 決策紀錄（全數定案）

| \# | 問題 | 決定 | 主要後果 |
| :---- | :---- | :---- | :---- |
| **D1** | Jira 與 harness 狀態關係 | 欄位所有權模型：每個欄位單一 writer | 不需要雙狀態機對帳 |
| **D2** | Outer loop YAML 權限深度 | 只決定 profile 綁定、接管時機、交還時機 | 不得出現步驟序列 |
| **D3** | Pending 解除權 | 三分類：`human-decision` / `external` / `unknown` | `unknown` 只能人解除 |
| **D4** | HITL 前端 | 不做獨立 GUI；Jira comment \+ `Agent Link` → detail page | AionUi 降為 P4 對照 spike |
| **D5** | Skill 決定權 | YAML 決定 skill，agent 決定 worktree | `worktree_policy: agent_decides` |
| **D6** | Jira 權限 | **專案隔離**——agent 只動專屬 project | 你多半就是該 project 的 admin，可在該 project 內自行設 transition condition \+ field config，等於就地取得 D6(a) 的效果 |
| **D6b** | Ticket 進場方式 | **直接在 agent project 開票**；之後另寫程式 monitor ClearQuest，再評估 bridge | 見下方三條約束 |
| **D7** | Resolve 觸發者 | **證據通過即 Resolve，人只負責 Close** | 需 Resolve comment 規格 \+ `@agent reject` 指令 |
| **D8** | Detail page 認證 | **內網限制、無登入、純唯讀** | 見 §4.7 的四條邊界 |
| **D9** | 對映表持久層 | **獨立 SQLite** | 必須包 repository 介面 \+ WAL \+ IMMEDIATE 交易 \+ 獨立備份 |
| **D10** | 併發上限 | **max\_running 8 / max\_awaiting\_close 3** | 執行與審查分開設閘門，見下 |

### D6b 衍生：ClearQuest bridge 的三條約束（現在就鎖）

1. **Source adapter 現在就抽薄薄一層**。內部用正規化的 `Ticket` 模型（`id` / `external_ref` / `state` / `assignee` / `comments`），Jira 是唯一實作。幾十行的事，晚做要動所有呼叫點。  
2. **Bridge 是獨立程式，harness 維持單一來源**。讓 harness 同時讀兩個 tracker，等於把它變成多 tracker 整合層——那是另一個產品。  
3. ⚠️ **Bridge 絕對不可寫 Jira 的 workflow state 或 `Agent Status`**。那會讓 C2 的單一 writer 模型當場崩掉，而且是最難查的 race：兩個程式以不同節奏改同一欄位。Bridge 只做 ClearQuest → Jira 單向建票；狀態同步一律反向（讀 Jira，寫回 ClearQuest）。要寫 Jira 就寫自己的欄位。

### D10 衍生：為什麼是兩個閘門而不是一個

真正的瓶頸不是執行，是**審查**——agent 產出速度可線性擴張，人的審查頻寬不行。而 D7 選了自動 Resolve，塞車會直接堆在 Resolve → Close 這一段，也就是堆在人身上。

所以設兩個獨立閘門：

- `max_running: 8` — 資源綁定（workspace checkout、container、API rate limit）  
- `max_awaiting_close: 3` — **審查綁定**。Resolve 但未 Close 的張數達 3 就停止派新工

這樣系統會在審查塞車時**自動節流**，而不是繼續堆積。8 這個數字才有意義——它是「機器最多能跑多少」，不是「人最多能消化多少」。

**P1 階段要盯的兩個數字**：實際同時執行數（有沒有真的用到 8）、`max_awaiting_close` 觸發頻率（如果常觸發，代表瓶頸確實在審查，該調的是 verify 強度而非併發數）。

**資源估算提醒**：8 個併發 \= 8 份 repo checkout \+ 8 個 container。以中型 C/C++ 專案計，磁碟與記憶體都要先算過，別等到跑起來才發現機器不夠。

---

## 10\. KPI 指標

### 10.0 兩個原則

**原則一：P1 不設目標值，只建立基線。** 在還沒有真實資料前設數字，會逼迫團隊為了達標而調鬆 `verify`——那正是這個系統最不能妥協的部分。P1 的任務是「量得出來」，P2 才是「量到多少」。

**原則二：每個效率指標都必須配一個制衡指標。** 效率指標全部可以靠「降低驗證強度」作弊，而且作弊在短期內看起來像進步。見 §10.5。

---

### 10.1 北極星指標

| 指標 | 定義 | 資料來源 |
| :---- | :---- | :---- |
| **First-pass Close rate** | 未經 `@agent reject` 就被 Close 的張數 ÷ 總 Resolve 張數 | L0 事件流 |

**為什麼是它**：這是唯一同時驗證三件事的指標——`verify` 夠不夠強、模型做不做得動、routing 有沒有把對的 ticket 派給對的 profile。三者任一出問題它都會掉。

**它也是唯一該用來決定要不要調高併發的依據。** 在這個數字穩定之前調高 `max_awaiting_close`，等於把不確定性直接推給審查的人。

---

### 10.2 效率指標

| 指標 | 定義 | 資料來源 | P1 |
| :---- | :---- | :---- | :---- |
| **Cycle time**（中位數 \+ p90） | 認領 → Resolve 的時間 | L0 事件時戳 | 建基線 |
| **Attempts per Resolve**（中位數） | 到 Resolve 為止的 attempt 數 | L1 | 建基線 |
| **Cost per Resolved ticket** | 該 ticket 所有 attempt 的 `total_cost_usd` 總和 | L2 `.result.json` | 建基線 |
| **Cost per Closed ticket** | 同上，但**包含被 reject 後重跑的成本** | L2 \+ L0 | 建基線 |
| **Throughput** | 每週 Closed 張數 | L0 | 建基線 |
| **Concurrency utilization** | 實際同時執行數 ÷ `max_running` | orchestrator | 觀察是否真用到 8 |
| **Queue wait time** | 被 `max_awaiting_close` 擋住的等待時間 | orchestrator | **觸發頻率是關鍵訊號** |
| **Human touch time** | 人花在該 ticket 上的時間（審查 \+ 打回） | 需人工估或從 Jira 活動推估 | 粗估即可 |

> **一律用中位數與 p90，不要用平均數。** Agent 執行時間是長尾分布，平均數會被少數卡死的 attempt 完全帶偏，掩蓋掉「多數任務其實很快」這個事實。  
>   
> **`Cost per Closed` 比 `Cost per Resolved` 誠實。** 前者包含返工，後者可以靠「多 Resolve 幾次碰運氣」灌水。

---

### 10.3 效益指標

| 指標 | 定義 | 資料來源 | 說明 |
| :---- | :---- | :---- | :---- |
| **First-pass Close rate** | 見 §10.1 | L0 | 北極星 |
| **Reject rate \+ 原因分布** | `@agent reject` 次數；reason 分類統計 | L0 comment 指令記錄 | **原因分布比比率更有用**——告訴你該補哪個 verify |
| **Automation coverage** | 被 routing 認領的張數 ÷ 進入 agent project 的總張數 | L0 `route_matched` | 沒被認領的是 routing 規則的缺口 |
| **Abandonment rate** | Cancelled \+ 人接手完成 ÷ 總認領張數 | L0 | 高 \= 接了不該接的 ticket |
| **Escape rate** | Close 後被 reopen，或事後發現有問題 | Jira reopen 事件 | **最落後但最誠實**的品質訊號 |
| **Ticket 複雜度分布** | 認領 ticket 的變更檔案數 / diff 大小分布 | agent 回報 | 防「只挑軟柿子」，見 §10.5 |

**Fuzzing profile 專屬**（沿用 oss-fuzz-gen 的評估維度）

| 指標 | 說明 |
| :---- | :---- |
| Build success rate | 生成的 harness 編得過的比例 |
| **Coverage delta vs 人工 harness** | 這是證明「值不值」的核心數字 |
| New unique crashes found | 真正的產出 |
| False positive rate | 回報但無法復現的 crash |

---

### 10.4 Debuggability 指標

對應三層排障地圖——**目標是「出事時能快速判定是哪一層」**。

| 指標 | 定義 | 資料來源 | 目標方向 |
| :---- | :---- | :---- | :---- |
| **Layer attribution rate** | 失敗案例中能明確歸因到 harness / loop / graph 某一層的比例 | 人工標註 \+ 故障定位表 | **↑**，低代表可觀測性不足 |
| **MTTD**（Mean Time To Diagnose） | 從發現異常到判定層級的時間 | 人工記錄 | **↓** |
| **UNKNOWN rate** | `outcome == UNKNOWN` ÷ 總 attempt | L1 | **↓**，但見下方警告 |
| **Silent failure count** | 沒有錯誤訊號但卡住的次數（Zed \#55501 那類） | liveness probe | **目標 0** |
| **Reproducibility rate** | 用 event log 重放能重現的失敗比例 | 重放測試 | **↑**，這是 event-sourced 架構的主要價值兌現 |
| **Time to first useful signal** | attempt 開始到能判斷「這次大概會不會成功」的時間 | L3 stream trace | **↓**，決定能不能早停省錢 |

> ⚠️ **UNKNOWN rate 不可單獨當 KPI。** 壓低它最快的方法是把本該 UNKNOWN 的情況誤判成 FAILURE——那會讓你以為系統變可靠了，實際上是把不確定性藏起來。 必須與 **Silent failure count** 一起看：UNKNOWN 下降但 silent failure 上升 \= 在作弊。

---

### 10.5 Traceability 指標

| 指標 | 定義 | 資料來源 | 目標 |
| :---- | :---- | :---- | :---- |
| **Trace completeness** | L0/L1/L2/L3 四層皆齊全的 attempt 比例 | trace 檔案盤點 | **100%**（這是唯一該在 P1 就設硬目標的） |
| **Link integrity** | 每個狀態轉移都能點到對應 L2 檔案的比例 | event log 檢查 | **100%** |
| **Provenance coverage** | Resolve 的每個變更檔案能追到「哪次 attempt、哪段 prompt、哪個 tool call」的比例 | L2/L3 \+ git | **↑** |
| **Audit answerability** | 「這行程式碼為什麼被改？」能在 N 分鐘內回答 | 抽樣演練 | 訂一個 N（建議 5 分鐘） |
| **Trace retention** | trace 保留期限內實際可讀取的比例 | 定期抽驗 | 100% |

> **Trace completeness 是唯一該在 P1 就設 100% 硬目標的指標。** 理由：它不需要模型變好、不需要 verify 變強、純粹是工程紀律。做不到 100% 就是有 bug，而且缺一層 trace 的那次執行，之後永遠查不了。  
>   
> 實作上加一個 CI 檢查：每個結束的 attempt 都必須產出四層檔案，缺任何一層就告警。

---

### 10.6 反指標與 Goodhart 防護

**每個效率指標都有一條作弊路徑，而且作弊在短期看起來像進步。**

| 想改善的指標 | 最快的作弊法 | 制衡指標 |
| :---- | :---- | :---- |
| Throughput ↑ | 調鬆 `verify` | **First-pass Close rate**、Escape rate |
| Cost per ticket ↓ | 只接簡單 ticket | **Automation coverage**、Ticket 複雜度分布 |
| Attempts per Resolve ↓ | 放寬通過條件 | **Escape rate** |
| Cycle time ↓ | 降低 `max_turns`，草率交件 | **Reject rate** |
| UNKNOWN rate ↓ | 把 UNKNOWN 誤判成 FAILURE | **Silent failure count** |
| First-pass Close rate ↑ | 人變成無腦 Close | **Escape rate**、Human touch time（異常低是警訊） |

> **最後一條最危險。** 如果 first-pass Close rate 很高但 human touch time 極短，很可能不是 agent 變強，是審查的人放棄認真看了。這正是 §4.6 要求 Resolve comment 必須帶齊證據的理由——讓認真審查的成本夠低，人才不會放棄。

---

### 10.7 分階段

| 階段 | 量什麼 | 設不設目標 |
| :---- | :---- | :---- |
| **P0** | Trace completeness、UNKNOWN rate、Silent failure count | Trace completeness \= 100%，其餘只記錄 |
| **P1** | 加上全部效率與效益指標 | **只建基線，不設目標值** |
| **P2** | 同上 \+ Layer attribution rate、MTTD、Reproducibility | 依 P1 基線訂目標；First-pass Close rate 開始當決策依據 |
| **P3+** | 加 Escape rate（需要時間才有意義） | 全面目標管理 |

---

### 10.8 明確不要量的東西

| 不要量 | 理由 |
| :---- | :---- |
| **Lines of code changed** | 與價值無關且方向不明——重構的價值常是負的行數 |
| **Token usage 單獨看** | 成本指標已涵蓋；單看 token 會誘導縮短 context 而傷害品質 |
| **Agent 自評成功率** | 這就是 "loop on confidence"。禁止進入任何儀表板 |
| **模型基準分數**（SWE-bench 等） | 與你們的 codebase 和 verify 無關，會誤導選型 |
| **每人每週處理 ticket 數** | 會把審查的人變成產線工人，直接摧毀 first-pass Close rate 的意義 |

---

## 附錄 A：主要參考來源

**框架**

- OpenHands Software Agent SDK — `github.com/OpenHands/software-agent-sdk`（MIT）  
- 設計論文 — arXiv 2511.03690（event-sourced state、immutable config、typed tools）  
- 持久化文件 — `docs.openhands.dev/sdk/guides/convo-persistence`  
- Hermes 委派文件 — `hermes-agent.nousresearch.com/docs/user-guide/features/delegation`  
- Hermes claude-code skill — `.../skills/bundled/autonomous-ai-agents/autonomous-ai-agents-claude-code`（v2.2.0, MIT）  
- Goose — `github.com/aaif-goose/goose`（Apache-2.0）  
- AionUi — `github.com/iOfficeAI/AionUi`（Apache-2.0）；ACP Setup wiki

**ACP 故障證據**

- OpenHands \#14260 — ACP session 無法跨沙箱重啟 resume（最完整分析）  
- Zed \#55501 — 用量上限後 ACP session 進入不可恢復狀態  
- T3 Code \#2838 — 重連時誤呼叫 newSession  
- ACP RFD: Session List — 協定缺 session 列舉

**方法論**

- `2026-07-19-AGENT-HARNESS-VS-LOOP-VS-GRAPH-ENGINEERING-THREE-LAYERS`（自有知識庫）— 三層排障地圖、故障定位決策表、迴圈七要素、五個昂貴錯誤、"a stack of loops"  
- Anthropic — Building Effective Agents（orchestrator-workers / evaluator-optimizer 原典）

**領域參考（fuzzing 分支）**

- `google/oss-fuzz-gen` — prompt 策略與評估指標（build rate / crash rate / coverage diff vs 人工 harness）

