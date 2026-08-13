# ARCP 系統需求書(SRS — System Requirements Specification)

> **文件目的**:本專案(agents-control-platform,ARCP)目前為 **PoC / 研究驗證版**。
> 未來將以本需求書向專業團隊**正式提出需求**——團隊可以拿本 repo 的程式改作,也可以
> 重新實作;無論哪條路,**本書是需求的單一正本**。內容涵蓋:問題陳述、使用場景
> (Before/After)、User Story + 驗收條件、功能/非功能需求總表、驗收標準與測試計畫、
> 第二期範圍。
>
> **狀態標註慣例**(貫穿全書,標示 PoC 現況,幫助團隊評估「可沿用 vs 要新做」):
> **✅ 已實作且實測**(PoC 內 CI 綠/真 Jira 驗過)、**◐ 部分完成**、
> **📐 設計已定、程式未接**、**🔮 第二期(未來需求)**、**❌ 明確不做**(刻意排除,推翻需知情)。
>
> 依據:原始需求口述稿(L0–L4 賦能階梯、L2→L3 Agent Architecture、agent 艦隊)、
> `docs/requirements.md`(What/Why 正本)、`docs/decisions.md`(D1–D14)、
> `docs/design/*`(18 份機制設計)、`docs/lessons.md`(17 條實測教訓)、`BACKLOG.md`、
> 三份操作手冊與 `docs/research/`(實驗釘住的事實)。追溯表見 §12。2026-08-13。

---

## 1. 問題陳述與系統目標

### 1.1 背景:AI 賦能成熟度階梯(L0–L4)與本平台的定位

| 階段 | 名稱 | 樣貌 |
|---|---|---|
| L0 | 手工作業 | 所有工作由人執行,沒有 AI 協助 |
| L1 | 個人 AI 賦能 | 員工自行使用 ChatGPT / Codex / Gemini / Claude 提高個人效率 |
| L2 | 技能可複用 | 有效的提示詞/工具/AI 技能整理成可共用資產(**Jarvis** skills),讓更多人重複使用 |
| **L3** | **流程化整合** | **AI 導入 CI/CD、SQC 等正式流程,由事件自動觸發執行** |
| L4 | 自主閉環 | AI 執行任務、檢查結果、持續運作;只有異常或需人判斷時才交人 |

人員角色隨階梯從「執行者」轉為「AI 協作者 → 操作員 → 治理者」;AI 從個人工具發展成
**可規模化、可驗證、可自動運行的流程能力**。

**本平台的定位:L2 → L3 的承載層。** 我們已有一批在 L2 驗證有效的 agent 技能
(Jarvis,於 Claude Code / Codex **互動模式**下開發打磨);要讓它們進入 L3(事件
驅動、無人值守、進正式流程),中間隔著三個斷層(§1.2),而這些斷層對**每一個**要上
L3 的 agent 都一樣——所以答案是**一個共用平台**,不是每個 agent 團隊各自解一遍。
(兩張手繪風資訊圖卡版見 [docs/l2-l3-infographic.html](l2-l3-infographic.html)。)

### 1.2 Before(現況痛點)

**L2 → L3 的三個斷層**——把互動模式用得很好的 agent 搬進非互動,必然遇到:

1. **工作模式改變**:互動 → 非互動,agent 變**黑盒子**。看不到中間狀態、無法中途
   介入(補 log、加需求、最後把關),出錯只能等它跑完才知道。
2. **執行環境改變**:Claude Code / Codex 自帶 harness engineering(上下文管理、
   工具編排、permission 體系)——L2 的好結果有一大半是它們給的。若 L3 改用裸 SDK
   直連 model,這層能力**不會自動跟過去**,等於把已驗證的 skills 重新 debug、
   重新開發一次。
3. **部署流程改變**:每個 agent 要上 L3,都得跟公司 agent flow 把**同一批問題**
   (debuggability、traceability、人機協作、resume、成本控管)重新討論一次——
   N 個 agent × 同一張問題清單 = 大量重複溝通與重工。

疊加 headless coding agent 自身的六個結構性缺口:

1. **不可靠**:行程 crash、假完成(agent 自稱 done / exit code=0 但任務沒做完——codex 收 SIGTERM 也回 rc=0)、卡住無人知。
2. **不可觀測**:跑了什麼、花了多少錢、卡在哪一步,事後無從稽核。
3. **不可控制**:跑起來就只能等;不能暫停、驅逐、改方向、換人做。
4. **無法事件驅動**:工作單在 Jira/ClearQuest 上,要人手動轉譯成 prompt、手動開跑、手動回報。
5. **人機協作無介面**:需要人補資訊/審批/評分時,只能靠口頭或 free-text 留言,易錯、不可稽核。
6. **成本不受控**:模型費用沒有上限機制(單票/月/全站),一個失控迴圈就燒掉預算。

### 1.3 After(目標圖像)

**你在 Jira 開票(或貼標籤)→ 系統看到 → 派一個 headless agent 去做 → 在隔離
workspace 執行、產出證據 → 確定性驗證(grader)過才算成功 → 需要人時 agent 在票上
@mention 你並附一次性表單連結;你填完,系統回寫 Jira 並讓 agent 續跑或關單。**
人用**既有的 Jira 操作**(開票、貼標籤、填表單、關單)就能指揮一支 agent 大軍,
全程可稽核、可回放、花費受控。

### 1.4 為什麼需要「共用平台」(給評估團隊的核心論證)

1. **斷層是平台性的,不是 agent 性的**:§1.2 的三斷層+六缺口,對 scan、fix、dev
   每一種 agent 完全相同。平台解一次,所有 agent 受益;不做平台,就是 N 個團隊
   各自重新發明監控、resume、表單、預算、稽核。§6.0 的艦隊對照表把這件事量化。
2. **保留 L2 資產,不掉進斷層 2**:平台直接以 `claude -p` / `codex exec` 為執行
   單元(rawcli),**完整沿用** Claude Code/Codex 的 harness engineering 與既有
   Jarvis skills——L2 打磨好的東西原封搬進 L3,不必改寫成裸 SDK 重來。
3. **把非互動黑盒子重新打開,補回斷層 1**:四層 trace + dashboard 讓「看不到」
   變「全程可回放」;一次性表單 + 指令台讓「插不了手」變「隨時可補資訊、改方向、
   中斷、換人」——互動模式失去的能力在平台層還回來。
4. **上線流程標準化,消滅斷層 3**:新 agent 上 L3 = 一個 profile(yaml)+ 一個
   label + verify 規則。debuggability/traceability/人機協作那張問題清單,由平台
   統一回答,不用每個 agent 團隊重談。
5. **L4 的地基現在就要打**:Jira 上留存的人類介入紀錄(何時介入、為什麼、給了
   什麼指示)+ 全量 session log,就是未來 Evolution Agent 學習「怎麼讓人不用
   介入」的素材。不上平台,這些數據散落各處、永遠收不回來。

### 1.5 世界觀:為什麼用 Jira 當紀錄機制(D1)

**Jira = 對外的工作日誌 + System of Record;Agent 以「員工」身分**接單 → 做事 →
更新進度 → 回報成果讓人評分關單,並像員工一樣被究責(assignee 恆掛它)。真正的工作與
完整細節在後台(workspace = 工作台;dashboard/transcript = 飛行記錄器);Jira 只承載
**經策展的摘要、決策、結果與連結**。此立場衍生出:assignee 恆定、受控表單、單一寫入者、
hash 稽核——全是為把 Jira 維持成**可信可稽核的日誌**而非 free-text 聊天室。

具體來說,Jira 作為紀錄機制承載七件事:

