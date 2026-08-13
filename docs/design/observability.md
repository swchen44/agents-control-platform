# 可觀測性 — 證據地圖 + journal 事件字典

> **離線除錯的地基**。ARCP 交付到內網後是凍結 snapshot,無法連外、無法問原作者 ——
> 診斷任何問題只能靠 repo 內文件 + runtime 落地的證據。這份文件回答三個問題:
> **證據在哪、怎麼讀、每個事件代表什麼**。實際排錯流程見 [troubleshooting](../troubleshooting.md)。

## 1. 證據地圖 —— 東西在哪

一個 Control Plane 實例的所有落地證據都在它的 runtime 目錄下(預設
`runtime/`;dashboard 的 `<runtime>` 引數指的就是它):

| 路徑 | 是什麼 | 怎麼讀 |
|---|---|---|
| `events.jsonl` | **journal** —— append-only 事件流,**主要證據軌** | 每行一個 JSON:`{ts, type, issue_id, key, …fields}`。見 §2 |
| `harness.db` | SQLite **狀態快照**(當下真值) | 表 `ticket_watch`(poller 水位)、`ticket_session`(每票 session/outcome/attempts/cost)、`trigger_state`(排程) |
| `runs/<run-id>/transcript/` | 每次 attempt 的**執行證據** | `stdout.log` / `stderr.log`(runner 原始輸出)、`run.tgz`(打包)。run-id = `<profile>__<key>__<epoch>` |
| `tickets/<id>/` | agent 的**隔離 workspace**(產出的檔案) | 任務實際改動的檔;`.arcp_sandbox.sb` = 該次的 seatbelt 設定 |

> **journal vs db 的分工**:journal 是「發生過什麼」(歷史、可回放、算 KPI/月花費);
> db 是「現在是什麼」(當下狀態、poller 水位)。兩者都在,**除錯先看 journal 還原經過,
> 再對 db 確認當下**。db 是從 journal 事件推導出來的當前值,不衝突時以 journal 為敘事、
> db 為現況。

runtime 目錄是 **gitignored**(不進版控);要保存某次現場,整個 `runs/<run-id>/` +
相關 journal 區段留存即可。

## 2. 怎麼讀 journal

格式:每行一個獨立 JSON(壞一行不毀全檔),欄位固定前綴 `ts`(epoch 秒)、`type`
(事件名)、`issue_id`(Jira 數字 id)、`key`(Jira key,如 `SCRUM-36`),其餘是該事件
的欄位(見 §3 字典)。

離線常用查法(純 stdlib,不需裝東西):

```bash
cd runtime

# 一張票的完整時間線(照發生順序)
python3 -c "import json,sys; [print(f\"{__import__('datetime').datetime.fromtimestamp(e['ts']):%H:%M:%S} {e['type']:24} {({k:v for k,v in e.items() if k not in ('ts','type','issue_id','key')})}\") for e in map(json.loads, open('events.jsonl')) if e['key']=='SCRUM-36']"

# 只看某類事件(例:所有 pending / 錯誤)
grep -E '"type": "(pending|dispatch_error|external_abort|workspace_unhealthy)"' events.jsonl

# 統計事件分佈(哪種事件最多、有沒有異常尖峰)
python3 -c "import json,collections; print(collections.Counter(json.loads(l)['type'] for l in open('events.jsonl')).most_common())"
```

> `ts` 是 epoch(UTC 基準的秒),dashboard 會轉成瀏覽器在地時區顯示;離線用
> `datetime.fromtimestamp` 轉本機時區即可。⚠️ 筆電睡眠會凍結計時器 → 可能看到假的
> 長間隔/假 stall(見 [troubleshooting](../troubleshooting.md) 與 LESSONS)。

## 3. 事件字典

**「有哪些事件 + 欄位」由 `scripts/gen_event_dict.py` 掃 code 自動產生(防漂移);
「每個事件的語意」手寫在下方分組。** 更新自動表:`uv run python scripts/gen_event_dict.py`
覆蓋下方標記區塊;`--check` 可比對是否漂移(CI/pre-commit 用)。

