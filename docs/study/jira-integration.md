# Jira Harness 整合設計(研究結論)

> 一句話結論:**用欄位所有權模型把 agent 接進 Jira 既有狀態機——每個欄位只有一個 writer、以數字 issue_id 為主鍵、outcome 三態(SUCCESS/FAILURE/UNKNOWN)、Jira Cloud 的所有髒細節全關進單一 source adapter。** 骨架用 OpenHands SDK、執行單元用 headless CLI(`claude -p` / `codex exec`),不走 ACP;session 的 source of truth 永遠是自己的 event log,不是 CLI 的 session 檔。

## 研究問題(agent 要如何對 Jira 負責)

Jira 已經是團隊工作的家:狀態機存在、穩定、與任務類型無關。問題不是「要不要自己發明流程」,而是**如何讓一個 headless coding agent 安全地成為 Jira 工作流裡的一個 assignee**——既能被 ticket 事件驅動、被人用留言指揮,又不會在人手動拖看板、改 assignee、關票時失控。

v5 把系統拆成雙層迴圈:

- **Outer loop(Graph 層)**:管 ticket 生命週期。輪詢/watch → routing(YAML regex,只選 profile)→ 決策 create / resume / 交還人工 / 忽略。週期是分鐘到天,**能中斷它的只有人**(Jira 上任何動作)。
- **Inner loop(Harness 層)**:管單次 workspace+skill 任務。建 workspace → 注入 skills → CLI 呼叫 → 確定性驗證(build/test/lint/schema)→ PASS / FAIL(有界重試)/ UNKNOWN(立即上報)。週期是秒到分鐘,由 orchestrator 用 timeout/budget 中斷。

**分工鐵律**:Outer loop 禁止描述任務步驟序列;Inner loop 禁止改寫 Jira 狀態。一旦 YAML 出現 `steps:` 或 `then:`,就是把 routing 寫成了任務分解圖,需 code review 護欄擋下。

## 核心設計決策(v5 的 D 系列重點)

| # | 決策 | 重點與後果 |
|---|---|---|
| **C2 / D1** | **欄位所有權模型** | 每個欄位只有一個 writer → 不需要雙狀態機對帳(見下節表) |
| **D2** | Outer loop YAML 權限深度 | 只決定 profile 綁定、接管時機、交還時機;不得出現步驟序列 |
| **C3** | 主鍵用 **數字 issue_id** | `ticket_key` 會因 project move 改變,只供顯示;查 mapping 表查得到就 resume、查不到就建 workspace |
| **D3** | Pending 三分類 | `human-decision` / `external` / `unknown`;**`pending:unknown` 只能人解除,絕不自動** |
| **D4** | HITL 前端 | 不做獨立 GUI;Jira comment + `Agent Link` → 自建唯讀 detail page |
| **D5** | Skill vs worktree 決定權 | YAML 決定 skill,agent 決定 worktree(`worktree_policy: agent_decides`) |
| **D6 / D6b** | Jira 權限與進場 | 專案隔離——agent 只動專屬 project;直接在 agent project 開票,外部系統之後另寫獨立 bridge(單向建票,絕不寫 Jira 狀態/`Agent Status`) |
| **D7** | Resolve 觸發者 | 證據通過即 Resolve,**人只負責 Close**;需帶齊證據的 Resolve comment + `@agent reject` 打回機制 |
| **D8** | Detail page 認證 | 內網限制、無登入、純唯讀;不要監聽 `0.0.0.0`,靠反向代理 access log 當唯一稽核軌跡 |
| **D9** | 對映表持久層 | 獨立 SQLite:WAL 模式 + `BEGIN IMMEDIATE` 交易(「查不到就建」必須原子化)+ 獨立備份 |
| **D10** | 併發雙閘門 | `max_running: 8`(資源綁定)/ `max_awaiting_close: 3`(**審查綁定**)——瓶頸是人的審查頻寬,審查塞車時自動節流 |

另外三個從 Hermes 抄來、OpenHands 沒有的設計:**UNKNOWN 三態**(擁有者行程消失時記 UNKNOWN,不猜)、**先落地再投遞**(完成事件先寫持久層再發佈)、**超時 dump 全執行緒堆疊**(「卡在巢狀 thread」與「慢速 provider」從外面看一樣)。

## Jira API 的坑(實測/研究抓到的)

這些不是理論,是 ARCP 在真實 Jira Cloud 實作 source adapter 時釘死的事實。

- **transition 用 `statusCategory` 而非狀態名**:狀態名是 locale 資料;transition 應比對目標的 `statusCategory.key`(`new` / `indeterminate` / `done`),category 是 locale-immune 的。實作即遍歷 `/transitions`,取第一個目標 category 相符者。
- **issue type 用 id 而非名稱**:type 名稱同樣是 locale 資料(「任務」/ Task / Tarefa 都可能),只有 id 穩定(`10003` = 標準 Task)。實測環境的 issue type 就是中文。
- **`/search/jql` 對無效 project 回空集合的假陰性**:Cloud 已在 2025 棄用 `GET /rest/api/3/search`,現行端點是 `/rest/api/3/search/jql`(用 `nextPageToken` 分頁)。**新端點對不存在的 project 回空集合、不報錯**——實測踩到:帳號下 project 顯示名與 **key 不同**(Jira 改名不改 key,key = SCRUM),誤用顯示名查詢會安靜回空、看起來像沒 ticket,別被騙。adapter 對 404/410 才 fallback 到 legacy 端點,兼容舊部署。
- **locale 問題貫穿全局**:狀態名、issue type 名都是 locale 資料,凡是拿「人看的字」當識別碼都會在換語系/換環境時炸——一律改用 category key、type id 等 locale-immune 識別碼。
- **rate limit 三管齊下**:`write_policy: coarse`(只在狀態轉移時寫 Jira,細節留 detail page)、輪詢間隔、JQL 範圍三者要一起調。實作上**只有寫入**會對 429/5xx 退避重試;讀取是冪等的,poll loop 下一輪自然重試,故讀取永不退避。
- **customfield ID 不要 hardcode**:`customfield_xxxxx` 在不同環境不同,用欄位名稱查詢後快取。
- **SSL 陷阱**:python.org 的 macOS build 不帶系統 CA,adapter 需自備 certifi。

