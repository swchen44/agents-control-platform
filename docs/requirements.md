# REQUIREMENTS — ARCP harness 需求總表(永久維護,含 Why)

> **這份是「為什麼要有這些能力」的單一真相**。docs/history/PLAN_wave*.md 是 how/checklist、
> DESIGN_*.md 是機制細節、git log 是流水帳;本檔把它們的 **What / Why / 現狀** 收斂成一頁。
>
> **維護規則(務必遵守)**:任何新需求或決策變更,**先更新本檔**(尤其 Why 一定保存),
> 再動工。每項標對應 wave/PLAN。Why 說明「當初為何這樣決定」,即使日後推翻也保留
> 舊 Why + 新 Why(用 ~~刪除線~~ 或「→ 改為」),讓決策脈絡永久可追。

## 0. 一句話 + 核心原則

讓 `claude -p` / `codex exec` 等 headless coding agent 由 **Jira 事件驅動**、
長時間可靠執行、**可觀測(trace)**、**可控制(control)**。

**Jira 的角色(世界觀,2026-08-08 定案)**:Jira = **對外的「工作日誌 + 系統帳本
(System of Record)」**;**Agent 以「員工」身分**在上面接單 → 做事 → 更新進度 → 回報
成果讓人評分關單,並像員工一樣被究責(assignee 恆掛它)。**真正的工作與完整細節在後台**
(workspace = 工作台;dashboard/transcript = 完整飛行記錄器);Jira 只承載**經策展的
摘要、決策、結果與連結**。「把成果報上去」= 報摘要 + 連到後台產物,不是把產物塞進 Jira。
- **Why**:公司本來就活在 Jira。讓 agent 像員工一樣對 Jira 負責,人就能用**既有管理儀式**
  (指派/留言/審核/關單)管一支自主 agent 大軍,不必學新工具;也解釋了 assignee 恆定、
  受控表單、單一寫入者、hash 稽核——全是為把 Jira 維持成**可信可稽核的日誌**而非 free-text
  聊天室(見 §14 [docs/design/interaction.md](design/interaction.md))。

| 原則 | Why |
|---|---|
| **證據型停止**(grader 終審,非信心) | agent 說「完成」不算數;確定性 verify 過才 SUCCESS。「loop on evidence, not confidence」——避免假完成 |
| **三態 outcome**(SUCCESS/FAILURE/UNKNOWN) | 分不清「失敗」與「無法證明」會誤重試燒錢或漏處理;UNKNOWN 只有人能解 |
| **envelope 契約跨 backend 不變** | 三 backend × 雙引擎共用 `{completed,session_id,cost,error,...}`,dispatcher/grader 零改動即可換執行單元(差異化層 runtime-agnostic) |
| **內網零外部依賴** | dashboard 只在內網跑,不可下載任何外部 CDN/字型/元件(W5.9);相依元件一律 vendor 進 repo |
| **省電優先:不用 caffeinate** | 使用者筆電沒充電時耗電太快;長跑靠 run_poller 迭代 timebox,不防睡 |

## 1. 任務源與路由(W1,Phase 0-3)

- **Jira 事件驅動**:poll → diff → route(標籤/keyword/assignee)→ dispatch。
  **Why**:使用者的工作單就是 Jira;人不改工具、事件自動接管。
- **watermark 冪等**:comment/state/assignee 變更只處理一次(SQLite watch state)。
  **Why**:重 poll 或 crash 重啟不可重放歷史事件。
- **內部觸發源**(scheduled cron / oneshot / script,W3.4/W4.6):非 Jira 票也能跑。
  **Why**:定時維護、一次性任務、跑任意 script(uvx/npx/.sh/.py)。cron 用五欄位
  crontab(W4.6),與 every 並存時 cron 優先。
- 現狀:`poller.py`/`routing.py`/`triggers.py`;config.yaml 設定。

## 2. 內層證據迴路(W1-W2)

- **template(class)→workspace(instance)**:profile 指 template folder,fork 前
  copytree 成 instance,命名 `{agent}__{key}__{issue_id}`(不變 issue_id 尾綴)。
  **Why**:native resume 綁 cwd,path 一旦建立不能變;summary 可編輯不可入 path。
- **grader 終審**、**bounded retry 餵失敗證據**、**A4 budget 上限**。
  **Why**:證據型停止;省錢(超支交人)。
- **G1 結構化契約** `{reason,status,next}`(claude `--json-schema`/codex
  `--output-schema`)。**Why**:agent 自報下一手,驅動 F3 換手;需 OpenAI strict
  schema 形狀(巢狀 additionalProperties:false)。