<!-- BEGIN gen_event_dict -->
| 事件 | 欄位(kwargs) | 產生點 |
|---|---|---|
| `aborted` | `author`, `detail`, `reason`, `request_id`, `state`, `to_key` | `src/arcp/commands.py`, `src/arcp/dispatcher.py`, `src/arcp/hil.py` |
| `adopted` | — | `scripts/run_poller.py` |
| `approval` | `agent_name`, `decision`, `request_id`, `revisions` | `src/arcp/dispatcher.py`, `src/arcp/hil.py` |
| `assignee_alert` | `assignee` | `src/arcp/commands.py` |
| `assignee_changed` | `new`, `old` | `src/arcp/poller.py` |
| `assignee_restored` | — | `src/arcp/commands.py` |
| `attempt_crash_recovered` | `resume` | `src/arcp/dispatcher.py` |
| `attempt_finished` | `attempt`, `cost`, `envelope`, `error_kind`, `profile`, `raw`, `structured`, `tokens`, `truly_resumed` | `src/arcp/dispatcher.py` |
| `attempt_skipped` | `pending`, `reason` | `src/arcp/dispatcher.py` |
| `attempt_started` | `attempt`, `preassigned` | `src/arcp/dispatcher.py` |
| `base_injected` | `base`, `dest` | `src/arcp/dispatcher.py` |
| `closed` | `agent_score`, `by`, `human_score`, `outcome`, `request_id` | `src/arcp/hil.py`, `src/arcp/scoring.py` |
| `command_accepted` | `author`, `command`, `ip` | `src/arcp/commands.py` |
| `command_link_posted` | — | `src/arcp/hil.py` |
| `comment_added` | `author`, `body`, `comment_id` | `src/arcp/poller.py` |
| `deliverables_posted` | `has_output`, `mode`, `n_attachments`, `skipped` | `src/arcp/deliverables.py` |
| `dispatch_error` | `error` | `src/arcp/poller.py` |
| `evicted` | `count`, `session` | `src/arcp/dispatcher.py` |
| `external_abort` | `state` | `src/arcp/commands.py` |
| `external_cleared` | `cause` | `src/arcp/dispatcher.py` |
| `handoff` | `author`, `from_profile`, `ip`, `kind`, `new_ticket`, `to`, `via` | `src/arcp/commands.py`, `src/arcp/dispatcher.py`, `src/arcp/hil.py` |
| `handoff_invalid` | `kind`, `to`, `via` | `src/arcp/dispatcher.py`, `src/arcp/hil.py` |
| `hil_requested` | `request_id`, `schema` | `src/arcp/hil.py` |
| `hil_resumed` | `reason`, `request_id`, `schema` | `src/arcp/hil.py` |
| `hil_stalled` | `reminders`, `request_id` | `src/arcp/scoring.py` |
| `hil_submitted` | `request_id`, `schema` | `src/arcp/hil.py` |
| `jira_write` | `action`, `detail` | `scripts/run_poller.py` |
| `job_fired` | `crid`, `job`, `run_name`, `task_idx` | `src/arcp/triggers.py` |
| `job_skip_duplicate` | `crid`, `job` | `src/arcp/triggers.py` |
| `new_issue` | `state`, `summary` | `src/arcp/poller.py` |
| `owner_changed` | `author`, `ip`, `new`, `old`, `reissued`, `retagged` | `src/arcp/commands.py` |
| `pending` | `cause`, `cost_usd`, `reason`, `scope`, `tokens` | `src/arcp/dispatcher.py` |
| `profile_selected` | `chosen`, `method`, `original` | `src/arcp/dispatcher.py` |
| `provenance_attached` | `attached`, `skipped` | `src/arcp/provenance.py` |
| `queued` | `engine`, `profile` | `src/arcp/poller.py` |
| `resolved` | `attempts`, `cost_usd`, `human_minutes_saved` | `src/arcp/dispatcher.py` |
| `result_written` | `result` | `src/arcp/provenance.py` |
| `route_matched` | `on_match`, `profile`, `route` | `src/arcp/poller.py` |
| `score_reminded` | `reminders` | `src/arcp/scoring.py` |
| `score_requested` | `request_id` | `src/arcp/scoring.py` |
| `script_run_finished` | `duration_sec`, `outcome`, `rc`, `timeout`, `trigger` | `src/arcp/triggers.py` |
| `script_run_started` | `cwd`, `script`, `trigger` | `src/arcp/triggers.py` |
| `security_approved` | `request_id`, `revised` | `src/arcp/hil.py` |
| `security_blocked` | `n_findings`, `scanner_error` | `src/arcp/dispatcher.py` |
| `security_scan` | `error`, `n_findings`, `ok` | `src/arcp/dispatcher.py` |
| `session_created` | `profile`, `workspace` | `src/arcp/dispatcher.py` |
| `status_changed` | `new`, `old` | `src/arcp/poller.py` |
| `status_synced` | `state`, `to` | `src/arcp/dispatcher.py` |
| `ticket_md_attached` | `filename`, `hash` | `src/arcp/provenance.py` |
| `transcript_packed` | `files`, `reason` | `src/arcp/control_api.py`, `src/arcp/dispatcher.py` |
| `trigger_error` | `error` | `src/arcp/poller.py`, `src/arcp/triggers.py` |
| `watcher_added` | `approver` | `src/arcp/dispatcher.py` |
| `workspace_reclaimed` | `age_days`, `outcome`, `path` | `src/arcp/retention.py` |
| `workspace_unhealthy` | `reason` | `src/arcp/dispatcher.py` |

