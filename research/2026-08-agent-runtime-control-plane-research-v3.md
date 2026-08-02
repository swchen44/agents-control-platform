# Agent Runtime / Control Plane 研究報告 v3 —— 從「上線可 trace 與 control」到落地選型與開工級規格

- **研究日期**:2026-08-01
- **相對前版**:v2(`2026-07-agent-runtime-control-plane-research.md`)確立了市場缺口與技術可行性;v3 在此之上**收斂到工程可執行**:(1) 把「上線常出錯,要能 trace 和 control」用三層排障地圖(Harness / Loop / Graph)明確定義成需求;(2) 整合 v2 七大能力 + 本輪 Jira pipeline + trace&control 成一份需求規格;(3) 給出**從零寫也能完成目標**的開工級設計(元件、統一 event schema、狀態機、REST API、rules.json、workspace spec);(4) 逐項比較三條實作路線(raw subprocess / OpenHands ACP / 從零寫)的優缺點與**維護成本**;(5) 附一個已實測跑通的 PoC(`examples/jira-agent-poc/`)。
- **研究方法**:v2 的 deep-research 多 agent 管線(106 agents 三票對抗驗證)+ 本輪 103-agent 複驗 + 兩個 OpenHands repo 原始碼級探索 + **本機實測**(claude 2.1.206 / codex-cli 0.142.5 / opencode 的真實事件流與 live 執行)+ 三份一手材料比對(ChatGPT「Claude headless 解決方案」RFC 討論、Bijit Ghosh 三層架構文、v2 報告)。
- **核心背景**:本專案(agents-control-platform,暫名 ARCP)要讓 `claude -p`、`codex exec` 這類 headless coding agent 能長時間可靠執行、可觀測、可控制、可從外部事件(Jira)驅動。**OpenHands 只是其中一個候選方法,不是前提**——本報告刻意寫到「若從零寫也能完成目標」的深度。

---

## 執行摘要(TL;DR)

**1. 需求的本質是「可觀測 + 可控制」,不是「更聰明的模型」。** 上線常出錯的痛點,用 2026-07 最出圈的三層排障地圖(Harness 管環境 / Loop 管回饋 / Graph 管流程)一拆就清楚:你要的 trace & control 幾乎全部落在 **Harness 層的 Observability 與 Execution control**,以及 **Loop 層的證據型停止規則**。這兩層「沒有一條需要買新框架,全是工程紀律」(三層文原話)。ARCP 的價值主張因此不是發明編排,而是把 trace/control 做成**跨 CLI 一致**的一層。

**2. 三條實作路線,建議「raw 一級 + OpenHands 對照」的混合。**
- **Raw subprocess(自寫 supervisor 包 `claude -p` / `codex exec`)**——與你日常用法一致、控制最直接、無第三方黑箱;代價是 recovery/approval 要自己補,且 CLI schema 會變(需版本化 + 協定回歸測試)。**設為一級公民。**
- **OpenHands ACP(agent-server 當底層)**——現成的 event-sourced、crash-safe conversation server + WS 監控 + 程式化核准,省掉大量地基;代價是多一層黑箱(ACP 外部 agent 對它不透明)、與你 raw 用法不同、版本收斂壓力大。**留作對照與可選後端。**
- **從零寫完整 runtime(ChatGPT RFC 路線)**——控制力最大、能實作誰都沒做的 git checkpoint 語意層;代價是與已擁擠的類別重造 80%。**不建議作為達成 Jira 需求的手段;若要做開源專案,只做差異化那幾層,疊在前兩條之上。**

**3. 本輪已用實測釘死幾個關鍵事實(不再是文件推論):**
- `claude -p` 與 `codex exec` 的真實事件 schema 已抓取並正規化(見 §6),兩者**終止語意不對稱**(claude 有明確 `result` 事件;`codex exec` 靠 `turn.completed`+process exit)——這正是統一層要吸收的差異。
- `claude -p` 有 `--session-id <uuid>`,可**預先指定 session id**,是 supervisor 端 resume 的關鍵(不必等 CLI 回傳才知道 id)。
- **Crash recovery 基線已實測可行(2026-08-01)**:`claude -p` 以預指定 session-id 啟動,在「尚無任何產出」與「工具執行中」兩時機分別以 SIGTERM/SIGKILL 殺掉(2×2 矩陣),`--resume <id>` **4/4 全部成功重接**——事件流帶同一 session id、agent 記得進度、**不重工**(crash 前已建檔案 mtime 不變)、任務補完;單 case 成本 $0.03-0.07(haiku)。改用 killpg(殺整個 process group)複驗仍 4/4。見 §9.3-1 與 `examples/jira-agent-poc/recovery_test.py`。
- **codex crash recovery 亦實測可行(2026-08-02)**:thread id 無法預指定,但從 `thread.started` 事件**事後擷取**來得及(連 turn.started 時殺都擷取得到)→ `codex exec resume <id>` 重接成功(2×2 全時機皆過;midtool×SIGTERM 於睡眠 artifact 釐清後補測 2/2 乾淨 PASS)。同輪釘住的陷阱:**⚠️ codex 收 SIGTERM 會優雅退場 rc=0**,「事件 OR exit code」雙判據會把中斷的 run 誤判 DONE——exit code 不能當完成證據,證據型停止(§9.3-2)從加分項升級為必要;resume 子命令**不吃 `--sandbox`**(rc=2,要 `-c sandbox_mode="..."`,driver 已修);kill 必須 **killpg** 否則 codex 的 zsh 子程序孤兒續跑、任務在 supervisor 背後被偷偷做完;codex 工具粒度/指令服從度變異大(同 prompt 有時單指令打包五步、有時逐步、有時無視 sleep),**事件流不可當進度真值,要以檔案系統/工作區真值為準**。
- `opencode acp` 子命令本機存在,OpenCode 的 ACP 路徑可行(先前 v2 只能推論)。
- PoC 已跑通:Jira issue → rule 命中 → 建 workspace + 裝 skill → 監督 `claude -p` / `codex exec` → 統一 trace 到 `done`。

**4. 需要自己補的,無論走哪條路都一樣(這是真正的工作):** Jira Server watcher、assignee/keyword 的 JSON 規則引擎、依規則裝 skills 的 glue、跨 CLI 一致的狀態機/卡住偵測/crash recovery。前三項 PoC 已示範;recovery 是最硬、也最有差異化價值的一塊(claude 端基線已實測可行,見 §9.3-1;worktree/codex/長跑情境待驗)。

---