- 現狀:`dispatcher.py`/`inner_runner.py`/`contract.py`。

## 3. 生命週期與人機協作(W2)

- **起點審批門**(per-profile `require_approval`):貼 plan 到 Jira description
  分區段 → 人填表 → assignee 交回機器人放行;填錯退回、超 max_revisions escalate。
  **Why**:高風險任務要人核准起點;用分區段 description 當多方協作表單。
- **分區段 description + hash**(human/control/agent:<名>,機器段附 sha256):
  區塊置頂、human 前置、結束標記、全掃描驗 hash+log、區塊外不碰。
  **Why**:多方各寫各段、機器段防篡改(不符→還原)+ 幂等(hash 沒變不重寫,省 Jira 寫)。
- **human_email 欄**:人填接手人 email,agent 轉票給人時 email→accountId 解析
  (選填,空→fallback approver)。**Why**:轉人類要能指定 assignee。
- **assignee = 資源開關**(W12):交人類=inactive(讓出 F1 額度、不派工);
  回機器人=resume。**Why**:「不在機器人手上就不吃機器資源」。
- **F3 換手**(`@agent next` = 同票換手(next) / G1 next):換 profile 重排隊 / 交人;session pin 優先於 route。
- 現狀:`approval.py`/`sections.py`/`commands.py`。真 Jira 實測 SCRUM-20/21/22 PASS。

## 4. 併發與資源閘門(concurrent, W1 F1)

- **F1 分層額度**:global max_running + per_engine + per_profile;超額 QUEUED(FIFO);
  inactive/pending/queued/終態**不占額度**(W8)。**Why**:怕機器 CPU/memory 不夠用。
- **並行 dispatch**(ThreadPoolExecutor)+ **stall/hang watchdog**(N13,無進展→killpg→resume)。
- **長駐共享 server**(openhands-server backend)+ 掛了重起續。
- 現狀:`gate.py`/`poller.py`/`server_manager.py`。

## 5. 三 backend × 雙引擎(B/B+/C,W3.1/W5.4)

- **rawcli**(主線)/ **openhands-acp** / **openhands-server** × **claude** / **codex**
  = 6 格矩陣全綠,共用同一 envelope 契約。**Why**:證明差異化層 runtime-agnostic;
  rawcli 保真≈A、語意乾淨、控制窗口。
- **W5.5 rawcli 脫 OpenHands 依賴**:agent.py 純 stdlib,rawcli 主線不需 591MB venv。
  **Why**:減依賴、內網易部署;openhands-acp/server 仍選配(需 venv)。
- 現狀:`arcp_rawcli/`(純 stdlib)、`inner_*_runner.py`。

## 6. 韌性與冪等(W3.2/W5.1/W5.3)

- **A2 冪等分層**:agent 層 native resume(transcript 重放);harness 層「先持久化
  再外寫」= at-most-once。盤點 9 路徑見 `docs/design/idempotency.md`。
- **sid 預派**(W5.1):attempt 前先持久化 attempts+預派 sid;crash 偵測(envelope 缺):
  有 sid→退還 attempt+resume,無 sid→UNKNOWN。**Why**:harness 中途死不重花錢;
  快照器首 attempt 也拿得到 sid。
- **E3 強制驅逐(evict/killpg,W5.3)**:`POST /evict/<id>` → 即刻 killpg 釋放
  CPU/memory,不耗 attempt,下輪 native resume。**Why**:agent 卡住/要即時讓出資源;
  同步 poll 架構下唯 control 線程能即時。**它是異常處理**,應正名中文+記次數(W6.3)。
- **retention 回收**(W3.3):終態保留 `retention_days`(預設 270)後刪 workspace,
  store/journal 留稽核。**Why**:工作區可拋、證據不可拋。

## 7. 控制面與熱重載(W2.6/W4.5)

- **REST 控制面**(內嵌 daemon,W13):`GET /status /health`、`POST /pause /resume
  /reload /shutdown`、`POST /evict/<id>`。
- **hot reload 範圍/關閉語意**見 `docs/design/hotreload.md`:reload=引用替換非交易、壞
  config 回 400 舊設定續用;graceful shutdown=當前輪(含壓縮)跑完退出;強制關閉
  靠冪等+三態兜底。**Why**:控制要作用於正在跑的 poller;省電不防睡 → 睡醒能續。
- ⚠️ **風險(W6 決策)**:control API 有寫入端點,若綁 0.0.0.0 無認證則任何內網 IP
  可 pause/shutdown/evict。使用者 2026-08-07 選擇接受(內網信任),綁定設為 config,
  預設可切回 127.0.0.1。

