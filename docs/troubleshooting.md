# Troubleshooting — 除錯 runbook

> **症狀導向**。從你看到的現象往下找:每條給「先看哪個證據 → 常見根因 → 處置」。
> 讀證據的方法(journal 在哪、怎麼篩一張票、事件語意)見
> [可觀測性](design/observability.md);歷史踩坑全紀錄見 [LESSONS](lessons.md)。
>
> **離線內網心法**:所有答案都在 runtime 證據 + 這份文件裡。先用
> [observability §2](design/observability.md) 把出問題那張票的 journal 時間線拉出來,
> 對照 [§4 典型序列](design/observability.md) 看它停在哪一步 —— 少了哪個事件,就是那一步出事。

## 快速分診

| 你看到 | 跳到 |
|---|---|
| 開了票/貼了 label,agent 完全沒反應 | [§1 票沒被處理](#1-票沒被處理) |
| agent 開始了但「卡住」不動 | [§2 卡住/沒進展](#2-卡住沒進展) |
| agent 說完成了但其實沒做對 | [§3 假完成](#3-假完成) |
| 真派工就爆(找不到 runner / spawn 失敗) | [§4 runner/派工失敗](#4-runner派工失敗) |
| crash 後重跑,或懷疑重工/重複副作用 | [§5 resume 與冪等](#5-resume-與冪等) |
| Jira 連不上/寫入失敗/整個降級暫停 | [§6 Jira 連線與降級](#6-jira-連線與降級) |
| 指令台下的指令沒效果 | [§7 指令沒效果](#7-指令沒效果指令台) |
| 花費爆掉/一直被預算擋 | [§8 花費與預算](#8-花費與預算) |
| dashboard 打不開或數字怪 | [§9 dashboard](#9-dashboard) |

---

## 1. 票沒被處理

**先看**:該票的 journal 有沒有 `route_matched`(見 [observability §3-A](design/observability.md))。

- **沒有 `new_issue`**:poller 根本沒看到這票。根因:`config.yaml` 的
  `source.jql` 沒涵蓋它(project/狀態/label 條件),或 poller 沒在跑。處置:用
  `scripts/smoke_jira.py`(唯讀)確認 jql 撈得到該票;確認 `run_poller.py` 有在跑。
- **有 `new_issue` 沒 `route_matched`**:沒命中任何 route。處置:對照
  `config.yaml` 的 `routes`,檢查 label/keyword/assignee 條件;`no-agent` 之類的
  排除 route 是否先命中。
- **`route_matched` 的 `on_match` 是 `ignore`/`notify_only`**:設計上就不派工
  (灰度只記錄)。要真的派工需 `create_or_resume`。
- **票是 poller 啟動前就存在的**:啟動會 `adopted`(認養水位),**只對啟動之後的
  新票/新留言反應,不重跑歷史**。要處理舊票:改動它(留言/改 label)產生新事件,
  或重開。

## 2. 卡住/沒進展

**先看**:journal 時間線停在哪個事件(這決定「卡在哪一層」)。

- **停在 `attempt_started` 沒有 `attempt_finished`**:agent 正在跑或真卡住。
  - 看該 run 的 `runs/<run-id>/transcript/stdout.log` 有沒有持續輸出。**有輸出=慢不是
    卡**(stall watchdog 定義:任何 stream 行都算進展;只有「工具執行中完全零輸出」才算
    stalled,見 [LESSONS #16](lessons.md))。
  - 真卡住 → 「強制驅逐」(dashboard 按鈕或 `POST /evict/<id>`):killpg 釋放資源、
    **不耗 attempt**,下輪自動 native resume 續跑(不重花錢)。這也是「不知原因卡住,
    evict/resume 就救回」的通用手段。
- **看到 `pending`**:讀它的 `reason`/`cause`/`scope` —— 這是「為什麼沒進展」的直接
  答案(預算超限、額度閘、需人、外部變更…)。對應處置見 §8(預算)/ §2下(排隊)/
  §6(外部)。
- **停在 `hil_requested` / `score_requested`(可能跟著 `score_reminded`)**:**在等人填
  表單,不是 bug**。去 Jira 那張票看 agent 的 `@mention` 留言 + 一次性表單連結,填了就
  會 `hil_submitted` → `hil_resumed`/`closed`。`hil_stalled` = 催了 N 次還沒人回。
- **大量 `queued`**:F1 額度閘滿在排隊(正常節流)。堆太多 = `concurrency` 設太低或
  agent 跑太久;調高上限或看為什麼單輪這麼久。
- **時間線有超長間隔但無異常事件**:可能是**筆電睡眠凍結計時器**造成的假 stall/假
  hang(見 [LESSONS 索引](lessons.md) + memory)。查 `pmset -g log` 對照時段;⚠️**不要**
  用 caffeinate(耗電),睡醒能續跑。

## 3. 假完成

**症狀**:agent 自稱/exit code=0,但任務其實沒做對。

- **這是設計上被防住的**:ARCP 用**證據型停止**——`attempt_finished(raw=completed)`
  只代表 runner 結束,**不代表任務完成**;要 profile 的 `verify`(grader)過才會
  `resolved(SUCCESS)`。看到 completed 卻沒 resolved,通常是 grader 沒過(正確行為)。
- **codex 收 SIGTERM 會優雅退場 rc=0**:「exit code=0」絕不能當完成證據(見
  [LESSONS 索引](lessons.md) A 路陷阱)。判讀一律以 grader + 檔案系統真值為準。
- **grader 過了但你覺得沒完成**:HIL(End) 三訊號並列(grader / agent 自評 / 你的
  評分)。用 `score_and_close` 表單選「續跑」(解終態+重置額度回進行中)或「關單」。
- **grader 一直不過但你覺得做對了**:可能是驗證與 feedback 資訊量不匹配(缺檔只報
  missing、不報 expected content → agent 無從補內容,見 [LESSONS #10](lessons.md))。
  讓任務描述含預期內容,或用內容敏感的 verify。

## 4. runner/派工失敗

**症狀**:真實派工時爆掉(離線測試都綠、一跑真的就出事)。

- **`unknown agent backend` / 找不到 runner 腳本**:runner 定位問題。runner 腳本在
  `scripts/inner_*_runner.py`,由 `src/arcp/inner_runner.py` 經 `arcp.paths.find_script`
  定位。快速驗:
  ```bash
  uv run python -c "from arcp.inner_runner import RUNNERS; import os; print({k: os.path.exists(v) for k,v in RUNNERS.items()})"
  ```
  全 `True` 才正常。**這正是 W12.1 曾踩的 bug**(套件搬進 `src/arcp/` 後路徑解析指錯),
  W12.4 已用 `arcp.paths` 修掉 —— 若你改動了資料夾結構,先跑這行確認(見 BACKLOG V1)。
- **openhands backend 報缺 venv**:`config.yaml` 的 openhands profile 依賴本機
  `.venv`(gitignored);fresh checkout/內網沒有。用 `config.example.yaml`(rawcli-only,
  純 stdlib 免 venv)或自建 venv。**主線 rawcli 不需要 venv**。
- **`dispatch_error` / `trigger_error`**:讀 `error` 欄位 + poller 主控台輸出。
- **kill 沒殺乾淨、子程序孤兒續跑**:必須殺 **process group**(killpg);只殺 CLI pid,
  codex 的 shell 子程序會孤兒把任務偷做完(見 [LESSONS 索引](lessons.md))。evict 走的
  就是 killpg。

## 5. resume 與冪等

- **crash 後有沒有續跑**:看下一輪的 `attempt_crash_recovered(resume=true)` +
  `attempt_finished(truly_resumed=true)` = 有續接且沒重工。
- **重工/重複副作用(重複留言、重跑同票)**:**冪等靠 store**(`harness.db`)——
  **絕不 wipe store**(見 [LESSONS #9](lessons.md))。store 沒了,open 票在 poller 眼中
  就是新工作 → 重派重跑重花錢。若懷疑,對 `ticket_session` 表確認該票的 attempts/outcome。
- **native resume 綁啟動 cwd**:workspace 搬家後原生 resume 會失敗,靠 transcript 降級
  救回(見 research v3 §9.3 / [LESSONS 索引](lessons.md))。

## 6. Jira 連線與降級

- **`CERTIFICATE_VERIFY_FAILED`**:python.org 版 macOS Python 不帶系統 CA。harness 的
  `jira_source` 已優先用 certifi;若你自己寫 urllib 腳本會中招 —— 用 curl(系統憑證)
  或走 jira_source(見 [LESSONS #1](lessons.md))。
- **HTTP 400 看不懂 Jira 在抱怨什麼**:錯誤 body 要浮出來。jira_source 的 `_request`
  已把 error body 前 400 字接進訊息(見 [LESSONS #5](lessons.md))。
- **建票 400 / issue type**:issue type 名稱是 locale 資料(中文站叫「任務」),不可
  hardcode 英文;用 id(見 [LESSONS #4](lessons.md))。
- **project 查得到卻是空的**:新 `/search/jql` 對不存在 project 回空集合不報錯(假陰性,
  [LESSONS #2](lessons.md))。用 `/project/search` 確認 project 存在;**project key 以 API
  列舉為準**(顯示名可改、key 固定,[LESSONS #3](lessons.md))。
- **整個實例降級/停派**:Jira 寫入或健康探針連續失敗 → poller **降級暫停**(停寫停派)。
  正常會在 poll 成功時自動解除;卡住可 `POST /recover`。這是 circuit-breaker,**不做
  work queue**(避免不同步)。人開表單時遇 Jira 異常 → 「暫勿送出、不落地」。

## 7. 指令沒效果(指令台)

人的指令改走「**指令台**」表單(description control 段的連結,取代舊 `@agent` 留言)。
在指令台按了指令但沒生效 →

- **表單頁直接告訴你原因**:狀態不適用(該指令此刻不可用)/ cancel·stop **未勾確認** /
  **缺 email**。照訊息修正即可。
- **連結顯示「已結案,指令台已停用」**:票已 close → token 失效(正常)。
- **journal 沒有 `command_accepted`**:指令沒被執行(被上面某條擋下),`author` 欄=提交 email。
- **找不到指令台連結**:看該票有沒有 `command_link_posted` 事件(票須先成 `create_or_resume`
  候選才會佈建);沒有 = 沒命中會派工的 route(見 §1)。
- **自動化下指令**:`POST /ticket/<id>/command {cmd,args,by}`,回 `{ok,message}`;`ok=false`
  的 `message` 就是原因。

## 8. 花費與預算(token / usd)

- **`pending(reason=budget, scope=…)`**:達某層上限(`scope` = `ticket-soft` / `ticket-hard`
  / `monthly` / `global`;事件帶 `cost_usd` + `tokens`)。
  - `ticket-soft` → 系統發**增額表單**(schema `budget_increase`),使用者自助調高本票上限
    (≤hard)→ `hil_resumed(reason=budget_increase)` → 下輪 resume。
  - `ticket-hard` / `monthly` / `global` → **只管理者**能改 config(profile `budget.*` 或
    `outer_loop.budget.*`)+ `POST /reload`;hard 即時讀 → 自動續跑。
  完整見 [設計/Budget](design/budget.md)。**只量到 token 或只量到 usd**(如 codex 無 cost)→
  由量得到的那個卡(不可量的用量讀作 0、不誤卡)。
- **花費比預期高很多**:多半是 **model 沒設對**——同任務 opus vs haiku 差約 8×(見
  [LESSONS #14](lessons.md))。**測試 profile 一律用便宜 model**,別把貴 model 留在測試
  profile(下次誰跑就吃 8× 陷阱)。model 是 profile 一行(`agent.model`)。
- **月花費怎麼算**:`store.monthly_cost` 掃 journal 的 `attempt_finished.cost` 加總,
  **per-instance**(各讀自己的 journal),跨實例不合計 —— 多實例併發請把 `concurrency`
  設保守。

## 9. dashboard

- **打不開**:確認 `scripts/detail_server.py` 有在跑(用 `uv run python scripts/detail_server.py`)、
  port 沒被占、`--runtime` 指到對的 runtime 目錄(預設 `runtime`)。綁定預設 `0.0.0.0`
  (內網開放);要鎖本機加 `--host 127.0.0.1`。
- **多實例數字混在一起/互相覆寫**:兩個實例 poll 了**同一 Jira project/重疊 jql** →
  互搶同批票、覆寫彼此狀態(併發 flaky 來源)。**分 project 或用不重疊的 label/JQL**;
  各實例分 name、分 control/dashboard port(見 README「多實例部署」)。
- **/agent 頁載入失敗**:多半是 `config.yaml` 的 openhands profiles 依賴缺失的 venv →
  `load_profiles` 擲錯。用 `config.example.yaml`(`ARCP_CONFIG=config.example.yaml`)。
- **元件/圖表沒出來**:dashboard 所有前端元件都是 **vendored**(內網零外部依賴);若
  缺,檢查 `vendor/` 的 vendored 資產是否完整(不該連任何 CDN)。

---

## 還是卡住?

1. 用 [observability §2](design/observability.md) 把該票 journal 時間線 + 對應
   `runs/<run-id>/transcript/` 完整拉出來。
2. 對照 [observability §4](design/observability.md) 的典型序列,定位「少了哪個事件」。
3. 翻 [LESSONS](lessons.md)(踩坑全紀錄)與 [FAQ](faq.md)。
4. 需要理解「為什麼這樣設計」→ [decisions](decisions.md) / [requirements](requirements.md)。
