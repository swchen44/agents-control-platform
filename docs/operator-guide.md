# 管理者手冊(Operator)

給「**營運這個 Control Plane**」的人 —— 起服務、顧健康、調設定、備份、處理異常。
(想用它辦事的人看 [使用者手冊](user-guide.md);想改程式的看 [開發者手冊](developer-guide.md)。)

## 0. 你管的三個進程 + 三個資料夾

- **poller**(`scripts/run_poller.py`):撿 Jira 票 → 派 agent。核心常駐。同時起 **control
  API**(預設 127.0.0.1:8787)+ **表單服務**(預設 :8790)。
- **dashboard**(`scripts/detail_server.py`):唯讀觀測(預設 :8788)。
- 資料夾:`config/`(設定,git 追蹤)、`vendor/`(離線資產)、**`runtime/`**(狀態:
  `harness.db` + `events.jsonl` + `runs/` + workspaces,**gitignore、絕不 wipe**)。

## 1. 起 / 停

```bash
uv sync                                             # 一次
# ~/.env: JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN
uv run python scripts/smoke_jira.py                 # 唯讀冒煙:驗 Jira 連線
uv run python scripts/run_poller.py [分鐘] [間隔秒] # 常駐 poller(預設 30 分、15 秒)
ARCP_DASH_HOST=127.0.0.1 uv run python scripts/detail_server.py   # 另開:dashboard
```

- **poller 參數化**:`python3 scripts/run_poller.py [minutes] [interval] [--control-port N]
  [--form-port N] [--log-level DEBUG|INFO|WARNING|ERROR]`。**`minutes=0` → 無限常駐**
  (24h+,靠外部排程 / Ctrl-C / `POST /shutdown` 停);預設 30 分、15 秒。例:`run_poller.py 0`
  (24 小時常駐)、`run_poller.py 0 15 --form-port 8899 --log-level DEBUG`。
- **dashboard 參數化**:`python3 scripts/detail_server.py [--port N] [--host H] [--runtime DIR]
  [--control-url URL] [--log-level LEVEL]`;`--host 127.0.0.1` 鎖本機;相容舊式位置參數
  `[runtime] [port] [control_url]`。
- **log 層級**:`--log-level` 等同設環境變數 `ARCP_LOG_LEVEL`(預設 INFO)。兩個服務都可用
  `-h` / `--help` 看完整說明。
- **優雅停**:`curl -X POST :8787/shutdown`(當前輪跑完退出);或直接 Ctrl-C。
- poller 是**時間盒**:到時自動退,靠外部(cron / 迭代)重起;重起不重跑(冪等靠 `runtime/`)。
  要 24h+ 常駐請用 `minutes=0`。

## 2. 日常控制(control API,或 dashboard Control 頁)

| 動作 | 指令 | 說明 |
|---|---|---|
| 狀態 | `GET :8787/status` | in_flight / queued / paused / degraded |
| 暫停派工 | `POST :8787/pause` | 只擋新派工,正在跑的不中斷 |
| 恢復 | `POST :8787/resume` | |
| 熱重載設定 | `POST :8787/reload` | 重讀 `config/`,**壞設定回 400、舊設定續用**(fail-safe) |
| 強制驅逐卡住 agent | `POST :8787/evict/<issue_id>` | killpg 釋放資源、不耗 attempt、下輪 native resume |
| 解除 Jira 降級 | `POST :8787/recover` | Jira 恢復後手動解降級(通常自動) |

## 3. 監控健康:Dashboard **Server 頁**

`/server` 頁有**效能監控**(8 個紅黃綠燈)+ 各 profile 效能表 + 系統資源 + 連線 + 強制驅逐統計:

- **紅燈就是熱點**:失敗率 >30%、排隊 >5、最舊未終態票 >24h、evict 近1h >3、花費 >$5/h、
  錯誤事件 >3、系統資源 >90%、journal >200MB。
- **bottleneck 心法**:ARCP 本身開銷小;慢幾乎都在 ① agent 執行時長(model)② Jira 延遲/降級
  ③ 並發飽和(排隊)。看燈 + 各 profile 時長/$ 找熱點;單票細節看 ticket 頁 trace。