> 共 54 種事件。本表由 `scripts/gen_event_dict.py` 掃 code 產生,勿手改。
<!-- END gen_event_dict -->

### 語意分組(手寫)

**⚠️ = 除錯時的異常訊號**,看到就往該事件的「連看」證據追。

**A. Poller 偵測(poller.py)** —— 外圈每輪掃 Jira 的差異:
- `new_issue` / `comment_added` / `status_changed` / `assignee_changed`:偵測到新票/新
  留言/狀態或 assignee 變動。正常的輸入訊號。
- `route_matched`:票命中某 route → 決定 profile 與 `on_match`(ignore/notify_only/
  create_or_resume)。**票沒被處理時第一個查這個**:沒有 `route_matched` = 沒命中任何
  route(jql/routes 設定問題)。
- `adopted`:啟動「認養 pass」把當下已存在的票標為水位(只對之後的新事件反應,不重跑
  歷史)。啟動時大量出現屬正常。
- `command_link_posted`:票首次成 `create_or_resume` 候選時佈建「指令台」——建綁票常駐
  command token、把連結寫進 description 的 control 段 + 貼一則指路 comment(取代 @agent
  comment 通道;冪等,每票一次)。人由此連結下 run/retry/hold/stop/cancel/next。

**B. 派工 + 證據迴路(dispatcher.py)** —— 內圈跑 agent:
- `profile_selected`(`original`/`chosen`/`method`):Q16 首次派工選了不同 profile(A/B 測試 /
  泛化 triage)。看到它 = 這票沒用 route 原 profile,而是 select 選出的 `chosen`。
- `aborted`(`reason`/`detail`):中止(不跑 agent)。`reason=untriageable` = triage(select)
  判不出適用 profile(select 回 `notfound`)→ session `profile=notfound`、`outcome=ABORTED`、
  Jira 轉取消(`source.cancel_status`,沒有則 done)。看 `detail` 知 select 給的原因。
- `session_created`(建 workspace)→ `attempt_started` → `attempt_finished`
  (`raw`=completed/error/unknown、`cost`、`truly_resumed`、`error_kind`)。正常一輪。
- `attempt_crash_recovered`(`resume`):偵測到上次 attempt 崩潰,這次靠 native resume
  續接。偶發正常;**頻繁出現** ⚠️ = 有東西一直在崩,連看該 run 的 `stderr.log`。
- `approval`:起點審批門的決策(pass/退回)。
- `resolved`:grader 終審通過 → 成功關閉一輪(帶 `attempts`/`cost_usd`/
  `human_minutes_saved`)。
- `deliverables_posted`(`has_output`/`mode`/`n_attachments`/`skipped`):終態貼交付物
  (agent 的 OUTPUT.json → Jira comment + 附件)。`has_output=false`=agent 沒寫 OUTPUT.json
  (只有 structured 自報);`mode=attach`(<6MB 附 issue)/`link`(≥6MB 下載頁)/`none`;
  `skipped`>0 = 有附件不存在或越界 workspace 被跳過(查 agent 宣告的路徑)。見
  [agent-output.md](agent-output.md)。
- ⚠️ `pending`:進入非終態等待,`cause`/`scope`/`reason` 說明為何(預算超限、額度閘
  QUEUED、需人、外部變更…)。**這是「卡住/沒進展」的核心線索** —— 讀 `reason`。
  `reason=budget` 時 `scope` = `ticket-soft`(→發 budget_increase 表單自助增額)/
  `ticket-hard`/`monthly`/`global`(→只管理者改 config+reload);帶 `cost_usd`+`tokens`。
  見 [budget.md](budget.md)。
