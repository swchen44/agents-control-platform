# 開發者手冊

給「想改 ARCP 程式」的人。跑法見 [使用者手冊](user-guide.md),目錄地圖見
[專案檔案介紹](project-overview.md)。

## 開發環境

```bash
uv sync --extra dev          # 相依 + ruff + pytest + editable arcp
uv run ruff check .          # lint(核心套件嚴格;測試/腳本 per-file 放寬)
```

啟用 pre-commit(每台機器一次):`git config core.hooksPath .githooks`
(對 staged `*.py` 跑 ruff;vendored 與 examples 不管)。

## 架構一句話

**差異化層 runtime-agnostic**:上游只吃正規化的 `Ticket` 模型 + 統一的 `envelope`
契約 `{completed, session_id, cost, error, …}`;換執行單元(rawcli / openhands-acp /
openhands-server × claude / codex)dispatcher 與 grader 零改動。詳
[設計/架構](design/architecture.md)(含分層圖 + 職責表 + node/edge graph)。

**核心原則**:證據型停止(grader 終審,非 agent 自稱)· 三態 outcome
(SUCCESS/FAILURE/UNKNOWN)· envelope 契約跨 backend 不變 · 內網零外部依賴 · 省電不
caffeinate。詳 [需求與理由](requirements.md)。

## 套件結構(`src/arcp/`)

分五層(輸入 → 決策 → 執行 → 人機協作 → 狀態·觀測·控制):

- **輸入**:`jira_source`(Jira Cloud 讀寫,唯一碰 Cloud 細節的檔)、`triggers`(排程)
- **決策**:`poller`(外圈輪詢 diff→journal→協調)、`routing`、`gate`(F1 分層額度閘)
- **執行**:`dispatcher`(派工/審批/證據迴路)、`inner_runner`、`workspace`+`isolation`、
  `contract`(envelope 契約)、`grader`
- **人機協作**:`approval`、`scoring`(HIL(End) 評分)、`commands`(@agent + 離手政策)、
  `sections`(description 三方分段 + hash)、`interaction` + `hil` + `form_server`(W11 表單)、
  `output`(讀 agent 的 OUTPUT.json)+ `deliverables`(組交付物 ADF comment + 附件)+
  `adf`(精簡 ADF builder)—— agent 產出契約,見 [design/agent-output.md](design/agent-output.md)
- **狀態·觀測·控制**:`store`(SQLite + journal)、`control_api`、`transcript`、`retention`
  (`detail_server.py` 唯讀 dashboard 在 `scripts/`)

### agent↔agent 交接(W10.3)在哪

- **HIL 表單驅動**:`hil._do_handoff`(被 `apply_submission` 呼叫)。表單欄位(`handoff_kind`
  next/base + `next_profile` + `handoff_prompt`)定義在 `interaction.FORM_SCHEMAS` 的
  `score_and_close`/`decision`;下拉候選由 `scoring.ScoreGate.profiles_fn` 注入 payload。
- **同票換手(next)**:reset session + pin profile + `workspace="(handoff)"` 哨值(與
  `dispatcher` 裡 agent 自發換手同一套機制)。
- **跨票換手(base)**:`hil._do_handoff` 用 `source.create_ticket` 建新票 + 預建 pinned session
  (`store.TicketSession.base_ref` = 來源票 issue_id);`dispatcher._inject_base` 於新票首次
  佈建後呼 `workspace.inject_base_context` 複製脈絡進 `ws/BASE_<key>/`,一次性後清 `base_ref`。
- **測試**:`tests/test_handoff.py`(指令式 @agent next)+ `tests/test_handoff_hil.py`(HIL 表單
  next/base/fail-safe/注入,免真 Jira 用 FakeSource.create_ticket)。真 `create_ticket` 寫入
  屬 V1 付費路徑(見 `scripts/reverify_v1.py` 清單)。設計見 [design/architecture.md §4](design/architecture.md)。

## 測試

測試在 `tests/`(自訂 runner,亦 pytest-相容),從 repo root 執行:

```bash
uv run python tests/test_<name>.py                # 單支
for t in tests/test_*.py; do uv run python "$t"; done   # 全單元
uv run python tests/harness_selftest.py           # 路由/config/指令 冒煙
uv run python tests/e2e_dashboard.py              # dashboard 端到端(spawn detail_server)
uv run python tests/e2e_form.py                   # 互動服務端到端(fake Jira + 真 HTTP)
```

- **離線集**(CI 跑):所有 `tests/test_*.py` + `harness_selftest` + `e2e_dashboard` +
  `e2e_form`。免 token、免網、免真 agent。