## 8. 可觀測:dashboard + transcript(W4/W5)

- **dashboard**(`detail_server.py`,獨立唯讀進程):四層 trace、過濾器置頂(時間
  範圍/status/summary/desc)、時間圖+金錢圖(SVG 零外部)、欄位排序+欄寬拖曳、
  CSV/JSON 匯出、DB Browser(唯讀 SQLite)、Control 頁。
- **transcript 可視化**:vendor `claude-code-log`(`tools/cclog/`,MIT),
  `render_transcript.py` 產 claude/codex/sub-agent HTML;close 打包 tgz(gzip -9)。
  **Why**:人要肉眼看 agent 在做什麼;內網零外部(vis-timeline 已 vendor + CSP 硬擋)。
- 現狀:真 Jira 實測 W4 全鏈路 PASS(SCRUM-23/24)。

## 9. 執行隔離(D1,W3.6 介面先行)

- `agent.isolation.provider`:auto/seatbelt/landlock/appcontainer/docker/none。
  現只實作 macOS seatbelt;其餘接受設定不啟用(warning)。**Why**:未來跨 OS 部署,
  OS 提供方優先、docker 為選項;介面先定,實作待部署前夕。

---

## 10. W6 新需求(2026-08-07 使用者口述,含決策)

### 10.0 不用 caffeinate ✅
使用者明令:沒充電耗電太快。**How**:長跑直接跑,靠 run_poller 迭代 timebox,不防睡。
見記憶 no-caffeinate。

### 10.1 Server 頁(新 tab)
顯示 **server 資訊 + per-process + per claude/codex** 三層:
- **server**:OS/claude/codex/python 版本、登入/金鑰**狀態**(🔒 只顯示有無/到期,
  **絕不顯示值**——安全底線)、python workspace、mem/cpu/uptime/free/disk、異常。
- **per-process**:Jira ID ↔ claude/codex PID / CPU% / MEM(**best-effort ps 對應**,
  cwd→workspace→Jira;純 stdlib,不改派工架構)。
- **per-workspace**:Jira ID、workspace path、安裝 skill 名(SKILL.md folder)、
  跑時間、session id、sub-session id、transcript path、各 workspace 磁碟用量。
- **Why**:運維要一眼看到「機器健康 + 每個 agent 在幹嘛吃多少資源」。
- **決策**:dashboard 綁 **0.0.0.0 無認證**(內網信任;才看得到「哪些 IP 連線」);
  金鑰只顯示狀態。

### 10.2 evict 正名 + 計數 + 說明(異常處理)
- 中文正名「**強制驅逐(killpg 釋放資源)**」;按鈕 hover 說明**何時用**(卡住/要即時
  讓出資源)、**如何恢復**(下輪自動 native resume,不重花錢)。
- **記次數**:store 計數器;Server 頁「異常」區顯示(每票/總計)。
- **Why**:evict 是異常處理,發生頻率是健康指標,要能追蹤。

### 10.3 transcript 主動/被動 + metadata(移除定時)
- **移除每 60s 定時快照**(W4.3 snapshotter);改:
  - **主動**:state/assignee 變、evict、close 時自動產。
  - **被動**:Jira 頁「Transcript(可視化/下載)」旁按鈕,按下即產新 .html。
  - **等人類的票也要有** transcript;in-progress 想看就按按鈕(不定時,省 loading)。
- **metadata**:每份產物存 **sidecar**(產生日期時間 + 原因〔state變/assignee變/
  evict/close/手動〕);Transcript 卡旁顯示「有無 + 時間 + 原因」。
- **cclog 支援 codex**:已接 `--provider codex`,W6 實測補證。
- **不漏 claude/codex sub-session** transcript。
- **Why**:定時產正在執行的太耗;改事件+按需,且要知道每份是何時、為何產的。
- **已實作(W6.5)**:snapshotter 移除;finalize 統一入口帶 reason 寫 `meta.json`
  (generated_at/reason/session_id/subs);被動 = control `POST /gen_transcript/<id>`
  + ticket 頁按鈕;卡片顯示時間/原因(中文化);cclog codex 真 session 實測通過。

