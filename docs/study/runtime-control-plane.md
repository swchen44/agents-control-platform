# Agent Runtime / Control Plane 總體研究(結論)

> **核心缺口:`claude -p`、`codex exec` 這類 headless coding agent 有能力,卻缺一層「跨 CLI 一致的可觀測 + 可控制」——一致的 crash recovery、統一的 trace/狀態機、卡住偵測、以及不採信 agent 自稱的證據型停止。ARCP 要做的不是發明編排,而是把 trace/control 做成跨 CLI 一致的那一層。**

這份文件策展 ARCP 專案的源頭研究:兩版 deep-research 報告(v2 市場/可行性驗證、v3 收斂到工程可執行)。它回答三個問題——**為什麼要做**(共同缺口何在)、**要做什麼**(需求規格與差異化)、**架構取向**(三條實作路線如何抉擇)。

## 研究問題(headless agent CLI 的共同缺口)

需求的本質是「**可觀測 + 可控制**」,不是「更聰明的模型」。v3 借 2026-07 最出圈的三層排障地圖(Bijit Ghosh,LangChain 官方認領)把「agent 又抽風了」翻譯成可執行的排查工單:

| 層 | 管什麼 | 它修的失敗 |
|---|---|---|
| **Harness** | 環境(model wrapper / runtime) | 「模型無法安全地做這件工作」 |
| **Loop** | 回饋(有界的可重複循環) | 「agent 停太早,或沒證據就宣稱做完」 |
| **Graph** | 流程(步驟的有向圖) | 「工作流難以推理或控制」 |

巢狀關係:**graph 跑在 harness 裡;loops 活在 graph 裡;harness 供應 loops 需要的 state、tools 與 evaluators。**

把上線痛點對號入座後,ARCP 要 trace/control 的點幾乎全落在 **Harness 的 Observability + Execution control + Safety/governance**,加上 **Loop 的證據型停止**:

- **跨 session 忘記進度、SSH 斷線全沒了** → Harness:持久化 state、checkpoint、session registry、記錄 `(session_id, cwd, flags)`。
- **多步流程失敗難定位** → Harness + Graph:與狀態機對齊的**有狀態 trace**(統一 event log)。
- **跑一圈宣稱「做完了」但產出全壞** → Loop:**證據型停止規則**(測試/schema/人審通過才算 done,不對 confidence 循環)。
- **重試二十次帳單翻三倍 bug 還在** → Loop:有界重試 + 預算感知停止 + 卡住偵測。
- **headless 沒人按 Allow 永遠卡住** → Harness:waiting-permission 偵測 + approval queue / 升級人類。
- **多給五個工具反而亂調用** → Harness:工具寧窄勿寬(依 issue 只裝該裝的 skills)。

v2 更早已用對抗式驗證確立:「agent control plane」是 2026 最擁擠的新類別之一,但**「跨 CLI 一致語意」這一層是空的**。學術量化佐證(435 篇編碼文獻,僅約 6% 實作任何 rollback,且無人報告 state 損毀後的復原成功率)——「**缺口不在原語不存在,而在整合與落地**」,這正是工程專案(而非新研究)的切入點。

三層文的七條實踐直接納入 ARCP 設計原則,其中兩條決定了 MVP 範圍:
- **先跑 Trace 再畫 Graph**——業務沒跑通就上圖編排是頭號昂貴錯誤。
- **工具寧窄勿寬**——別把 harness 當垃圾場。

> 直接推論:**先做好 Harness + Loop(trace / control / 證據停止 / rules 選 skills),暫時不上 Graph 編排。** Jira pipeline 目前是「一個 issue → 一個 agent 跑一個有界任務」,還沒有實質分支/並行/審批鏈,過早上圖只會更脆。

## 提出的取向(supervisor 一級公民、OpenHands 可插拔對照、差異化層投入哪裡)

v3 攤開三條實作路線,結論是**混合:主線 A(raw 一級)+ 可選後端 B(OpenHands 對照),C 只做差異化層。**

- **A. Raw subprocess(自寫 supervisor 包 `claude -p` / `codex exec`)**——與日常用法一致、控制最直接(直接掌 PID、stdin/out、flags)、無第三方黑箱;代價是 recovery/approval 要自己補,且 CLI schema 會漂移(需版本化 + 協定回歸測試)。**設為一級公民。**
- **B. OpenHands ACP(agent-server 當底層)**——現成的 event-sourced、crash-safe conversation server + WS 監控 + 程式化核准,省掉大量地基;代價是多一層黑箱(ACP 外部 agent 對它不透明)、與 raw 用法不同、三方版本收斂壓力大。**留作對照與可選後端。**
- **C. 從零寫完整 runtime**——控制力最大、能實作誰都沒做的 git checkpoint 語意層;代價是與已擁擠的類別重造 80%。**不建議當作達成 Jira 需求的手段;若要做開源專案,只做差異化那幾層,疊在前兩條之上。**