- **票列過濾(dashboard 上方過濾列)**:profile / summary / description 三個關鍵字框,預設
  **一般字串包含比對(不分大小寫)**;勾「🔤 Regex」checkbox → 改**正則(regex,亦不分大小寫)**。
  無效正則該框標紅、暫不過濾;過濾狀態寫進 URL(可分享深連結)。對應 REST:
  `GET /api/v1/tickets?q=<關鍵字或正則>&field=<key|summary|profile|desc|all>&mode=<match|regex>`
  (match=不分大小寫子字串;regex=正則亦不分大小寫);回傳含 `filter`,無效正則時另含 `filter_error`。
- 除錯用 journal:見 [可觀測性](design/observability.md) + [troubleshooting](troubleshooting.md)。

## 4. 調設定(不重啟)

- 設定在 `config/config.yaml`(+ 拆檔的 `config/profiles/<名>.yaml`)。改完 `POST /reload`。
- **新增一個 agent(profile)**:在 `config/profiles/<名>.yaml` 建一個(檔名=profile 名,
  範例見 [設計/workspace](design/workspace.md)),`config.yaml` 的 `outer_loop.routes` 加比對
  規則指到它 → `POST /reload`。
- **A/B 測試 / 自動選 profile(Q16)**:main profile 加 `select` 區塊 —— `candidates`(候選
  profile 名清單,每個名字**須以 main 名為前綴**)+ `method: random | script` + `script`
  (method=script 時的腳本路徑)。**首次派工時選一個實際 profile 並 pin 進 session**
  (resume 不重選,確保同一票結果穩定)。`method=script` 時,腳本吃 JSON stdin(含 ticket 資訊 /
  clearquest_id / 候選及其 yaml 路徑)→ stdout 印出要用的 profile 名 → 可做條件式 triage。
  **任何失敗 fail-safe 回 main**;journal 記 `profile_selected`(original / chosen / method),
  在 dashboard 事件時間軸 / `/api/v1/tickets` 可觀測「這票實際跑哪個 profile」。詳見
  [設計/選擇](design/selection.md)。
- **控管花費**:profile 的 `max_budget_usd`(單次)/ `max_budget_monthly_usd`(月);超支交人。
- **控管並發**:`outer_loop.concurrency`(global + per-engine + per-profile);超額 QUEUED。

## 5. 備份與還原(Q4 runbook)

**要備份三樣**(其餘可重生):

| 備份 | 是什麼 | 備份法 |
|---|---|---|
| `config/` | 你的設定 + profiles/templates/skills/hooks | 已在 git → push 即備份 |
| `runtime/harness.db` | **狀態/冪等記憶**(ticket_session/watch/interactions) | **停 poller** 後複製,或 `sqlite3 runtime/harness.db ".backup bak.db"`(WAL,線上備份用 `.backup`) |
| `runtime/events.jsonl` + `runtime/runs/` | journal + transcript(稽核軌) | 直接複製 |
| `~/.env` | 憑證 | **另外**安全保管(絕不進 git) |

