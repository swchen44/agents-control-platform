# Config 參數參考(全鍵:作用 + 預設值)

> 給管理者的**設定總表**。範例起步:`cp config/config.example.yaml config/config.yaml`;
> 改完 `POST :8787/reload` 熱生效(壞設定回 400、舊設定續用)。
> 檔案選擇:CLI `--config <檔名>`(純檔名=`config/` 下;預設 `config.yaml`,
> 亦可 env `ARCP_CONFIG`);`--runtime <dir>` 覆寫狀態目錄。
> 機制細節連結到各設計正本;profiles 可拆檔放 `config/profiles/<名>.yaml`(檔名=profile 名)。

## 1. `outer_loop.source.*`(Jira 來源與全域行為)

| 鍵 | 作用 | 預設 |
|---|---|---|
| `name` | 實例名(dashboard 標題/多實例分辨;env `ARCP_NAME` 可覆寫) | —(建議必填) |
| `project` | Jira project key(agent-job 開票、跨票交接開新票用) | `""` |
| `jql` | poller 監看哪些票(**務必**帶 project 與狀態條件) | —(必填) |
| `poll_interval_sec` | 輪詢間隔秒(CLI `-i` 可覆寫) | `30` |
| `jira_base_url` | Jira URL(非機密;不設則用 `~/.env` 的 `JIRA_BASE_URL`) | 未設 |
| `jira_flavor` | `cloud` \| `dc`(Data Center:API v2/PAT/純文字,見 [jira-dc](design/jira-dc.md)) | `cloud` |
| `issue_type_id` | 開票用 issue type **id**(名稱是 locale 資料勿用;KP2 Task=10012) | `10003` |
| `runtime_dir` | 狀態目錄(DB/journal/workspaces;整測隔離用 `runtime-test`) | `runtime` |
| `dashboard_url` | 結案回寫 result 段附 ticket 連結(填人瀏覽器連得到的 URL) | 未設(不附) |
| `write_retry` | Jira 寫入重試 `{max, base_sec}`(指數退避) | `{max:5, base_sec:1}` |
| `cancel_status` | triage 判不出/中止時想轉的取消狀態名(精確名) | 未設(退回 done) |
| `bot_account_id` | 機器人 accountId(assignee 恆定/審批放行收回用) | 未設 |
| `admin_emails` | 全站管理者名單(表單/指令台門禁豁免,[identity-gate](design/identity-gate.md)) | `[]` |
| `user_map` | email→accountId/username 手動映射(DC user search 缺時) | `{}` |
| `username_rule` | 查無使用者時的推導模板(如 `{local}`) | `""` |
| `external_change.cancel_states` | 人在看板直接關票視為取消的狀態名清單 | `[完成, Done, Concluído]` |
| `comments_lookback` | 每輪撈留言數上限 | 依實作 |

### `source.status_sync`(內部態→Jira 狀態同步,[主題 N];選配,不設=不同步)

| 鍵 | 內部態 | 例(KP2) |
|---|---|---|
| `running` | 執行中 | In Progress |
| `hil_middle` | 過程中等人 | Pending |
| `hil_end` | 終態等評分 | Resolve |
| `closed` | 人授權關單(精確名+兩步保險) | Closed |
| `aborted` | 中止 | Cancelled |

### `source.security_scan`(TICKET.md 安全掃描,[security-scan](design/security-scan.md);選配,不設=關)

| 鍵 | 作用 | 預設 |
|---|---|---|
| `command` | 掃描器命令(如 `skill-scanner`;**設了才開**,掃描器故障=fail-closed 交人審) | 未設(關) |
| `fail_on` | 擋下門檻嚴重度 | 依實作 |

## 2. `outer_loop` 其他段

| 鍵 | 作用 | 預設 |
|---|---|---|
| `concurrency.max_running` | 全站同時跑的 agent 上限(F1 閘門,超過排隊) | `1` |
| `concurrency.per_engine` | 各引擎上限 `{claude: N, codex: M}` | `{}` |
| `concurrency.per_profile` | 各 profile 上限 | `{}` |
| `budget.monthly_max_usd` | **全站**月 USD 上限(破→全站卡,只管理者能改,[budget](design/budget.md)) | 未設(不限) |
| `budget.monthly_max_tokens` | 全站月 token 上限 | 未設(不限) |
| `control.host` / `.port` | control API(pause/reload/evict/指令 REST) | `127.0.0.1:8787` |
| `form.host` / `.port` | 一次性表單服務 | `127.0.0.1:8790` |
| `form.base_url` | 表單連結 base(要「人瀏覽器連得到」;內網手機可及要填主機 IP) | `http://<host>:<port>` |
| `form.mention_account_id` | 通知 @mention 的人(accountId) | `""`(不 mention) |