**差異化層投入哪裡**——v2 逐項比對後,四個「沒人做」的缺口是 ARCP 走 C 時該做、也只該做的:

1. 跨 CLI 的 **session 層級 crash recovery**(持久化 `(session_id, cwd, flags)` → 偵測進程死亡 → `--resume` 重接原對話)。
2. 跨 CLI 的**執行狀態機 / 卡住偵測**(在外部 CLI 事件流上判 thinking / tool / 等 permission / retry / stalled)。
3. 跨 CLI 統一的**執行中 approval queue**。
4. **git checkpoint 語意層**(事件史 ↔ 工作區 git 狀態對齊)——學術驗證確認是未解問題,競爭最少,最乾淨的差異化點。

**Driver 策略:不自創協定,對齊 ACP。** ACP(Agent Client Protocol,JSON-RPC over stdio)已有 claude/codex/gemini adapter 生態;ARCP 的價值在 ACP 之上/之外的 supervisor 語意(狀態機、recovery、checkpoint、集中 approval——皆非 ACP 協定範圍)。三條路在 ARCP 裡都實作同一個 `Driver` Protocol,可對照跑。

**無論走哪條路都要自己補的(這是真正的工作):** Jira Server watcher、assignee/keyword 的 JSON 規則引擎、依規則裝 skills 的 glue、跨 CLI 一致的狀態機/卡住偵測/crash recovery。前三項 PoC 已示範;recovery 是最硬、也最有差異化價值的一塊。

## v2 → v3 的演進(有什麼改變 / 釘死了什麼事實)

- **v2**:市場缺口驗證 + 技術可行性 + 企業整合模式(對抗式驗證為主,106 agents 三票對抗)。確立「跨 CLI 一致語意」缺口成立、定位收窄為「headless CLI coding agent 的跨 CLI session 層級 runtime / supervisor」、Driver 對齊 ACP。
- **v3**:在此之上**收斂到工程可執行**——需求規格化(三層地圖 → FR-H/L/C 編號)+ 開工級設計(元件架構、統一 `AgentEvent` schema、狀態機、REST API、rules.json、workspace spec)+ 三路線優缺點與維護成本對照 + **可跑 PoC 與真實 schema 實測**。v2 的第三方判定與缺口結論在 v3 直接引用,不重驗。

**v3 用本機實測釘死的關鍵事實(不再是文件推論):**

- **終止語意不對稱**:`claude -p` 有明確 `result` 事件;`codex exec` 靠 `turn.completed` + process exit,無獨立終止事件——統一層要吸收這差異。
- `claude -p` 的 **`--session-id <uuid>` 可預先指定**,是 supervisor 端 resume 的關鍵(不必等 CLI 回傳才知道 id);codex 的 thread id 無法預指定,但從 `thread.started` 事件**事後擷取**來得及。
- **Crash recovery 基線已實測可行**:claude 與 codex 各以「尚無產出 / 工具執行中 × SIGTERM/SIGKILL」2×2 矩陣測試,`--resume` / `codex exec resume` **全過**——同一 session/thread id、記得進度、**不重工**、任務補完;單 case 成本 $0.03–0.07(haiku)。詳見 [crash-recovery](crash-recovery.md)。
- **⚠️ codex 收 SIGTERM 會優雅退場 rc=0**:「事件 OR exit code」雙判據會把中斷的 run 誤判 DONE——**exit code 不能當完成證據**,這把證據型停止從加分項升級為必要。
- **kill 必須 killpg**:否則 codex 的子程序孤兒續跑、任務在 supervisor 背後被偷偷做完;codex 工具粒度變異大,**事件流不可當進度真值,要以檔案系統/工作區真值為準**。
- **claude session store 綁啟動時 cwd**:workspace `mv`(或 git worktree)之後原生 resume 死於 `No conversation found`——但 **transcript 降級救回**(journal 不綁 cwd),證明了「原生 resume → transcript 注入 → 全新重跑」三段梯度第二階的必要。
- **claude permission 矩陣實測**:v2 記的 mode 詞彙已換代為 acceptEdits/auto/bypassPermissions/manual/dontAsk/plan;**headless 下沒有任何 mode 會掛住等核准**(拒絕是立即的,agent 收到 denial 後續跑)——supervisor 應盯事件流中的 denial,不是偵測卡住。acceptEdits 實際範圍比名稱寬(連 Bash `touch` 都放行)。
- `opencode acp` 子命令本機存在(v2 只能推論)。
- **PoC 已跑通**:Jira issue → rule 命中 → 建 workspace + 裝 skill → 監督 `claude -p` / `codex exec` → 統一 trace 到 `done`;離線 replay 與 live 全流程(各約 $0.02)皆過。