# 第一部分:問題定義 —— 用三層排障地圖把「trace & control」講清楚

## 1.1 為什麼要先做這一步

你的新增需求是「上線常出錯,要能 trace 和 control」。這句話若不拆層,會退化成「加 log + 加 retry」。2026-07 的三層架構文(Bijit Ghosh,X 上 58 萬瀏覽/6.5 萬收藏;LangChain 官方 Harrison Chase 親自回應認領)提供了一張把「agent 又抽風了」翻譯成「可執行排查工單」的地圖。ARCP 要解的問題,精確地落在這張地圖的特定格子裡。

## 1.2 三層心智模型(environment → feedback → flow)

| 層 | 管什麼 | 核心物件 | 它修的失敗 |
|---|---|---|---|
| **Harness** | 環境(operational capability) | model wrapper / runtime | 「模型無法安全地做這件工作」 |
| **Loop** | 回饋(iteration & evidence) | 有界的可重複循環 | 「agent 停太早,或沒證據就宣稱做完」 |
| **Graph** | 流程(explicit control flow) | 步驟的有向圖 | 「工作流難以推理或控制」 |

巢狀關係:**graph 跑在 harness 裡;loops 活在 graph 裡;harness 供應 loops 需要的 state、tools 與 evaluators。**

## 1.3 把你的痛點對號入座:故障定位決策表 → ARCP 職責

三層文的「症狀 → 層 → 修法」決策表,直接映射到 ARCP 要做的事:

| 症狀(你上線遇到的) | 該查層 | ARCP 對應職責(= 要 trace/control 的點) |
|---|---|---|
| agent 跨 session 忘記進度、SSH 斷線全沒了 | **Harness** | 持久化 state、checkpoint、session registry、`(session_id, cwd, flags)` 記錄 |
| 多步流程中的失敗難以定位 | **Harness + Graph** | 與狀態機/節點對齊的**有狀態 trace**(統一 event log) |
| agent 跑一圈宣稱「做完了」但產出全壞 | **Loop** | **證據型停止規則**(測試/schema/人審通過才算 done,不對 confidence 循環) |
| 重試二十次 token 帳單翻三倍 bug 還在 | **Loop** | 有界重試 + 預算感知停止 + 卡住偵測 |
| headless 沒人按 Allow 永遠卡住 | **Harness** | waiting-permission 偵測 + approval queue / 升級人類 |
| 多給五個工具反而亂調用 | **Harness** | 工具寧窄勿寬(rules.json 依 issue 只裝該裝的 skills) |

**結論:你要的「trace & control」= Harness 的 Observability + Execution control + Safety/governance,加上 Loop 的證據型停止。** 這正是 ARCP 這一層的定義,也是本報告需求規格(第二部分)的骨架。

## 1.4 三層文的七條實踐(直接納入 ARCP 設計原則)

1. **證據驅動的停止規則**——"Do not loop on confidence. Loop on evidence."
2. **有界重試**——每個循環寫清楚:可測目標、每輪新證據、最大重試數、具名兜底路徑。
3. **最小權限**——權限/網路隔離/白名單/密鑰/人工授權一樣不省。
4. **進度檔 + Git 歷史做持久化**(Anthropic 多 session 經驗)。
5. **確定性檢查優先於模型自評**——同模型自寫自評有共同盲區。
6. **先跑 Trace 再畫 Graph**——業務沒跑通就上圖編排是頭號昂貴錯誤。
7. **工具寧窄勿寬**——別把 harness 當垃圾場。

> 對 ARCP 的直接推論(對應三層文第 6、7 條):**先做好 Harness+Loop(trace/control/證據停止/rules 選 skills),暫時不要上 Graph 編排。** 你的 Jira pipeline 目前是「一個 issue → 一個 agent 跑一個有界任務」,還沒有實質分支/並行/審批鏈,過早上圖只會更脆。這也決定了 ARCP MVP 的範圍。

---

# 第二部分:需求規格(整合 v2 七大能力 + Jira pipeline + trace&control)

以下用 FR(功能需求)編號。標 ★ 者為本輪(trace&control + Jira)新增或升級的重點。

## 2.1 Harness 層 —— 環境與可觀測(trace 的家)

- **FR-H1 Headless 執行**:以 headless 模式啟動 `claude -p` / `codex exec`(一級)、可選 OpenCode(`opencode acp`/`serve`)、OpenHands SDK/agent-server。
- **FR-H2 Workspace 隔離**:每個 issue 一個獨立工作資料夾(或 git worktree);可指定 repo/branch。
- **FR-H3 依規則裝 skills ★**:依 JSON 規則(keyword/assignee)把選定的 AgentSkills 裝進該 workspace(`.claude/skills/` 或 `.openhands/skills/`,或啟動時注入)。
- **FR-H4 統一 trace ★**:所有 worker 的原生事件正規化成單一 `AgentEvent` schema,append-only 落地成可稽核 event log,並映射到執行狀態機。**這是「多步失敗難定位」的解法。**
- **FR-H5 Observability ★**:每個 run 的 running time、state、last event、token、cost、retry count 即時可查;支援 observer/回呼(dashboard、告警、Jira 回寫)。
- **FR-H6 Persistence / Session registry**:持久化 `(run_id, agent, session_id, cwd, flags, state)`;supervisor 可 `kill -9` 後由此重建。
- **FR-H7 Safety/governance**:最小權限(permission-mode / sandbox 等級)、密鑰以 env/secret 注入不落 log。

## 2.2 Loop 層 —— 回饋與證據(control 的核心)

- **FR-L1 執行狀態機 ★**:NEW→STARTING→RUNNING↔THINKING/RUNNING_TOOL→WAITING_PERMISSION/WAITING_HUMAN→RECOVERING→DONE/FAILED/STALLED,由事件驅動。
- **FR-L2 卡住偵測 ★**:reset-on-progress watchdog(「slow is legal; stalled is not」)——無進度超時且非合法等待態 → STALLED。
- **FR-L3 證據型停止 ★**:任務完成判定不採信 agent 自稱,而以確定性檢查(測試通過/schema/檔案存在/CI)為準;無證據不標 DONE。
- **FR-L4 有界重試 + 預算**:最大重試次數、token/cost 上限;超限進兜底路徑(升級人類)。
- **FR-L5 控制面 ★**:pause / resume / kill / interrupt;waiting-permission 超時 → 升級(開 Jira ticket 轉人類)。
- **FR-L6 Crash recovery ★**:worker 死亡 → 依 exit code 與 last state 決定 resume(`claude --resume <id>` / `codex exec resume <id>`)或重跑;resume 失敗視為一級路徑。

