# Agent Runtime / Control Plane 市場缺口驗證、技術可行性與企業整合模式研究報告（v2）

- **研究日期**：2026-07-31 ~ 2026-08-01（v2：完成全部對抗式驗證與六項深查後改版）
- **研究方法**：
  - deep-research 多 agent 管線（問題拆解 → 5 路並行搜尋 → 來源抓取與 claim 萃取 → **每個 claim 三票對抗式驗證** → 合成）：因額度中斷分三輪跑完，最終 **106 個 agent 全數完成**，產出 8 項合併後的已驗證 finding、4 項被推翻的 claim。
  - 六路補充深查 agent（agx、Anthropic 官方 repo + Agent AFK、omnara + superplane、Jenkins + OpenCode/Amp、OpenHands OSS 本體 + agent-server），皆讀到官方文件與原始碼層。
- **背景**：本專案（agents-control-platform，暫名 ARCP）計畫建立開源 Agent Runtime / Control Plane，讓 Claude Code、Codex CLI 等 headless coding agent 能長時間可靠執行。功能構想：crash recovery、session attach/resume、human approval queue、git checkpoint、多 agent 調度、observability、外部事件整合（Jira/Jenkins）。

## 可信度標註說明

| 標記 | 意義 |
|------|------|
| ✅ 已驗證 | 通過三票對抗式驗證（獨立驗證 agent 以一手來源逐字核對、嘗試反駁失敗；標註得票） |
| ⚠️ 未經對抗驗證 | 來源為官方文件/原始碼（深查 agent 直接讀取），固有可信度高但未跑三票程序 |
| ❌ 已被推翻 | 對抗式驗證 ≥2/3 票認定該說法過度延伸或錯誤（保留正確核心的改寫版另行標註） |

---

## 執行摘要（TL;DR）

**1. 「幾乎沒有 open source 做這件事」已確定不成立（✅ 多輪驗證）**——「agent control plane」是 2026 年最擁擠的新類別之一：mission-control（✅ 3-0×3）、LiteLLM Agent Control Plane（✅ 3-0×3）、**OpenHands / Agent Canvas（本研究最重要的發現）**、superplane、omnara（已死）、以及 Temporal 正式進場 durable execution 層（✅ 3-0×4）。

**2. 最大的單一發現：OpenHands 已重定位為「self-hosted developer control center for coding agents」**，透過 **ACP（Agent Client Protocol）** 可以 spawn 並監督 Claude Code / Codex / Gemini CLI / OpenCode——它就是朝 ARCP 的定位收斂的 82.7k stars、MIT 專案。同時它的 agent-server 提供現成的 supervisor-ready HTTP/WS 控制面（pause/interrupt/fork/navigate/程式化核准）。**ACP 的存在也改變 RFC 策略：driver 協定應評估對齊 ACP，而非另行發明。**

**3. 但 ARCP 的精確缺口在逐項比對後仍然成立，且邊界更清楚了：「跨 CLI 一致語意」這一層是空的。** OpenHands 的 crash recovery 與 Stuck Detector 只對自家 agent loop 完整（ACP 外部 agent 是黑箱）；跨 agent 集中 approval queue 被劃在其 Enterprise 版；git checkpoint（事件史 ↔ 工作區 git 狀態對齊）沒有任何專案做。學術量化證據支持（✅ 3-0）：435 篇文獻僅 6% 實作任何 rollback；runtime 原語「存在但與框架脫節——缺口不在原語、在整合落地」；coding agent 的 memory 層未綁定 git commit graph 的 rollback 原語。

**4. 技術可行性**：Claude Code（✅ 3-0×2）與 Codex CLI（✅ 3-0×3）的 headless + 事件流 + resume 經對抗式驗證確認；OpenHands（agent-server）、OpenCode（serve API）、Amp（Claude 相容 stream-json + 雲端 thread）由原始碼級深查確認（⚠️）。**注意：一項關於 Claude Code permission 細節的說法被 0-3 推翻**——permission 精確行為必須以實測為準（見 2.3）。

**5. 決策建議不變但升級**：值得做，定位收窄為「headless CLI coding agent 的跨 CLI session 層級 runtime/supervisor」；driver 層對齊 ACP、把 OpenHands agent-server 列為一級 driver、把 mission-control 視為可能的整合對象；最乾淨的差異化是 git checkpoint 語意層與跨 CLI 卡住偵測。詳見第四部分。

---

# 第一部分：市場缺口驗證

## 1.1 結論：類別已擁擠，但「跨 CLI 一致語意」的缺口經逐項驗證仍然存在

原始假設（「大部分 framework 做 Agent Workflow 而非 Agent Runtime」）的**方向正確且獲得學術量化支持**，但「幾乎沒人做」的部分已過時。2026-08-01 的精確圖景：

- 生態盤點（awesome-cli-coding-agents，2026-07-29 更新）：90+ CLI coding agents、約 40 個 session manager / parallel runner、約 30 個 orchestrator / autonomous loop（⚠️）。
- 文獻量化（✅ 3-0，arXiv:2606.30306，2026-06 survey，435 篇編碼文獻）：**僅 27 篇（約 6%）實作任何 rollback 機制**（2025 年前 0%、2026 年 9.5%），且沒有任何研究報告 state 損毀後的復原成功率——「recovery/rollback 是最未被解決的能力」；文獻重心在累積/檢索 state（workflow 面向），不在治理/復原/撤銷 state（runtime 面向）。
- 學術 runtime 原語已存在但脫節（✅ 3-0，medium confidence）：AIOS、Quine、AgentLibOS、ActPlane、DeltaBox、CRAB、OpenRATH 等提供 checkpoint-restore、process identity、audit 原語，但「典型部署的 agent 是把一個 ungoverned store 疊在一個本可治理它的 runtime 上」——**缺口不在原語不存在，而在整合與落地**，這正是工程專案（而非新研究）的切入點。