## 3. `outer_loop.routes[]`(票→profile 比對;**先到先贏**)

| 鍵 | 作用 | 預設 |
|---|---|---|
| `name` | route 名(journal `route_matched` 顯示) | 必填 |
| `when.*` | 比對條件(**AND**;清單=任一命中):`labels` / `summary` / `description` / `comments` / `assignee` / `state`(文字類=regex) | 必填 |
| `profile` | 命中後用哪個 profile | ignore 時可略 |
| `on_match` | `ignore`(排除)/ `notify_only`(灰度只記錄)/ `create_or_resume`(真派工) | 必填 |

> label 慣例:入場券一律 `arcp.` 前綴;profile 名不在此列。

## 4. `outer_loop.triggers[]`(排程/單次 job,[主題 J];選配)

| 鍵 | 作用 | 預設 |
|---|---|---|
| `name` | job 名(水位鍵;重測歸零:刪 `trigger_state` 該列) | 必填 |
| `trigger_type` | `task`(產 prompt 開票)/ `task_script`(script 自定 labels/crid) | 必填 |
| `script` | 執行命令(相對 `config/scripts/`,**必放 subfolder**,cwd 進 subfolder) | 必填 |
| `cron` / `every` | 週期(cron 格式 / 秒數) | 二擇一 |
| `count` | 總次數上限(如一次性=1) | 不限 |
| `run_name` / `summary` / `labels` / `crid` / `timeout_sec` | 開票內容與逾時 | — |

## 5. `inner_loop.profiles.<名>`(agent 設定;可拆檔 `config/profiles/`)

### 5.1 任務與生命週期

| 鍵 | 作用 | 預設 |
|---|---|---|
| `goal` | 總目標(渲染進 TICKET.md「目標」段;可用 `{crid}` 等[插值](design/provenance.md)) | 未設 |
| `verify[]` | 確定性驗收(grader 依據+渲染進 TICKET.md):每步 `files`(檔名→null=存在即可/字串=完全比對)/ `cmd`(argv,rc=0 過;**不插值**)/ `json`(`{file, require:[鍵], types:{}}`) | 必填(空=以描述交付) |
| `loop.max_attempts` | 驗證失敗重試上限(帶失敗證據 native resume) | 必填 |
| `loop.on_unknown` | UNKNOWN 處置——**只能 `pending`**(v5 D3 不自動重試) | `pending` |
| `require_approval` | 起點審批門(表單提交即放行,[lifecycle §4](design/lifecycle.md)) | `false` |
| `approver` | 審批者 email/accountId(開票加 watcher;門禁豁免) | 未設 |
| `auto_close` | `off`(人評關單)/ `on_success` / `all`(自動關,human_score=agent 自評) | `off` |
| `max_revisions` | 表單退回重填上限 | `3` |
| `retention_days` | 終態後 workspace 保留天數(`0`=不回收) | `270` |
| `human_minutes_est` | 人做同任務估時(分;KPI 人力節省用) | 未設 |

### 5.2 `budget`(per-ticket/月;[budget](design/budget.md))

| 鍵 | 作用 | 預設 |
|---|---|---|
| `ticket_soft_usd` / `ticket_soft_tokens` | 單票**軟**上限:破→暫停+發自助增額表單(≤hard) | 未設(不限) |
| `ticket_hard_usd` / `ticket_hard_tokens` | 單票**硬**上限:只管理者改 profile | 未設(不限) |
| `monthly_max_usd` / `monthly_max_tokens` | 月/此 agent 上限(只管理者) | 未設(不限) |

> token 與 usd 都檢查、誰先破誰卡;量不到的 metric 讀 0 不誤卡(codex 常只有 token)。

### 5.3 `workspace`(佈建;[workspace](design/workspace.md))