### 10.4 REST API 文件(2026-08-07 更新:改用 vendored Swagger UI)
目前**無**任何連結。~~加自寫 `/docs` 頁~~ → **vendor Swagger UI**(swagger-ui-dist
5.32.12,Apache-2.0,~1.7MB)進 repo,serve `/docs` = Swagger UI(讀本地
`/openapi.json`),連結放 Server 頁。
**Why**:使用者 2026-08-07 指示 vendor 回來——Swagger UI 美觀實用、可 try-it-out;
評估確認它是自包靜態檔,vendor 後完全離線(不違反內網原則)。原「手寫」理由
(Swagger UI 需 CDN)在 vendor 後不成立。⚠️ try-it-out 對寫入端點=真操控,頁面標註。
- **已實作(W6.5)**:vendor `tools/vendor/swagger-ui/`(css+bundle.js+LICENSE,
  無外部引用);detail_server 出 `/openapi.json`(手寫 3.1,唯讀+寫入分兩 server,
  寫入端點 tag『control-plane ⚠️』+ operation-level server 指向 control API)、
  `/docs`(Swagger UI,專屬 CSP 含 unsafe-eval 因 bundle 有 1 處 new Function)、
  `/swagger-assets/<file>`;Server 頁連結。Chrome 實測離線渲染 + 無 console 錯誤。

### 10.6 事件時間軸(2026-08-07 新增)
使用者看到 `aN.events.jsonl`(L3 events)不解其用,想要「有時間、看得到 user/agent
事件、何時留言 Jira/改 status」+ 類似 transcript 的 timeline 元件。
**釐清(重要)**:
- `aN.events.jsonl`(L3)= **agent 自身對話**(有時間戳),餵 Conversation 分頁 +
  cclog transcript(cclog 本身已有 vis-timeline)。**不記 Jira 互動**。
- **journal `events.jsonl`** = **harness↔Jira 生命週期**(comment_added/assignee_changed/
  resolved/handoff/evicted/transcript_packed…),**都有時間**——這才是「何時留言/改
  status」的來源,資料已齊,只是沒做成時間軸。
**做法**:per-ticket 詳情頁加「**事件時間軸**」,用 journal 事件(重用已 vendor 的
vis-timeline 元件)+ 明確補記 harness 的 Jira 寫入(留言/assign/transition 於寫入
時 journal 一筆 `jira_write`,讓時間軸清楚顯示「HH:MM 留言 Jira: SUCCESS」)。
**決策(2026-08-07 敲定)**:時間軸範圍 = **只 harness/Jira 生命週期**(乾淨);
agent 對話留給 transcript 的 timeline,不併入(避免吵雜、避免重複)。
- **已實作(W6.7)**:JiraCloudSource 加 `on_write` 回呼(source 層統一,涵蓋
  comment/assign/transition/description 24 個寫入點,免逐點改),run_poller 接成
  `store.journal("jira_write",…)`;ticket 頁加事件時間軸(重用 vendored vis-timeline,
  四分組:外部輸入/Jira 寫入/生命週期/執行·產物),**刻意放 `</main>` 之外**——5s
  自動刷新只換 main,widget 不被摧毀(Chrome 實測過刷新後 widget 存活、無閃爍)。

### 10.5 連線 IP 追蹤 + history
dashboard/control 記錄連線 client IP + 時間;Server 頁顯示目前連線 + 近期 history。
**Why**:內網開放後要知道誰在連。

## 11. 已知風險 / 未做(留後續)

- control API 寫入端點 + 0.0.0.0 無認證 = 內網任何人可控 poller(§7,已接受,可設定切回)。
- landlock/docker 隔離**實作**未做(介面已就緒,W3.6)。
- openhands-acp/server backend 若確定不用可整個移除(六格對照已存證)。
- 異步架構(assignee 自動即時 kill + rehydrate)為大工程,未排。
- 量產 python 標準結構另開 repo(需定 repo 名/公開與否)。

## 12. W7 新需求(2026-08-07 口述 brainstorming 對齊,R1–R9)

> 來源:使用者口述 brainstorming,經一次一題決策樹對齊(11 題)。**盡量不動 Jira
> 原生(workflow/權限/jql/關票流程)**,新增欄位皆 additive。實作見 `docs/history/PLAN_wave7.md`。

### 12.1 人類完成度評分(R1 / R2)
AI 做完(SUCCESS **或** FAILURE 終態)交人時,人給 **0–10** 分(內部 ×10 = %),
評「對照這個 agent 的目標,完成度多少」。分數填在 description 的 **human 段
`score`**;harness 每輪讀,填了 `journal("human_score")`,沒填週期提醒。
- **關票權責**:**維持人類關票**;harness **軟性把關**(不硬鎖、不改 Jira 權限)。
  成功/失敗終態 → 交人 + 票保持開(本來就沒關)→ 人填分後自己關;沒填就關 =
  dashboard 標「未評分」(可接受的漏接)。
