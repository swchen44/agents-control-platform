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

## 4. 設定 config.yaml

複製範例、改成你的:

```bash
cp config/config.example.yaml config/config.yaml
```

重點欄位:
- `outer_loop.source.jql` — poller 監看哪些票(務必帶 `project=…` 等條件)。
- `outer_loop.routes` — 票 → profile 的比對(標籤/keyword/assignee);`on_match`:
  `ignore` / `notify_only`(灰度只記錄)/ `create_or_resume`(真的派工)。
- `inner_loop.profiles.<name>` — agent 設定:`agent.backend`(rawcli 免 venv)、
  `engine`(claude/codex)、`model`、`verify`(確定性檢查)、`loop.max_attempts`、
  `goal`、`budget`(token/usd soft/hard + 月上限,見 §8.5)、`human_minutes_est`。
- `control` / `form` — 控制面與互動表單服務的 host/port。

## 5. 跑起來

```bash
uv run python scripts/run_poller.py                 # 預設 30 分、每 15 秒;時間盒到自動退
uv run python scripts/run_poller.py -m 0            # 無限常駐(24h+;靠 Ctrl-C / POST /shutdown 停)
# 可選:-m/--minutes 分鐘、-i/--interval 秒、--control-port/--form-port/--log-level(見 -h)
```

啟動時會「認養」當下已存在的票(只對之後的新票/新留言反應,不重跑歷史)。同時起:
- **control API**(預設 127.0.0.1:8787):`/status /health` + `POST /pause /resume
  /reload /shutdown /evict/<id> /recover`。
- **互動表單服務**(預設 127.0.0.1:8790):人填的一次性 token 表單。

## 6. 看 dashboard(唯讀觀測)

```bash
uv run python scripts/detail_server.py --host 127.0.0.1   # runtime 資料預設 runtime/
# 開 http://127.0.0.1:8788(-h 看 --port/--runtime/--control-url/--log-level)
```

- **Dashboard** — KPI(進行中/排隊/HIL(Middle)/HIL(End)/成功/失敗/失敗率)、時間圖、
  各 profile 圖、票表(狀態徽章、完成度分數、cost)。
- **ticket 頁** — 四層 trace(L0/L1/L2/L3)、Conversation、事件時間軸、transcript 下載。
- **Introduction** — HIL 狀態機、模組架構圖 + 職責表、概念說明(皆可拖曳/縮放)。
- **Server / Control / Agent Detail / DB Browser** — 系統資訊 / 控制 / 設定 / 唯讀 DB。
- **效能監控(在 Server 頁)** — 8 個紅黃綠燈(失敗率/排隊/最舊等待/evict/花費速率/錯誤/系統資源/journal 大小)+ 各 profile 效能表 + bottleneck 說明。

⚠️ dashboard 綁 `0.0.0.0` 會對內網開放(顯示系統/程序資訊);鎖本機用
`--host 127.0.0.1`。內網零外部依賴(不吃 CDN)。

## 7. 人怎麼介入(HIL)

- **通知**:agent 需要你時,在票上留言並 `@mention` 你,附一次性表單連結(不改 assignee)。
- **表單型別**:`need_info`(補資訊)/ `decision`(選項核可)/ `score_and_close`
  (評分 0–10 + 裁決:關單 / 續跑 / **改派下一棒**)。
- 你填完送出 → 系統回寫 Jira description 的 human 段 + 貼稽核 comment,並讓 agent
  resume;`score_and_close` 選「關單」→ 系統幫你把 Jira 轉 Done。
- **看得到 agent 產了什麼**:agent 完成時回傳結構化產出 —— Jira comment 有「完成/未完成」
  自報 + 程式碼(Gerrit)連結 + 附件(小檔直接附到票、大檔給下載連結);**評分表單頁**更是
  自足駕駛艙:渲染成果敘事 + 可下載附件 + 花費/attempts + Jira/transcript 連結,不用離開就評分。
  (產出契約見 [design/agent-output.md](design/agent-output.md)。)