| 鍵 | 作用 | 預設 |
|---|---|---|
| `template` | `empty` \| `<name>_template`(`config/templates/` 整包 copytree) | 必填 |
| `folder` | 工作區路徑模板(可用 `{issue_id}` `{key}` `{agent}`) | 必填 |
| `install` | 佈建命令(設了就不 copytree;ARCP 附 `<ws> <template>` 兩參數) | 未設 |
| `common_skills` / `common_hooks` | 從 `config/skills|hooks/` 選子集複製進 ws | `[]` |
| `inject_md` | 是否貼 `inject_claude_md_end.md` 到 CLAUDE.md/AGENTS.md 尾 | `true` |

### 5.4 `agent`(執行單元)

| 鍵 | 作用 | 預設 |
|---|---|---|
| `backend` | `rawcli`(主線,純 stdlib)/ `openhands-acp` / `openhands-server` | `openhands-acp`(建議明填 rawcli) |
| `engine` | `claude` \| `codex`(rawcli;決定事件協議與 resume 方式) | `claude` |
| `command` | **執行檔覆寫**:程式名或絕對路徑(如 `/tools/bin/claudeoss`)——內網把 CLI 包裝成別名時用;只換 argv[0],協議仍依 engine | 未設(用 `claude`/`codex`) |
| `model` | 模型。**未設=不帶 `--model`(CLI 帳號預設,可能較貴)**;測試一律顯式設便宜 model(差 ~8×) | 未設(不帶參數) |
| `os_sandbox` | claude 的 macOS seatbelt 隔離(寫檔限 workspace) | `false` |
| `sandbox` | codex 內建 `--sandbox`(read-only/workspace-write) | `workspace-write` |
| `timeout_sec` | 單 attempt 硬逾時 | `300` |
| `stall_seconds` | 卡死偵測:此秒數內事件流零輸出→killpg→下輪 resume(不耗 attempt)。**自訂必須 > 最長單一前景命令**(長 build 期間事件流完全靜默,實測);`0`=停用 | `3600` |
| `extra_args` | 彈性附加參數(list),**原樣接在 command line 最後面** | `[]` |
| `output_schema` | G1 結構化自評契約(agent 回 status/score/next;auto_close 自評來源) | `false` |
| `venv` | backend 的 venv 路徑(rawcli 免;openhands 系必填) | 未設(系統 python) |
| `acp_server` / `acp_model` | openhands-acp 的 adapter 與模型 | `claude-code` / 未設 |
| `server_managed` / `server_port` / `server_api_key` | openhands-server 常駐模式 | `false` / `18010` / local |

### 5.5 `select`(A/B 測試 / 條件 triage,Q16;選配)

| 鍵 | 作用 | 預設 |
|---|---|---|
| `candidates` | 候選 profile 名(random 須同族前綴;script 可回任何已定義名、可遞歸) | 必填 |
| `method` | `random`(隨機分流,**統計可比的真 A/B**)/ `script` | 必填 |
| `script` | method=script:相對 `config/scripts/`、必放 subfolder;JSON stdin→stdout 回 profile 名;回 `notfound`=中止不派工 | — |

> 首次派工選一次、**鎖定進 session**(resume 不重選)。

## 6. 快速對照:哪個行為在哪個鍵

| 想做什麼 | 鍵 |
|---|---|
| 換監看的 Jira 專案 | `source.jql` + `source.project` |
| 新增一個 agent | `config/profiles/<名>.yaml` + `routes` 加一條 → `/reload` |
| 開審批門 | profile `require_approval: true` + `approver` |
| 無人值守自動關單 | profile `auto_close: on_success` |
| 控花費 | profile `budget.*`(票/月)+ `outer_loop.budget`(全站) |
| 指定負責人門禁 | 票 description 頂部 `email:`(+全站 `admin_emails`) |
| Jira 看板同步 | `source.status_sync` 五鍵 |
| 任務簡報安全掃描 | `source.security_scan.command` |
| 整測與正式隔離 | `--config config.test.yaml`(runtime_dir/port 全分離) |

**description 頂部變數**(票層,非 config):`crid:` / `email:` / `prompt:` 三鍵
(yaml 風格到空行止;`{crid}` 等可在 goal/CLAUDE.md 插值)——見
[provenance](design/provenance.md) 與 [lifecycle §4.2](design/lifecycle.md)。