| # | 用途 | 對應能力 |
|---|---|---|
| 1 | 留存 status 供 monitor / kanban | 狀態同步(FR-39)、dashboard |
| 2 | assignee / @mention 承載人機互動與 agent 間互動 | HIL(FR-19–24)、交接(FR-25) |
| 3 | 留存 context id 供 resume(不重工) | native resume(FR-14) |
| 4 | 算效率與效益(每票花費/省時/評分) | KPI(FR-36)、效益公式(US-K7) |
| 5 | debuggability / traceability 的單一入口 | 四層 trace(FR-32)、存證上票 |
| 6 | lessons learned 沉澱 | 結案結果區 + 附件回放 |
| 7 | **L4 伏筆**:人類為何介入的 log,供 agent 日後自我進化 | Evolution Agent(§11) |

---

## 2. 名詞定義

| 術語 | 定義 |
|---|---|
| **headless agent / 執行單元** | `claude -p` 或 `codex exec` 的一次非互動執行 |
| **attempt** | 執行單元的一次完整呼叫(一張票可有多次,bounded retry) |
| **envelope** | 跨 backend/引擎統一的結果契約 `{completed, session_id, cost, error, …}` |
| **grader / verify** | 確定性驗證(files/cmd/json),SUCCESS 的唯一判定者 |
| **三態 outcome** | SUCCESS / FAILURE / **UNKNOWN**(無法證明,只有人能解) |
| **label(入場券)** | Jira 票標籤,決定要不要進場、走哪條 route;系統只讀不改;命名空間 `arcp.` 前綴 |
| **route** | config 中 label→profile 的比對規則;`on_match`: ignore / notify_only / create_or_resume |
| **profile** | 一個 agent 的完整定義(engine/model/workspace/verify/budget/approval…) |
| **triage / select** | 首次派工時由腳本/隨機從候選池選定 profile(可遞歸,max 10 層),鎖進 session |
| **workspace** | 每票一個的隔離工作區;agent 只讀它、不連 Jira |
| **TICKET.md** | 系統從 Jira 渲染進 workspace 的任務簡報(目標/描述/人類指示/驗收標準) |
| **HIL** | Human-in-the-Loop;6 態生命週期中的 HIL(Middle)(過程中等人)與 HIL(End)(終點評分) |
| **一次性表單** | ≥128-bit token 的受控網頁表單,人類輸入的唯一正式通道 |
| **指令台** | 綁票的常駐指令網頁(run/retry/hold/stop/cancel/next/set_email) |
| **journal** | append-only 事件流 `events.jsonl`(53 種事件),歷史真相 |
| **store** | SQLite `harness.db`(4 表),當下狀態與冪等記憶,**絕不 wipe** |
| **CR / WITS / ClearQuest(CQ)** | 公司內部 issue tracking(QA/客戶來源),單位=**CR**(有 CR 編號);口述需求稱 **WITS**,PoC 程式與文件以 ClearQuest/CQ 代稱**同一角色**;CR 是 Jira 票的上游來源(`crid` 關聯) |
| **L0–L4** | AI 賦能成熟度階梯(手工→個人賦能→技能複用→流程化整合→自主閉環);本平台=L2→L3 承載層(§1.1) |
| **Jarvis** | 公司內部 L2 技能資產(可複用的 agent skills),於 Claude Code/Codex 互動模式下開發 |
| **daemon agent** | 常駐調度者:watch WITS/Jira、分類、開票、喚醒 non-interactive agent、監控卡住/異常——此角色由**平台本體**(poller/routing/triage/dispatcher/triggers)實作,不必每個團隊自寫 while loop |
| **non-interactive agent** | headless 執行單元(`claude -p`/`codex exec` + workspace + skills);context 限於單一 ticket、可 resume;被 daemon agent 呼叫 |
| **internal-agent loop / cross-agent loop** | agent 行程**內**的自迴圈(sub-agent、前景等待)vs 由平台「喚醒→留言 ticket→resume」的**跨行程**迴圈;長流程監控定案用 cross-agent(見 §6.0) |

---

## 3. 系統範圍

### 3.1 範圍內

- Jira 事件驅動的 agent 派工、生命週期管理、證據驗證、人機協作、觀測與控制。
- 執行單元:`claude -p` 與 `codex exec`(rawcli 直呼,純 stdlib);backend 可插拔。
- 內部排程(agent-job 開票 / script-job 跑腳本)。
- Jira Cloud 與 Data Center 雙相容(`jira_flavor` 一鍵切換)。
- 單機部署、多實例並存;離線內網凍結 snapshot 交付。

### 3.2 明確不做(❌ 刻意排除——推翻須知情)

| 不做 | 理由 |
|---|---|
| Docker workspace 隔離 | 使用者決策(2026-08-12);維持 seatbelt(claude)/`--sandbox`(codex);provider 介面保留 |
| Jira 異常 work queue 緩衝 | 不同步風險;用降級暫停/恢復(circuit-breaker)取代(D9) |
| tool-output ledger | native resume + at-most-once 寫入順序 + 一次性 token 已達成目標(A2 結論) |
| grader 過即自動轉 Done | 改 HIL(End) 人評分授權關單(B3 改設計) |
| caffeinate 防睡 | 耗電;長跑靠 timebox 迭代,睡醒能續(D14) |
| 獨立 GUI(HIL 前端) | Jira comment + 表單 + detail page 已足(v5 D4) |
| LLM 評審器 | 驗證一律確定性檢查(v5 約束) |
| KPI 設目標值(P1) | 避免逼人調鬆 verify 作弊;P1 只建基線(kpi.md 原則一) |

---

## 4. 角色(Actors)

| 角色 | 職責 | 介面 |
|---|---|---|
| **使用者(開票人)** | 開票/貼標籤下任務;description 自然語言 + 頂部 yaml 變數(`crid`/`email`/`prompt`);填表單;下指令 | Jira、HIL 表單、指令台、dashboard |
| **負責人(owner)** | description `email` 指定(可多位);身分門禁允許提交的人;評分關單 | 同上,受門禁保護 |
| **審批者(approver)** | 開跑前看 plan 放行(require_approval);email 豁免門禁 | 審批表單 |
| **管理者(operator)** | 起停/控制/監控/備份/多實例/hot reload/異常處置;調 budget;豁免門禁 | control API、dashboard、config |
| **開發者** | 改程式、加 backend/profile;維護需求正本 | 原始碼、CI |
| **離線分析者(AI/人)** | 內網凍結 snapshot 上只靠 repo 文件 + runtime 證據除錯 | journal、docs |
| **系統角色** | poller / dispatcher / grader / triggers / form_server / control_api / store / detail_server | — |
| **外部系統** | Jira(Cloud/DC)、ClearQuest、Gerrit | REST |

---

## 5. 核心設計約束(D1–D14 — 未來團隊必須遵守,或明知理由才能推翻)

| # | 約束 | 一句話理由 |
|---|---|---|
| D1 | Jira=System of Record;Agent=員工;細節在後台 | 人用既有管理儀式管 agent 大軍 |
| D2 | **證據型停止**:grader 終審,agent 自稱/exit code 一律不可信 | 避免假完成(核心 IP) |
| D3 | **三態 outcome**:UNKNOWN 只有人能解,不自動重試 | 分不清失敗與無法證明會誤重試燒錢 |
| D4 | **envelope 契約跨 backend 不變** | 換執行單元 dispatcher/grader 零改動 |
| D5 | **內網零外部依賴**:前端元件一律 vendored,不吃 CDN | 離線可用、可稽核 |
| D6 | 生命週期 = **HIL 6 態**(todo/running/queued/HIL-Middle/HIL-End/aborted + closed);DB 無 state 欄,唯讀推導 | 結果與持有者兩維度分開;單一真相 |
| D7 | HIL(End) **三訊號並列**:grader + agent 自評 0–10 + 人評 0–10 | 多訊號交叉對照 |
| D8 | 人類輸入一律**一次性 token 表單**;assignee 恆定;單一寫入者;hash 稽核 | free-text 易錯難稽核 |
| D9 | Jira 異常 = **降級暫停/恢復**,不做 work queue | queue 有不同步風險 |
| D10 | 交接兩機制對等:**同票 next**(重置 session)/ **跨票 base**(系統建新票+脈絡注入);交接=ABORTED 不算失敗 | 失敗率 KPI 誠實 |
| D11 | 併發 = **F1 分層額度閘**(global/per-engine/per-profile),超額 QUEUED;HIL/終態不占額度 | 機器資源有限 |
| D12 | 冪等 = agent 層 native resume + harness 層**先持久化再外寫**(at-most-once) | crash 不重花錢 |
| D13 | src-layout + uv + CI 矩陣(Py 3.10–3.13)+ fresh checkout 必綠 | 可安裝可貢獻 |
| D14 | 不用 caffeinate;容忍睡眠中斷、睡醒能續 | 省電;韌性靠 resume 不靠防睡 |

