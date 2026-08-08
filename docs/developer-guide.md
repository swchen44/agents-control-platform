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
  `sections`(description 三方分段 + hash)、`interaction` + `hil` + `form_server`(W11 表單)
- **狀態·觀測·控制**:`store`(SQLite + journal)、`control_api`、`transcript`、`retention`
  (`detail_server.py` 唯讀 dashboard 在 `scripts/`)

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
- CI 用 `ARCP_CONFIG=routes.example.yaml`(避免依賴本機才有的 openhands venv)。
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

在 `routes.yaml` 的 `inner_loop.profiles` 加一項(見 `routes.example.yaml` 範本):
`agent`(backend/engine/model/sandbox)、`verify`(確定性檢查)、`loop.max_attempts`、
`goal` / 預算 / `human_minutes_est`;再在 `outer_loop.routes` 加比對規則指到它。

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