- ⚠️ `workspace_unhealthy`(`reason`):workspace 檢查不過。連看 `tickets/<id>/`。
- ⚠️ `evicted`(`count`/`session`):被強制驅逐(killpg 釋放資源)。人為(按鈕/`POST
  /evict`)屬正常;非預期出現要查誰觸發。

**C. agent↔agent 交接(dispatcher/commands/hil)**:
- `handoff`(`kind`/`to`/`from_profile`;`via=hil` 表人在 HIL 表單選的、`new_ticket`=跨票新票):
  換手。`kind=agent`/`next`=同票換 profile;`kind=base`=跨票(建 `new_ticket` 交新 profile);
  `kind=human`=交人。W10.3:HIL(End/Middle) 表單可選 `next`(同票)或 `base`(跨票)。
- `base_injected`(`base`/`dest`):跨票換手(base)子票首次佈建後,已把來源票 `base` 的脈絡
  (TICKET.md + 最後 envelope)注入 workspace 的 `dest`(BASE_<key>/);一次性,之後 resume 不重注。
- ⚠️ `handoff_invalid`(`kind`/`to`/`via`):換手目標無效(profile 不存在/kind 空)→ 換手沒
  生效;`via=hil` 時已 fail-safe 降級為續跑原 agent(不硬失敗),查 `to`/`kind`。

**D. 指令通道(commands.py / 指令台)** —— 人走「指令台」表單、自動化走 REST API
(`apply_command`;取代舊 `@agent` comment 通道):
- `command_accepted`(`command`/`author`):一個指令(run/retry/hold/stop/cancel)已執行,
  `author` = 提交者 email。**指令沒效果**時查它有沒有出現:沒有 = 表單擋在前面(狀態
  不適用 / 破壞性未確認 / 缺 email),或連結已失效(票 close)。`next` 換手記 `handoff`
  (`kind=command`)。佈建見 `command_link_posted`(§A)。
- ⚠️ `external_abort`(`state`):外部把票改成 `cancel_states`(如「完成」)→ 中止。
  `external_cleared`(`cause`):外部變更已消化。
- `assignee_alert` / `assignee_restored`:assignee 被改離 agent(告警留言)/ 改回
  (安靜恢復)。W11 起 assignee 恆定=Agent,被改動是異常訊號。

**E. HIL 人機互動(hil.py / scoring.py)** —— 一次性表單:
- `hil_requested`(`request_id`/`schema`)→(人填)→ `hil_submitted` → `hil_resumed`
  (`reason`,續跑)或 `closed`(`by`,關單)。正常的人機閉環。
- `closed`(`by`):`by=human`=人在表單關單;**`by=auto`**=profile 設 `auto_close` 自動關
  (跳過 HIL,`human_score`=`agent_score`,`outcome` 保留——FAILURE 仍算失敗)。看到
  `by=auto` 表這票**沒經人審**(profile 的自動化決策),見 agent-output.md / profiles。
- `score_requested`(HIL(End) 評分表單發出)→ `score_reminded`(`reminders`,催)→
  ⚠️ `hil_stalled`(`reminders` 達上限仍沒人回)。**票停在等人**時看這串:停在
  `hil_requested`/`score_requested` 沒有後續 = 在等人填表單(正常等待,非 bug)。

**F. 排程觸發 / jobs(triggers.py,J1 統一)** —— 兩種 job 都先 `script_run_*`:
- `script_run_started`(`cwd`/`script`)→ `script_run_finished`(`rc`/`outcome`/`timeout`/
  `duration_sec`):跑 `config/scripts/<subfolder>/…`(cwd 進 subfolder),log 存
  `runs/…/transcript/`(stdout/stderr.log + run.tgz;dashboard 可看可下載)。兩種 type 共用。
- `job_fired`(`job`/`run_name`/`task_idx`/`crid`):**agent-job** 解析 script stdout 的 JSON
  任務 → **像人一樣 create_ticket**(不建 session、不鎖定 profile)→ 票走正常 route/triage。
  多筆 → 同輪多個 `job_fired`(task_idx 遞增)。`crid` = 來源 ClearQuest CR id → 寫進票的
  description 最上面 yaml(`crid: …`)→ dispatcher 建 session 時讀回 `session.clearquest_id`
  (去重 + close→CQ 回寫;非 CR job 為 None)。**script-job 不開票**(只有 script_run_*)。