---

## 6. 使用場景(Before / After)

### 6.0 第一波 agent 艦隊(目標應用圖像)

> 平台要承載的具體 agent 清單(源自原始需求口述)。重點不在單一 agent 多強,而在
> **共通需求**:每一列都需要同一批平台能力——這就是「做一個平台,而不是每個 agent
> 各自解」的量化理由。

| Agent | 類型 | 輸入 | 產出 | 特殊需求 |
|---|---|---|---|---|
| **Assignment Agent** | daemon | watch WITS/Jira(REST/websocket;可由 Jenkins/人驅動) | 依 keyword/CR 分類(新 feature / SQC / HQA / 客戶)→ 開 Jira 票、指派 agent、fork 呼叫、監控卡住/資訊更新/重啟/異常、收 session log | **= 平台本體**:poller/routing/triage/dispatcher/triggers 已實作此角色 |
| **Security Scan Agent** | 排程 scan | 排程掃 codebase | 掃 coding quality bug → 發 WITS CR | **不重複掃、不重複發**(去重) |
| **Quality Scan Agent** | 排程 scan | 同上 | 同上 | 同上 |
| **Memslim Scan Agent** | 排程 scan | 排程掃 memory size | 發 WITS CR | 同上 |
| **Fuzz fix Agent** | fix | ticket/CR 編號 | analysis report + code patch + UT/IT report | 實驗:x86、wut;CI/CD |
| **Cov fix Agent** | fix | ticket/CR 編號 | 同上 | 實驗:x86、local scan;CI/CD;preflight 可 trigger Coverity |
| **Memslim fix Agent** | fix | ticket/CR 編號 | 同上 | 實驗:x86、build;CI/CD |
| **Phy regression fix Agent** | fix | ticket/CR 編號 | 同上 | **夾版本**;控制實體設備(快車/autout/connsysplant) |
| **Performance fix Agent** | fix | ticket/CR 編號 | 同上 | 同上 |
| **專業 Dev Agent** | dev(無標準答案) | system requirement / spec / standard / HW SRS·SDS | FRD、SW SRS/SDS、code patch、analysis/UT/IT report | 設計 test case;需人審批與評分把關 |
| **Evolution Agent** | L4(遠期) | ticket/CR status、session log、gerrit | 找出做不完/中斷的問題 → 計畫修改/增加/實驗,強化 scan/fix/dev 的 skills | 依賴平台留存的人類介入紀錄(§1.5 第 7 點) |

agent 分三類心智模型:**scan**(找問題,發 CR)、**fix**(有標準答案地解問題)、
**dev**(沒有清楚標準答案,人把關比重高)。各專業領域 agent 自行安裝不同 skills
(拆成 flow skill + 共用 skill;工作量大時傾向開 **sub-agent** 而非跨 agent——
跨 agent overhead 高,sub-agent 才能共享 context)。

**共通需求 → 平台能力對照**(每個 agent 都要,平台一次提供):

| 每個 agent 都需要 | 平台能力(FR) |
|---|---|
| 吃 ticket/CR 編號、關聯來源、用 CR 號查回票 | FR-05(crid 契約 + 三合一查詢) |
| scan 類「不重複掃/不重複發」 | FR-05 crid 去重 + FR-02 watermark |
| 產出三件套(analysis/patch/report)且可驗收 | FR-11 verify(證據型停止)+ FR-22 交付物駕駛艙 |
| 進 CI/CD / preflight 的長等待與監控 | FR-15 timeout/stall + cross-agent loop(下述定案) |
| 中途要人:補 log/需求、決策、review+submit | FR-19–24 HIL 表單(Normal flow=場景 S1/S6;Except flow=S3) |
| 人下完指示切回機器人、從原 context 續跑 | FR-14 native resume(context id 存 Jira/DB) |
| 做不動換人/換方法 | FR-25 交接(=cross-agent loop 的「轉給 Assignment 再派」) |
| 每次執行的 session/sub-session log 留存 | FR-32 四層 trace + FR-35 transcript |
| 花費不失控 | FR-30 六層預算 |
| 各自的專業 skills、設備控制工具 | FR-08 workspace(common_skills 選子集、install 腳本) |

**Loop engineering 定案**(原始需求中的兩案抉擇,已被平台實驗定讞):監控 CI/CD 這類
長流程,(a)agent 行程內開 timer(internal-agent loop)vs(b)由 WITS/Jira 監控
engine 定期喚醒(cross-agent loop)。**定案 (b)**——與原始需求的偏好一致,且有硬
證據:headless 行程內建立的排程「回報成功,但行程一退出就靜默失效、永不執行」
(研究報告實驗 3),行程內 timer 在非互動模式**根本不成立**。正確形狀=平台喚醒 →
agent 檢查 → 更新/留言 ticket → resume。行程內只允許**前景等待**(實驗 4:codex
前景等 5 分鐘 build 可行)與 **sub-agent fan-out**(實驗 1:claude 會等全部完成)。

> 每個場景:**Before**(沒有系統時人怎麼受苦)→ **After**(系統行為,含關鍵事件)→ 涉及的 Epic。

### S1 崩潰修復 CR 全自動(happy path)

- **Before**:CQ 進一張「登入頁 Safari 崩潰」CR。工程師手動看 CR → 開 IDE → 修 → 測 → 回報 CQ/Jira。夜間/週末堆單;新手修不動;資深被瑣事佔滿。
- **After**:排程 job 掃 CQ(🔮 bridge;PoC 已支援 job 開票)→ 開 Jira 票貼 `arcp.crashfix` label + `crid` → poller 撿到、route 命中 → triage 腳本依內容選 `crashfix_fast/careful` → workspace 佈建(模板+skills+TICKET.md)→ `claude -p` 修復 → 產出 `FIXED.md` → grader 驗證通過 → 交付物(摘要+Gerrit 連結+附件)貼回票 → 人評分關單 → 系統轉 Done、全程存證附件上票。journal:`new_issue → route_matched → profile_selected → session_created → attempt_* → resolved → deliverables_posted → closed`。
- Epic:A、B、C、D、F、K。

### S2 高風險任務要人審批

- **Before**:怕 agent 亂動生產相關 repo,乾脆不敢自動化。
- **After**:profile 設 `require_approval: true` → 票進場後**先擋在審批門**(HIL-Middle,零機器資源:fork 前不佔子進程,lazy provision 連磁碟都不佔)→ 票上貼 plan + @mention 審批者 + 一次性審批表單 → 審批者看 plan、提交即放行(格式錯表單就地擋)→ 才開始花錢跑。
- Epic:B、F、J。

### S3 中途改方向(hold)

- **Before**:發現 agent 方向錯了只能等它跑完浪費錢,或殺進程丟掉全部進度。
- **After**:指令台按 `hold` → **立即 killpg**(不耗 attempt、不丟資料)→ 系統開 hold 表單 → 人填「改先跑測試」→ 寫進 TICKET.md「人類指示(累加)」段 → agent native resume 帶著新指示續跑。
- Epic:E、F、H。