- **捕捉時機**:**關票前**讀 human 段(∵ jql `statusCategory != Done`,票一 Done
  就從搜尋消失、harness 看不到——所以只能在還開著時抓)。
- **agent 目標來源**:新增 `Profile.goal`(人可讀),交人時寫進 **`agent:<profile>`
  段**(每次交人必寫、不漏);human 段 score 旁放小註解「0–10:對照上方目標的完成度」。
  未設 goal → fallback route/profile 名。
- **Why**:要一個「AI 到底幫了多少」的客觀數據,才能算每張 Jira 的效益;放 human 段
  沿用既有 section 機制(如 human_email)、不動架構;軟性把關是使用者在「硬鎖 vs
  最小改動」間選了最小改動(硬鎖需 Jira 管理員設 workflow 權限,可能無權)。
- **決策脈絡**:曾考慮「只有 agent 能 close」+「Jira 權限硬鎖」→ 因需動 Jira 設定/
  可能無權,改為「人關票 + harness 軟性把關」。尺度曾在 0-100 / 0-5 間,最後定 **0-10**。

### 12.2 效益計算(R3)
dashboard 呈現每張 Jira 效益 = **(score/10) × 省下工時 × 時薪 − AI 花費**;
未評分不計入平均。省下工時用既有 `Profile.human_minutes_est`,**未設預設 240 分(4h)**。
- **不用 Python 讀 log 預測工時**(現況也沒這樣做):準確度存疑;先用 Profile 靜態值。
  Python 讀 session/sub/transcript 動態預測列為**未來選項**。
- **Why**:效益 = 省的人力價值扣掉 AI 成本,再乘人主觀有用度;score 低=還有 gap。

### 12.3 per-profile 比較 + 狀態分類(R4)
- dashboard 第一頁**已有 profile 欄**;filter **加 profile 關鍵字**(目前缺)。
- 三張 per-profile 圖:①縱 profile×橫 Jira 數(依狀態堆疊上色)②縱 profile×橫花費
  (AI 花費 / 人力$ / 差值=效益)③縱 profile×橫平均完成度%。
- **狀態分類(8 態,依我們 DB 真實狀態,非 Jira 原生)**:待處理 / 進行中 / 排隊 /
  等待人類(pending) / 交人(inactive) / 成功 / 失敗 / 撤銷。**同時是 R6 狀態機節點**。
- **Why**:要能比較不同 profile 的量、成本、有用度;精細狀態才看得出票卡在哪(診斷)。

### 12.4 Agent Detail tab(R5)
新開 tab,顯示 **harness 設定**(config.yaml:jql/並發/control/retry…)+ **每個
Profile 全參數**(backend/engine/skills/verify/budgets/approver/goal/human_minutes_est/
retention…)。與現有 **Server tab**(機器/系統/程序,W6.1)分工:Server=機器現況,
Agent Detail=設定檔內容。
- **Why**:設定目前只在 config.yaml,網頁看不到;人要在網頁就能查 agent 怎麼配的。

### 12.5 概念 / 生命週期 / 狀態機頁(R6)
新「概念/說明」tab:Jira 生命週期 + **8 態狀態機圖(純 SVG,零依賴)** + 「狀態存哪」
說明;同內容寫進 **README**。
- **狀態存哪(釐清)**:Jira `status` 存 Jira、鏡射到 DB `ticket_watch.last_state`;
  我們的**內部判定** `outcome`(SUCCESS/FAILURE/ABORTED)+ `pending_reason` 只存
  DB `ticket_session`、**不寫回 Jira**;harness **不主動 transition Jira 狀態**(只留言)。
- **Why**:搞定系統先搞定資料流生命週期;新人/使用者要有一頁看懂設計。

### 12.6 預算:單次 + 月上限 + spawn 前預檢(R7)
- 既有:`Profile.max_budget_usd`(單次/累計,達標→pending:budget,**attempt 後**檢查)。
- 新增:`Profile.max_budget_monthly_usd`(**日曆月**、跨票、per-profile)。
- **spawn 前預檢**:每次要 spawn claude/codex 前,檢查 (a) 此票累計 vs 單次上限
  (或 `budget_override`)、(b) 此 profile 當月累計 vs 月上限;任一達標→不 spawn、
  pending:budget、交人。
- **單次放寬**:human 段填 `budget_override`(USD),per-ticket、下次 dispatch 吃、
  不改 Profile、不影響月上限。**月上限只能改 Profile 設定**(hot reload)才續跑。