**還原**:把 `config/` + `runtime/` 複製回原位、`~/.env` 就位 → `run_poller.py`。poller 讀
`runtime/` 續跑(open 票不重派、不重花錢)。⚠️ **切勿 wipe `runtime/`**:那是冪等的記憶,
清掉 open 票會被當新工作重派([LESSONS #9](lessons.md))。

## 6. 多實例(同機並存多個 Control Plane)

複製整個資料夾成獨立一份,各自 `runtime/` + 設定 + port。**務必分**:`config.yaml` 的
`source.name`、**Jira project/jql(絕不重疊,否則互搶票)**、control port、dashboard port +
指向。細節見 [README「多實例部署」](../README.md)。

## 7. 異常處置

| 症狀 | 處置 |
|---|---|
| 某票卡住不動 | `POST /evict/<id>`(釋放 + 下輪 resume);或看 ticket 頁 trace |
| 整個實例停派、`degraded` | Jira 寫入/健康連續失敗 → 自動降級;恢復通常自動,卡住 `POST /recover` |
| 花費爆 | 多半 model 設錯(opus vs haiku 差 ~8×);測試 profile 一律用便宜 model |
| dashboard 打不開 | 確認 detail_server 在跑、port 沒被占、`<runtime>` 指對 |
| 更多 | [troubleshooting runbook](troubleshooting.md) |

## 8. 安全(內網)

- dashboard/control **預設綁 `0.0.0.0`(內網開放、無認證)**:唯讀 dashboard 會顯示系統/程序
  資訊;control API 有寫入端點(pause/shutdown/evict)。**要鎖本機**:`ARCP_DASH_HOST=127.0.0.1`
  + control `host: 127.0.0.1`。這是信任邊界的取捨,見 [requirements §7](requirements.md)。
- 憑證只在 `~/.env`,永不進 git、dashboard 只顯示「有/無/到期」不顯示值。
- 互動表單的一次性 token 是機密,勿記入共用日誌。

## 9. 升級/改動後複驗

改了設定、資料夾結構或升級版本後,跑複驗助手確認沒壞:

```bash
uv run python scripts/reverify_v1.py --offline   # 免費本機:runner 路徑/config 載入/事件字典
uv run python scripts/reverify_v1.py             # 再加 Jira 唯讀連線(需 ~/.env,不派 agent)
```

它會印出**付費部分**(真派一次工才驗得到:runner spawn / select / install / hold / 自評 /
human-prompt)的逐項清單,你在有 agent/充電時對照 dashboard trace + `runtime/events.jsonl` 打勾。

## 10. 互動表單(HIL)一次性連結:設計、持久化與重啟

人機互動(補資訊 / 核可 / 評分裁決 / **改派下一棒**)一律走**一次性 token 表單**,不讓人
直接編 Jira description。你身為 operator 要知道它的儲存模型,才懂備份與重啟為何安全。

- **連結長怎樣**:`<form_base>/form/<token>`,`token` = `secrets.token_urlsafe(32)`
  (≈256-bit 不可預測亂數)。一條連結綁「單一票 + 單一表單 schema + 單一 token」。
  **token 即 capability**:誰有這 URL 誰就能填 → 當機密看待(見 §8;form 服務已回
  `Cache-Control: no-store` + `Referrer-Policy: no-referrer`,勿把連結記進共用日誌)。
- **存在哪 → 永久儲存,不是記憶體**:全存在 `runtime/harness.db` 的 **`interactions` 表**
  (request_id / token / issue_id / schema / created_at / expires_at / **status** /
  payload / submission / submitted_by / reminders)。表單服務(`form_server`)**無狀態**——
  每次開連結都拿 token 去 DB 查(`get_interaction`),不靠任何記憶體變數。
- **「一次性」怎麼保證**:靠 **status 狀態機**(`pending → submitted / expired / invalidated`),
  不是靠刪 token。填過送出 → status 改 `submitted` 並落 DB;之後同連結只顯示唯讀「已提交」頁。
- **重啟安不安全 → 安全**。因為狀態全在 DB:poller / 表單服務 / 整台重啟後,未填的連結
  照樣能填、已填的仍唯讀、逾期的仍逾期,**完全還原**。這也是為何 **`runtime/` 絕不能 wipe**
  (那是冪等記憶,見 §5)——清掉 = 所有未回的表單連結失憶。
- **提交那刻的 Jira 把關**:送出時若 Jira 異常,系統**不落地**(不改 status)、回「稍後再試」,
  你可原連結重送 → 不會出現「DB 記已提交但 Jira 沒回寫」的不同步(不做 work queue,見 §7)。

## 常見問題(Operator FAQ)

- **第一次要手動建 database 嗎?** 不用。`Store` 首次跑自動在 `runtime/harness.db` 建表,零手動。
- **一次性表單連結重啟後還記得嗎?** 記得。存在 `harness.db` 的 `interactions` 表(非記憶體),
  靠 status 狀態機保證一次性,重啟完全還原。設計細節見 §10。
- **突然多一張 `[base:XXX]` 的票是誰建的?** 是**跨票換手(base)**:人在 HIL 表單選
  「改派下一棒 → 換手種類=跨票換手」,系統會用 `create_ticket` 在同 project 自動開一張新票交給
  選定 profile,原票收 ABORTED(交接,非失敗)。新票沿用原票 labels(故走同 route)、下一輪
  自動被撿起跑(首次佈建時注入 base 脈絡)。這是**唯一**由系統(非人)建 Jira 票的路徑;journal 有
  `handoff(kind=base)` + `base_injected`。詳見 [design/architecture.md §4.1](design/architecture.md)。
- **hot reload 會先驗設定檔嗎?** 會。壞 config 擲 ConfigError → 回 400、**舊設定原封續用**
  (不弄死 poller);見 [hotreload](design/hotreload.md)。
- **poller 每次跑多久?** 由你給的「分鐘」時間盒決定(預設 30 分)。到時退出、重起續跑。
- 其餘見 [FAQ](faq.md)。