### S4 花費失控(budget)

- **Before**:一個失控迴圈燒掉整月 API 預算,事後才發現。
- **After**:6 層上限({單票 soft/hard, 月/agent, 全站} × {token, usd})**每輪 attempt 前預檢**;破 soft → 票暫停 + 發自助增額表單(≤hard);破 hard/月/全站 → 通知管理者改 config + hot reload 後自動續跑。dashboard 有花費速率紅黃綠燈與月用量。
- Epic:I、H、K。

### S5 第一棒做不好,換手

- **Before**:換一個人/工具重做 = 手動複製脈絡、重講一遍需求。
- **After**:評分表單裁決選 `handoff` → **同票 next**(同票重置 session、換 profile 重新佈建,脈絡留在票上)或 **跨票 base**(系統自動建新票、把前一棒 TICKET.md+最後 envelope 注入新 workspace 的 `BASE_<key>/`,本票收 ABORTED「交接」不算失敗)。填不全 → fail-safe 續跑原 agent。
- Epic:G、F。

### S6 做完之後:評分、關單、存證

- **Before**:agent 說做完就算做完;三個月後想回查「當時為什麼這樣改」,什麼都不剩。
- **After**:終態(成功/失敗/未知都一樣)→ HIL(End) 評分表單 = **自足駕駛艙**(成果敘事、附件下載、三訊號並列:grader/agent 自評/人評、花費、transcript 連結)→ 人評 0–10 + 裁決(關單/續跑/換手)→ 系統轉 Done → **存證上票**:description 置頂結果區(完成度/評分/花費/時長)+ 附件(每版 TICKET.md、timeline.jsonl、SESSION 快照、對話 HTML)。離開 ARCP 只看 Jira 也能回放全程。無人值守場景用 profile `auto_close`(off/on_success/all)。
- Epic:F、K。

### S7 排程巡檢與 CR 掃描

- **Before**:每天早上人手動檢查昨日失敗、掃 CQ 新 CR。
- **After**:`outer_loop.triggers[]` 排程:**script-job**(純跑腳本,rc 判定成敗、stdout 進 transcript)或 **agent-job**(腳本 stdout JSON 任務清單 → 每筆**像人一樣開票**貼 label、帶 `crid`,票走完整 route→triage,不預鎖 profile)。`count`(1 單次/0 無上限/N 次)× `cron`/`every`。
- Epic:A、L。

### S8 事後稽核與離線除錯(內網凍結 snapshot)

- **Before**:內網環境出問題,連不了外、問不到原作者,只能瞎猜。
- **After**:證據四層齊備(L0 journal / L1 envelope / L2 attempt log / L3 對話),`trace_lint` 保證每個結束的 attempt 100% 可稽核;文件自足(ai-debugging→troubleshooting→observability→lessons 標準除錯路徑);dashboard 全部元件 vendored 離線可用;事件字典 53 種由程式自動產生防漂移。
- Epic:K、M。

### S9 多負責人與身分門禁

- **Before**:誰都能對票下指令/填表單,出事無從追責。
- **After**:description `email: a@x.com, b@y.com` → 首建鎖進 `owner_email_list` → 表單/指令台**上鎖**(名單內/管理者/審批者才能提交);每次提交記 email+IP+內容稽核;改負責人走 `set_email`(門禁閉環、re-tag、重發表單)。沒填 email 的票不受限(選填)。
- Epic:J。

### S10 營運:異常與規模

- **Before**:服務掛了/Jira 掛了/機器satur了,只能重啟碰運氣。
- **After**:Jira 連續失敗 → **自動降級停派**(不假裝成功),恢復自動或 `POST /recover`;票卡住 → `/evict` killpg(不耗 attempt、下輪 native resume);改設定 → hot reload(壞 config 回 400 舊設定續用);機器不夠 → 併發閘排隊 + 8+1 紅黃綠燈;要多個 Control Plane → 複製資料夾分 project/port 並存。
- Epic:H、E、M、K。

---

## 7. User Stories 與驗收條件

> 格式:**US-<Epic><n>**(As a … I want … so that …)+ **AC**(驗收條件,Given/When/Then 或斷言)。
> 條目後的狀態標註 = PoC 現況。

### Epic A — 任務攝入與路由

- **US-A1** ✅ 身為使用者,我在 Jira 開票貼 label 就能讓 agent 接手,不用學新工具。
  **AC**:Given 票帶 `arcp.*` label 且命中 `create_or_resume` route;When poller 下一輪(≤poll_interval,預設 15–30s)掃到;Then journal 有 `new_issue`+`route_matched`(含 route/profile/on_match),且票進入派工候選。未命中任何 route 的票**完全不被碰**。
- **US-A2** ✅ 身為管理者,我要灰度上線:某些 label 只記錄不派工。
  **AC**:route `on_match: notify_only` → 只記 `route_matched`,不建 session、不佈建指令台;`ignore` → 完全不理。
- **US-A3** ✅ 身為系統,同一事件(留言/狀態變更)絕不處理兩次。
  **AC**:`ticket_watch` 水位(last_comment_id/last_state/last_assignee)以 issue_id 為 key;重啟/重 poll 後 Given 舊事件;Then 不重放。啟動時「認養」既有票(journal `adopted`),不重跑歷史。
- **US-A4** ✅ 身為管理者,我能排程週期/單次工作。
  **AC**:`triggers[]` 支援 `trigger_type: script-job|agent-job`、`count`(1/0/N)、`cron`(五欄位,與 `every` 並存時 cron 優先)。script-job:rc==0 → SUCCESS,rc≠0/timeout → FAILURE,stdout/stderr 存 transcript。agent-job:stdout JSON 清單每筆開真票(**不預鎖 profile**,走 route→triage);stdout 非 JSON/rc≠0 → `trigger_error`、該輪不開票。
- **US-A5** ✅ 身為自動化腳本,我開的票要能關聯來源 CR。
  **AC**:description 頂部 yaml `crid:` → `session.clearquest_id`;`GET /api/v1/tickets/<CR-id>` 能以 CR id 查回該票(三合一解析器:key/內部 id/CR id)。

### Epic B — 派工與 triage

- **US-B1** ✅ 身為管理者,我要一張票用哪個 agent 是可條件化、可 A/B 的。
  **AC**:main profile 的 `select`(method random|script);random 候選須同族前綴、均勻分流;script 收 stdin JSON(ticket/crid/候選+all_profiles)、印 `{"profile","reason"}`,可回**任何**已定義 profile、可遞歸(max 10 層、繞圈截斷);逾時 60s。選定即鎖 `ticket_session.profile`,**resume 不重選**;選≠main 才記 `profile_selected`。
- **US-B2** ✅ 身為管理者,triage 判「這票不適合 agent」時要如實中止,不硬跑。
  **AC**:script 印 `notfound` → outcome=ABORTED(untriageable)、Jira 轉 `cancel_status`(未設則優雅退回 done-category);腳本壞/無效名 → **fail-safe 回 main 照跑**(journal `error`)——「明確中止」與「暫時性故障」分開處理。
- **US-B3** ✅ 身為管理者,機器資源要有分層上限。
  **AC**:F1 三層額度(global/per-engine/per-profile);超額 → `queued`(FIFO,journal+dashboard 徽章);HIL/終態/inactive 不占額度。

### Epic C — 執行單元與 workspace

- **US-C1** ✅ 身為 agent,我需要一個自足的隔離工作區,不碰 Jira。
  **AC**:每票一個 workspace(命名 `<agent>__<key>__<issue_id>`,路徑一旦建立不變);TICKET.md 含標頭/目標/描述/人類指示(累加)/驗收標準/Jira 連結;人要 agent 知道的事**只能**經 TICKET.md 或 workspace 檔進入;Jira 留言**不進** TICKET.md(安全與稽核)。