## 2.3 Connector 層 —— 外部事件驅動(Jira pipeline)

- **FR-C1 Jira Server watcher ★**:輪詢(JQL `updated >= -Nm`)為主,webhook 為可選;無需 Jira 管理員權限。
- **FR-C2 規則引擎 ★**:JSON 規則,match 條件支援 assignee / keywords_any / keywords_all / status;action 指定 agent / skills / repo / model / prompt_template;first-match-wins。
- **FR-C3 Dispatcher ★**:命中規則 → 建 workspace + 裝 skills → 起監督 run。
- **FR-C4 結果回寫**:run 完成/失敗/卡住 → Jira comment / transition;卡住時附 session/workspace/resume 指令讓工程師接手。
- **FR-C5 去重與可靠性**:以 `(key, status)` 去重(狀態變更可重觸發);單次 poll 失敗不可害死 watcher。

## 2.4 明確 Non-Goals(本階段不做)

- **不做 Graph 編排引擎**(三層文第 6/7 條:先 trace 再畫圖;目前無實質分支需求)。
- **不做 durable execution 引擎**(Temporal 已進場,見 v2)。
- **不自創 driver 通訊協定**(對齊 ACP;見 v2 §4.3)。
- **不做多租戶治理 UI / LLM gateway**(OpenHands Enterprise、LiteLLM、mission-control 已做)。

---

# 第三部分:從零寫也能完成目標 —— 開工級設計

> 本部分證明「不依賴 OpenHands 也能達標」,深度到可以直接開工。所有介面都有對應的可跑 PoC 程式碼(`examples/jira-agent-poc/`),不是紙上談兵。

## 3.1 元件架構

```
                    ┌────────────────────────────────────────────┐
                    │                 Jira Server                 │
                    └───────────────┬────────────────────────────┘
                        poll JQL    │  ▲ comment / transition (FR-C4)
                                    ▼  │
        ┌───────────────────────────────────────────────────────┐
        │  Connector 層   jira_watcher.py                         │
        │   • JiraClient(REST, PAT/basic)                        │
        │   • RuleEngine(rules.json: assignee/keyword→決策)      │  FR-C1/C2/C3/C5
        │   • Dispatcher                                          │
        └───────────────┬───────────────────────────────────────┘
                        │ Decision(agent, skills, repo, prompt)
                        ▼
        ┌───────────────────────────────────────────────────────┐
        │  Harness 層   workspace.py                              │
        │   • provision(): 建 <issue>/ 資料夾 + 裝 skills         │  FR-H2/H3
        └───────────────┬───────────────────────────────────────┘
                        │ Task(run_id, prompt, cwd, session_id)
                        ▼
        ┌───────────────────────────────────────────────────────┐
        │  Runtime 核心   supervisor.py + drivers.py + events.py  │
        │   • Driver: build_command / normalize(native→AgentEvent)│  FR-H1/H4
        │   • Supervisor: spawn → 讀事件流 → 狀態機 → journal      │  FR-L1/L2/L5
        │       → watchdog(卡住)→ control(pause/kill/resume)   │  FR-L6/H5/H6
        │   • Journal: events.jsonl(稽核) + snapshot.json(重建)│
        └───────────────┬───────────────────────────────────────┘
                        │ AgentEvent stream
                        ▼           observers →(dashboard / 告警 / Jira 回寫)
        ┌───────────────────────────────────────────────────────┐
        │  Workers(raw subprocess,一級)                         │
        │   claude -p ──stream-json──┐                            │
        │   codex exec ──--json──────┼─► 同一組 AgentEvent 詞彙   │
        │   opencode acp / OpenHands agent-server(可選後端)     │
        └───────────────────────────────────────────────────────┘
```

設計原則(取自 ChatGPT RFC 討論 + 三層文):Agent is Disposable、Runtime Owns State、Stateless Supervisor(可 kill -9 重建)、Worker Independence(各自 PID)、Everything is Event、Everything is Recoverable。

## 3.2 統一 Event Schema(FR-H4)—— 跨 CLI 的 lingua franca

正規化事件詞彙(小而穩;driver 無法對應的原生事件降級成 `RAW`,仍入 trace):

```
run.started · thinking · message · tool.started · tool.finished
waiting.permission · waiting.human · api.retry · token.usage
run.completed · run.failed · raw
```

`AgentEvent` 欄位:`run_id, agent, type, ts, session_id, text, tool_name,
cost_usd, tokens_in, tokens_out, raw`(`raw` 永遠保留原生 dict → 零資訊損失、全稽核)。實作見 `arcp_poc/events.py`。

## 3.3 執行狀態機(FR-L1)

```
NEW → STARTING → RUNNING ⇄ THINKING / RUNNING_TOOL
                   │           │
                   │           ├→ WAITING_PERMISSION ─(超時)→ WAITING_HUMAN(開 Jira)
                   │           └→ API_RETRY → RECOVERING
                   │
                   ├─(watchdog 無進度)→ STALLED → WAITING_HUMAN
                   └→ DONE | FAILED(exit code 決定;FAILED→嘗試 resume)
```

純轉移函式 `next_state(current, event)`,terminal 態 sticky。實作見 `events.py` 的 `_EVENT_TO_STATE`。

## 3.4 Driver 介面(FR-H1)—— raw 一級,ACP 可換

```python
class Driver(Protocol):
    name: str
    def build_command(self, task: Task, resume: bool = False) -> list[str]: ...
    def normalize(self, native: dict, run_id: str) -> AgentEvent | None: ...
```

- `ClaudeDriver`:`claude -p <prompt> --output-format stream-json --verbose
  --include-partial-messages [--session-id <uuid> | --resume <uuid>]
  --permission-mode <mode> [--allowedTools ...]`
- `CodexDriver`:`codex exec --json --sandbox <mode> --skip-git-repo-check <prompt>`;
  resume:`codex exec resume <SESSION_ID> --json ... <prompt>`
- OpenHands ACP:不 spawn CLI,改 `POST /api/conversations`(見 §7),正規化的是
  agent-server 的事件流,同樣實作這個 Protocol → **可插拔對照**。

實作與真實 schema 對應見 `arcp_poc/drivers.py`。

## 3.5 REST API 草案(FR-H5 / FR-L5)

供 dashboard / 外部觸發 / 人工控制(對齊 v2「driver 對齊 ACP、控制面對齊 agent-server」的結論):