- 月彙總資料源:`attempt_finished` journal 補 `cost`+`profile`(現在只有時間戳、無 cost)。
- **Why**:怕燒錢失控;「跑前檢查」才不會多燒一個 attempt(現況是跑完才擋)。

### 12.7 給 LLM 監控用的 REST API(R8)
人用 Claude Code/codex 當監控 LLM,需要**完整唯讀查詢 API**:給 ticket 就能拿狀態 +
log。**擴充** W6.5 既有 API(非從零):
- **`/api/v1/`** 版本化命名空間(唯讀;沿用 dashboard 0.0.0.0 無認證)。
- **三合一解析器**:`{ref}` 接受 Jira key(SCRUM-42)/ 內部 id / **ClearQuest CR id**
  → 同一張票。
- **結構化 JSON 為主 + 原始檔可取**:單票狀態 JSON(profile/8態/attempts/cost/pending/
  score/budget/時間軸摘要)、L3 事件(`aN.events.jsonl`→JSON)、原始 session/sub-session
  jsonl 可 raw 下載(`?tail=N`)。納入現有 OpenAPI/Swagger。
- DB **加 `clearquest_id` 欄(nullable)**。
- **Why**:監控者本身也是 LLM,要能程式化讀狀態/log 才能協助監控回報;結構化省 token、
  原始檔留給深挖;CR id 之後要能查(見 R9)。
- **決策脈絡**:曾想把「查 key」設計成通用 `{source,key}` 多票源抽象 → 使用者澄清
  **ClearQuest 不取代 Jira**,CR 只是 Jira 票上的一欄 → 改為單票源 + `clearquest_id` 欄
  + 三合一解析器。

### 12.8 ClearQuest 監控整合(R9,**未來 To-Do,現在只記不做**)
使用者描述的流程:**監控 ClearQuest**,當 CR 的 **title / 人名 / keyword 命中** →
**建資料夾 + 複製 template 到新資料夾 + 開一張(Jira)追蹤票**,並在 DB 記該 **CR id**。
ClearQuest **不取代 Jira**(Jira 仍是票系統;CQ 是額外的觸發源 + 記在票上的 id)。
- ✅ **已確認(2026-08-08)**:這是 **ARCP 未來要做的新功能**,**不是**現有流程/別的
  工具在跑。也就是「監控 CQ → 命中建資料夾+套模板+開追蹤票+記 CR id」整條由 ARCP 自己
  實作(未來新增一個 ClearQuest 觸發源,類比現有 Jira poller)。
- **W7 只先備資料**:`clearquest_id` 欄(見 R8);CQ 監控/建資料夾/套模板/開票**不實作**。
- **Why**:先把資料模型留好,CQ 整合日後只加來源解析器,不動已公開的 API。

## 13. W10 新需求:HIL 生命週期 / triage 閘 / agent↔agent 交接(2026-08-08 口述,12 題決策樹定案)

> 使用者要求「一改全改」(文件/程式/網站)。**W10.1 模型/圖/網頁 + W10.4 架構圖 +
> W10.5 互動 + W10.2 HIL 行為(W11+group A 落地)+ W10.3 a2a 交接(2026-08-09 由 HIL
> 表單驅動實作)皆已完成**。完整設計見
> [docs/design/architecture.md](design/architecture.md) 與 [docs/design/lifecycle.md](design/lifecycle.md)。

### 13.1 HIL 生命週期(Model A)
- `success/failure/unknown` **不再是頂層狀態**,收斂成 **HIL(End)** 的「結果」屬性;
  舊 `inactive`(交人)+ 非終態 `pending` 合併成 **HIL(Middle)**(帶原因)。6 態:
  `todo / running / queued / hil_middle / hil_end / aborted`,`closed` 為概念終點。
- **HIL(Middle) resume**:`assignee`→機器人 為觸發;讀 description `human` 段重評條件
  (審批已填/預算已放寬/純交人無條件)滿足才查排隊+resume,否則 re-block。不佔 F1 額度。
- **HIL(End)**:人評分(0–10)後 (A) 續做→關票、或 (B) 判可續→**native resume + 重置
  attempt 額度**回 running(清 outcome、score 留 journal)。UNKNOWN 歸 HIL(End) 第三結果。
- **Why**:交人與等待人類語意一致(都=等人),合併降低心智負擔;把「結果」與「誰持有票」
  兩維度分開,概念更乾淨;續跑重置額度是因人明確指示續做,不該一回去就撞 max-attempts。

