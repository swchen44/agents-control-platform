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
uv run python scripts/run_poller.py                 # 常駐 poller(預設 30 分、每 15 秒)
uv run python scripts/detail_server.py --host 127.0.0.1   # 另開:dashboard(鎖本機)
```

- **一律用 `uv run` 執行**(專案用 uv 管環境);兩個服務都可用 `-h` / `--help` 看完整說明。
- **poller 參數化**:`uv run python scripts/run_poller.py [-m MINUTES] [-i INTERVAL]
  [--control-port N] [--form-port N] [--log-level DEBUG|INFO|WARNING|ERROR]`。
  **`-m 0`(`--minutes 0`)→ 無限常駐**(24h+,靠外部排程 / Ctrl-C / `POST /shutdown` 停);
  預設 30 分、每 15 秒。例:`run_poller.py -m 0`(24 小時常駐)、
  `run_poller.py -m 0 -i 15 --form-port 8899 --log-level DEBUG`。
- **dashboard 參數化**:`uv run python scripts/detail_server.py [--port N] [--host H]
  [--runtime DIR] [--control-url URL] [--log-level LEVEL]`;`--host 127.0.0.1` 鎖本機、
  `--control-url` 指向本實例的 control API。**全走 CLI flag(不再讀 env)**。
- **log 層級**:`--log-level` 等同設環境變數 `ARCP_LOG_LEVEL`(預設 INFO)。
- **環境變數速查**(絕大多數不用設,有預設或走 CLI/config):

  | 變數 | 用途 | 預設 / 來源 |
  |---|---|---|
  | `JIRA_EMAIL` / `JIRA_API_TOKEN` | **憑證(必填)** | `~/.env`,永不進 git、不顯示值 |
  | `JIRA_BASE_URL` | Jira 實例 URL(**非機密**) | 建議放 **config.yaml `source.jira_base_url`**;不設則用 `~/.env` 的 `JIRA_BASE_URL` |
  | `ARCP_CONFIG` | config 檔路徑 | 預設 `config.yaml`(或 `arcp.paths`) |
  | `ARCP_LOG_LEVEL` / `ARCP_LOG_FILE` | log 層級 / 檔 | INFO / 無(`--log-level` 會設前者) |
  | `ARCP_NAME` | dashboard 實例名覆寫 | config `source.name` |
  | `ARCP_HOURLY_RATE` | ROI 顯示的人力時薪(選配) | 無(不設就不顯示人力成本對比) |

  Jira 連結由 **`source.jira_base_url`(config)→ `~/.env` JIRA_BASE_URL** 解析;email/token
  一律只在 `~/.env`。`config.yaml` 進版控,填 base_url 前確認 URL 可公開,否則留在 `~/.env`。
- **優雅停**:`curl -X POST :8787/shutdown`(當前輪跑完退出);或直接 Ctrl-C。
- poller 是**時間盒**:到時自動退,靠外部(cron / 迭代)重起;重起不重跑(冪等靠 `runtime/`)。
  要 24h+ 常駐請用 `-m 0`。

## 2. 日常控制(control API,或 dashboard Control 頁)

| 動作 | 指令 | 說明 |
|---|---|---|
| 狀態 | `GET :8787/status` | in_flight / queued / paused / degraded |
| 暫停派工 | `POST :8787/pause` | 只擋新派工,正在跑的不中斷 |
| 恢復 | `POST :8787/resume` | |
| 熱重載設定 | `POST :8787/reload` | 重讀 `config/`,**壞設定回 400、舊設定續用**(fail-safe) |
| 強制驅逐卡住 agent | `POST :8787/evict/<issue_id>` | killpg 釋放資源、不耗 attempt、下輪 native resume |
| 解除 Jira 降級 | `POST :8787/recover` | Jira 恢復後手動解降級(通常自動) |
| 對某票下指令 | `POST :8787/ticket/<id>/command` | body `{cmd,args,by}`;見下方「指令台」 |

### 2.1 指令台(人下指令的介面,取代舊 `@agent` 留言)

人的指令(run/retry/hold/stop/cancel/next)改走「**指令台**」表單:每張被接管的票,poller
首次派工時把連結寫進 **description 的 control 段** + 貼一則指路 comment(事件
`command_link_posted`),綁票、可重複用、票 close 才失效。表單依票**當前狀態**動態列可用指令、
附說明,需填 email、破壞性指令(cancel/stop)要二次確認。

- **自動化 / 程式**走 REST:`POST :8787/ticket/<id>/command`,body `{cmd,args,by}`
  (`args.profile` 供 next),回 `{ok,message}`。與指令台同一套核心、在 poller 行程(hold
  能正確 killpg)。
- **已移除**舊的 `@agent` 留言指令通道與 `commands.allowed_commenters` 白名單(未 release,
  不相容)。REST 端點沿用 control API 的信任模型(綁 127.0.0.1;要開放見 §8 安全)。

## 3. 監控健康:Dashboard **Server 頁**

`/server` 頁有**效能監控**(8 個紅黃綠燈)+ 各 profile 效能表 + 系統資源 + 連線 + 強制驅逐統計:

- **紅燈就是熱點**:失敗率 >30%、排隊 >5、最舊未終態票 >24h、evict 近1h >3、花費 >$5/h、
  錯誤事件 >3、系統資源 >90%、journal >200MB。
- **bottleneck 心法**:ARCP 本身開銷小;慢幾乎都在 ① agent 執行時長(model)② Jira 延遲/降級
  ③ 並發飽和(排隊)。看燈 + 各 profile 時長/$ 找熱點;單票細節看 ticket 頁 trace。
- **budget 燈**:Server 頁第 9 燈「budget 月用量(最高)」——全站/各 profile 月用量對上限的最高
  利用率(見 §4 budget)。

### 3.1 單票詳情頁(`/ticket/<key>`)

主頁票表**保持精簡**(掃視用);點 key 進**詳情頁**看全部。詳情頁「**來源・連結・用量**」卡:
- **來源**:人開/route、排程/單次 **job**、**跨票交接子票**、**ClearQuest CR** —— 由 journal +
  session 推導(零額外欄位)。
- **連結**:**Jira**(需 config `source.jira_base_url` 或 `~/.env` JIRA_BASE_URL 才成連結,
  不設則顯示 key)、**CR**
  (`clearquest_id`;CQ base_url 設了才成連結)、以及**本票發過的一次性連結清單**(指令台 /
  評分·決策·hold / budget 增額,含類型/狀態/建立時間/**完整可點 token URL** + 大檔 `/files`)。
- **用量**:per-ticket cost/tokens vs soft/hard 的 bar。
- ⚠️ **安全**:一次性連結是 **capability URL**(有連結即可操作/下載)。詳情頁會列出完整連結,
  所以 **dashboard 必須鎖本機/內網存取**(見 §8);別把 dashboard 開給不該操作的人。
- **票列過濾(dashboard 上方過濾列)**:profile / summary / description 三個關鍵字框,預設
  **一般字串包含比對(不分大小寫)**;勾「🔤 Regex」checkbox → 改**正則(regex,亦不分大小寫)**。
  無效正則該框標紅、暫不過濾;過濾狀態寫進 URL(可分享深連結)。對應 REST:
  `GET /api/v1/tickets?q=<關鍵字或正則>&field=<key|summary|profile|desc|all>&mode=<match|regex>`
  (match=不分大小寫子字串;regex=正則亦不分大小寫);回傳含 `filter`,無效正則時另含 `filter_error`。
- **agent 交付物**:終態時 agent 的 `OUTPUT.json` 會貼回 Jira —— comment(自報 + Gerrit
  連結 + 附件)+ 評分表單頁駕駛艙。附件**總和 <6MB 直接附到 Jira 票**、**≥6MB 走表單服務的
  `/files/<token>` 下載頁**(TTL 綁票)。看 journal `deliverables_posted`(mode=attach/link/none、
  n_attachments、skipped);設計見 [design/agent-output.md](design/agent-output.md)。
- 除錯用 journal:見 [可觀測性](design/observability.md) + [troubleshooting](troubleshooting.md)。

## 4. 調設定(不重啟)

- 設定在 `config/config.yaml`(+ 拆檔的 `config/profiles/<名>.yaml`)。改完 `POST /reload`。
- **新增一個 agent(profile)**:在 `config/profiles/<名>.yaml` 建一個(檔名=profile 名,
  範例見 [設計/workspace](design/workspace.md)),`config.yaml` 的 `outer_loop.routes` 加比對
  規則指到它 → `POST /reload`。
- **label vs profile(心智模型)**:**label = 入場券** —— poller 只撿「label 命中某條
  `create_or_resume` route」的票去派工;沒對到 route 的 label 就進不了場。**profile = 進場後
  誰來做** —— 由 route 或 triage 決定、鎖定在該票的 session。所以要讓一張票被跑:先給它一個
  對得到 route 的 **label**(入場),profile 再由 route/triage 決定。
- **A/B 測試 / 自動選 profile(Q16)**:main profile 加 `select` 區塊 —— `candidates`(候選
  profile 名清單,每個名字**須以 main 名為前綴**)+ `method: random | script` + `script`
  (method=script 時的腳本路徑)。**首次派工時選一個實際 profile 並 寫入 session**
  (resume 不重選,確保同一票結果穩定)。`method=script` 時,腳本吃 JSON stdin(含 ticket 資訊 /
  clearquest_id / 候選及其 yaml 路徑)→ stdout 印出要用的 profile 名 → 可做條件式 triage。
  **任何失敗 fail-safe 回 main**;journal 記 `profile_selected`(original / chosen / method),
  在 dashboard 事件時間軸 / `/api/v1/tickets` 可觀測「這票實際跑哪個 profile」。詳見
  [設計/選擇](design/selection.md)。
- **triage 判不出 → Jira 取消狀態(`source.cancel_status`)**:當 `select`(triage)腳本回
  `notfound`(判不出適用的 agent profile),票會中止(內部 `outcome=ABORTED`)。若設了
  `source.cancel_status: "Cancelled"`(或你 workflow 的取消狀態名),harness 會**優先把 Jira
  轉到那個狀態**;沒設或該 workflow 沒有這狀態 → 優雅退回一般結案(done-category),不擋流程。
  Jira 沒有內建「取消」類別,所以這靠你 workflow 的狀態名——**公司內部 Jira workflow 較豐富時
  很實用**(例:「Cancelled / Won't Do」)。其他 close(HIL 成功/失敗)維持一般 done。
- **(預留)CQ 回寫**:若票來源是 ClearQuest(`clearquest_id` 有值),未來會在 close 時把 Jira
  連結 + 結果回寫 CQ(`cq_writeback` 設定,含 base_url + 欄位對映)。**目前保留擴充點、尚未接
  實際 HTTP**(等 CQ 端 URL/欄位確定)。設計見 [design/lifecycle.md](design/lifecycle.md)。
- **控管 token / 花費(budget)**:6 個上限 = {per-ticket, 月/agent, 全站} × {token, usd}
  (完整見 [設計/Budget](design/budget.md)):
  - **profile `budget:`**:`ticket_soft_usd`/`ticket_hard_usd`/`ticket_soft_tokens`/
    `ticket_hard_tokens`(單票 soft/hard)+ `monthly_max_usd`/`monthly_max_tokens`(月/此
    agent hard)。soft 破 → 使用者可**自助增額**(≤hard);hard/月破 → **只你(管理者)能改**。
  - **全站 `outer_loop.budget:`**:`monthly_max_usd`/`monthly_max_tokens`(整個實例每月總量)。
  - **管理者調上限流程**:改 `config.yaml` 對應欄位 → `POST /reload`。hard **即時讀 profile**,
    reload 後該 profile 所有卡在上限的票**自動續跑**;事後可調回。全部欄位 None=不限;
    load 時驗 **soft ≤ hard**。
  - **看用量**:dashboard **Agent Detail 頁「budget 當月用量 vs 上限」卡**(全站 + 各 profile
    月 cost/tokens vs 上限,綠<80%/黃≥80%/紅≥100%)。達上限 = `pending:budget`。
- **控管並發**:`outer_loop.concurrency`(global + per-engine + per-profile);超額 QUEUED。
- **排程 / 單次 job(週期或一次執行 agent)**:`outer_loop.triggers[]` 每個 job:
  `run_name` + `profile`(agent-job)或 `script`(script-job)+ **`count`**(次數上限:
  1=單次、0=無上限需 cron、N=N 次;預設 1)+ **`cron`/`every`**(時機)。**agent-job 會開
  一張真 Jira 票**(`create_ticket` + 直接指定 profile,跳過 routing/HIL)—— 任務內容給
  `task`(靜態字串)或 `task_script`(跑腳本→stdout JSON,可多筆→每筆開一張,如「掃 CQ 新
  CR」);票帶 `labels` **須對到一條 create_or_resume route** 才會被派工。無人值守就讓該
  profile 設 `auto_close`。**script-job 不開 Jira**(inline 跑、結果進 dashboard;簡易排程
  也可直接用 crontab)。看 journal `job_fired`(profile/task_idx)。範例:
  ```yaml
  outer_loop:
    triggers:
      - run_name: daily-scan
        profile: scanner          # agent-job → 開真 Jira 票
        count: 0                  # 0=無上限(需 cron);1=單次;N=N 次
        cron: "0 3 * * *"
        labels: [agent]           # 須對到 create_or_resume route
        task: "巡檢昨日失敗票並回報"
        # task_script: 'uv run gen_tasks.py'   # 改動態:stdout JSON [{summary,description,labels?}]
  ```
- **自動關單 `auto_close`(profile 欄,無人值守用)**:`off`(預設,正常 HIL 人評分)/
  `on_success`(只 SUCCESS 自動關,FAILURE/UNKNOWN 仍發表單交人)/ `all`(全終態自動關)。
  自動關時**跳過評分表單**、`human_score` 直接取 **agent 自評**(contract.score)、轉 Done、
  journal `closed(by=auto)`;**outcome 保留**(FAILURE 仍算失敗、dashboard 失敗率照算)、
  交付物 comment 照貼(事後可查)。它與 `require_approval`(開跑前門檻)是人機光譜兩端 ——
  全自動 profile = 不審批 + `auto_close: all`;高風險 profile 維持 `off`。同一 profile 只能一種
  行為,要兩種就開兩個 profile。詳見 [design/agent-output.md §9](design/agent-output.md)。

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
  資訊;control API 有寫入端點(pause/shutdown/evict)。**要鎖本機**:dashboard 加
  `--host 127.0.0.1`、control 設 `config.yaml` 的 `source.control.host: 127.0.0.1`。
  這是信任邊界的取捨,見 [requirements §7](requirements.md)。
- 憑證只在 `~/.env`,永不進 git、dashboard 只顯示「有/無/到期」不顯示值。
- 互動表單的一次性 token 是機密,勿記入共用日誌。

## 9. 升級/改動後複驗

改了設定、資料夾結構或升級版本後,跑複驗助手確認沒壞:

```bash
uv run python scripts/reverify_v1.py --offline   # 免費本機:runner 路徑/config 載入/事件字典
uv run python scripts/reverify_v1.py             # 再加 Jira 唯讀連線(需 ~/.env,不派 agent)
```

它會印出**付費部分**(真派一次工才驗得到:runner spawn / select / install / hold / 自評 /
human-prompt / handoff)的逐項清單,你在有 agent/充電時對照 dashboard trace +
`runtime/events.jsonl` 打勾。

**付費部分請照** [V1 付費複驗 checklist](v1-reverify-checklist.md) **一步步跑**(每步:做什麼 →
預期 journal 事件 → 在哪看 → 打勾框;約 $0.1–0.3 haiku、需充電)。

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