```
POST /runs                 建立並啟動一個 run(body: agent, prompt, cwd, skills, session_id)
GET  /runs                 列出 runs(state 過濾)
GET  /runs/{id}            單一 run 快照(state/cost/tokens/last_event)
GET  /runs/{id}/events     事件史(分頁)—— 即 events.jsonl
WS   /runs/{id}/events     即時事件流(observer 訂閱)
POST /runs/{id}/pause      SIGSTOP(raw)/ 協定 pause(OpenHands)
POST /runs/{id}/resume     SIGCONT / --resume 重接
POST /runs/{id}/kill       終止
POST /runs/{id}/approve    回應 waiting.permission(核准/駁回)
POST /runs/{id}/escalate   升級人類(開 Jira ticket)
```

## 3.6 rules.json 格式(FR-C2)

```json
{
  "rules": [
    {
      "name": "ops-bug-to-codex",
      "match": { "assignee": ["swchen.tw"], "keywords_any": ["bug","error","crash","regression"] },
      "action": {
        "agent": "codex",
        "skills": ["jira-bugfix"],
        "repo": "git@github.com:example/ops.git",
        "prompt_template": "You are fixing a production bug from Jira {key}.\nTitle: {summary}\n\n{description}\nReproduce, fix, add a regression test. Do not push."
      }
    }
  ]
}
```

match 支援 `assignee` / `keywords_any` / `keywords_all` / `status`;first-match-wins。實作見 `arcp_poc/rules.py`。

## 3.7 Workspace / 持久化 Spec(FR-H2/H6)

```
runtime/
  <run_id>/
    events.jsonl      # append-only 統一事件(稽核 / trace / replay)
    snapshot.json     # run_id, agent, session_id, cwd, state, pid, cost — reconciliation 用
  workspaces/
    <ISSUE-KEY>/
      .claude/skills/<skill>/SKILL.md   # 依規則裝入的 skills
      (repo 內容 / git worktree)
  seen.json           # watcher 去重狀態
```

`snapshot.json` 是 stateless supervisor 的重建依據:重啟時掃 snapshot 找非終止 run → 比對 PID/session → attach 或 `--resume`。

## 3.8 從零寫的工作量估計

| 模組 | 內容 | 估行數 | PoC 狀態 |
|---|---|---|---|
| events + 狀態機 | 統一 schema、轉移函式 | ~120 | ✅ 已實作 |
| drivers | claude/codex 正規化 + 指令建構 | ~180 | ✅ 已實作(真實 schema) |
| supervisor | spawn/trace/watchdog/control/journal | ~200 | ✅ 已實作(live+replay) |
| rules + workspace | 規則引擎 + skills provision | ~150 | ✅ 已實作 |
| jira_watcher | 輪詢 + 去重 + dispatch | ~120 | ✅ 已實作(Jira 需真環境) |
| **MVP 合計** | | **~770** | **PoC 已跑通** |
| recovery(硬) | resume 重接 + 降級路徑 + worktree 陷阱 | ~250 | ⬜ TODO(差異化) |
| REST/WS + dashboard | 控制面 + UI | ~400 | ⬜ 可用 FastAPI |

**判讀:MVP 從零寫約 800 行、數天內可得**——因為難的部分(event-sourcing、durable execution)在此範圍是「把 CLI 的事件流落地 + 狀態機」,不是重造 Temporal。真正的深水區是 recovery(§5、§10)。

---

# 第四部分:三條實作路線的優缺點與維護成本(你最想要的對照)

> 你明確說「這三種目前不知優缺點和維護成本」。以下逐條攤開。維護成本以「誰的變更會逼你改碼」為判準。

> ⚠️ **證據級別(2026-08-03 更新)**:**A 欄實跑**(PoC live,§5.3);**B 欄 claude 側已實跑對照**(`examples/openhands-acp-poc/COMPARISON.md`:headless 可行、auth 零設定、事件粒度 14 vs A 的 248;codex 側冒煙過、對照待 quota),recovery/approval 子項仍為分析;**C 欄仍是分析推論**。 C 的最小可跑版其實就是 A(PoC),差別在 recovery/REST/dashboard 尚未實作。要把 B 升級成實跑對照,見 §9.3 第 4 項(需起 agent-server + 實作 `OpenHandsACPDriver`)。

## 4.1 對照總表

| 面向 | A. Raw subprocess(自寫) | B. OpenHands ACP(agent-server 當底層) | C. 從零寫完整 runtime |
|---|---|---|---|
| 與你日常用法一致 | ✅ 就是 `claude -p`/`codex exec` | ⚠️ 經 ACP wrapper,非你直呼 | ✅(底層仍是 raw) |
| 上手 / MVP 時間 | 短(PoC 已跑通) | 中(要起 agent-server、學 API) | 長 |
| 控制粒度 | 高(直接掌 PID、stdin/out、flags) | 中(agent-server 提供,但 ACP 外部 agent 是黑箱) | 最高 |
| Trace 完整度 | 高(直接吃原生 stream-json/JSONL) | 中(吃 agent-server 正規化事件;ACPToolCallEvent 夠用但較淺) | 最高 |
| Crash recovery | ✅ **claude+codex resume 基線已實測**(§9.3-1;兩者 2×2 矩陣全過、SIGTERM-rc=0 陷阱已釘、降級 transcript 路徑亦驗證);worktree/長跑陷阱待驗 | ✅ 自家 conversation event-sourced/crash-safe;⚠️ ACP 外部 agent 只存自己側引用 | ⬜ 要自己做(但可做最好) |
| 執行中人工核准 | ⬜ 要自己補(hook/SDK 路徑) | ✅ `confirmation_policy` + `respond_to_confirmation`(但純 headless CLI 強制 always-approve) | ⬜ 要自己做 |
| 依賴風險 | CLI schema 變(claude/codex 各自) | OpenHands 版本收斂快 + ACP 協定演化 + CLI schema | 只有 CLI schema |
| 差異化空間 | 中(你掌控全鏈) | 低(跟著 OpenHands 走) | 高(git checkpoint 等) |

## 4.2 維護成本(誰的變更逼你改碼)

