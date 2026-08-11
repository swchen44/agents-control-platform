# ARCP — Agent Runtime / Control Plane

[![CI](https://github.com/swchen44/agents-control-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/swchen44/agents-control-platform/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![uv](https://img.shields.io/badge/packaging-uv-de5fe9)](https://github.com/astral-sh/uv)

讓 `claude -p`、`codex exec` 這類 **headless coding agent** 能長時間可靠執行、
**可觀測(trace)**、**可控制(control)**,並由 **Jira 事件驅動**。

> Make headless coding agents (Claude Code / Codex CLI) long-running, traceable,
> controllable, and Jira-event-driven — with every claim pinned by real experiments,
> not documentation folklore.

## 這是什麼

你在 Jira 開票(或貼標籤)→ ARCP 的 poller 看到 → 派一個 headless coding agent
(`claude -p` / `codex exec`)去做 → 在隔離 workspace 執行、產出證據 → 確定性驗證
(grader)過才算成功 → 需要人時 agent 在票上 `@mention` 你並附**一次性表單連結**;你填完,
系統回寫 Jira 並讓 agent 續跑或關單。**你用既有的 Jira 操作就能指揮一支 agent 大軍。**

> Make headless coding agents (Claude Code / Codex CLI) long-running, traceable,
> controllable, and Jira-event-driven — every claim pinned by real experiments.

## 特色

- **證據型停止**:agent 自稱「完成」不算數,確定性 `verify` 過才 SUCCESS;三態 outcome
  (SUCCESS / FAILURE / **UNKNOWN**)。
- **跨 backend/引擎統一契約**:rawcli(純 stdlib,免 venv)/ openhands-acp /
  openhands-server × claude / codex,共用同一 envelope,換執行單元零改動。
- **韌性**:native resume(crash 不重工)、bounded retry、stall 看門狗、killpg evict、
  預算閘(每票 / 每月每 agent / 全站 × token/usd 共 6 層,每輪派工前 precheck)。
- **HIL 人機介面**:一次性 token 受控表單(補資訊/決策/評分關單)取代人手編 Jira;
  全程可稽核(hash + journal)。
- **可觀測**:唯讀 dashboard(KPI/trace/事件時間軸/狀態機/架構圖)+ transcript,
  **內網零外部依賴**(所有元件 vendored)。
- **可控制**:REST 控制面(pause/resume/reload/shutdown/evict/recover)。

## 快速開始

```bash
uv sync                                   # 裝相依 + editable 安裝 arcp(需 Python ≥ 3.10)
# ~/.env 放 JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN(不進版控)

cp config/config.example.yaml config/config.yaml   # 改 jql / project / profile
uv run python scripts/smoke_jira.py       # 唯讀冒煙:驗 Jira 連線
uv run python scripts/run_poller.py       # 起 poller(+ control 8787 + 表單服務 8790)

# 另開一個 terminal 看 dashboard(runtime 資料預設 runtime/)
uv run python scripts/detail_server.py --host 127.0.0.1
# → http://127.0.0.1:8788
```

> 從 repo root 執行即可 —— 腳本(`scripts/`)、設定(`config/`)、vendored 資產(`vendor/`)、
> runtime 資料(`runtime/`)、runner 全由 `arcp.paths` 以 repo-root 相對解析,不綁 cwd。

完整步驟見 **[使用者手冊](docs/user-guide.md)**。

## 文件

| 對象 | 文件 |
|---|---|
| 使用者 | [使用者手冊](docs/user-guide.md) |
| 管理者 | [管理者手冊](docs/operator-guide.md)(起停/控制/監控/備份/多實例) |
| 開發者 | [開發者手冊](docs/developer-guide.md) · [專案檔案介紹](docs/project-overview.md) |
| 想懂為什麼 | [需求與理由](docs/requirements.md) · [決策記錄](docs/decisions.md) · [FAQ](docs/faq.md) |
| 設計細節 | [生命週期](docs/design/lifecycle.md) · [架構](docs/design/architecture.md) · [互動服務](docs/design/interaction.md) · [其餘](docs/index.md) |

文件總覽:[docs/index.md](docs/index.md)。

## 架構一眼看

```
Jira 事件 ─▶ poller(diff→journal)─▶ routing ─▶ gate(F1 額度)─▶ dispatcher
        ─▶ workspace+skills ─▶ 執行單元(claude -p | codex exec)─▶ envelope 契約
        ─▶ grader 終審 ─▶ SUCCESS/FAILURE/UNKNOWN
                              │  需要人 → @mention + 一次性表單 → 回寫 Jira → resume/關單
        觀測: dashboard + transcript      控制: REST(pause/evict/…)
```

分層模組圖 + 職責表 + node/edge graph 見 [架構](docs/design/architecture.md)
與 dashboard 的 **Introduction** 頁。

## 資料流生命週期 / 狀態機(W10:HIL 模型)

搞定系統先搞定**資料流的生命週期**。一張 Jira 票在 harness 內部走 6 個 canonical 狀態
(W10 起改 **HIL(Human In the Loop)模型**):

```
待處理 ─路由·派工─▶ 進行中 ⇄ 排隊
 (todo)     │  (running)  (queued)
            ├─ 過程中需人 ─▶ HIL(Middle) ─ assignee→機器人·條件滿足 ─▶ 回 running
            │   (triage/審批/預算/交人)
            ├─ 完成/用盡attempts/UNKNOWN ─▶ HIL(End)  結果={成功|失敗|未定}
            │        │ 人評分(0–10)
            │        ├─(A)人續做→關票(closed,概念終點)
            │        └─(B)人判可續→native resume·重置額度→回 running
            └─ cancel/外部關Done/交接 ─▶ 撤銷(aborted)
```

（互動版:dashboard「概念」tab 有純 SVG 狀態機圖 + 6 態說明 + **模組架構圖/職責表**。）

**6 態**:待處理 / 進行中 / 排隊 / **HIL(Middle)** / **HIL(End)** / 撤銷。`closed` 是
概念終點(人關 Jira→離開 jql)。`success/failure/unknown` 是 **HIL(End) 的結果屬性**,
不再是頂層狀態。

- **HIL(Middle)**(過程中等人)= 舊「交人 inactive」+「等待人類 pending」合併;含開跑前
  的 triage/審批。resume 觸發 = `assignee` 改回機器人,harness 讀 description `human` 段
  重評條件(審批已填/預算已放寬/純交人無條件)滿足才續跑。
- **HIL(End)**(終點交人)= 跑完轉人評分,再由人 (A) 續做後關票、或 (B) 判可續 → native
  resume + 重置額度回「進行中」。

**狀態存在哪(重要)**:
- **Jira 這邊**:真正的 `status`(To Do/進行中/Done)存 Jira,harness 只讀進來鏡射到
  DB `ticket_watch.last_state`。
- **我們系統這邊**:內部判定 `outcome`(SUCCESS/FAILURE/ABORTED/UNKNOWN)+
  `pending_reason` 只存 DB `ticket_session`,**不寫回 Jira**;上面 6 態即由這些欄位
  (加 queued/inactive/有無 session)推導的單一 canonical 狀態(`canonical_state()`
  唯讀映射)。
- **關票=人授權、系統執行**:成功/失敗後 agent 發 `score_and_close` 一次性表單,人評分
  (0–10)+ 裁決;選「關單」系統幫忙轉 Jira Done,選「續跑」解終態重置額度回進行中。
  (W11 起改表單化,取代人手編 description;見 [互動服務](docs/design/interaction.md)。)
- **生命週期事件**都記在 journal `events.jsonl`(new_issue/attempt_*/resolved/pending/
  handoff/jira_write/human_score…);ticket 詳情頁的**事件時間軸**由它繪製。

> 完整分層模組架構、職責表、以及 **agent↔agent 交接(同票 `next` vs 跨票 `base` 怎麼選)**
> 見 [docs/design/architecture.md](docs/design/architecture.md)。生命週期細節見
> [docs/design/lifecycle.md](docs/design/lifecycle.md)。
> ℹ️ HIL **行為**與**互動服務**(W11)程式已接線,真 Jira 端到端整合測進行中;跨票
> `base` a2a 交接為目標設計、待實作。

## 多實例部署(同一台機器並存多個 Control Plane)

想同時跑多個 Control Plane(例:一個顧 SCRUM、一個顧 OPS),做法是**複製整個
`agents-control-platform` 資料夾**成獨立一份,各自有獨立 `runtime/`、設定與 port。
每個實例在 dashboard 左上角會顯示 `ARCP Control Plane · <name>` 方便分辨。

**每個實例務必各自不同的(否則會互相干擾):**

| 項目 | 在哪設 | 為何 |
|---|---|---|
| **實例名 `name`** | `config.yaml` → `outer_loop.source.name`(或 env `ARCP_NAME`) | 顯示在 dashboard 標題/瀏覽器分頁,分辨是哪個實例 |
| **Jira project + jql**(最重要) | `config.yaml` → `source.project` / `source.jql` | ⚠️ **兩個實例絕不可 poll 同一 project/重疊 jql** —— 否則兩個 poller 互搶同一批票、覆寫彼此狀態(這正是我們 e2e 併發時撞到的 flaky 來源)。用不同 project,或至少用不重疊的 label/JQL 濾條 |
| **control API port** | `config.yaml` → `control.port`(預設 8787) | 每實例的 REST 控制面要獨立 port |
| **dashboard port** | `detail_server.py --port N`(預設 8788) | 每實例的 dashboard 要獨立 port |
| **dashboard→control 指向** | `detail_server.py --control-url http://127.0.0.1:<control-port>` | dashboard 的 Evict/狀態按鈕要打到**自己這個**實例的 control port,不能指到別台 |

**共用、但要留意的:**

- **Jira 憑證** `~/.env`(`JIRA_BASE_URL/EMAIL/API_TOKEN`):同一個 Jira 站的不同
  project 可共用同一份;若要接**不同 Jira 站**,需為該實例準備不同憑證來源
  (目前 `config.jira_credentials` 固定讀 `~/.env`,跨站需自行調整 env path)。
- **機器人帳號 `bot_account_id`**:同站同 bot 帳號在**不同 project** 上並存 OK(自家
  `[agent]` 留言互相忽略);但若不慎讓兩實例落到**同一 project**,一方會把另一方的
  assignee/留言當「外部變更」處理 → 再次強調:**分 project**。
- **claude / codex 登入**(`~/.claude`、`~/.codex`)全域共用:沒問題,但兩實例的
  agent 併發會共用同一組 API rate limit 與**花費**。預算上限(6 層 token/usd)是
  **per-instance**(各讀自己的 journal),**跨實例總花費不會合計** → 併發時把每實例的
  `concurrency` 設保守一點,避免合計超過機器/額度。
- **agent session / transcript 檔**(`~/.claude/projects`、`~/.codex/sessions`)全域:
  session id 唯一不衝突;transcript 與月花費彙總各讀自己實例的 journal,per-instance OK。
- **dashboard 綁定**:預設 `0.0.0.0`(內網開放)。多實例只要 port 不同即可並存;要鎖
  本機加 `--host 127.0.0.1`。

**快速範例(起第二個實例 "ops"):**

```bash
cp -R agents-control-platform arcp-ops && cd arcp-ops
# 編輯 config/config.yaml:source.name: ops、source.project/jql 改別的專案、control.port: 8797
uv run python scripts/run_poller.py &                    # 用 config.yaml 的 control.port
uv run python scripts/detail_server.py --host 127.0.0.1 \
  --port 8798 --control-url http://127.0.0.1:8797   # dashboard 8798 → 指自己的 control 8797
```

> 一句話:**分資料夾、分 name、分 Jira project/jql、分 port(control + dashboard)、
> dashboard 指向自己的 control**。其餘(憑證/登入/session 檔)可共用,但預算與機器
> 資源是 per-instance、不跨實例合計,併發請設保守。

## 現況與路線圖

研究階段(pre-alpha),介面會變。已完成:統一 event schema、雙 CLI driver、
supervisor(live+replay)、rules 引擎、Jira watcher、crash→resume 基線實測。

進行中 / 下一步(節錄自 research v3 §9.3):

- [x] **證據型停止**:確定性 grader 決定 DONE,證據不過覆寫 FAILED(`arcp_poc/grader.py`)
- [x] journal → transcript 降級 resume:session store 遺失時從 journal 渲染
      transcript 開新 session 續跑,live 驗證不重工(`--resume-mode transcript`)
- [x] Claude permission 行為矩陣:6 mode × 雙探針實測——headless 下拒絕即時、
      **沒有 mode 會掛住等核准**;acceptEdits 實際範圍比名稱寬(`permission_matrix.py`)
- [x] **自動 recovery 迴路**:run → grade → 梯度 resume,live 驗證硬 crash 與
      rc=0 假完成皆自動修復(`arcp_poc/recovery_loop.py` + `loop_demo.py`)
- [x] workspace 搬家情境 resume(#48835 一般形式):claude session 綁啟動 cwd,
      搬家後原生 resume 必死——transcript 降級救回不重工(`workspace_recovery_test.py`)
- [x] OpenHands ACP 對照(路線 B):SDK in-process headless 跑通、本機登入免 key、
      同任務同 grader 對照 A 248 vs B 14 事件(`examples/openhands-acp-poc/`)
- [x] **Jira 驅動 harness(B):outer/inner loop、三態 outcome、指令通道、
      agent-server + 視覺化(detail page)**(`src/arcp/` + `scripts/`,M1-M4;真 Jira 端到端)
- [x] **RawCLIAgent(路線 C):raw CLI 純 stdlib,不 fork;三方對照 C 集大成
      (保真≈A、語意乾淨勝 B、控制窗口/可視化兼得)**(`src/arcp/rawcli/`,C.0-C.6)
- [x] waiting-permission → Jira ticket 升級迴路:denial 事件驅動開票 + 結果回寫
      (含結構化 permission_denials 與 resume 指令,`arcp_poc/escalation.py`)

## License

尚未定(研究階段)。引用或試用歡迎開 issue 交流。