## 1.2 第一類：Workflow Engine / Durable Execution

### Temporal 與 LangGraph 的精確分界（✅ 3-0×4，本輪最完整的驗證群）

- Temporal 於 2026-07-16 推出**官方 LangGraph plugin**（Python、Public Preview、`temporalio[langgraph]`）：既有 LangGraph agent 獲得 automatic failure recovery、crash-surviving runs、可等數天的 human-in-the-loop——主流 workflow vendor 已直接進入「agent durable execution」層。
- LangGraph 自身的 checkpointing 是 **durable data 而非 durable execution**：failure 後的 recovery 是**手動的**（需自行偵測失敗、以相同 thread_id 重新 invoke）；其 checkpointer/store 原語（thread 級 snapshot、HIL、time travel）只適用於 LangGraph graph 建構的 agent。
- ❌ **被推翻的強版本**（1-2）：「LangGraph 完全沒有 execution 級 crash recovery、process 死了 run 就死了」——LangChain 官方文件明文提供 persistence 層與 resume 流程，且第一方 LangGraph Platform（LangSmith Deployment）就是含 fault tolerance 的託管 runtime。報告用詞維持保守版：**durable data、手動 recovery**。
- 共同限制（✅）：這一整類恢復的是**自家 graph/workflow**，無法監督 Claude Code / Codex CLI 這類外部 headless CLI process。

## 1.3 第二類：開源 Orchestrator / Control Plane

### ⭐ OpenHands / Agent Canvas + ACP（⚠️ 原始碼級深查——**最直接的競品，也是最現成的 driver**）