- **A. Raw**:主要維護面是 **CLI 事件 schema 漂移**——claude 2.x 與 codex 0.14x 都在快速演化(v2 已記錄 omnara 之死正是「wrapper 跟不上 Claude Code 更新」)。緩解:driver 層版本化 + 用真實 fixtures 做**協定回歸測試**(PoC 已內建 `fixtures/*.jsonl` + `selftest.py`,schema 一變測試就紅)。**不解析內部 transcript JSONL**(v2 §2.3 第 4 點),只吃官方 stream-json/`--json`。維護成本:**中,但可自動化偵測**。
- **B. OpenHands ACP**:維護面是**三重演化**——OpenHands 本體(v1.8.0 beta、agent-server 版本 pin 1.39.1)、ACP 協定(`@agentclientprotocol/*` 版本)、加上底層 CLI schema。好處是 crash-safe/approval 的地基由它維護,你少寫;壞處是你被綁在它的升級節奏,且 ACP 外部 agent 的行為它也控制不了。維護成本:**地基省事,但整合面受制於人**。
- **C. 從零寫**:維護面最小(只有 CLI schema),但**你要維護整個 runtime**(recovery、狀態機、REST、dashboard 的所有 bug)。維護成本:**表面單純,總量最大**。

## 4.3 建議路線(混合)

**主線 A(raw 一級)+ 可選後端 B(OpenHands 對照)。**
1. **先用 A 把 Jira pipeline 跑起來**(PoC 已證明可行):你日常就用 `claude -p`/`codex exec`,supervisor 給你 trace+control+rules+skills,數天可上線內部用。
2. **把 Driver 介面留成可插拔**(PoC 已如此):需要 crash-safe conversation / 程式化核准 / Docker sandbox 隔離時,加一個 `OpenHandsACPDriver` 走 agent-server,同一套上層碼即可對照跑,用真實負載決定值不值得。
3. **C 只做差異化**:若要做開源專案,把 v2 驗證過「沒人做」的 **git checkpoint 語意層** 與 **跨 CLI 卡住偵測/recovery** 做成疊在 A/B 之上的 supervisor 能力,而不是另一個 runtime。你的 Jira pipeline 就是它的第一個真實試驗場。

---

# 第五部分:真實事件流 ground truth 與 PoC 實測(本輪新增)

## 5.1 抓到的真實 schema(本機,2026-08-01)

**`claude -p --output-format stream-json --verbose`**(claude 2.1.206)事件序:
`system/init`(帶 `session_id`、`tools`、`model`、`permissionMode`)→ 多個
`system/thinking_tokens`(串流思考 token 計數)→ `assistant`(content: thinking→text,帶 usage)→ `rate_limit_event` → `result`(帶 `duration_ms`、`num_turns`、`result`、`total_cost_usd`、`usage`、`permission_denials`、`terminal_reason`)。

**`codex exec --json`**(codex-cli 0.142.5)事件序:
`thread.started`(帶 `thread_id`)→ `turn.started` → `item.completed`(item: `agent_message`/`text`,或 `command_execution` 等)→ `turn.completed`(帶 `usage`)。**無獨立終止事件**——`turn.completed` + process exit 即完成。

## 5.2 關鍵發現(實測釘死,非文件推論)

1. **終止語意不對稱**:claude 有明確 `result` 事件;codex 靠 `turn.completed`+exit。統一層必須用「事件 OR exit code」雙判據(PoC `supervisor._finalize_on_exit` 已實作:rc==0→DONE,rc≠0→FAILED→可 resume)。**⚠️ 2026-08-02 實測推翻其充分性**:codex 收 SIGTERM 優雅退場 rc=0,中斷的 run 會被誤判 DONE——雙判據只能判「程序結束」,不能判「任務完成」,完成與否必須靠證據型 grader(§9.3-2)。
2. **`--session-id <uuid>` 可預先指定**(claude):supervisor 啟動前就決定 session id → 直接持久化 `(session_id, cwd)`,不必等回傳。這讓 crash recovery 的「重接原對話」在 raw 路徑可行。
3. **codex 讀 stdin**:非 tty 時 `codex exec` 會讀 stdin;supervisor 必須 `stdin=DEVNULL` 否則卡住(PoC 已修)。
4. **`opencode acp` 本機存在**:OpenCode 的 ACP 路徑可行(v2 只能推論);但 raw `opencode run` 仍不穩(v2 §2.3),要走 ACP 或 `serve`。

## 5.3 PoC 實測結果(`examples/jira-agent-poc/`)

- **離線 replay**(免 token):真實 `fixtures/*.jsonl` 過同一套 normalize→狀態機→journal,claude 與 codex **都到 `done`**,產出相同事件詞彙。
- **7/7 self-test 通過**:rule 引擎(bug+assignee→codex/jira-bugfix、docs→claude、無match)、事件正規化、狀態機到終止、session 捕獲。
- **Live 全流程**(真實付費,各約 $0.02):
  - `claude -p`:reached `done`、cost $0.0189、預先指定 session id 生效。
  - `codex exec`:rule 命中 `ops-bug-to-codex` → skill `jira-bugfix` 裝進 `.claude/skills/` → reached `done`。
- **產出檔**:`events.jsonl`(稽核 trace)、`snapshot.json`(帶 session_id/pid/state/cost 供 reconciliation)。

---

# 第六部分:OpenHands 深入評估(併入 v2 之後的定案)

> 本節整合先前對 OpenHands(Agent Canvas + software-agent-sdk)的原始碼級探索,作為路線 B 的依據。

## 6.1 兩個專案

- **software-agent-sdk**:OpenHands 生態的 agent 核心(V1 全面重構,arXiv:2511.03690:event-sourced、deterministic replay、系統性失敗較 V0 降 61%、SWE-Bench 77.6)。四包:`openhands-sdk`(核心)、`openhands-tools`、`openhands-agent-server`(FastAPI REST/WS)、`openhands-workspace`(Docker/Apptainer/Cloud sandbox)。
- **OpenHands / Agent Canvas**:React/Electron 前端控制中心,經 REST/WS 消費 agent-server;排程/webhook 在獨立 sidecar(`openhands-automation`)。

## 6.2 需求逐項對照(定案)

| 需求 | 現成程度 | 說明 |
|---|---|---|
| Watch Jira Server | ❌ 自己寫 | OSS 無 Jira 整合(一鍵 Jira 在閉源 Cloud;且僅限自家 agent)。automation sidecar 支援 cron+webhook,但事件來源未證實含泛用/Jira |
| assignee/title JSON 規則 | ❌ 自己寫 | 任何觸發機制都沒有此類條件匹配 |
| 建工作資料夾 | ✅ 現成 | `StartConversationRequest.workspace.working_dir`;`worktree:true` 自動建 git worktree |
| 依規則裝 skills | ✅ 機制現成 | `POST /api/skills/install`(git URL/GitHub/本機)、啟動時 `agent_context.skills`(含 KeywordTrigger)、或 `.openhands/skills/` |
| headless claude/codex | ✅ 內建 ACP | provider `claude-code`(`@agentclientprotocol/claude-agent-acp`)、`codex`(`@agentclientprotocol/codex-acp`);subprocess/JSON-RPC-over-stdio |
| headless opencode | ⚠️ custom ACP | `acp_command:["opencode","acp"]`(本機已確認 `opencode acp` 存在);組合未實測 |
| headless SDK agent | ✅ 原生 | `agent_kind:"openhands"` |
| 監控 | ✅ 現成 | WS `/sockets/events/{id}`、`ExecutionStatus`(含 stuck)、Stuck Detector、cost metrics、`pause`/`interrupt`/`respond_to_confirmation` |