- **US-C2** ✅ 身為管理者,workspace 佈建要可程序化客製。
  **AC**:三擇一:`workspace_template`(整包 copytree)/`workspace_install`(命令佈建,附 `<ws> <template>` 兩個絕對路徑參數,rc==0 才成功)/empty;`common_skills`/`common_hooks` 從庫選子集複製;`inject_md` 全域行為守則注入(marker 冪等);目標解析容 `.claude/`↔`.agents/`、CLAUDE.md↔AGENTS.md 四情況。佈建原子性:`.arcp_provisioned` marker;中途 crash → rmtree 重建。resume 時只刷新 TICKET.md,其餘跳過。
- **US-C3** ✅ 身為系統,跨 backend/引擎的結果必須是同一契約。
  **AC**:envelope `{completed, session_id, cost, tokens, error, …}` 跨 rawcli/openhands-acp/openhands-server × claude/codex 不變(6 格矩陣);結構化輸出用 claude `--json-schema` / codex `--output-schema`(strict schema 形狀);agent 自報 `{reason,status,next,summary,score}`。
- **US-C4** ✅ 身為管理者,agent 檔案寫入要被沙箱限制。
  **AC**:isolation provider(auto/seatbelt/landlock/appcontainer/docker/none);macOS seatbelt 已實測(workspace 可寫、/tmp 擋、**白名單絕不含 /private/tmp**——symlink 逃逸教訓);codex 用自帶 `--sandbox`;未實作 provider 接受設定不啟用+WARNING。
- **US-C5** ✅ 身為使用者,description 我只寫自然語言,少數變數有插值。
  **AC**:goal/描述/人類指示支援 `{crid}{email}{prompt}{key}` 占位符代入;**verify cmd 不插值**(注入防護);未知占位符原樣保留不炸。

### Epic D — 證據型停止(核心 IP)

- **US-D1** ✅ 身為管理者,agent 自稱完成不算數,證據過了才算。
  **AC**:profile `verify`(files 存在/cmd rc==0/json 鍵與型別)全過 → outcome=SUCCESS(journal `resolved`);**exit code、事件流、agent 自稱一律不得作為完成判定**(codex SIGTERM rc=0 實測教訓)。
- **US-D2** ✅ 身為管理者,驗證不過要把失敗證據餵回下一輪。
  **AC**:Given verify 失敗且 attempts < max_attempts;Then 下一輪 resume 的 prompt 含具體缺失(缺哪個檔/哪個指令 rc≠0);attempt 編號連續不跳號;耗盡 → FAILURE 進 HIL(End)。
- **US-D3** ✅ 身為管理者,「無法證明」不能與「失敗」混同。
  **AC**:agent 行程消失/envelope 缺 → UNKNOWN;UNKNOWN **絕不自動重試**,交人(comment+transcript),人確認副作用後下指令;infra 故障(venv 壞/port 占)→ pending:external,修好自動續跑**不耗 attempt**。

### Epic E — 韌性與冪等

- **US-E1** ✅ 身為管理者,harness 或 agent 中途死掉不能重工、不能重花錢。
  **AC**:2×2 崩潰矩陣(early/midtool × SIGTERM/SIGKILL)× claude/codex 全過:native resume 接回原對話、已完成工具不重做;sid 預派(claude `--session-id` 預指定;codex 從 `thread.started` 事後擷取)→ crash 偵測:有 sid 退還 attempt+resume、無 sid → UNKNOWN;harness 層先持久化再外寫(at-most-once),Jira comment 不重複。
- **US-E2** ✅ 身為管理者,卡住的 agent 要能被偵測與救回。
  **AC**:stall watchdog:`stall_seconds` 內事件流無進展 → killpg → 下輪 resume;**任何 stream 行都算進展**(slow is legal, stalled is not);⚠️ 設定值必須 0(停用)或 > 最長單一前景命令時間(長 build 期間事件流靜默——實測)。
- **US-E3** ✅ 身為管理者,我要能立即驅逐一個 agent 而不損失進度。
  **AC**:`POST /evict/<id>` → 看門狗(1s 輪詢)killpg 整個進程組(孤兒杜絕)→ envelope `error_kind=evicted` → **不耗 attempt**、session 留存 → 下輪 native resume;實測 t+8s 觸發、t+9.3s 結束;evict 次數入 store 計數與 Server 頁。
- **US-E4** ✅ 身為管理者,終態工作區要自動回收但證據永存。
  **AC**:終態後 `retention_days`(預設 270)回收 workspace;store/journal/上票附件不刪。
- **US-E5** ✅ 身為系統,workspace 搬家/claude session store 遺失要有降級路徑。
  **AC**:三段梯度:native resume → transcript 降級(journal 渲染 transcript 開新 session,實測不重工)→ 全新重跑。

### Epic F — HIL 人機協作

- **US-F1** ✅ 身為使用者,系統要我介入時必須通知我、給我一個不會填錯的介面。
  **AC**:@mention comment + 一次性表單連結(assignee 不變);token ≥128-bit、單次(提交即失效、再開唯讀)、綁單票+單 Request ID+單 schema、票終態即全失效、**重啟仍有效**(存 DB);7 種表單:need_info/decision/hold/approval/security_review/budget_increase/score_and_close;前後端雙驗;催辦(預設 1 天重 @mention、10 次無回應記異常)。
- **US-F2** ✅ 身為評分者,終態頁要讓我不離開就能裁決。
  **AC**:score_and_close = 自足駕駛艙:summary_md/程式碼(Gerrit)連結/附件下載/三訊號(grader、agent 自評 0–10、人評 0–10 必填)/花費/attempts/Jira+transcript 連結;裁決:關單(系統轉 Done)/續跑(解終態+重置額度回 running)/換手。
- **US-F3** ✅ 身為系統,人的輸入要回寫 Jira 且可稽核。
  **AC**:表單提交 → 寫 description human 段(hash+日期)+ 稽核 comment;人手改機器段 → hash 不符 → 告警+還原;回寫冪等(hash 沒變不重寫)。
- **US-F4** ✅ 身為管理者,可疑的任務簡報要先擋下給人看(prompt injection 防線)。
  **AC**:spawn 前純靜態掃描 TICKET.md 全文(cisco skill-scanner;**fail-closed**:掃描器故障=命中);命中 ≥ `fail_on`(預設 high)→ pending:security + security_review 表單(原文+命中理由+可修文字框);人審=最終裁決(`sec_reviewed_at` 蓋章後不再擋);修訂存 sidecar,Jira description 不動;未設定=功能關。
- **US-F5** ✅ 身為無人值守營運者,低風險 profile 要能自動關單。
  **AC**:`auto_close: off|on_success|all`;自動關 human_score=agent 自評、journal `closed(by=auto)`;**outcome 保留**(FAILURE 照算失敗率,不粉飾)。

### Epic G — 交接(agent↔agent)

- **US-G1** ✅ 同票換手:表單/指令台選 `next <profile>` → session 重置(sid/attempts 歸零)、鎖新 profile、重新佈建;**非 native resume**;脈絡靠票上內容進新 TICKET.md。
- **US-G2** ✅ 跨票換手:表單選 `base` + 下一棒 + 交接指示 → **系統**建新票(summary `[base:<原票>]`、沿用 labels 走同 route)、預建鎖定 session(base_ref);新票首次佈建注入 `BASE_<key>/`(原 TICKET.md+最後 envelope)+人類指示指路;本票 ABORTED(交接,不算失敗);注入一次性(注入後清 base_ref)。
- **US-G3** ✅ fail-safe:kind/profile 填不全 → 降級續跑原 agent(journal `handoff_invalid`),不弄壞本票。跨引擎(claude↔codex)無法 native resume → 建議走 base。