- ⚠️ `trigger_error`(`error`):script rc≠0、stdout 非 JSON(agent-job 該回任務清單)、
  或 create_ticket 失敗、script 路徑越界。連看該 run 的 `transcript/stderr.log`。

**G. 額度閘 + 觀測(poller/control_api/dispatcher/retention)**:
- `queued`(`engine`/`profile`):F1 分層額度滿 → 排隊(FIFO)。正常的節流;**大量堆積**
  = 併發設太低或 agent 跑太久。
- `jira_write`(`action`/`detail`):harness→Jira 的每次寫入(留言/assign/transition),
  供 dashboard 事件時間軸顯示。
- `transcript_packed`(`reason`):打包 transcript(換手/交人/evict/close/按鈕觸發)。
- `workspace_reclaimed`(`age_days`/`outcome`):保留策略回收老 workspace。

### 3.1 高風險事件詳解(除錯常追的)

分組概述已在 §3(**每個事件都在那裡有一句話語意**,grep 事件名即可查);這裡把最常導致
「卡住/失敗/沒預期行為/看不懂」的事件逐一展開,格式統一 **何時發 / 正常樣態 /
異常訊號 / 連看哪個證據**。

**`session_created`** — 首次為某票建 workspace + session(dispatcher)
- 何時:一張票**首次真的派工**(通過 route + gate + 審批門)。
- 正常:每票只出現一次;之後都是 resume(不再有 session_created)。`workspace` 欄=實體路徑。
- 異常:同一票**反覆** session_created = workspace 一直被判不健康重建(見 `workspace_unhealthy`)
  或 session 被誤清。連看 `workspace` 路徑 + `tickets/<id>/`。
- 注意:換手後 workspace 設哨值「(handoff)」→ 下輪重建屬正常(見 §C 換手)。

**`profile_selected`** — Q16 首次派工選了不同 profile(dispatcher)
- 何時:main profile 設了 `select`(A/B 測試 / 自動選 profile),首次派工選出 `chosen`。
- 正常:每票至多一次;`chosen` 就是這票**實際跑**的 profile(已 寫入 session,resume 不重選)。
  `method=random` 剛好選回 main 時**不發**此事件(屬正常,不是漏)。
- 異常:以為設了 script triage 卻選到非預期 profile → 對照 `original`/`chosen`/`method`,
  再看 script 的 stderr(以 `[select:<key>]` 記錄)。fail-safe 會回 main(見 selection.md)。
- 連看:`chosen` + 該 profile 的後續 attempt;設計/除錯見 [selection.md](selection.md)。

**`queued`** — F1 分層額度滿 → 排隊(poller)
- 何時:同時在跑的 agent 達 global / per-engine / per-profile 上限,新候選排 FIFO。
- 正常:短暫排隊、隨額度釋出即派工(下輪不再 queued 而是 session_created/attempt_started)。
- 異常:**大量堆積、長時間卡在 queued** = 併發設太低,或 agent 跑太久沒釋放額度。
  連看 `engine`/`profile` + dashboard Server 頁「排隊深度」燈 + 各 profile 平均時長。

**`pending`** — 進入非終態等待(dispatcher)
- 何時:一輪結束但不能收尾,要等某條件。
- 正常:`reason` 明確(等人、額度、預算)。
- 異常:`reason` 指向 infra 錯誤、或同一票反覆 pending 不前進。
- 連看:`reason`/`cause`/`scope` 欄位 → 對應 §3 的 B/E 或 troubleshooting §2/§8。

**`attempt_finished`** — 一次 attempt 收尾(dispatcher/triggers)
- 何時:runner 程序結束。
- 正常:`raw=completed` 且後續有 `resolved`(grader 過);`truly_resumed` 反映有無重工。
- 異常:`raw=error`(看 `error_kind`:infra/stalled/task/no-terminal)、或 `completed`
  但遲遲沒 `resolved`(grader 沒過)、或 `cost` 異常高(model 設錯,見 troubleshooting §8)。
- 連看:同 run 的 `runs/<run-id>/transcript/stdout.log`+`stderr.log`;`envelope` 欄位。