## 6.3 路線 B 的呼叫方式(對照 raw)

```
raw 路徑(A):  supervisor spawn  claude -p / codex exec  → 直接吃 stream-json
OpenHands(B): POST /api/conversations {
                 workspace:{kind:"LocalWorkspace", working_dir}, worktree:true,
                 agent_kind:"acp", acp_server:"claude-code"|"codex"
                    (或 acp_command:["opencode","acp"]),
                 agent_context:{skills:[...]},
                 confirmation_policy:{...}
              }  → 訂閱 WS /sockets/events/{id}
```

兩條路在 ARCP 裡都實作同一個 `Driver` Protocol,可對照跑。

## 6.4 ACP 的 resume 語意 vs raw(2026-08-02 原始碼查證)

> 動機:§9.3-1 實測完 raw 路徑的 crash recovery 後,回頭查「同樣的事在 ACP 下
> 是否更容易」。以下皆讀本機 clone 原始碼查證(`software-agent-sdk/openhands-sdk/
> openhands/sdk/agent/acp_agent.py` ~4160 行、`event/resume_transcript.py`),非文件推論。

**1. ACP 協定有 resume:`session/load`。** OpenHands `ACPAgent` 持久化 ACP session id,
重啟時先試 `load_session(cwd, session_id, mcp_servers)`;server 回 `ACPRequestError`
(不認識該 session,如 sandbox 回收)就退回 `new_session` 重開。邊角處理成熟:
resume 後 model 設定遺失要重套(`_reapply_session_model_on_resume`)、id 序列化遮罩、
MCP servers 重掛。

**2. 但底層耐久性與 raw 是同一個來源。** `acp_server: claude-code|codex|gemini-cli|custom`
映射到 npx adapter(claude-code → `@agentclientprotocol/claude-agent-acp`),adapter 的
loadSession 最終委託 CLI 自家 session 檔——**就是 §9.3-1 我們直接 `--resume` 的那個東西**。
session 檔沒了兩邊一樣救不回。ACP 的真實增益:
- **介面統一**:一個 `session/load` 取代各家 argv 怪癖(raw 實測踩的
  `codex exec resume` 不吃 `--sandbox` 這類坑,ACP 下不存在)。
- **終止語意較乾淨**:prompt 回應帶明確 `stopReason`,優於 codex「SIGTERM 也 rc=0」;
  但一樣只證明 turn 結束,**不證明任務完成——證據型 grader(§9.3-2)兩條路都躲不掉**。
- 代價不變:多一層 adapter、三方版本漂移(§4.2)。

**3. 「存對話、注入新 session」的降級機制 OpenHands 真的有——bootstrap-prompt resume。**
`resume_transcript.py`:當 `session/load` 不可用,開全新 session,把 SDK 側留存的事件史
渲染成開場訊息注入。設計細節值得抄:固定標記 `<<RESUMED CONVERSATION>>`(防重複包裝)、
總量上限 60k 字元**砍舊留新**、逐訊息 8k / 逐工具輸出 2k 各自截斷。
⚠️ 誠實註記:在本機 snapshot 裡 `render_resume_transcript` **沒有任何呼叫者**——
渲染器寫好了但自動接線未進開源版,OpenHands 目前實際行為是 load 失敗→乾淨重開。

**4. 對 ARCP 的結論:最值得偷的是把 bootstrap-transcript 當 raw 路徑的降級層。**
supervisor 本來就 journal 全部事件(`events.jsonl`),素材齊全——`--resume` 失敗時
從 journal 渲染 transcript 注入新 session,形成三段梯度:
**原生 resume → transcript 注入 → 全新重跑**,比 OpenHands 實際接線的多一階,
且不引入 OpenHands 依賴,只抄其驗證過的渲染設計。已列入 §9.3 清單。

**立場備忘(使用者,2026-08-02)**:未來不排除「直接拿 OpenHands 改」作基底——
他們把坑都走過一遍(上述 resume 容錯、model 重套、id 遮罩皆是證據)。**持續觀察,
暫不決策**;現階段仍照 §4.3 混合路線走,Driver 介面保持可插拔以保留此選項。

---

# 第七部分:第三方方案簡述對照(引用 v2,不重驗)

> 以下結論來自 v2 的對抗式驗證(標註得票),此處僅摘要定位,避免重造。

| 專案 | 定位 | 與 ARCP 的關係 | v2 判定 |
|---|---|---|---|
| **OpenHands** | self-hosted control center + ACP | 最現成的可選後端(路線 B) | 收斂壓力最大;approval 集中版在 Enterprise、ACP 外部 agent 是黑箱→留缺口 |
| **mission-control**(builderz-labs) | 單機 control plane(SQLite) | 功能重疊最大的先行者;可整合對象 | ✅3-0×3;只有 task requeue,無 session-level recovery/git checkpoint |
| **Temporal + LangGraph plugin** | durable execution | 不重造;需要跨進程 durable 時再引 | ✅3-0×4;恢復自家 graph,不能監督外部 CLI |
| **LiteLLM Agent Control Plane** | LLM gateway + sessions/CRON | 不重造 LLM routing/budget | ✅3-0×3;支援的 runtime 全為託管/API 型 |
| **superplane** | server 端 workflow control plane | 互補;ARCP 可當其 runner 上的 session 層 supervisor | ⚠️;approval 是 workflow node 級,非 tool-call 級 |
| **jenkinsci/ai-agent-plugin** | CI 內嵌 agent runner | 追蹤/貢獻對象 | ⚠️;綁 build 生命週期、無 session 管理 |
| **omnara**(已死) | 本機 CLI wrapper + 手機核准 | 反面教材 | 死因:PTY 掃 TUI buffer 跟不上 Claude Code 更新 → ARCP 走結構化事件流繞開 |
| **agx / Agent AFK** | stateless re-run / 自建 runtime | 設計參考(checkpoint schema / watchdog 原則) | ⚠️;非 CLI control plane |