- **改派下一棒(handoff,W10.3)**:裁決選 `handoff` 後,再選**換手種類** + 下一棒 profile
  (下拉,候選=系統載入的全部 profile)+ 交接指示:
  - **同票換手(next)**:同一張票換一個 profile/引擎在**這張票**接手 —— 重置 session
    (session_id、attempts 歸零)、鎖定新 profile、依新 profile 的 template 重新佈建
    workspace,回「進行中」。**非 native resume**(新 profile 重新開始,不接手前一棒的原生
    session);脈絡全留在這張 Jira 票(留言 / description / 人類指示 → 新 TICKET.md)。
  - **跨票換手(base)**:**系統自動**用 `create_ticket` 在同 project 另開一張**新票**交給
    選定 profile,並預建其 session(base_ref 指回本票);本票收 ABORTED(標記為「交接」,
    不算失敗)。新票下一輪首次佈建時,把本票脈絡(TICKET.md + 最後 envelope)複製進新票
    工作區的 `ws/BASE_<本票>/` + 人類指示段指路。適合換引擎 / 重開 / 跨專案但要保留前輪脈絡時。
  - 沒填全換手種類 / profile → fail-safe 降級回「續跑原 agent」(不會弄壞本票)。
- Jira 暫時異常時,表單會提示「暫勿送出」;送出也會回「稍後再試」(不會假裝成功)。
- **連結是一次性的**:填過一次後同一連結只會顯示唯讀結果;連結**重啟後仍有效**
  (存在資料庫,非記憶體),所以你晚點再開也還在。
- **有些票會自動關單**:若該 profile 設了 `auto_close`(無人值守用),票跑完不會發評分表單給你
  —— 系統直接把**人類評分 = agent 自評**、自動關單(comment 會標 `by=auto`)。`on_success` 只
  自動關成功的、失敗仍會找你;`all` 則全自動。要不要自動關由管理者在 profile 設定。

## 8. 指令台(下指令的地方,取代留言)

**不要在 Jira 打指令留言了**——每張被接管的票,系統會在 **description 最上方的 control 段**
放一個「**指令台**」連結,並在開票時貼一則指路 comment。點進去就是一個網頁,依票**當前狀態**
列出此刻能下的指令,每個都附**用途 / 時機 / 副作用 / 效果**說明:

| 指令 | 做什麼 |
|---|---|
| `run` | 解除等待、讓 agent 接著跑 |
| `retry` | 從頭再試(attempts 歸零) |
| `hold` | **立即中斷**正在跑的 agent → 開一張表單請你給新指示 |
| `stop` | 交還人工(暫不再派 agent) |
| `cancel` | 取消本票(破壞性,需勾選確認) |
| `next <profile>` | 同票換手:換一個 agent profile 接手 |

- **填 email**:送出前要填你的 email(供稽核;瀏覽器會記住,不用每次重打)。
- **破壞性指令**(cancel / stop)要**勾選確認**才會送出。
- **這個連結綁本票、可重複用**,一直有效到**票結案**才失效(不是一次性的)。
- 換手也可走 HIL 表單(§7 改派下一棒);表單能選「跨票換手」,指令台 `next` 只做同票換手。

> 自動化 / 程式要下指令走 **REST API**:`POST /ticket/<id>/command`(見管理者手冊),
> 與指令台同一套核心。

## 8.5 碰到 token / 花費上限(自助增額)

每張票有 **soft / hard** 兩層 token 與 USD 上限(default 由管理者在 profile 設)。agent 每
輪開跑前會檢查累計用量:

- **達 soft 上限** → 系統暫停(`pending:budget`)並發你一張**增額表單**(@mention + 一次性
  連結):上面顯示**已用多少 token/USD、soft/hard 是多少、目前做到哪的 summary**。你可以
  **自助把本票上限調高**(填新值,**不得超過 hard**)→ 送出後 agent 下輪繼續跑。
- **達 hard 上限 / 月或全站上限** → 你**不能自助**;系統會留言請你**通知管理者**改設定
  (profile / 全站 yaml)後 hot reload,調好本票就自動續跑。

> token 與 USD **兩個都會檢查、哪個先破就卡**;某引擎(如 codex)可能只有 token 統計,那就
> 由 token 卡。詳見 [設計/Budget](design/budget.md)。

## 9. 常見操作

- **暫停/恢復派工**:`curl -X POST http://127.0.0.1:8787/pause`(或 dashboard Control 頁)。
- **強制驅逐卡住的 agent**:ticket 頁「⏻ 強制驅逐」或 `POST /evict/<id>`(killpg 釋放
  資源、不耗 attempt、下輪自動 native resume)。
- **在 dashboard 過濾票**:上方過濾列 profile / summary / description 三個關鍵字框,預設
  **一般字串包含比對(不分大小寫)**;勾「🔤 Regex」checkbox → 改**正則(regex,亦不分大小寫)**。
  無效正則該框標紅、暫不過濾;過濾狀態寫進 URL,可分享深連結。
- **Jira 恢復後解除降級**:自動偵測,或 `POST /recover` / dashboard「🩺 Recover」。

疑問見 [FAQ](faq.md)。