### 13.2 triage 閘(開跑前人判定 + 選 profile)
- 把 `require_approval` **泛化**:新增 **global 開關**(+ 保留 per-profile)。狀態 =
  HIL(Middle)·待審視,由 `todo` 進入(dispatch 前)。人在 `human` 段填 `agent_name`
  放行(可改 profile)或標 **decline** → `aborted`(reason=unsuitable)。
- **Why**:怕路由挑錯 profile / 這題不適合 agent;開跑前先讓人把關,復用已驗證的審批機制。

### 13.3 agent↔agent 交接(兩機制並存,文件須寫清楚何時用哪個)
> **W10.3 定案演進(2026-08-09)**:原案為「人自建 Jira + harness 只登記」;實作時
> 改為 **HIL 表單驅動 + 系統建票(一步完成,使用者選定「系統在 agent project 建」)**。
> 兩者脈絡注入邏輯相同,差別在「誰觸發、誰建票」。實作見 [architecture.md §4.1](design/architecture.md)。
- **同票換手(next)**:就地換 profile/引擎。觸發 = HIL 表單選 `handoff_kind=next`,或 agent 自發
  (`@agent next <profile>` 指令 / envelope `status=handoff`)。適合「同一件事繼續」。
- **跨票換手(base)**:觸發 = HIL(End/Middle) 表單選 `handoff_kind=base` + 下一棒 profile +
  交接 prompt。**系統**(`hil._do_handoff`)在同 project `create_ticket` 建新票、預建其
  session(pin 新 profile + `base_ref`);dispatcher 於新票首次佈建後**注入脈絡**(複製 base
  的 TICKET.md/最後 envelope 進 `ws/BASE_<key>/` + human 指示段前置指路);本票收成
  **ABORTED(交接→新票,非 failure)**。源票術語=**base(基底票)**。適合「換引擎/重開/
  跨專案/人策展重啟」等泛化場景。
- **Why**:交接方法越泛化越好;由 HIL 表單一次完成(選種類+下一棒+prompt)最少人手;
  系統建票省掉「人自建再貼 ref」的來回,下一棒由人選(不交給路由猜);本地檔注入脈絡 →
  內網/離線最可靠;fail-safe(資料不完整→續跑原 agent)不讓誤填毀掉本票。

### 13.4 互動式圖形(內網離線)
- 狀態機 + 架構圖用 **svg-pan-zoom**(vendored,離線,同 vis-timeline)可拖曳/縮放。
- **Why**:內網看,JS lib 必須預先下載 vendor,不能吃 CDN。

## 14. W11 互動服務:HIL 人機介面(2026-08-08 口述定案,取代人編 description)

> 完整設計見 [docs/design/interaction.md](design/interaction.md)。**已實作**(W11 一次性
> 表單 + group A HIL 行為 + W10.3 handoff 表單欄位)。

### 14.1 核心原則
- **assignee 恆定=Agent**;Description/state **單一寫入者=Agent/系統**;人類輸入一律經
  **受控表單**(不吃 free-text);通知用 **@mention**(不動 assignee、不轉 state);全程可稽核。
- **Why**:用 Jira description free-text 下指令易錯難處理;結構化表單 + 單一寫入者 + 可稽核
  才穩。

### 14.2 互動流程 + 表單
- Agent 需人 → comment `@mention` +（**一次性連結** + 有效期 + Request ID）→ 人開受控表單
  (版本化 schema、前後端雙驗、顯示 ticket 上下文)→ 送出 → 系統寫回 **Human Section
  (hash+日期)** + 稽核 comment → **表單提交=HIL resume 觸發**(取代 assignee)。
- 表單型別:`need_info`/`decision`(=HIL(Middle))、`score_and_close`(=HIL(End),三訊號
  grader/agent自評0–10/人類0–10 + 關票裁決)。
- **Why**:一次性表單防填錯、可回寫存證;提交當觸發比 assignee ping-pong 乾淨。

### 14.3 Token
- ≥128-bit 亂數、單次(提交即失效)、綁 ticket+Request ID+schema、有效期綁請求生命週期
  (票關即失效、可設短窗)、與常駐 Detail Page 不同物件、不入共用日誌。
- **Why**:capability URL 即授權;綁定 + 短命 + 單次 = 最小暴露面。**修正 AI 原案固定 3 個月**。

### 14.4 獨立服務 + 安全模型分離
- 互動服務=獨立進程/port(人面向 + token 寫入),與唯讀 dashboard、內部 control_api 分開。
- **Why**:寫入面 + token 授權的安全模型,不該混進 zero-auth 唯讀 dashboard 或內部 control。