**v2 定案的缺口(v3 仍成立)**:跨 CLI 一致的 session-level crash recovery、卡住偵測、統一 approval queue、git checkpoint 語意層——**這四項是 ARCP 走路線 C(差異化)時該做的,也只該做這些。**

---

# 第八部分:生產就緒檢查清單(套用三層文,對照 ARCP 現況)

| 層 | 檢查項(三層文) | ARCP MVP 狀態 |
|---|---|---|
| Harness | 工具窄、有文件、可觀測? | ✅ rules 選 skills;統一 trace |
| Harness | 狀態耐久?能暫停/檢視/恢復? | ✅ snapshot;⚠️ resume 待補 |
| Harness | 權限最小? | ✅ permission-mode/sandbox 可設 |
| Loop | 什麼證據證明成功? | ⬜ **證據型停止待補**(目前靠 exit code / result 事件,尚未接確定性檢查) |
| Loop | 失敗回什麼回饋?幾次重試?預算耗盡? | ⚠️ 有界重試/預算待補 |
| Graph | 哪些路徑確定性?人工閘門? | N/A(本階段不上 graph) |
| Evaluation | 能重播 trace、比較版本? | ✅ events.jsonl 可 replay;⚠️ 版本比較待補 |
| Operations | cost/延遲/失敗率/人工介入率被監控? | ⚠️ cost/state 有;聚合 dashboard 待補 |

**最該優先補的兩項**(三層文最強調、ARCP 最缺):**Loop 的證據型停止**(別讓 agent 自稱 done)、**cross-CLI recovery**。→ **兩項基線皆已補上,且已接成迴路(2026-08-02)**:grader 覆寫機制(§9.3-2)+ claude/codex resume 實測(§9.3-1)+ 自動 recovery 迴路(§9.3-8,live 驗證 crash 與 rc=0 假完成皆自動修復)。

---

# 第九部分:決策建議與下一步

## 9.1 建議(定案)

1. **路線:A(raw 一級)為主線,B(OpenHands ACP)為可選後端,C 只做差異化層。** 理由見第四部分。
2. **MVP 範圍:第二部分的 FR-H1~H7、FR-L1~L5、FR-C1~C5**(不含 recovery 深水區與 graph)。PoC 已覆蓋其中大半。
3. **先做 trace+control+rules+skills 跑通 Jira pipeline**(三層文第 6 條:先跑 trace 再想複雜編排)。

## 9.2 PoC 現況(`examples/jira-agent-poc/`)

已實作並實測:統一 event schema、狀態機、claude/codex raw driver(真實 schema)、supervisor(live+replay+watchdog+control)、rules 引擎、skills provision、Jira watcher(輪詢/去重/dispatch)。self-test 全過(22 項);claude/codex live 各跑通。另有 crash-recovery 矩陣 harness(`recovery_test.py`,§9.3-1)、crash/resume fixtures、證據型停止 grader(`grader.py`,§9.3-2)、transcript 降級 resume(§9.3-7)、自動 recovery 迴路(`recovery_loop.py`,§9.3-8)。

## 9.3 下一步 PoC 實驗清單(先於大量寫碼)

1. **Crash recovery 實測** — ✅ **claude 部分已完成(2026-08-01)**:`recovery_test.py`
   2×2 矩陣(early「尚無產出」/midtool「工具執行中」× SIGTERM/SIGKILL)**4/4 case PASS、
   16/16 判準**(C1 resume 完成 / C2 同 session id / C3 檔案鏈齊全 / C4 不重工),
   總成本 $0.185(haiku);真實 crash+resume 事件流入
   `fixtures/claude_p_{crash,resume}_real.jsonl`(已用 replay 管線回歸驗證:crash 流停
   `running`、resume 流到 `done`)。
   **codex 部分(2026-08-02)**:early×SIGTERM/SIGKILL、midtool×SIGKILL 皆 PASS,
   thread id 事後擷取路徑成立;fixtures:`codex_exec_{crash,resume}_real.jsonl`。
   實驗過程另釘住 SIGTERM-rc=0 誤判、resume argv、killpg、事件粒度不可靠四陷阱
   (見執行摘要第 3 點),並發現**實驗機系統睡眠會凍結 supervisor 計時器**產生
   假 stall/假 hang——live 監督要防睡(caffeinate 只擋 idle sleep)或跑在 server。
   codex midtool×SIGTERM 已於 2026-08-02 補測 2/2 乾淨 PASS(2×2 補齊)。
   **workspace 搬家情境(#48835 的資料夾一般形式)已實測(2026-08-02,
   `workspace_recovery_test.py`,4/4 PASS)**:claude session store 綁**啟動時 cwd**
   (`~/.claude/projects/<編碼路徑>/`),workspace `mv` 之後原生 resume 死於
   `No conversation found with session ID`(2.1.206 實錄)——但 **transcript 降級
   救回**:ARCP journal 跟著 journal_root 走不綁 cwd,新 session 續跑不重工。
   梯度第二階的必要性在真實陷阱上證明。git worktree 形式同機制(worktree 路徑
   就是不同 cwd),未另測。
   **尚未做**:長跑/大 context 下的 resume。
2. **證據型停止** — ✅ **已實作(2026-08-02)**:`arcp_poc/grader.py`
   (`FileChecklistGrader` / `CommandGrader` / `AllOf`,Verdict 附理由入 journal),
   supervisor 掛 `grader` 後 DONE 需過證據——**證據不過即覆寫 FAILED**(sticky 終端
   狀態唯一被批准的例外:證據高於自稱)。selftest 14/14 含「DONE 流 + 證據缺 → FAILED」;
   recovery_test 的 C3 判準已 dogfood 此 grader。直接封堵 SIGTERM-rc=0 假完成(§6.4)。