- **需真依賴**(CI 不跑):`scripts/smoke_jira.py`(真 Jira)、`tests/e2e_c*` /
  `e2e_codex*`(openhands venv / 真 agent)。
- CI 用 `ARCP_CONFIG=config.example.yaml`(避免依賴本機才有的 openhands venv)。
- 腳本/設定/vendored/runner 由 `arcp.paths` 以 repo-root 相對解析,測試不綁 cwd;
  少數 import 腳本的測試(`test_kpi` / `test_hotreload`)靠 `tests/_env.py` 把
  `scripts/` 放進 `sys.path`。

真 Jira 冒煙(讀寫,測後還原):

```bash
uv run python scripts/smoke_jira.py                            # 唯讀
uv run python scripts/smoke_jira.py --write --ticket SCRUM-XX  # 含寫入(改測試票再還原)
```

## 加一個 backend

1. 在 `inner_*_runner.py` 加執行單元,產出符合 `contract` 的 envelope。
2. profile 的 `agent.backend` 指到它;dispatcher/grader **不用改**(契約不變)。

## 加一個 profile

在 `config.yaml` 的 `inner_loop.profiles` 加一項(見 `config.example.yaml` 範本):
`agent`(backend/engine/model/sandbox)、`verify`(確定性檢查)、`loop.max_attempts`、
`goal` / 預算 / `human_minutes_est`;再在 `outer_loop.routes` 加比對規則指到它。

`verify` 每步 `files` / `cmd` / `json` 擇一(grader 對應 `FileChecklistGrader` /
`CommandGrader` / `JsonGrader`,`AllOf` 組合)。build/test/lint = `cmd` 型別;`json`
(C1)= JSON 檔的形狀檢查(存在+可解析+必要鍵/型別),見 `tests/test_grader.py`。

profile 收尾政策 `auto_close: off|on_success|all`(`ScoreGate._auto_close`):自動關時
`human_score=agent_score`(contract `score`)、`transition("done")`、journal `closed(by=auto)`,
outcome 保留、不覆寫 handoff。與 `require_approval` 是人機光譜兩端。見
[design/agent-output.md §9](design/agent-output.md)。

## A/B 測試 / 自動選 profile

首次派工可從同族候選裡自動選一個 profile(A/B 分流或條件式 triage)。實作在
`src/arcp/selection.py`(`select_profile`),接線在 `dispatcher.handle` 的**首次派工**分支
(`sess is None` 且 main profile 有 `select`):選中的 profile 會 pin 進 session,resume 不
重選。設定(`select` 區塊 random/script 範例)、fail-safe、與 triage 的關係、觀測方式見
[design/selection.md](design/selection.md)。

## 服務 CLI 參數

一律用 `uv run python scripts/<script>.py` 執行;兩支都 argparse、支援 `-h`、**無位置參數**
(全 flag)、**不讀 env**(除 `--log-level` 等同 `ARCP_LOG_LEVEL`):
- `run_poller.py`:`-m/--minutes`(`-m 0` = 無限常駐,靠外部排程 / Ctrl-C / `POST /shutdown` 停)、
  `-i/--interval`、`--control-port`、`--form-port`、`--log-level`。
- `detail_server.py`:`--port`、`--host`(`--host 127.0.0.1` 鎖本機)、`--runtime`、
  `--control-url`、`--log-level`。

## CI / CD

- `.github/workflows/ci.yml`:push/PR → Python 3.10–3.13 矩陣 → `uv sync --extra dev`
  → `ruff check` → `uv build` → 離線測試。
- `.github/workflows/cd.yml`:打 tag `v*` → `uv build` → GitHub Release 附 wheel/sdist
  (尚未發 PyPI)。

## 慣例

- 每個工作階段(wave)單獨 commit;commit 訊息帶 Why。
- 新需求/決策**先更新 [requirements.md](requirements.md)**(保存 Why),再動工。
- 核心套件 `src/arcp/` 維持 ruff 嚴格 clean。
- 貢獻流程見 [CONTRIBUTING](../CONTRIBUTING.md)。

## 已知限制 / 除錯 FAQ

- **強制中斷(evict / `@agent hold`)是立即 killpg,不是優雅停**:進行中的工具步驟會被
  硬殺。**不丟資料** —— 下輪 native resume 會從 session 接回、重跑被砍的那一步(檔案系統
  真值 + grader 保證正確)。未做「SIGTERM→10s→SIGKILL」優雅停,因 native resume 已保進度、
  grace 效益低。**debug 時若看到某工具步驟在 resume 後重跑一次,這是預期現象**,非 bug。
  設計見 [interaction §13.4](design/interaction.md)。