**`attempt_crash_recovered`** — 偵測上次崩潰、這次續接(dispatcher)
- 何時:上一輪 attempt 沒正常收尾,本輪 native resume。
- 正常:偶發;隨後 `attempt_finished(truly_resumed=true)` = 沒重工。
- 異常:**連續多輪**出現 = 有東西一直崩。
- 連看:前一個 run 的 `stderr.log`(崩在哪)。

**`workspace_unhealthy`** — workspace 檢查不過(dispatcher)
- 異常訊號本身。連看 `reason` + `tickets/<id>/`(workspace 實體)。

**`evicted`** — killpg 強制驅逐(dispatcher)
- 正常:人為(dashboard 按鈕 / `POST /evict/<id>`)釋放卡住的 agent,不耗 attempt。
- 異常:沒人按卻出現 → 查誰呼叫 control API。
- 連看:`count`(殺了幾個程序)、隨後應有 `attempt_crash_recovered` 續跑。

**`external_abort`** — 外部把票改成 cancel_states(commands)
- 正常:人主動在 Jira 取消/關掉。
- 異常:非預期的狀態變更把跑到一半的票中止。
- 連看:`state` 欄位 + 該票的 `status_changed`/`assignee_changed` + Jira 端操作記錄。

**`handoff` / `base_injected`** — agent↔agent 交接(dispatcher/hil,W10.3)
- 何時:人在 HIL 表單選「改派下一棒」或 agent 自發換手。`kind=next`(同票)/ `base`
  (跨票)/ `agent`(自發同票)/ `human`(交人);`via=hil` 表人在表單觸發。
- 正常(跨票換手 base):`handoff(kind=base, new_ticket=SCRUM-N, via=hil)` → 下一輪新票出現
  `base_injected(base=舊票, dest=BASE_<key>)`(來源脈絡已注入)→ 新票照常 `session_created`
  …;舊票同時 `outcome=ABORTED`(非 failure)。
- 異常:有 `handoff(kind=base)` 但新票遲遲沒 `base_injected` → 新票沒被 poller 撿到
  (labels/route 不符?)或來源 session 找不到(看 hil 警告 log);查新票是否在 jql 視野內。
- 連看:兩票的 `key` 各篩一條時間線對照;新票 workspace 的 `BASE_<key>/`(TICKET.md+envelope)。

**`handoff_invalid`** — 換手目標無效(dispatcher/hil)
- 異常訊號:`to` 指的 profile 不存在、或 `kind` 空 → 換手沒生效。`via=hil` 時已 fail-safe
  降級為續跑原 agent(不硬失敗)。連看 `to`/`kind` + `config.yaml` profiles(名字對不對)。

**`hil_stalled`** — 評分/需人表單催了 N 次仍無人回(scoring)
- 正常:確實在等人(非 bug)。
- 異常:如果你以為已經填了卻還 stalled → 表單提交沒成功(Jira 降級時「不落地」?)。
- 連看:`reminders` 次數;Jira 該票的 @mention 留言 + 表單連結;troubleshooting §2/§6。

**`command_accepted` 沒出現** — 指令台指令沒生效
- 何時:人在指令台按了指令,但票狀態沒變。
- 連看:表單頁的錯誤訊息(狀態不適用 / cancel·stop 未勾確認 / 缺 email);連結是否已
  失效(票 close → `command_link_posted` 後看有無 `closed`);`command_accepted` 有沒有
  進 journal(`author`=提交 email)。對應 troubleshooting §7。

**`dispatch_error` / `trigger_error`** — 派工/觸發時擲例外(poller)
- 異常訊號本身。連看 `error` 字串 + poller 主控台輸出 + troubleshooting §4。

## 4. 串一張票 end-to-end(典型序列)

用事件序列快速判讀一張票走到哪、哪裡不對(照 `key` 篩出時間線後對照):

- **正常成功**:`new_issue` → `route_matched` → `session_created` → `attempt_started`
  → `attempt_finished(raw=completed)` → `resolved` →(HIL(End) 評分)`score_requested`
  → `hil_submitted` → `closed`。
- **失敗**:… → `attempt_finished(raw=error, error_kind=…)` →(retry 數輪)→ `pending
  (reason=…)` 或 `resolved(FAILURE)`。看 `error_kind`(infra/stalled/task/no-terminal)
  分辨基礎設施 vs 任務問題。