一項被 v2 自己推翻的教訓:先前 v1 寫入的「permission 文件級描述」在對抗式驗證中未存活(0-3),v3 以實測釐清——**permission 精確行為必須以實測為準**。

## 生產就緒的缺口清單(擷取原文)

v3 §8 套用三層文檢查清單對照 ARCP MVP 現況:

| 層 | 檢查項 | ARCP MVP 狀態 |
|---|---|---|
| Harness | 工具窄、有文件、可觀測? | ✅ rules 選 skills;統一 trace |
| Harness | 狀態耐久?能暫停/檢視/恢復? | ✅ snapshot;resume 基線已補 |
| Harness | 權限最小? | ✅ permission-mode / sandbox 可設 |
| Loop | 什麼證據證明成功? | ✅ 證據型 grader 已補(先前靠 exit code / result 事件) |
| Loop | 失敗回什麼回饋?幾次重試?預算耗盡? | ⚠️ 有界重試 / 預算待補 |
| Graph | 哪些路徑確定性?人工閘門? | N/A(本階段不上 graph) |
| Evaluation | 能重播 trace、比較版本? | ✅ events.jsonl 可 replay;⚠️ 版本比較待補 |
| Operations | cost/延遲/失敗率/人工介入率被監控? | ⚠️ cost/state 有;聚合 dashboard 待補 |

**三層文最強調、ARCP 最該優先補的兩項**:**Loop 的證據型停止**(別讓 agent 自稱 done)與 **cross-CLI recovery**。v3 記錄兩項基線皆已補上並接成迴路——grader 覆寫機制 + claude/codex resume 實測 + 自動 recovery 迴路(live 驗證:crash 與 rc=0 假完成皆自動修復)。

**§9.3 尚未做的深水區**:長跑 / 大 context 下的 resume、opencode via ACP 相容性、有界重試與預算上限、REST/WS + dashboard 控制面。

## 對 ARCP 的影響(這份研究如何定調了三路線與後續 wave)

1. **定調三路線的分工**:raw supervisor 是一級公民(專案 PoC `examples/jira-agent-poc/` 的主體),OpenHands ACP 是可插拔對照後端(`examples/openhands-acp-poc/`,claude 側已實跑,A 248 事件 vs B 14 事件的粒度對照,詳見 [backend-abc](backend-abc.md)),從零寫只保留給差異化層。這正對應 repo CLAUDE.md 的一句話:「**OpenHands 只是候選方法之一,不是前提**」。

2. **釘死 MVP 範圍**:第二部分的 FR-H1~H7 / FR-L1~L5 / FR-C1~C5(不含 recovery 深水區與 graph 編排);先做 trace + control + rules + skills 跑通 Jira pipeline,再談複雜編排。

3. **後續 wave 的技術債表**:v3 §9.3 的 PoC 實驗清單直接成為後續 wave 的骨架——crash recovery 矩陣、證據型停止 grader、permission 行為矩陣、OpenHands ACP 對照、waiting-permission → 開 Jira ticket 升級迴路、自動 recovery 迴路、journal → transcript 降級 resume,多數已在 PoC 落地並 live 驗證。

4. **從研究帶入實作的硬性紀律**:driver 只吃官方 stream-json / `--json`,**不解析內部 transcript JSONL**;每個 driver 用真實 fixtures 做**協定回歸測試**(schema 一變測試就紅);kill 一律 killpg;完成與否一律靠證據型 grader,不信 exit code。

5. **保留的開放選項**:使用者立場備忘(2026-08-02)——未來不排除「直接拿 OpenHands 改」作基底(他們把 resume 容錯、model 重套、id 遮罩等坑都走過);現階段持續觀察、暫不決策,Driver 介面保持可插拔以保留此選項。

## 原始出處

- v3(主源,收斂到工程可執行 + PoC 實測):[../../research/2026-08-agent-runtime-control-plane-research-v3.md](../../research/2026-08-agent-runtime-control-plane-research-v3.md)
- v2(前版,市場缺口/技術可行性/企業整合的對抗式驗證):[../../research/2026-07-agent-runtime-control-plane-research.md](../../research/2026-07-agent-runtime-control-plane-research.md)