### Epic H — 指令台與控制面

- **US-H1** ✅ 身為使用者,每張被接管的票有一個常駐指令網頁。
  **AC**:description control 段連結+指路 comment;依**當前狀態**動態列指令(run/retry/hold/stop/cancel/next/set_email),各附用途/時機/副作用;必填 email 稽核;破壞性(cancel/stop/set_email)二次確認;綁本票、close 才失效;不適用時頁面直接說明原因。
- **US-H2** ✅ 身為自動化,同一套指令走 REST。
  **AC**:`POST /ticket/<id>/command {cmd,args,by}` 與指令台共用 `apply_command`;回 `{ok,message}`。
- **US-H3** ✅ 身為管理者,服務要能不重啟調設定與優雅起停。
  **AC**:`POST /reload` = 引用替換(當輪舊值、下輪新值);壞 config → 400、**舊設定原封續用**;可 reload:routes/jql/concurrency/profiles/triggers;不可:port/憑證/程式碼。`POST /shutdown` 當前輪自然跑完(最長 attempt timeout+收尾);kill -9 靠冪等+三態兜底。`/pause /resume /status /health /recover`。
- **US-H4** ✅ 身為管理者,Jira 掛掉時系統不能假裝成功。
  **AC**:寫入/健康連續失敗 → 降級停派停寫;表單頁提示「暫勿送出」;仍送出 → 不落地、回「稍後再試」;恢復自動(probe)或手動(`/recover`)。

### Epic I — 預算

- **US-I1** ✅ 六層上限 {單票 soft/hard, 月/agent, 全站} × {token, usd};**每輪 attempt/resume 前預檢**(檢查順序 ticket hard→soft→月→全站,誰先破誰卡 → pending:budget 帶 scope);CLI 無上限參數 → harness 外部卡;兩 metric 都量就都查、量不到讀 0 不誤卡(codex 無 cost);soft 存 session、hard 即時讀 profile(hot reload 立即生效);load 驗 soft≤hard。
- **US-I2** ✅ soft 破 → 自助增額表單(≤hard、attempt 不重來);hard/月/全站破 → 管理者改 config+reload 後自動續跑。
- **US-I3** ✅ token/cost 從串流 usage 加總進 envelope/session/journal;月/全站掃 journal 加總,per-instance 不跨實例合計;dashboard 燈號(綠<80%/黃≥80%/紅≥100%)。

### Epic J — 身分門禁

- **US-J1** ✅ description `email:`(逗號多位)首建鎖進 `owner_email_list`(後改不同步,只認 set_email);比對規則:名單空→放行;提交者 ∈ owners ∪ admin_emails ∪ {approver} → 放行;否則擋;比對前 strip+lowercase;表單與指令台都比對。
- **US-J2** ✅ 稽核:每次提交記 `submitted_by`+`submitted_ip`+內容;journal 帶 author+ip。
- **US-J3** ✅ `set_email` 整組取代(預填現值、留空=解除門禁)、逐一驗格式(任一無效整筆拒)、re-tag 每位新負責人+重發待填表單、破壞性二次確認、事件 `owner_changed`。
- **US-J4** ✅ approver 首建自動加 Jira watcher(best-effort,失敗不擋派工)。

### Epic K — 可觀測與 KPI

- **US-K1** ✅ 四層證據:L0 journal(53 種事件,固定 ts/type/issue_id/key,每行獨立 JSON)/ L1 envelope / L2 attempt log / L3 對話;**trace completeness 100% 是唯一 P1 硬指標**(`trace_lint`:completed/error 的 attempt 必有合法 L2+非空 L3,入 CI)。
- **US-K2** ✅ 事件字典由程式自動產生(`gen_event_dict.py --check` 入 CI 防漂移),語意分組手寫;退役事件可辨識。
- **US-K3** ✅ dashboard 七頁(唯讀、明暗雙主題、**零 CDN**):KPI/票表(過濾:profile/summary/desc 關鍵字或 regex,狀態入 URL 可分享)/全域 Timeline(每票狀態色帶:藍執行/黃等人/紫排隊)/單票駕駛艙(四層 trace+事件時間軸+transcript)/Introduction(狀態機+架構圖,svg-pan-zoom)/Server(8+1 紅黃綠燈+per-process/workspace)/Agent Detail(config 呈現)/DB Browser(唯讀);Swagger `/docs`(vendored)。
- **US-K4** ✅ transcript:claude/codex/sub-agent 皆可渲染 HTML(vendored claude-code-log);產生時機=事件驅動(state 變/evict/close)+按需按鈕,含 metadata sidecar(時間/原因);close 打包 tgz。
- **US-K5** ✅ KPI 框架:北極星 First-pass Close(嚴格/進行雙報)、效率(cycle time、attempts、$ per close——一律中位數/p90 不用平均)、制衡(打回率/人評中位/UNKNOWN rate/放棄+abort 原因)、coverage;三時間窗(7/30/全);**P1 不設目標值**;`GET /api/v1/kpi?days=N&profile=X`;A/B 手選對照附「非隨機分流僅供參考」警語。
- **US-K6** ✅ LLM 也能監控:唯讀 `GET /api/v1/tickets[/{ref}[/events|/logs]]`(ref 三合一;結構化 JSON 為主、原始 jsonl 可 `?tail=N`)。
- **US-K7** ✅ 效益:每票效益 = (score/10) × human_minutes_est × 時薪 − AI 花費;未評分不計入平均。

### Epic L — 外部系統整合

- **US-L1** ✅ Jira Cloud/DC 一鍵切換(`jira_flavor: dc`):端點(api/3↔api/2)、認證(API token↔PAT/Basic)、識別(accountId↔username)、mention(`[~accountid:x]`↔`[~username]`)、格式(ADF↔wiki)全自動;email→識別碼四步查序(user_map→快取→user search→username_rule);**差異顯式建模**(DC 下 accountid mention 是安靜失敗——不通知)。⏳ 真 DC 站首驗照 checklist(mention 真觸發通知最關鍵)。
- **US-L2** ✅ 狀態同步(選配):`status_sync` 五鍵映射(running→In Progress/hil_middle→Pending/hil_end→Resolve/close→Closed/abort→Cancelled);**精確按名稱轉**(不 fallback category——多 done 狀態挑錯教訓);close 兩步保險;queued/inactive 不動;未設=不轉。
- **US-L3** 🔮 CR/ClearQuest 閉環:掃 CQ 命中(title/人名/keyword)→ 開票貼 label 帶 crid(PoC 的 agent-job+task_script 已可承載);close → 回寫 CQ(Jira 連結+結果,**所有** close 含取消失敗;擴充點 `cq_writeback` 已預留,⛔ 等 CQ base_url+欄位名)。
- **US-L4** ✅ Jira 寫入退避(write_retry 指數退避);錯誤 body 必浮出(前 400 字)。

### Epic M — 部署與維運