[OpenHands/OpenHands](https://github.com/OpenHands/OpenHands)（82,713 stars，MIT，v1.8.0，beta）已重定位：

> "The self-hosted developer control center for coding agents and automations. Run OpenHands, Claude Code, Codex, Gemini, or any ACP-compatible agent across local, remote, and cloud backends."

關鍵事實：

1. **「只管自家 agent」的假設自 2026-06 起失效**。經 **ACP（Agent Client Protocol，JSON-RPC over stdio）**，其 SDK 以 subprocess spawn 第三方官方 CLI（`ACPAgent(acp_command=["npx","-y","@agentclientprotocol/claude-agent-acp"])`），支援 Claude Code、Codex、Gemini CLI、OpenCode——事件串流、permission 請求、session resume、token/cost 全部過橋。
2. **V1 架構 = [software-agent-sdk](https://github.com/OpenHands/software-agent-sdk)**（四包：sdk/tools/workspace/**agent-server**），event-sourced 設計（arXiv:2511.03690："an event-sourced state model with deterministic replay"、"V1 reduces system-attributable failures by 61% relative to V0"）。conversation 為 append-only EventLog，官方明言 "conversation state is always recoverable, even if the process crashes unexpectedly"。
3. **agent-server**（[原始碼目錄](https://github.com/OpenHands/software-agent-sdk/tree/main/openhands-agent-server/openhands/agent_server)）是現成的 supervisor-ready 控制面：REST + WebSocket + webhook，端點含 `POST /conversations/{id}/run|pause|interrupt|fork|navigate`（interrupt 立即取消 in-flight LLM 呼叫；navigate 是 event-tree 時間旅行）、`respond_to_confirmation`（程式化核准/駁回 pending action）、`confirmation_policy`、git 唯讀觀測（changes/diff/commits）。Docker image：`ghcr.io/openhands/agent-server:latest-python`。
4. **Stuck Detector 內建且預設開啟**：五種 pattern（同 action-observation 重複 4+、同 action-error 3+、monologue 3+、ping-pong 6+、重複 context window error），語意比對。
5. **OSS vs Enterprise 分界**：runtime 核心（event sourcing、pause/resume、confirmation、ACP、stuck detector、agent-server 全部端點）都在 MIT 開源側；商業版收多租戶治理（SAML/RBAC）、one-click 整合（Slack/Jira）、規模化 sandbox fleet、LLM gateway/budgets、觀測聚合。

**與 ARCP 四項差異化的逐項重疊**：

| ARCP 功能 | OpenHands 現況 | 重疊度 |
|---|---|---|
| (a) session 層級 crash recovery | 自家 conversation event-sourced、crash-safe；**ACP 外部 agent 的 recovery 依賴該 agent 自身 resume 能力**，OpenHands 只保存自己側的事件與 session 引用 | 高（自家）／中（別家）——**跨 CLI 一致的 recovery 語意仍空** |
| (b) 執行狀態機／卡住偵測 | Stuck Detector 完整但掛在自家 conversation 事件上；**ACP agent 內部 tool loop 是黑箱** | 高（自家）／低（別家）——**跨 CLI 卡住偵測沒有** |
| (c) 統一 approval queue | 自家有 confirmation policy + REST 核准端點；ACP 也把 permission 請求過橋；但**跨多 agent、跨 session 的集中 approval queue/審計流被劃在 Enterprise** | 中、且升高中——OSS 側有窗口但要快 |
| (d) git checkpoint 語意層 | git router 唯讀；fork/navigate 是**事件史**的分支回捲，不是**工作區 git 狀態**的 checkpoint/rollback | 低——「事件史 ↔ git 狀態對齊」是 ARCP 最乾淨的差異化點 |

另注意其限制：純 headless CLI 模式強制 `always-approve`（無人工核准可掛）；本機互動 CLI session 無 attach 介面（要監督需跑 agent-server 形態）；ACPAgent 對外部 agent 的控制粒度低於自家（tools/mcp_config 等不可傳）。

### builderz-labs/mission-control（✅ 3-0×3——功能面重疊最大的直接先行者）

[github.com/builderz-labs/mission-control](https://github.com/builderz-labs/mission-control) — MIT、TypeScript、2026-02 建立、v2.3.0（2026-07-25）、5,889 stars（22 forks 比例異常，星數判讀保守）、實質單人維護。

- **已驗證涵蓋**：明確支援 Claude Code/Codex；程式碼實作 per-agent session dispatch（`claude --session-id` 建 persistent base session、`claude --resume <uuid> --fork-session` 派工）；approvals（Aegis task 品質閘門）、schedules、webhooks、activity stream、token/cost、heartbeat + stale-task requeue。
- **已驗證未涵蓋**：README/CHANGELOG/docs 經 grep **零次提及 crash recovery 與 git checkpoint**；其恢復是 task 層級 requeue（整個 task 重排），非 session 續接；approval 是 task 完成閘門，非 mid-run tool 核准；自認 alpha、"Adapter depth varies by runtime"（Codex adapter 深度未經驗證，可能只是淺層 dispatch）。

### LiteLLM Agent Control Plane（✅ 3-0×3）

[github.com/LiteLLM-Labs/litellm-agent-control-plane](https://github.com/LiteLLM-Labs/litellm-agent-control-plane) — MIT、Rust、2026-05-07 建立、約 1,180 stars、LiteLLM 創辦人親自開發、最後 push 2026-06-20。

- 已實作（原始碼驗證）：persistent sessions、CRON、cross-session memory、unified API；**codebase 中另有 README 未寫的 HITL approval inbox 與 Slack 整合**（報告不可宣稱其完全沒有 approval 能力）。
- **已驗證邊界**：支援的 runtime 全為託管/API 型（Claude Managed Agents、Cursor Agents API、OpenCode、OpenClaw、Deep Agents、Hermes）；repo 內的 `claude.rs`/`codex.rs` 只是把模型呼叫導向 LiteLLM gateway 的設定精靈；`claude_code.rs` harness 是 one-shot `bypassPermissions` 執行、無 resume——**本機 CLI 長時間可靠執行監督的缺口未被此專案填補**。

### omnara（⚠️ 原始碼深查——**最有價值的反面教材**）

[github.com/omnara-ai/omnara](https://github.com/omnara-ai/omnara)（2,656 stars，Apache-2.0）——與 ARCP 最相似的先行者：本機 CLI wrapper（Claude Code/Codex/Amp）+ 手機/網頁監看與核准。**已於 2025-11-09 正式宣告停止維護**，官方死因：

> "This version was built as a wrapper around the Claude Code CLI, **which became unfeasible to maintain with Claude Code's constant updates.**"

原始碼證實其技術路線的脆弱性：PTY 包裹 + 掃 TUI buffer 抓 "Permission required" 字串 + 以「'esc to interrupt' 字樣消失 3.5 秒」判 idle；無 crash recovery（進程退出 wrapper 即自我了結）；三種 CLI 三套做法無統一抽象。**教訓：ARCP 走 headless 結構化事件流正好繞開 omnara 的死因；但 headless schema 同樣會變——adapter 層必須版本化 + 協定回歸測試。**

### agx（⚠️ 原始碼深查——名字響亮但不填缺口）

[github.com/ramarlina/agx](https://github.com/ramarlina/agx)（26 stars、2026-04-29 後停更、**repo 無 LICENSE 檔**、實質單人）。awesome list 描述「durable Wake→Work→Sleep loops that resume instantly across sessions」有誤導性，實查：

- 其「resume」是**刻意丟棄原對話**、從語意 checkpoint（objective/criteria/plan/dontRepeat + git HEAD sha + dirty-diff patch）重建 ~1,800-token prompt 開**全新 process**——自家文件明言 "AGX agents are stateless between runs"。全 repo 無任何 `--resume`/session-id 重接邏輯。
- stream-json 只抽 assistant 文字，tool_use/permission 事件全丟；卡住偵測只有 30 分鐘 SIGKILL；所有 run 掛 `--dangerously-skip-permissions`/`--yolo`。
- 對 ARCP：(a)(b)(c) 不重疊（哲學相反：stateless re-run 派 vs live session 重接派）；(d) 部分重疊——其 git checkpoint schema（HEAD sha + `git diff HEAD` patch + `apply --3way` 還原 + 5 分鐘 auto-checkpoint + `[checkpoint:]` marker 協定）是可參考的設計；另外 `apps/local/lib/cli-runner.ts` 是六家 CLI headless 旗標的現成對照範本。無 LICENSE 檔 → 不宜整合，僅作設計參考。

### Agent AFK（⚠️ 原始碼深查——互補，且是 (b)(c) 的最佳 prior art）

[github.com/griffinwork40/agent-afk](https://github.com/griffinwork40/agent-afk)（46 stars、Apache-2.0 open-core、單人但極活躍）。**不是 CLI 的 control plane——是自建 runtime**（直接呼叫 model API，不 spawn `claude -p`）。但其內部機制值得 RFC 引用：

- **Stall 偵測設計原則**（原始碼註解品質極高）：TTFB timeout、progress-aware 的 `stream-stall-timeout`（"Slow is legal; stalled is not. A total-duration bound cannot tell them apart; a reset-on-progress bound can."）、防 heartbeat-gaming 的 idle-watchdog（progress-aware + pause-aware + tool-in-flight 暫停計時）。
- **park-and-notify 核准迴路**：模型提問時經 Telegram 通知操作者，"waiting minutes/hours for an AFK operator is the designed behavior… deliberately no deadline"；無人回應則進 Blocked terminal state。
- 明確 terminal states、append-only trace receipt（`TraceWriter.sealOnProcessExit()` 在進程死亡時封存 `incomplete:true`）。

### superplane（⚠️ 深查——server 端 workflow 層，互補非競品）

[github.com/superplanehq/superplane](https://github.com/superplanehq/superplane)（4,372 stars、Apache-2.0、Semaphore CI 創辦團隊、$2.6M pre-seed、每週一版）。event-driven workflow control plane（Go）：agent 執行在雲端 sandbox 或 Runners（**Runner 預裝 Claude Code/OpenCode/Codex CLI**，但只當黑盒 step——不解析 stream-json、不做 session resume）。approval 是 workflow node 間的多人 RBAC gate，非 tool-call 級；durable execution 是 step 級（重跑=任務重來）。**命名注意**：它自稱 "the open source control plane for agentic engineering"——ARCP 敘述需明確區隔（machine-local session runtime vs server-side workflow orchestration）；甚至可以定位成「ARCP 作為 superplane runner 上 coding agent step 的 session 層 supervisor」。

### anthropics/cwc-long-running-agents（⚠️ 深查——官方 pattern 示範，非產品）

[github.com/anthropics/cwc-long-running-agents](https://github.com/anthropics/cwc-long-running-agents)（603 stars、僅 3 commits、README 明言 "Event demo; not maintained"）——Code with Claude 2026 大會示範品：default-FAIL verify gate、fresh-context evaluator subagent、PROGRESS.md handoff + commit-on-stop、kill-switch/steer 檔案旗標、外層 `while` 迴圈重呼 `claude -p`。**真正的官方 canonical 是它引用的兩篇 Anthropic Engineering 文章**（RFC 應引用並對齊術語）：

- [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)（2025-11-26）
- [Harness Design for Long-Running Application Development](https://anthropic.com/engineering/harness-design-long-running-apps)（2026-03-24）

### 其他（⚠️ 名錄級）

claude-squad（tmux 多 session）、Crystal（parallel worktree）、claude-flow（swarm workflow）——皆為 multiplexing/workflow，無 durable runtime 主張。OpenHands 官方 blog 的三層論（Harness/Orchestrator/Control Plane）與「Stripe Minions、Coinbase Forge 自建」證言見 [openhands.dev/blog/agent-control-plane](https://www.openhands.dev/blog/agent-control-plane)。

## 1.4 第三類：商用產品（⚠️ 本類 claim 未經對抗式驗證，來源為官方公告與二手報導）

| 產品 | 已產品化的能力 | 明確不涵蓋 / 限制 |
|---|---|---|
| Anthropic Claude Managed Agents（2026-04） | 官方託管 runtime：Brain/Hands/Session、sandboxed execution、checkpointing、credential scoping、$0.08/session-hour | 閉源託管 |
| Claude Code Routines（2026-04-14 preview） | cron（≥1h）、專屬 HTTP endpoint、GitHub 8 類事件觸發、雲端執行 | 執行中無人工核准、失敗不自動 retry、15 runs/日、`claude/` 分支限制 |
| Claude in Slack / auto mode / remote control | @Claude、ambient 監控、classifier 篩 permission、跨裝置接續 | Enterprise/Team 限定 |
| Cursor Background Agents + Automations | 8 小時級長跑、worktree 隔離 + PR 回流、Slack/Linear/GitHub/PagerDuty/webhook 觸發、跨 run memory | 閉源託管；Privacy Mode 不可用 |
| GitHub Copilot coding agent | issue 指派 → Actions VM → PR；Agent HQ | 綁 GitHub 生態 |
| Devin | Jira/Slack 整合、playbook 路由、multi-org 路由 | 取消 $500 Core 方案 |
| Google Agent Runtime | 數天級自主、Agent Sessions、Memory Bank、Sandbox | 平台綁定 |

共同點：全部把 agent 搬進自家雲。**開源、自架、supervise 本機（或自選環境的）CLI** 仍是商用產品共同留下的空位。

## 1.5 第四類：學術（驗證後修訂）

- **AIOS**（✅ 3-0×2，COLM 2025，開源）：AIOS kernel 提供 scheduling、context/memory/storage 管理、access control——概念層與 ARCP 重疊。其 context manager 的 snapshot/restore 粒度是**單次 LLM generation 的暫停恢復**（beam search tree state/已解碼文字），非 CLI session/process 層級 crash recovery。repo 實作比論文簡化（issue #285）。
- **Always-On Agents survey**（✅ 3-0×3，medium confidence——單一來源、未同儕審查的 2026-06 preprint，方法學紀錄完整）：435 篇文獻僅 6% 實作 rollback；**coding agent 的 memory 層未綁定 git commit graph 的 rollback 原語（revert commit 不會 revert 衍生 memory）——正是 ARCP git checkpoint 對應的未解問題**（2026-07 的 MemTxn 新作亦未用 version-control rollback 語意）。
- **DeltaBox**：❌ 兩項具體 claim 被推翻（0-3 與 1-2）——「沒有任何主流系統提供快速中間狀態 checkpoint/restore」與毫秒級數字均不可引用為事實。保留的謹慎表述：sandbox 層快照有研究原型存在，ARCP 的 checkpoint 應停在 git/session 語意層。

## 1.6 缺口的精確邊界（v2 修訂——本研究的核心結論）

**已被填補、不要再做的**：類別概念與 governance UI（mission-control、LiteLLM ACP、OpenHands Enterprise）；framework 型 agent 的 durable execution（Temporal ✅）；雲端託管長跑 + 排程/事件觸發（商用四家）；LLM routing/budget（LiteLLM）；**單一 runtime 內的 stuck detection 與 confirmation（OpenHands 對自家 agent 已完整）**；平行 session/worktree 管理。

**仍然開放、可辯護的缺口（全部指向同一件事：跨 CLI 一致語意）**：

1. **跨 CLI 的 session 層級 crash recovery**：持久化 `(session_id, cwd, 啟動旗標)` → 偵測進程死亡 → `claude --resume` / `codex exec resume` 重接**原對話**。mission-control 只有 task requeue（✅）；OpenHands 對 ACP 外部 agent 只存自己側引用；agx 是丟棄對話重跑（⚠️ 原始碼證實）。
2. **跨 CLI 的執行狀態機（卡住偵測）**：由 stream-json/JSONL 事件判斷 thinking / tool / 等 permission / API retry / stalled。OpenHands Stuck Detector 只看得到自家事件；Agent AFK 的 watchdog 設計原則是最佳 prior art 但對象是自家 SSE stream。**沒有人把它做在外部 CLI 的事件流上。**
3. **跨 CLI 統一的執行中 approval queue**：OpenHands OSS 有單 conversation 核准（跨 agent 集中版在 Enterprise）；Claude Code 走 hook/SDK 路徑（細節需實測，見 2.3）；Codex 執行中無互動核准。統一層有真實價值與真實難度。
4. **git checkpoint 語意層（事件史 ↔ 工作區 git 狀態對齊）**：學術驗證確認這是未解問題（✅）；OpenHands 的 fork/navigate 只回捲事件史；agx 的 checkpoint schema 可參考但綁死自家模型。**這是 ARCP 最乾淨、競爭最少的差異化點。**
5. **Driver 層**：**不再建議發明新 spec**——ACP 已存在且 OpenHands 生態在推（claude/codex/gemini 的 ACP adapter 已有）。ARCP 應評估：driver 層對齊 ACP + 補 ACP 沒有的監督語意（狀態機、recovery、checkpoint 是 ACP 協定外的 supervisor 關注點）。

**反方論點（誠實保留）**：build-vs-buy 主流建議仍是「用現成 harness + managed runtime」；Atlassian 工程師對無人值守效益的質疑；大廠與 OpenHands 補洞速度極快——(c) 的 OSS 窗口尤其可能在數月內關閉。

---

# 第二部分：技術可行性——Headless CLI 能力對照（v2）

## 2.1 能力對照表（六家）

| 能力 | Claude Code ✅ | Codex CLI ✅ | Gemini CLI ⚠️ | OpenCode ⚠️ | Amp ⚠️ | OpenHands ⚠️ |
|---|---|---|---|---|---|---|
| Headless 執行 | `claude -p`（官方預告 `--bare` 將成 `-p` 預設） | `codex exec` | `-p`（非 TTY 自動） | `opencode run` | `amp -x` | `openhands --headless -t` |
| 事件流 | `--output-format stream-json`（NDJSON；`system/init` 含 capabilities、`system/api_retry`） | `--json`（JSONL：`thread.started`、`turn.*`、`item.*`、`error`） | `stream-json`（init/message/tool_use/tool_result/error/result） | `--format json`（**無正式 schema 文件**）；**`opencode serve`：SSE `GET /event` + OpenAPI 3.1**（六家唯一原生 server） | `--stream-json`（**官方自稱盡量相容 Claude Code 格式**；`--stream-json-input` 可 stdin 驅動多輪） | stdout JSONL + **agent-server WS/REST/webhook 三管道** |
| Session resume | `--resume <id>`／`--continue`／`--fork-session`（目錄範圍限制；worktree 情境有不可靠回報 issue #48835） | `codex exec resume --last`／`resume <SESSION_ID>`（JSONL 落地 `~/.codex/sessions/`；SDK 有 resumeThread） | headless 文件未提（缺口） | `--continue`／`--session <id>`／`--fork`（headless 下有 "Session not found" 實績 #28407） | `amp threads continue <id>`——**thread 存雲端、跨機器 resume（獨有）**；mid-turn crash 語意未文件化 | conversation event-sourced、官方明言 crash-safe；REST pause/interrupt/fork/navigate |
| 程式化 permission | `--allowedTools`、permission modes、SDK `canUseTool`、PreToolUse hook（**精確行為需實測，見 2.3 第 5 點**） | `--sandbox` 三級（啟動時靜態；**執行中無互動核准**） | 文件未提 | `--auto`、`OPENCODE_PERMISSION` env JSON、serve 模式可 API 側控 | 預設**不問核准**；legacy `amp.permissions`（含 `delegate` 轉外部 helper——天然 supervisor 掛載點）；Neo 後改 plugin 制；SDK `createPermission()` | 三 policy（Always/Never/ConfirmRisky+SecurityAnalyzer）+ REST `respond_to_confirmation`；**純 headless CLI 強制 always-approve** |
| 官方長跑/伺服器 | Routines / Managed Agents（雲端；本機無） | Codex cloud tasks | 無 | **`opencode serve`**（headless API server） | runners、Enterprise Workspace API | **agent-server**（Docker、WS/REST/webhook） |

驗證級距說明：Claude Code 與 Codex 的 headless/事件流/resume 為 ✅（3-0 對抗驗證）；其餘四家為 ⚠️（官方文件與原始碼深查，未跑三票程序）。

## 2.2 可行性判定：成立，且 supervisor 介面分兩型

- **subprocess 型**（Claude Code、Codex、Gemini、OpenCode run、Amp）：spawn CLI + 解析 stdout 事件流 + resume flag 重接。
- **server 型**（OpenHands agent-server、OpenCode serve、Amp 雲端 thread）：HTTP/WS attach，天然支援遠端 pause/interrupt。
- ARCP 的 driver 抽象應同時涵蓋兩型；Amp 的 Claude 相容 stream-json 使其 adapter 邊際成本最低。

## 2.3 關鍵工程陷阱（v2 修訂）

1. **Resume 的目錄範圍**（Claude Code ✅）：session lookup 限原專案目錄及其 git worktrees；**worktree 情境有不可靠回報（issue #48835）**——supervisor 須持久化 `(session_id, cwd, 旗標)` 並將 resume 失敗視為一級路徑處理。
2. **Resume 不還原的狀態**（Claude Code ⚠️）：`bypassPermissions`/`plan` 永不還原；`--mcp-config`、`--settings`、`--add-dir` 等需重傳。
3. **並發寫入**（Claude Code ⚠️）：同 session 雙進程 resume 不 fork 會交錯寫 transcript——調度器須對 session 互斥。
4. **不要解析 transcript JSONL**（Claude Code ⚠️）：內部格式、版本間會變；用 stream-json/hooks/SDK。
5. **❌ Permission 行為的文件級描述被推翻（0-3）**：先前寫入 v1 的「`dontAsk` 拒絕一切、`acceptEdits` 下未核准操作直接 abort、approval callback 僅限 SDK」等敘述**在對抗式驗證中未存活，不可視為已驗證事實**。這不代表相反為真——代表官方文件表述與實際行為的對應需要**實測釐清**。行動項：ARCP PoC 的第一批實驗就應包含 permission 行為矩陣測試（各 permission mode × 未核准 tool call 的實際行為：abort？掛起？拒絕後續跑？）。
6. **Codex 無執行中核准**（✅ sandbox 旗標為啟動時靜態）：統一 approval queue 對 Codex 只能做「失敗 → 人核准 → 升權 resume」補償模式。
7. **OpenCode headless 穩定性實績**（⚠️ issue 群）：`run` 有 hang-on-API-error（#8203）、tool call 後不退出（#17516）、靜默提前退出（#13946/#28605）、headless resume 失敗（#28407）、JSON 流缺 user turn（#29997）——**adapter 應以 `opencode serve` HTTP+SSE 為第一路徑**，subprocess 為 fallback 且必配 watchdog、exit code 0 不可信。
8. **Amp 的兩面性**（⚠️）：協定相容性最好（Claude parser 可近乎複用）+ 雲端 thread 跨機器 resume；但 exit code 語意未文件化（以 `result` 訊息判成敗）、thread 強制上雲（資料治理）、**破壞性變更頻繁**（Neo 全重寫、permission 制度一年內改制）——版本 pin + 協定回歸測試必備。
9. **Gemini CLI 最弱**（⚠️）：headless 文件無 resume、無 approval mode；exit code 53 = turn limit。
10. **OpenHands 的 approval 形態綁定**（⚠️）：純 headless CLI 強制 always-approve——要掛核准就必須跑 agent-server 形態。

---

# 第三部分：企業整合模式（v2：補上 Jenkins）

> 本部分多數 claim 未經對抗式驗證（合成階段明確標註 Q3 證據存活率最低）；Jenkins 一節為深查 agent 以官方 plugin 頁/原始碼補齊（⚠️）。

## 3.1 觸發模式分類

| 模式 | 實例 |
|---|---|
| Assignment-based | Devin：Jira ticket 指派給 service account；GitHub Copilot：issue 指派 → Actions VM → PR |
| Label / mention-based | Devin playbook labels（`!plan`/`!implement`）；OpenHands：label `fix-me` 或 `@openhands-agent`（先驗證觸發者 repo 權限） |
| Automation-rule / webhook | Cursor Automations（Slack/Linear/GitHub/PagerDuty + 自訂 webhook）；Rovo Dev（Jira Automation 觸發 stored prompt）；Claude Code Routines（GitHub 8 類事件、bearer token endpoint） |
| Schedule / cron | Routines（≥1h）、Cursor Automations、OpenHands OSS 的 scheduled/polling automations、LiteLLM ACP CRON |

## 3.2 Jenkins 整合現況（v2 新增——上一輪的空白已補）

結論：**真實存在但極早期，空白本身已被證據確認**。

1. **Anthropic 官方零 Jenkins 文件**：headless 頁只連 GitHub Actions 與 GitLab CI/CD。
2. **關鍵發現：[jenkinsci/ai-agent-plugin](https://github.com/jenkinsci/ai-agent-plugin)**——Jenkins 官方 org 第一個泛用 AI agent build step plugin（2026 年成形、13 stars、4 天前才發版、單一維護者）。支援 7 家 agent（Claude Code、Codex、Cursor、OpenCode、Antigravity、Gemini、Grok Build），以 headless 旗標啟動並解析 stream-json，**在 build 頁面即時渲染對話、tool calls、thinking blocks，且有 approval gates（暫停 build 等人審後才執行 tool）與 token/cost 統計**。本質是「嵌在 Jenkins 內的 mini agent runner + observer」——綁死 build 生命週期、無 session 管理/resume——是潛在整合對象而非完整競品，**值得持續追蹤甚至貢獻**。
3. **社群模式**：pipeline 內裸呼叫 `claude -p`（credentials + `--max-turns` + `--allowedTools`）——多篇 2025-2026 blog 證實這是主流 DIY 拼法。
4. **Webhook 模式實例**：Jira comment → Jira Automation webhook → Jenkins [Generic Webhook Trigger Plugin](https://github.com/jenkinsci/generic-webhook-trigger-plugin) → agent 執行層 → Jira comment 回寫（2026-05 完整實作文）。Jenkins 在此只當 dispatcher，缺 session 監督層——正是 ARCP 切入點。
5. **反向整合**：[jenkinsci/mcp-server-plugin](https://github.com/jenkinsci/mcp-server-plugin)（官方 MCP server）讓 agent 查 build/觸發 build。
6. **CloudBees**：Unify（"AI-powered context and control plane for enterprise DevSecOps"）+ MCP server + Agentic DevOps World 峰會——**delivery governance 層，與 ARCP 互補**；無「在 Jenkins 內跑 coding agent」產品。

## 3.3 參考架構與 approval 模式（⚠️）

- OpenHands enterprise 整合管線：webhook receipt（FastAPI）→ HMAC-SHA256 驗證 → 外部 ID → Keycloak 身分映射 → conversation 派發 → 閉環回貼（GitHub App installation token）→ webhook + exponential-backoff polling 混合。位於 `enterprise/` 目錄（非 OSS 核心——本身即缺口訊號）。
- Devin scoping-only 模式（先貼 plan + confidence、人工放行再執行）＝pre-flight approval 形態；Atlassian 複雜度分流（低中→雲端 agent、高→本機人審）；Permit.io 最佳實踐（destructive ops 設 explicit checkpoint、approval gate 內建於執行環境保留 context、policy before prompt）。
- Webhook vs polling：webhook 有重複投遞/亂序/silent gap；polling 燒 rate limit 且部分企業 SaaS 無 webhook。實務形態：webhook 為主 + reconciliation polling 補漏 + event queue 去重。

---

# 第四部分：決策建議（v2 修訂）

## 4.1 該不該做？——值得做，附三個修正

1. **prior art 誠實化**：RFC 必須引用 mission-control、LiteLLM ACP、OpenHands/ACP、Temporal、兩篇 Anthropic Engineering 文章、Always-On survey——這讓 spec 更有公信力，也是學術驗證過的敘事（缺口在「整合與落地」而非「原語不存在」）。
2. **定位收窄且更精確**：從「Agent Control Plane」改為「**headless CLI coding agent 的跨 CLI session 層級 runtime / supervisor**」。「跨 CLI 一致語意」是每一項差異化共同的辯護理由——單 runtime 內的版本 OpenHands/Agent AFK 都做了，跨家統一的沒有人做。
3. **Driver 層策略改變**：**評估對齊 ACP 而非自創協定**。ACP 已有 claude/codex/gemini adapter 生態；ARCP 的價值在 ACP 之上/之外的 supervisor 語意（狀態機、recovery、checkpoint、集中 approval queue——皆非 ACP 協定範圍）。同時 subprocess 直連（stream-json/JSONL）仍需保留為第二路徑，因 ACP adapter 未必暴露全部原生事件。

## 4.2 該做什麼（差異化核心，v2 排序）

1. **git checkpoint 語意層（升為第一優先）**：「事件史 ↔ 工作區 git 狀態對齊」——學術驗證確認未解（✅）、OpenHands 沒做、agx 有可參考 schema、競爭最少。這是 RFC 裡最能站住的一章。
2. **跨 CLI 執行狀態機**：在外部 CLI 的事件流上實作 stall/waiting-permission/api-retry 判定；借鏡 Agent AFK 的 reset-on-progress watchdog 原則與 OpenHands Stuck Detector 的 pattern 清單。
3. **跨 CLI session 層級 crash recovery**：`(session_id, cwd, 旗標)` 持久化 + `--resume`/`exec resume` 重接 + 2.3 陷阱清單的系統化處理（含 resume 失敗的降級路徑——參考 agx 的「語意 checkpoint 重建 prompt」作為 fallback tier）。
4. **跨 CLI 統一 approval queue**：Claude（hook/SDK 路徑，行為先實測）、OpenHands（REST respond_to_confirmation）、Amp（permission delegate/plugin）、Codex（失敗→升權 resume 補償）。注意 OpenHands 把集中版劃在 Enterprise——OSS 窗口存在但會關。
5. **Driver 抽象（雙型）**：subprocess 型 + server 型；一級 driver：Claude Code、Codex、OpenHands agent-server；二級：Amp（parser 複用）、OpenCode（serve API）、Gemini（最弱）。

## 4.3 不該重造什麼（v2）

| 不重造 | 替代 |
|---|---|
| Durable execution 引擎 | Temporal（✅ 已進場） |
| Driver 通訊協定 | **ACP**（對齊/擴充，不另造） |
| 單 runtime 的 conversation server | **OpenHands agent-server**（直接列為一級 driver） |
| LLM routing / budget / cost | LiteLLM 生態 |
| Governance / spend UI | mission-control（評估貢獻/整合） |
| Sandbox 層快照 | 研究原型存在（具體效能數字未驗證，勿引用）；ARCP 停在 git/session 語意層 |
| CI 內嵌 agent runner | jenkinsci/ai-agent-plugin（追蹤/貢獻） |
| 雲端託管 runtime、平行 worktree UI | 商用四家；既有 ~40 專案 |

## 4.4 風險與時間窗（v2）

- **OpenHands 是最大的收斂壓力**：ACP 路線 + agent-server + 82.7k stars 社群。它往「跨 CLI 統一語意」走只差幾步（尤其 (c)）；ARCP 的機會在於它把集中治理留給 Enterprise、且 ACP agent 對它是黑箱。
- **mission-control**：單人但活躍，已做 per-agent session dispatch，往 session-level recovery 走是時間問題。
- **時效性**：本報告是 2026-08-01 快照；mission-control（alpha）、LiteLLM ACP（experimental）、Temporal plugin（Public Preview）、Routines（preview 限制）都在快速演化。
- **價值主張爭議**：無人值守長跑的效益仍有一線工程師質疑——RFC 應把「人在迴圈的效率」納入設計目標。

## 4.5 建議的下一步（v2）

- **A. RFC-0001 動筆**：範圍=1.6 的五項缺口；prior art=4.1 清單；術語對齊 Anthropic 兩篇 engineering blog + ACP。
- **B. PoC 實驗清單（先於大量寫碼）**：(1) **Claude Code permission 行為矩陣實測**（驗證被推翻的文件級描述的實際行為）；(2) `claude -p` crash → `--resume` 重接的可靠性測試（含 worktree 情境 #48835）；(3) `codex exec resume` 同項測試；(4) ACP adapter 事件豐富度 vs 原生 stream-json 對照——決定 driver 雙路徑的取捨。
- **C. 生態接觸**：評估對 mission-control（session-level recovery）與 jenkinsci/ai-agent-plugin（session 管理）的貢獻路徑；追蹤 OpenHands ACP roadmap。

---

# 附錄

## A. 主要來源清單

**已驗證發現的一手來源（✅）**
- https://github.com/builderz-labs/mission-control （README、CHANGELOG、docs/cli-integration.md、docs/orchestration.md、src/lib/claude-code-sessions.ts）
- https://github.com/LiteLLM-Labs/litellm-agent-control-plane （README、scheduler.rs、sessions migrations、approval inbox 原始碼）
- https://temporal.io/blog/temporal-langgraph-plugin-durable-execution 、 https://docs.temporal.io/develop/python/integrations/langgraph
- https://docs.langchain.com/oss/python/langgraph/durable-execution
- https://arxiv.org/abs/2403.16971 （AIOS）、 https://github.com/agiresearch/AIOS
- https://arxiv.org/pdf/2606.30306 （Always-On Agents survey）、 https://arxiv.org/abs/2605.17444 （MemTxn）
- https://code.claude.com/docs/en/headless 、 https://code.claude.com/docs/en/sessions
- https://developers.openai.com/codex/noninteractive

**深查一手來源（⚠️ 原始碼/官方文件級）**
- OpenHands：https://github.com/OpenHands/OpenHands 、 https://github.com/OpenHands/software-agent-sdk （含 [agent_server 目錄](https://github.com/OpenHands/software-agent-sdk/tree/main/openhands-agent-server/openhands/agent_server)）、 https://docs.openhands.dev/ （headless、persistence、security、stuck-detector、agent-acp、enterprise-vs-oss）、 https://www.openhands.dev/blog/use-any-coding-agent-in-openhands-with-acp 、 https://arxiv.org/abs/2511.03690
- agx：https://github.com/ramarlina/agx （checkpoints.js、git.js、resume.js、runs.js、executor.js、cli-runner.ts、state-machine.ts、SKILL.md）
- Agent AFK：https://github.com/griffinwork40/agent-afk 、 https://docs.agentafk.com
- omnara：https://github.com/omnara-ai/omnara （claude_wrapper_v3.py、headless/claude_code.py、stdio_server.py、git_utils.py）
- superplane：https://github.com/superplanehq/superplane 、 https://docs.superplane.com
- cwc：https://github.com/anthropics/cwc-long-running-agents ；canonical：https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents 、 https://anthropic.com/engineering/harness-design-long-running-apps
- Jenkins：https://github.com/jenkinsci/ai-agent-plugin 、 https://plugins.jenkins.io/ai-agent/ 、 https://github.com/jenkinsci/generic-webhook-trigger-plugin 、 https://github.com/jenkinsci/mcp-server-plugin 、 https://shenxianpeng.github.io/en/posts/2026/jira-ai-agent/ 、 CloudBees Unify 相關
- OpenCode：https://opencode.ai/docs/cli/ 、 https://opencode.ai/docs/server/ 、 https://opencode.ai/docs/github/ 、 anomalyco/opencode issues #8203/#17516/#16380/#13946/#28605/#10012/#28407/#29997/#2923
- Amp：https://ampcode.com/manual 、 https://ampcode.com/manual/appendix 、 https://ampcode.com/news/streaming-json 、 https://ampcode.com/news/neo 、 https://ampcode.com/manual/sdk 、 https://ampcode.com/security

**商用/企業整合（⚠️）**：InfoQ Code with Claude、Routines guide、cursor.com/blog/automations、openhands.dev/blog/agent-control-plane、docs.devin.ai/integrations/jira、Atlassian Rovo blog、Permit.io HITL、awesome-cli-coding-agents、slavadubrov.github.io、addyosmani.com/blog/long-running-agents

## B. 對抗式驗證結果摘要（最終）

**8 項合併後的已驗證 finding**：mission-control（3-0×3）、LiteLLM ACP（3-0×3）、Temporal/LangGraph 分界（3-0×4）、AIOS（3-0×2）、學術 runtime 原語脫節（3-0，medium）、rollback 文獻量化 + git-memory 綁定缺口（3-0×3，medium）、Claude Code headless/resume（3-0×2）、Codex exec/resume（3-0×3）。

**4 項被推翻**：LangGraph「完全無 execution 級 recovery」強版本（1-2）；DeltaBox「無主流系統提供快速 checkpoint」（0-3）與毫秒級數字（1-2）；**Claude Code permission 文件級描述**（0-3——需實測）。

## C. 研究限制（v2）

1. **企業整合模式（第三部分）的多數 claim 未經對抗式驗證**——合成階段明確標註 Q3 證據存活率最低；引用 Devin/Rovo/Cursor 細節前建議複核官方文件。
2. **Gemini/OpenCode/Amp/OpenHands 的能力對照未跑三票程序**（深查 agent 讀官方文件與原始碼，可信度高但級距不同）。
3. **Claude Code permission 精確行為未定**（❌ 0-3）——列為 PoC 第一批實驗。
4. **文獻量化數據依賴單一未同儕審查 preprint**（arXiv:2606.30306）。
5. **mission-control 的 Codex adapter 深度未驗證**；其星數比例異常。
6. **時效性**：全部為 2026-08-01 快照，主要專案皆在快速演化。