3. **Claude permission 行為矩陣** — ✅ **已實測(2026-08-02,claude 2.1.206,`permission_matrix.py`)**:
   6 mode × 雙探針(Write 建檔 / Bash touch),無 allowedTools,headless `-p`:

   | mode | Write | Bash(touch) | 行為 |
   |---|---|---|---|
   | acceptEdits | ✅ | ✅ | 兩者皆放行(11s) |
   | bypassPermissions | ✅ | ✅ | 兩者皆放行(11s) |
   | auto | ❌ | ❌ | 立即拒,agent 續跑並自報 denied(11s) |
   | manual | ❌ | ❌ | 同上(13s) |
   | dontAsk | ❌ | ❌ | 同上(10s) |
   | plan | ❌ | ❌ | 只產計畫不動檔(76s) |

   釘死的事實:① mode 詞彙已換代(v2 記的 default/… 已不存在,現為
   acceptEdits/auto/bypassPermissions/manual/dontAsk/plan)——v2 §2.3-5 被推翻的根源。
   ② **acceptEdits 實際範圍比名稱寬**:連 Bash `touch` 都放行(已用
   `--setting-sources project` 隔離使用者設定複驗,非 allowlist 干擾;完整邊界未測)。
   ③ **headless 下沒有任何 mode 會掛住等核准**(無一到 120s timeout):拒絕是立即的,
   agent 收到 denial 後續跑——supervisor 的 waiting-permission 偵測應該**盯事件流中的
   denial,不是偵測卡住**。④ auto/manual/dontAsk 在無 allowlist 時全拒(auto ≠ 自動接受)。
4. **OpenHands ACP 對照** — ✅ **claude 側已實跑(2026-08-03,`examples/openhands-acp-poc/`)**:
   SDK in-process(ACPAgent + adapter npx pin)headless 跑通,**本機訂閱登入免 API key**;
   同任務同 grader 對照:**A 248 事件 vs B 14 事件(~18:1)**——A 有 thinking/token 級
   watchdog 原料,B 是工具呼叫級語意層;詳 `COMPARISON.md`(各格標證據級別)。
   codex 側:adapter 冒煙 PASS,對照數據點被 ChatGPT quota 擋下(8/31 重置後補)。
   陷阱實錄:litellm rust-wheel(鎖 1.93.0 解)、npx 首跑下載 > SDK 90s timeout(要預熱)、
   quota 跨路線共用。B 路 resume(`acp_resume_session_id`)仍未實跑。
5. **opencode via ACP**:`acp_command:["opencode","acp"]` 實測相容性。
6. **waiting-permission → 開 Jira ticket 升級迴路** — ✅ **已實作並 live 驗證(2026-08-02)**:
   `arcp_poc/escalation.py` + `escalation_demo.py`。依 §9.3-3 實測改為**事件驅動**
   (headless 不會卡等核准,盯 denial 事件而非偵測卡住):driver 把真實 denial 文案
   正規化為 WAITING_PERMISSION → EscalationObserver 首個 denial 開票、後續 denial
   追加 comment、終端事件把 **`result.permission_denials` 結構化清單** + resume 指令
   回寫原始 issue。離線用真實 denial 流(`fixtures/claude_p_denial_real.jsonl`)回歸,
   live demo:`denial→DRY-1 開票→comment→OPS-42 回寫`,$0.028。
   Jira 端為 DryRunJiraClient(JSONL outbox),REST 實作可直接替換。
8. **自動 recovery 迴路** — ✅ **已實作並 live 驗證(2026-08-02)**:
   `arcp_poc/recovery_loop.py`(run → grade → 依梯度升級 resume,同一 rung 不重試,
   有重試上限;grader 必備——loop on evidence)+ `loop_demo.py`。兩個 live 場景:
   claude midtool×SIGKILL 硬 crash → 迴路 native resume 修復 `initial:failed →
   native:done`;**codex midtool×SIGTERM 的 rc=0 假完成被 grader 否決
   (`evidence FAIL: missing step3/4/5`)→ 迴路自動 resume 原 thread 補完**。
   實測釘住的最大陷阱,如今端到端自動抓到並修復。selftest 22 項含迴路策略
   (首試即成/crash 修復/無 id 跳 native/梯度用盡放棄)。

7. **journal → transcript 降級 resume** — ✅ **已實作並 live 驗證(2026-08-02)**:
   `arcp_poc/resume_transcript.py`(marker/總量 60k 砍舊留新/逐訊息 8k 截斷,設計
   抄自 OpenHands §6.4)+ `recovery_test.py --resume-mode transcript`。實測
   claude midtool×SIGKILL:crash 後**不用原生 resume**,從 journal 渲染 transcript
   開全新 session → 4/4 判準 PASS(含不重工——新 session 無記憶,全靠 transcript
   告知進度)。三段梯度「原生 resume → transcript 注入 → 全新重跑」前兩階皆有實測;
   此路徑同時解決「codex 太早死、thread id 沒擷取到」的無 id 情境。

---

# 附錄

## A. 本輪一手材料

- 本機實測:claude 2.1.206(`claude -p` stream-json)、codex-cli 0.142.5(`codex exec --json`)、opencode(`opencode acp`);真實事件流存於 `examples/jira-agent-poc/fixtures/`。
- OpenHands 原始碼:`software-agent-sdk/openhands-{sdk,agent-server}`、`OpenHands/src`(`acp-providers.ts`、`agent-server-adapter.ts`、`automation.ts`、`docs/ACP_AGENTS.md`)。
- 線上:docs.openhands.dev、GitHub OpenHands/OpenHands#14374、arXiv:2511.03690、opencode.ai/docs/acp/、github.com/OpenHands/automation。
- 三層排障地圖:`knowledge_from_ai_summary/.../2026-07-19-AGENT-HARNESS-VS-LOOP-VS-GRAPH-ENGINEERING-THREE-LAYERS.md`(Bijit Ghosh 原文 + LangChain 官方回應 + Why QQ 導讀)。
- ChatGPT「Claude headless 解決方案」RFC 討論(headless 七大痛點、四層架構、Jira/Jenkins connector、RFC-0001~0010、Responsibility Matrix)。

## B. v2 → v3 差異

- v2:市場缺口驗證 + 技術可行性 + 企業整合模式(對抗式驗證為主)。
- v3:需求規格化(三層地圖)+ 開工級設計 + 三路線優缺點/維護成本 + **可跑 PoC 與真實 schema 實測**。v2 的第三方判定與缺口結論在 v3 直接引用,不重驗。

## C. 研究限制

1. Live 實測已覆蓋 trivial prompt happy path 與 **claude/codex crash→resume 基線**(§9.3-1,兩者 2×2 皆補齊)+ transcript 降級路徑;**長跑/worktree 情境**仍需 §9.3 的 PoC 實驗。codex 會載入使用者 plugin(如 superpowers)造成行為變異,對照實驗宜 `--ignore-user-config`(未實測)。
2. OpenHands automation sidecar 的泛用 webhook 支援未從原始碼證實(不影響建議,因 watcher 取代其角色)。
3. opencode via ACP、claude permission 矩陣為待實測項。
4. 全部為 2026-08-01 快照;claude/codex/OpenHands 皆快速演化,driver 需版本化 + 協定回歸測試。
