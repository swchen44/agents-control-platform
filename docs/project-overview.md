# 專案檔案介紹

整個 repo 的目錄地圖 —— 每個資料夾/關鍵檔案在幹嘛。

## 頂層

```
agents-control-platform/
├── pyproject.toml          # 套件定義(hatchling)+ 相依 + [tool.ruff]
├── uv.lock                 # 鎖定相依(CI 可重現)
├── LICENSE                 # MIT
├── README.md               # 專案門面
├── CONTRIBUTING.md         # 貢獻指南
├── CHANGELOG.md            # 版本變更
├── HANDOFF.md              # 給接手 session 的脈絡(內部)
├── BACKLOG.md              # 待辦/路線(內部)
├── src/arcp/               # ← 套件本體(可安裝)
├── scripts/                # 可執行入口 + 被 spawn 的 runner + demo
├── tests/                  # 單元 + 端到端測試
├── harness/                # 設定 + vendored 資產 + runtime 資料(dev 工作區)
├── docs/                   # 文件(本資料夾;研究報告在 docs/research/)
├── examples/               # PoC / 對照樣本(dev-only,不入 wheel)
└── .github/workflows/      # CI / CD
```

## `src/arcp/` — 套件(五層)

| 層 | 檔 | 職責 |
|---|---|---|
| 輸入 | `jira_source.py` | Jira Cloud 讀寫封裝(唯一碰 Cloud 細節);`triggers.py` 排程觸發 |
| 決策 | `poller.py`(OuterLoop 外圈輪詢)、`routing.py`(route 比對)、`gate.py`(F1 分層額度閘) |
| 執行 | `dispatcher.py`(派工+證據迴路)、`inner_runner.py`、`workspace.py`+`isolation.py`、`contract.py`(envelope)、`grader.py`(確定性驗證) |
| 人機協作 | `approval.py`(起點審批)、`scoring.py`(HIL(End) 評分)、`commands.py`(@agent 指令 + 離手政策)、`sections.py`(description 三方分段+hash)、`interaction.py`+`hil.py`+`form_server.py`(W11 一次性表單) |
| 狀態·觀測·控制 | `store.py`(SQLite + journal)、`control_api.py`(REST 控制面)、`transcript.py`、`retention.py` |
| 其他 | `rawcli/`(RawCLIAgent,純 stdlib 執行單元)、`config.py`、`logutil.py`、`profiles.py`、`ticket.py`、`sysinfo.py`、`server_manager.py` |

模組間關係(trigger/輸入/輸出/上下游)見 [設計/架構](design/architecture.md)。

## `scripts/` — 可執行入口 + runner

- **主程式**:`run_poller.py`(常駐 poller)、`detail_server.py`(唯讀 dashboard)、
  `run_trigger.py`(oneshot 觸發)、`smoke_jira.py`(真 Jira 冒煙)
- **執行單元 runner**(被 `arcp.inner_runner` spawn):`inner_rawcli_runner.py` /
  `inner_acp_runner.py` / `inner_agentserver_runner.py`、`c0_*`(server launcher / stub)
- **demo/spike**:`compare_abc.py`、`demo_concurrent.py`、`spike_c0.py`

> 全部從 repo root 執行即可(`uv run python scripts/<x>.py`)。設定/vendored/runtime 由
> `arcp.paths` 以 repo-root 相對解析,不綁 cwd。

## `tests/` — 測試

- `test_*.py`(單元)、`harness_selftest.py`、`e2e_dashboard.py` / `e2e_form.py`(離線 e2e,
  **CI 跑**);`e2e_c*` / `e2e_codex*` / `smoke` 等需真 Jira/agent 的**不在 CI**。
- `_env.py`:路徑啟動(把 `scripts/` 放進 `sys.path`,供少數 import 腳本的測試用)。

## `harness/` — 設定 + vendored 資產 + runtime

- **設定**:`routes.yaml`(你的實際設定,`~/.env` 放憑證)、`routes.example.yaml`(範例/CI 用)
- **vendored**:`tools/cclog/`(claude-code-log,MIT,transcript 渲染)、
  `tools/…/vendor/`(swagger-ui / vis-timeline / svg-pan-zoom,離線)
- **runtime 資料**(gitignored):`runtime_live/`、`runtime_*/`
- **歷史文件**:`PLAN_wave*.md`、`TEST_real_jira.md`(踩坑教訓已移到
  [docs/lessons.md](lessons.md))

## `docs/`

見 [index](index.md)。`docs/design/` 是各子系統機制細節;`docs/research/` 是研究/實驗的
「結論 + 比較」策展文章 **+ 原始 deep-research 長文**(兩層同放);`docs/troubleshooting.md`
+ `docs/design/observability.md` + `docs/ai-debugging.md` 是離線除錯層。

## `examples/`(dev-only)

`examples/jira-agent-poc/`(A 路線 PoC,`arcp_poc.*`;grader 已併入套件)、
`examples/openhands-*`(openhands backend 選配,需 venv)。**不入 wheel、CI 不跑**。
(研究報告已移到 [`docs/research/`](research/README.md)。)