### 14.5 assignee 被改
- 記 journal 告警 + 貼一次 comment 提醒(冪等),**不強制改回**。
- **Why**:不搶 assignee、不製造 revert→通知的噪音迴圈。

### 14.6 催辦 / 異常記號(v1)
- 回應期限(1天,可設)→ 逾期重 @mention;N 次(10,可設)無回應 → DB 記異常計數 + comment。
- **Why**:避免票卡死等不到人;異常統計供後續制度化處理。

### 14.7 Jira 異常處理(簡易版,**不做 work queue**)
- 健康偵測失敗 → 系統「降級暫停」(停寫入/派工),不佇列;人開表單先測 Jira,異常則明示
  「請先檢視、暫勿送出」,仍送出→直接回報異常不落地;恢復由 probe 自動或**管理者手動**
  (管理頁通知 poller)→ 續跑。
- **Why**:GRA/Jira 會中斷;work queue 有不同步風險,故用暫停/恢復(circuit-breaker)取代。

### 14.8 關票裁決
- `score_and_close` 送出 → **系統幫忙轉 Jira Done**(option a);人透過表單授權、系統執行。
- **Why**:呼應「單一寫入者=Agent/系統也寫 state」;人只需在表單裁決,不必手動轉 Jira。

### 14.9 Agent Link 欄 + REST API(v1)
- Agent Link:常駐 Detail Page 連結寫進票(與一次性連結不同物件)。
- REST API:互動服務能力開成乾淨 REST,供未來人類自己的 agent(Hermes/openclaw 類)代理;
  proxy 本體遠期。
- **Why**:人隨時可點進觀測;API 先定形,未來接人類代理 agent 不必重構。

## 15. Workspace 佈建能力(2026-08-09 口述定案;設計見 [design/workspace.md](design/workspace.md))

一張票 → 一個隔離工作區。除既有的「整包 copytree 模板」外,新增三個能力,讓不同 agent
的起手環境可精準客製。完整流程/目標解析/schema 見設計文件;這裡記 What/Why。

### 15.1 install 腳本佈建(`workspace_install`)
- **What**:profile 可設一條安裝命令(argv:`uv run x.py`/`uvx x`/`npx x`/`./x.sh`/
  `python x.py`)。建 workspace 空資料夾後執行,ARCP 附兩個絕對路徑參數
  `<workspace> <template>`,cwd=模板夾,stdout/stderr 用 logger 吐出,rc==0 才算成功;
  設了 install 就用它佈建、不自動 copytree。
- **Why**:copytree 只能靜態複製;真實環境常需 **git clone / 產生設定 / 改檔再複製**
  等程序化佈建 —— 交給腳本最靈活,又不把這些邏輯塞進 harness 核心。

### 15.2 common skills 選子集(`common_skills`)
- **What**:`config/skills/<name>/` 是可重用的 skill 庫;profile 用 `common_skills: [..]`
  **選子集**,佈建時整包複製到 workspace 的 skills 目錄。
- **Why**:多數 agent 共用一組能力但各取所需;集中維護一份、按 profile 選,勝過每個模板
  各自塞一份 skills(會漂移)。

### 15.3 行為守則注入(`inject_md`,`config/templates/inject_claude_md_end.md`)
- **What**:全域 inject 檔的內容,佈建最後一步貼到 workspace 的 `CLAUDE.md` / `AGENTS.md`
  尾(marker 包住、冪等);profile `inject_md: false` 可關。
- **Why**:共同工作守則(先讀 TICKET.md、對驗收標準做、只動 workspace…)要能**一處改、
  處處生效**,不必每個模板重寫。

### 15.4 統一目標解析(skills 與 md 共用)
- **What**:`.claude/*` vs `.agents/*`、`CLAUDE.md` vs `AGENTS.md` —— 兩者都不存在就建
  `.claude` 側;只一個存在就用它;兩個都在且互為 link 就做一次、不同檔就兩邊都做。
- **Why**:順應模板既定的慣例(有的用 Claude Code 的 `.claude/`,有的用 AGENTS 生態的
  `.agents/`),不強加單一約定;link 去重避免重複貼/重複複製。

### 15.5 TICKET.md 加 goal / 驗收標準 / Jira 連結
- **What**:任務簡報加「目標」(profile.goal)、「驗收標準」(由 profile.verify 渲染成
  人看得懂的檔案/指令門檻)、「Jira 連結」(`<base_url>/browse/<key>`)。
- **Why**:讓 agent **對著證據做**(loop on evidence,呼應 [D2](decisions.md)),而非自以為
  完成;人也能從 workspace 反查回 Jira。