- **US-M1** ✅ 安裝:Python ≥3.10、uv sync 即裝(rawcli 主線純 stdlib 免 venv);憑證只在 `~/.env` 絕不進版控;dashboard 只顯示金鑰有無/到期,**絕不顯示值**。
- **US-M2** ✅ 三進程:poller(+control :8787+form :8790)、dashboard(:8788);poller timebox 迭代(`-m 0` 常駐);🔮 systemd/daemon 化(B4 殘留)。
- **US-M3** ✅ 備份=三樣(config git、harness.db 用 `.backup`、events.jsonl+runs 複製);還原後續跑不重派;**絕不 wipe runtime/**。
- **US-M4** ✅ 多實例:複製資料夾,分 name/project·jql(不可重疊,否則互搶)/port/控制指向;預算 per-instance 不合計。
- **US-M5** ✅ 離線內網:凍結 snapshot 自足(文件+vendored 資產+事件字典);不連外、不裝新套件。
- **US-M6** ✅ headless 環境約束(實驗釘住):跑 poller 的機器用**乾淨 HOME**(全域 skills 全量漏入 attempt——46 個實測;訂閱登入下無 CLI 隔離開關);`CLAUDE_CODE_DISABLE_CRON=1` per-spawn/poller 環境注入(session 排程在 -p 內靜默失效);profile 明文禁背景跑交付工作(~5s 寬限被殺);CLI 版本釘選+升版冒煙。

---

## 8. 功能需求總表(FR)

> 對 §7 的濃縮索引,便於投影片與追溯。狀態同 §7。

| FR | 需求 | Epic | 狀態 |
|---|---|---|---|
| FR-01 | Jira poll→diff→route→dispatch 事件驅動 | A | ✅ |
| FR-02 | watermark 冪等(事件只處理一次) | A | ✅ |
| FR-03 | route on_match 三值(ignore/notify_only/create_or_resume)灰度 | A | ✅ |
| FR-04 | 排程 job(script-job/agent-job;count×cron) | A/L | ✅ |
| FR-05 | description yaml 契約(crid/email/prompt)+變數插值 | A/C | ✅ |
| FR-06 | triage/select(random/script、遞歸 10 層、notfound 中止、fail-safe 回 main、鎖 session) | B | ✅ |
| FR-07 | F1 三層併發閘+QUEUED | B | ✅ |
| FR-08 | workspace 佈建(template/install/skills/hooks/inject;原子性 marker;TICKET.md 渲染) | C | ✅ |
| FR-09 | envelope 跨 backend 契約+結構化輸出 schema | C | ✅ |
| FR-10 | 執行隔離 provider(seatbelt 實測;landlock/appcontainer 預留;docker ❌) | C | ✅/❌ |
| FR-11 | 證據型停止(verify files/cmd/json;grader 終審) | D | ✅ |
| FR-12 | bounded retry+失敗證據回餵 | D | ✅ |
| FR-13 | 三態 outcome;UNKNOWN 不自動重試;infra 不耗 attempt | D | ✅ |
| FR-14 | native resume(2×2 矩陣)+sid 預派+transcript 降級 | E | ✅ |
| FR-15 | stall watchdog(進展=任何 stream 行) | E | ✅ |
| FR-16 | evict killpg(不耗 attempt;1s 看門狗;計數) | E | ✅ |
| FR-17 | retention 回收(預設 270 天;證據不刪) | E | ✅ |
| FR-18 | HIL 6 態生命週期(canonical_state 唯讀推導) | F | ✅ |
| FR-19 | 一次性 token 表單 ×7 種(≥128-bit、單次、綁定、重啟有效) | F | ✅ |
| FR-20 | 審批門(require_approval;零資源等待;表單放行) | F | ✅ |
| FR-21 | 安全掃描 fail-closed+人審裁決 | F | ✅ |
| FR-22 | 評分關單(三訊號;0–10;裁決關/續/換;系統轉 Done) | F | ✅ |
| FR-23 | auto_close(off/on_success/all;outcome 不粉飾) | F | ✅ |
| FR-24 | 催辦與異常計數(1 天/10 次,可設) | F | ✅ |
| FR-25 | 交接 next/base(fail-safe 降級;base=系統建票+脈絡注入;ABORTED 不算失敗) | G | ✅ |
| FR-26 | 指令台(狀態動態選單;email 稽核;破壞性確認;close 失效) | H | ✅ |
| FR-27 | REST 控制面(status/health/pause/resume/reload/shutdown/evict/recover/command) | H | ✅ |
| FR-28 | hot reload(引用替換;壞 config 舊值續用) | H | ✅ |
| FR-29 | Jira 降級/恢復(circuit-breaker;不假裝成功) | H | ✅ |
| FR-30 | 預算六層+spawn 前預檢+自助增額 | I | ✅ |
| FR-31 | 身分門禁(owner_email_list;豁免;IP 稽核;set_email) | J | ✅ |
| FR-32 | 四層 trace+trace_lint 100% | K | ✅ |
| FR-33 | journal 事件字典自動防漂移(53 種) | K | ✅ |
| FR-34 | dashboard 七頁+Swagger(全 vendored) | K | ✅ |
| FR-35 | transcript HTML(claude/codex/sub-agent;事件驅動+按需) | K | ✅ |
| FR-36 | KPI 框架(北極星雙報+制衡;中位數;不設目標) | K | ✅ |
| FR-37 | 唯讀監控 API(/api/v1;ref 三合一) | K | ✅ |
| FR-38 | Jira DC 相容(flavor 切換;識別/格式/認證) | L | ✅(真站待首驗) |
| FR-39 | 狀態同步(五鍵;精確名稱;兩步保險) | L | ✅ |
| FR-40 | CQ 閉環(掃 CR 開票+close 回寫) | L | 🔮(I1 ⛔ 等 CQ 資訊) |
| FR-41 | 多實例/備份還原/離線自足 | M | ✅ |
| FR-42 | Postgres+leased queue(多機) | M | 🔮(A1) |
| FR-43 | systemd/daemon 化 | M | 🔮(B4) |
| FR-44 | Agent Status/Link 自訂欄位+transition condition | L | 🔮(B2,需 Jira admin) |
| FR-45 | codex `--sandbox` 端到端驗證 | C | 🔮(D2,待 quota) |

---

## 9. 非功能需求(NFR)

### 9.1 可靠性

- **NFR-R1** 崩潰恢復:任一行程(harness/agent)在任意時點被 SIGTERM/SIGKILL,重啟後不重工、不重複寫 Jira、不多花錢(at-most-once+native resume;2×2 矩陣為驗收基準)。
- **NFR-R2** 完成判定不得依賴 exit code / 事件流 / agent 自稱;**進度與完成的真值在檔案系統**。
- **NFR-R3** 睡眠韌性:筆電睡眠凍結計時器,醒後能續(watchdog 不得把睡眠誤判 stall——先查 pmset log)。
- **NFR-R4** store 是唯一冪等記憶:營運**絕不 wipe**;測試一律用隔離 config+獨立 runtime。

### 9.2 安全

- **NFR-S1** 憑證只在 `~/.env`/keychain;任何 UI/log 絕不顯示值。
- **NFR-S2** token=capability:≥128-bit、單次、綁定、短命;capability URL 視為機密。
- **NFR-S3** 三種安全模型分離:唯讀 dashboard(內網 zero-auth)/ control API(寫,預設 127.0.0.1,綁 0.0.0.0 為知情決策)/ 互動服務(寫 Jira,token 授權)。
- **NFR-S4** 注入面控管:verify cmd 不插值;TICKET.md 掃描 fail-closed;Jira 留言不進 TICKET.md;seatbelt 白名單精確 subpath(絕不含 /private/tmp)。
- **NFR-S5** 稽核完整:誰(email)/何時/從哪(IP)/送了什麼/是否遭竄改(hash),全程可答。

### 9.3 可觀測性

- **NFR-O1** trace completeness 100%(唯一硬指標,CI 強制)。
- **NFR-O2** 錯誤體必浮出(HTTP 錯誤前 400 字);「查不到」與「不存在」可區分(project 假陰性教訓)。
- **NFR-O3** 識別碼紀律:顯示名(project 名/issue type 名/狀態名)一律不當識別;用 key/id/statusCategory。

### 9.4 效能與資源

- **NFR-P1** ARCP 自身開銷須遠小於 agent 執行時長(瓶頸永遠在 model/Jira/併發,不在 harness)。
- **NFR-P2** 資源開關語意:不在機器人手上=不吃 CPU/memory(killpg 硬關,禁 SIGSTOP)。
- **NFR-P3** 測試預設便宜模型(haiku);opus 差 ~8×。

### 9.5 可維運性

- **NFR-M1** 設定 fail-fast:壞 config 死在 load(ConfigError),不上線;reload 壞值回 400 舊值續用。
- **NFR-M2** fresh checkout CI 必綠(Py 3.10–3.13);不得依賴 gitignored venv。
- **NFR-M3** 文件自足:凍結 snapshot 上,AI/人只靠 repo 文件+runtime 證據能完成除錯(標準路徑 ai-debugging→troubleshooting→observability→lessons)。

### 9.6 相容性與環境約束(headless 實驗釘住)

- **NFR-C1** 支援 Jira Cloud+DC;claude+codex;差異顯式建模於單一 source/driver 檔。
- **NFR-C2** headless 衛生(全部有實驗依據):session 排程在 `-p` 靜默失效 → 禁用(`CLAUDE_CODE_DISABLE_CRON=1`,範圍限 poller 子行程);背景工作 ~5s 被殺 → 長工前景等;`stall_seconds`=0 或>最長單一命令;subagent fan-out 要 claude、跨 cwd resume 韌性要 codex;poller 機器乾淨 HOME(全域 skills 漏入+context 稅);CLI 版本釘選+升版冒煙。

---

## 10. 驗收標準與測試計畫

### 10.1 驗收層級(PoC 已建立,團隊應沿用或等價替代)

| 層級 | 內容 | 成本 |
|---|---|---|
| **L1 離線集(CI,每 commit)** | ruff、全部單元測試、harness_selftest、e2e_dashboard、e2e_form(fake Jira+真 HTTP)、`gen_event_dict.py --check`、trace_lint 合成六情境 | 免費 |
| **L2 整測(隔離環境)** | KP2 流程:T1 完成流/T2 job 分流/T3 cancel/T4 審批 Pending/T5 安全掃描/T6 審批門/T9 插值+存證+結案回寫;browser E2E;獨立 config.test.yaml+runtime-test+獨立 port | ~$0.1–0.2 |
| **L3 付費複驗(升級後)** | v1-reverify-checklist 十步:基本派工/佈建原子性/select/hold/人類指示/自評/next/base/retry 計數/交付物 | ~$0.1–0.3 |
| **L4 真環境對照** | E1(A/B/C×claude/codex 四格同 grader 對照)、E2(crash→resume 硬證據:context 傳承/不重工/完成) | 低 |

**驗收判定原則**:每一步以 **journal 事件**為證(「做什麼→預期事件→在哪看」);任何驗收不得以 UI 目視或 agent 自稱替代事件與檔案證據。

### 10.2 關鍵驗收斷言(摘自 PoC 的承諾清單,團隊交付必須逐條可證)

1. `attempt_finished(raw=completed)` ≠ 完成;必須 grader 過才 `resolved`。
2. UNKNOWN 不自動重試;infra 故障不耗 attempt。
3. 一次性表單:填過唯讀、重啟仍有效、票關全失效。
4. Jira 掛掉:表單可看、送出不落地、恢復自動解降級。
5. evict/hold:不耗 attempt、不丟資料、下輪續跑。
6. 交接 base:新票有 `BASE_<key>/` 脈絡、原票 ABORTED 不算失敗。
7. hot reload:壞 config 服務不死、舊設定續用。
8. 預算:soft 卡得住、增額後不重跑 attempt、月/全站掃 journal 可對帳。
9. 門禁:非名單 email 提交被擋、稽核記錄齊全。
10. 備份還原:還原後 open 票不重派、不重花錢。
11. 存證:結案票離開 ARCP 後,僅憑 Jira 附件能回放全程。
12. trace_lint 100%;事件字典無漂移。

### 10.3 KPI(上線後量測框架——不是驗收門檻)

- **北極星**:First-pass Close rate(嚴格/進行雙報;無人為返工=沒 retry 指令、沒打回、沒換手)。
- **效率**(全用中位數/p90):Cycle time、Attempts per close、Cost per close(含返工)、Throughput。
- **制衡**(防作弊,效率指標必配):打回率、人評中位、UNKNOWN rate、Abandonment(+abort 原因分布)。
- **原則**:P1 只建基線不設目標;First-pass 升但人評/打回變差=在調鬆 verify(作弊訊號)。

---

## 11. 第二期範圍(未來需求,依 BACKLOG 殘留)

| 項 | 內容 | 前置/阻塞 |
|---|---|---|
| **I1** close→CQ 回寫(所有 close;`cq_writeback` 擴充點已留) | ⛔ CQ base_url+欄位名 |
| **R9** WITS/ClearQuest 觸發源(掃 CR 命中 title/人名/keyword→開票+記 crid;類比 Jira poller) | CQ/WITS API 存取 |
| **L4 Evolution Agent** 讀 ticket/CR status+session log+gerrit,找出做不完/中斷問題,計畫修改/增加/實驗,強化 scan/fix/dev agents 的 skills(自主閉環) | 平台留存的人類介入紀錄與四層 trace(先上平台是前置) |
| **A1** SQLite→Postgres+leased queue(lease/heartbeat/reaper,參考 qm) | 多機生產需求成立時 |
| **B1** 真 Jira Server/DC 站首驗(checklist 已備;mention 通知最關鍵) | 公司環境 |
| **B2** Agent Status/Link 自訂欄位+transition condition | Jira admin 權限 |
| **B4** systemd/daemon 常駐化 | — |
| **D2** codex `--sandbox` 端到端 | quota |
| **E3** 閒置 evict→rehydrate 對照 | 已被 stall watchdog+resume 覆蓋,優先級低 |
| **Cloud 三坑實測** | user search 無權限回空/emailAddress 隱私空/個人 email 查不到——拿真帳號驗 canary 區分 |
| 其他 | 動態工時預測(取代 human_minutes_est 靜態值)、KPI Layer attribution/MTTD/reopen 率、A/B 家族自動分組、timeline.html 單檔視覺化、description email 改動自動同步 | 均為記錄性候選 |

---

## 12. 依據與追溯

| 本書章節 | 正本文件 |
|---|---|
| §1.1–1.2 階梯與斷層/§6.0 艦隊 | 原始需求口述稿(2026-08,L2→L3 Agent Architecture)+ 手繪資訊圖卡 [`docs/l2-l3-infographic.html`](l2-l3-infographic.html) |
| §1 世界觀/§5 約束 | `docs/decisions.md`(D1–D14)、`docs/requirements.md` §0 |
| §6 場景 | `docs/walkthrough-cr-to-agent.md`、`docs/user-guide.md`、`docs/operator-guide.md` |
| §7 Epic A–M | `docs/requirements.md` §1–15 + `docs/design/`(lifecycle/architecture/interaction/workspace/selection/budget/identity-gate/agent-output/observability/database/hotreload/kpi/security-scan/jira-dc/provenance/idempotency/isolation/transcript) |
| §9 NFR | `docs/lessons.md`(17 條)、`docs/research/2026-08-headless-scheduling-subagents.md`(6 實驗)、`docs/interactive-to-headless.md` |
| §10 驗收 | `docs/v1-reverify-checklist.md`、`tests/`(CI 離線集)、`tests/it_kp2.py`、`docs/design/kpi.md` |
| §11 第二期 | `BACKLOG.md`(主題 A–N 殘留項) |
| 已實測事實 | `docs/research/README.md` 結論層(crash-recovery/backend-abc/jira-integration/qm-comparison)、`HANDOFF.md` §3 |

> **給接手團隊的三句話**:①**證據型停止與三態 outcome 是本系統的靈魂**,任何簡化都會
> 退化成「信 agent 自稱」而重蹈假完成覆轍;②**Jira 只放策展摘要、細節在後台**,守住
> 單一寫入者與受控表單,Jira 才能一直是可信帳本;③PoC 的每一條教訓(§9、lessons.md)
> 都是真金白銀踩出來的,重寫時請先讀完再設計。
