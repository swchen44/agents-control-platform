# 使用者手冊

給「想把 ARCP 跑起來、用它管 agent」的人。開發細節見 [開發者手冊](developer-guide.md)。

## 1. 這是什麼

你在 Jira 開票(或貼標籤)→ ARCP 的 poller 看到 → 派一個 headless coding agent
(`claude -p` / `codex exec`)去做 → agent 在隔離 workspace 內執行、產出證據 →
確定性驗證(grader)過才算 SUCCESS → 需要人時,agent 在票上 @mention 你並附一個
**一次性表單連結**;你填完,系統把結果寫回 Jira 並讓 agent 續跑或關單。

**你用既有的 Jira 操作**(開票、貼標籤、留言、填表單、關單)就能指揮一支 agent 大軍。

## 2. 安裝

需要 Python ≥ 3.10 與 [uv](https://github.com/astral-sh/uv)。

```bash
git clone https://github.com/swchen44/agents-control-platform
cd agents-control-platform
uv sync                      # 建 .venv、裝相依 + editable 安裝 arcp
```

或用 pip:`python3 -m pip install -e .`

## 3. 設定 Jira 憑證

在 `~/.env` 放(**絕不進版控**):

```
JIRA_BASE_URL=https://your-org.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=<你的 Atlassian API token>
```

驗證連線:`uv run python scripts/smoke_jira.py`(唯讀:auth + search;從 repo root)。

## 4. 設定 routes.yaml

複製範例、改成你的:

```bash
cp config/routes.example.yaml config/routes.yaml
```

重點欄位:
- `outer_loop.source.jql` — poller 監看哪些票(務必帶 `project=…` 等條件)。
- `outer_loop.routes` — 票 → profile 的比對(標籤/keyword/assignee);`on_match`:
  `ignore` / `notify_only`(灰度只記錄)/ `create_or_resume`(真的派工)。
- `inner_loop.profiles.<name>` — agent 設定:`agent.backend`(rawcli 免 venv)、
  `engine`(claude/codex)、`model`、`verify`(確定性檢查)、`loop.max_attempts`、
  `goal`、`max_budget_usd` / `max_budget_monthly_usd`、`human_minutes_est`。
- `control` / `form` — 控制面與互動表單服務的 host/port。

## 5. 跑起來

```bash
uv run python scripts/run_poller.py [分鐘] [間隔秒]   # 預設 30 分、15 秒;時間盒到自動退
```

啟動時會「認養」當下已存在的票(只對之後的新票/新留言反應,不重跑歷史)。同時起:
- **control API**(預設 127.0.0.1:8787):`/status /health` + `POST /pause /resume
  /reload /shutdown /evict/<id> /recover`。
- **互動表單服務**(預設 127.0.0.1:8790):人填的一次性 token 表單。

## 6. 看 dashboard(唯讀觀測)

```bash
ARCP_DASH_HOST=127.0.0.1 uv run python scripts/detail_server.py   # runtime 資料預設 runtime/
# 開 http://127.0.0.1:8788
```

- **Dashboard** — KPI(進行中/排隊/HIL(Middle)/HIL(End)/成功/失敗/失敗率)、時間圖、
  各 profile 圖、票表(狀態徽章、完成度分數、cost)。
- **ticket 頁** — 四層 trace(L0/L1/L2/L3)、Conversation、事件時間軸、transcript 下載。
- **Introduction** — HIL 狀態機、模組架構圖 + 職責表、概念說明(皆可拖曳/縮放)。
- **Server / Control / Agent Detail / DB Browser** — 系統資訊 / 控制 / 設定 / 唯讀 DB。

⚠️ dashboard 綁 `0.0.0.0` 會對內網開放(顯示系統/程序資訊);鎖本機用
`ARCP_DASH_HOST=127.0.0.1`。內網零外部依賴(不吃 CDN)。

## 7. 人怎麼介入(HIL)

- **通知**:agent 需要你時,在票上留言並 `@mention` 你,附一次性表單連結(不改 assignee)。
- **表單型別**:`need_info`(補資訊)/ `decision`(選項核可)/ `score_and_close`
  (評分 0–10 + 裁決:關單 or 續跑)。
- 你填完送出 → 系統回寫 Jira description 的 human 段 + 貼稽核 comment,並讓 agent
  resume;`score_and_close` 選「關單」→ 系統幫你把 Jira 轉 Done。
- Jira 暫時異常時,表單會提示「暫勿送出」;送出也會回「稍後再試」(不會假裝成功)。

## 8. 留言指令(輔助路徑,保留)

在票上留言(需在白名單):`@agent run` / `retry` / `stop` / `cancel` /
`next <profile>`(換手)/ `handoff`。

## 9. 常見操作

- **暫停/恢復派工**:`curl -X POST http://127.0.0.1:8787/pause`(或 dashboard Control 頁)。
- **強制驅逐卡住的 agent**:ticket 頁「⏻ 強制驅逐」或 `POST /evict/<id>`(killpg 釋放
  資源、不耗 attempt、下輪自動 native resume)。
- **Jira 恢復後解除降級**:自動偵測,或 `POST /recover` / dashboard「🩺 Recover」。

疑問見 [FAQ](faq.md)。