- **卡住等人**:… → `hil_requested`/`score_requested` → `score_reminded`×N →
  `hil_stalled`。停在這串沒後續 = 等人填表單(去看 Jira 那張票的 @mention + 表單連結)。
- **crash 後續跑**:… → `attempt_started` →(崩)→ 下輪 `attempt_crash_recovered
  (resume=true)` → `attempt_finished`。`truly_resumed=true` 代表沒重工。
- **跨票換手(base)**(W10.3):舊票 … → `score_requested` →(人填 handoff base)→
  `handoff(kind=base, new_ticket=SCRUM-N, via=hil)` + 舊票 `outcome=ABORTED`;新票下一輪
  → `base_injected(base=舊票)` → `session_created` → `attempt_started` …(帶 BASE_ 脈絡)。
  同票換手(next)則舊票不 ABORTED,而是同 `key` 直接 `handoff(kind=next)` → 下輪換 profile 重跑。
- **被驅逐後回收**:`evicted` →(下輪)`attempt_crash_recovered` 續跑。evict 不耗
  attempt、不重花錢。

### 真實範例(取自 runtime_live 的 journal)

一張 filechain 任務**成功**的真實時間線(欄位已精簡):

```
+ 0.0s  new_issue         {state: ToDo}
+ 0.0s  route_matched     {route: filechain-rawcli, profile: filechain-rawcli, on_match: create_or_resume}
+ 0.0s  session_created   {profile: filechain-rawcli}
+19.6s  attempt_finished  {attempt: 1, raw: completed, truly_resumed: false}
+20.3s  resolved          {attempts: 1, cost_usd: 0.0354, human_minutes_saved: 15.0}
+20.8s  transcript_packed {files: [final.html, transcript.tgz]}
+44.1s  comment_added     {comment_id: 10043, author: Shao-wei Chen}
```

判讀:命中 route → 建 session → 單次 attempt `completed` → grader 過 `resolved`
(花 $0.035、估省 15 分人力)→ 打包 transcript → 回寫 Jira comment。**沒有 `pending`、
`attempt_crash_recovered`、`hil_stalled`** = 一路順。

> ⚠️ **舊 journal 可能含「退役事件」**:事件字典(§3)反映**當前 code**;歷史 journal
> 可能有現在不再產生的事件。例如 W11 前的「交人 inactive」模型會寫 `inactive_set`,
> W11 改 HIL 後已由 `hil_requested`/`hil_resumed` 取代 —— 在舊 runtime 看到字典裡沒有
> 的事件名,先查它是不是某版本已退役的,而非資料損壞。

## 5. dashboard 定位(有畫面時)

`scripts/detail_server.py` 起唯讀 dashboard:總覽 KPI(進行中/排隊/HIL/成功/失敗率)、
每張票詳情頁的**事件時間軸**(就是 journal 的視覺化,含 HH:MM 的 jira_write/handoff/
評分)、狀態機圖、transcript 檢視。離線內網完全可用(所有元件 vendored、零外部依賴)。
沒有畫面時,§2 的 journal 查法 + §4 的序列對照就足以定位。

## 6. trace 完整性自檢(C2)

`scripts/trace_lint.py` 掃 runtime,確認每個「有跑過 attempt」的票四層證據齊全:
completed/error 的 attempt **必須**有 L2(`attempts/aN.envelope.json`,合法 JSON、帶
completed|error)+ L3(`attempts/aN.events.jsonl`,非空);UNKNOWN(runner 死/無 envelope)
**依設計可缺**,不算失敗。缺任一該有的層 → 列出 + rc!=0(供審計)。

```bash
uv run python scripts/trace_lint.py [runtime_dir]   # 預設 runtime/;無資料視為通過
```

邏輯由 `tests/test_trace_lint.py` 在 CI 驗證(合成 runtime 六情境)。這是 v5 唯一的 P1
硬 KPI —— 每個結束的 attempt 都要留得下可稽核的四層證據。

## 7. 維護:防止字典漂移

事件字典的自動表對應 code；改了 `store.journal(...)` 的事件名/欄位後:

```bash
uv run python scripts/gen_event_dict.py            # 看新表
uv run python scripts/gen_event_dict.py --check    # 比對本文內嵌區塊是否一致(不一致回非 0)
```

把新輸出貼回 §3 的 `BEGIN/END gen_event_dict` 標記區塊即可。語意分組(§3 手寫段)請
一併補上新事件的一行說明。