## 關鍵取捨(表格)

**欄位所有權模型(D1/C2 的核心:每欄單一 writer)**

| 欄位 | Writer | 用途 |
|---|---|---|
| Workflow state(New/In Progress/Pending/Resolve/Close/Cancelled) | **當下的 assignee**(agent 工作時就是 assignee) | 人類可見的粗粒度進度 |
| `Agent Status`(自訂) | **只有 harness** | `IDLE`/`PREPARING`/`RUNNING`/`VERIFYING`/`PENDING:<reason>`/`DONE`/`ABORTED` |
| `Agent Link`(自訂 URL) | **只有 harness** | 指向 detail page |
| Comments | 人 **與** harness 皆可 | **人 → agent 的唯一指令通道** |
| Assignee | 人 | **改走 = 隱含撤銷授權** → 立即 pending |

> 「人不能改 state」是**流程慣例不是技術保證**:要真擋住得設 Jira workflow transition conditions(限 assignee)+ field configuration(兩欄唯讀)。即使設了,偵測邏輯仍要實作當最後防線。
> （註:W11 後 ARCP 改為 **assignee 恆定=Agent**、人機互動走一次性表單,見 [decisions D8](../decisions.md);此處為 v5 原始設計。）

**Jira Cloud vs Server(差異全關進 source adapter)**

| 面向 | Jira Cloud(現行實驗環境) | Jira Server/DC(正式環境) |
|---|---|---|
| 端點 | REST v3、`/search/jql` + `nextPageToken` | 由 adapter 換檔支援 |
| 富文本 | ADF(需攤平/組裝) | adapter 內處理 |
| 認證 | email + API token 的 basic auth | adapter 內處理 |
| 隔離策略 | **`jira_source.py` 是唯一知道 Cloud 細節的檔** | 換 runtime = 換這一個檔,上游全部只吃正規化 `Ticket` 模型(D6b) |

**寫入策略(D10 + rate limit 防護)**

| 策略 | 行為 | 理由 |
|---|---|---|
| `write_policy: coarse` | 只在狀態轉移時寫 Jira | 每 attempt 都寫會撞 rate limit 且製造噪音;細節放 detail page |
| 寫入退避 | 429/5xx 才退避重試,**僅限寫入** | 寫入非冪等、代價高 |
| 讀取不退避 | poll loop 下一輪重試 | 讀取冪等,無需即時重試 |

## 對 ARCP 的影響(哪些進了實作)

v5 的 P0/P1 已在本 repo 落地,實測反過來驗證/補強了設計:

- **`jira_source.py` 是唯一碰 Cloud 的檔**:v3 端點、ADF、basic auth 全部封裝於此;上游只消費正規化 `Ticket` 模型,換 Jira Server 只改這一個檔(D6b 三條約束就地兌現)。
- **transition 用 category、create 用 issue type id**:`transition(id_or_key, to_category)` 遍歷可用 transition 取目標 `statusCategory.key` 相符者;`create_ticket` 預設 `issue_type_id="10003"`。兩者都刻意繞開 locale。
- **`/search/jql` 新端點 + 舊端點 fallback**:對 404/410 才退回 legacy,並已把「無效 project 回空集合」的假陰性寫進註解與環境事實(key=SCRUM)。
- **工作日誌模型**:SQLite(WAL + `BEGIN IMMEDIATE`)存 `issue_id → (workspace, cli_session_id)` 對映表 + `events.jsonl` journal;**store 是唯一記憶**,斷網/斷線 resume 全靠磁碟狀態,無記憶體依賴。harness→Jira 每次寫入(留言/assign/transition)經 `on_write` 回呼記成 `jira_write` 事件,供時間軸顯示。
- **三態 outcome 落地**:UNKNOWN(行程消失/無 envelope)→ `pending:unknown`,只有人能解除、不自動重試;fault-injection E2E 已驗(timeout 殺 runner → UNKNOWN)。
- **指令通道 + 外部變更防護**:`@agent run/retry/stop/cancel` + commenter 白名單 + ack 回覆 + watermark 冪等;out-of-band 關票 → ABORTED、assignee 改走 → `pending:external`。
- **對映到 v5 的 P0-P4**:Phase 0-3(source adapter、routing/watch/watermark 灰度、dispatch、指令通道)皆 E2E 綠;Phase 4(`Agent Status`/`Agent Link` 自訂欄位、detail page、Resolve transition、D10 雙閘門、常駐 poller)為 v5 P2 剩餘。

## 原始出處

- 整合分析(× ARCP 實測對照):[../../research/2026-08-jira-harness-integration.md](../../research/2026-08-jira-harness-integration.md)
- v5 設計(選型研究 + 起手 prompt,含 C1-C4 / D1-D10 / §10 KPI):[../../research/2026-08-jira-agent-harness-design-v5.md](../../research/2026-08-jira-agent-harness-design-v5.md)
- 實作對照:`harness/PLAN_B.md`(開發 checklist 與環境事實)、`src/arcp/jira_source.py`(唯一碰 Cloud 的 source adapter)
