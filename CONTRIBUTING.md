# Contributing

歡迎貢獻。開發細節見 [docs/developer-guide.md](docs/developer-guide.md)。

## 環境

```bash
uv sync --extra dev
git config core.hooksPath .githooks   # 啟用 pre-commit(ruff),每台機器一次
```

## 送 PR 前的檢查(= CI 會跑的)

```bash
uv run ruff check .
uv build
ARCP_CONFIG=routes.example.yaml sh -c '
  for t in tests/test_*.py; do uv run python "$t"; done
  uv run python tests/harness_selftest.py
  uv run python tests/e2e_dashboard.py
  uv run python tests/e2e_form.py'
```

從 repo root 執行即可(腳本/設定/vendored 由 `arcp.paths` 以 repo-root 相對解析,不綁 cwd)。
全綠再送。CI(`.github/workflows/ci.yml`)會在 Python 3.10–3.13 重跑一次。
需真 Jira/agent 的測試(`scripts/smoke_jira.py`、`tests/e2e_c*`)**不在 CI**,請本機自行驗證。

## 規範

- **核心套件 `src/arcp/` 維持 ruff 嚴格 clean**;測試/腳本有 per-file 放寬。
- **新需求或決策變更,先更新 [docs/requirements.md](docs/requirements.md)**(保存 Why),
  再動工;跨系統的重大取捨補到 [docs/decisions.md](docs/decisions.md)。
- commit 訊息帶「為什麼」,一個邏輯變更一個 commit。
- 相依元件一律 **vendor 進 repo**(內網零外部依賴原則),勿引入 CDN。
- 不要 commit 憑證(在 `~/.env`)、runtime 資料、venv、工具快取(見 `.gitignore`)。

## 安全

- Jira 憑證只放 `~/.env`,永不進版控、永不顯示。
- dashboard 預設鎖 `127.0.0.1`;綁 `0.0.0.0` 會對內網開放,請確認信任邊界。
- 互動表單的一次性 token 是機密,勿記入共用日誌。
