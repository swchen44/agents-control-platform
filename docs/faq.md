# FAQ

## 一般

**Q:ARCP 跟直接跑 `claude -p` 差在哪?**
A:ARCP 讓它**長時間可靠執行、可觀測、可控制、由 Jira 驅動**:證據型停止(不信 agent
自稱)、三態 outcome、bounded retry、預算閘、並發資源閘、事件 trace、transcript、REST
控制面、人機協作(HIL)。你管的是一支 agent 大軍,不是單次呼叫。

**Q:為什麼用 Jira?**
A:公司本來就活在 Jira。讓 agent 像員工一樣對 Jira 負責 → 人用既有儀式(指派/留言/審核/
關單)就能管 agent,不必學新工具。Jira 是工作日誌;真工作在後台。見 [決策 D1](decisions.md)。

**Q:一定要 Jira 嗎?**
A:目前來源是 Jira Cloud(`jira_source.py` 是唯一碰 Cloud 細節的檔,換 Server/DC 只改一檔)。
另有內部觸發源(scheduled/oneshot/script)可不經 Jira 跑任務。

## 執行

**Q:一定要 OpenHands / venv 嗎?**
A:**不用**。主線 `rawcli` backend 純 stdlib(直接 spawn `claude -p` / `codex exec`),
系統 python 即可跑。openhands-acp / openhands-server backend 是選配(需 venv)。
`config.example.yaml` 就是 rawcli-only。

**Q:claude 還是 codex?**
A:都支援(profile 的 `agent.engine`)。共用同一 envelope 契約,換引擎不改 dispatcher。

**Q:agent 卡住了怎麼辦?**
A:「強制驅逐」(ticket 頁按鈕或 `POST /evict/<id>`)→ killpg 釋放資源、不耗 attempt、
下輪自動 native resume 續跑(不重花錢)。

**Q:效能瓶頸在哪?怎麼找?**
A:ARCP 本身開銷小(poll + diff + SQLite);瓶頸幾乎都在 ① **agent 執行時長**(model,非
ARCP)② **Jira API 延遲/降級** ③ **並發飽和**(排隊)。看 dashboard **Server 頁的效能監控**
(8 個紅黃綠燈 + 各 profile 時長/花費表),紅/黃燈就是熱點;單票細節看 ticket 頁 trace。

**Q:會不會燒錢?**
A:有預算閘:profile `max_budget_usd`(單次)/ `max_budget_monthly_usd`(月),達上限交人;
人可在表單放寬。model 也可選(haiku 省、opus 強)。

**Q:同一種任務想比較兩個 profile(A/B 測試),或依票內容自動選 profile?**
A:可以(Q16)。在 main profile 加 `select` 區塊:`candidates`(候選 profile,名字**須以 main
名為前綴**)+ `method: random`(均勻分流)或 `method: script`(腳本吃 JSON stdin → stdout 印
出 profile 名,可依 ticket 內容做條件式 triage)。**首次派工選一次並 寫入 session**(resume
不重選,同票結果穩定);任何失敗 fail-safe 回 main。journal 記 `profile_selected`(original /
chosen / method),在 dashboard 事件時間軸 / `/api/v1/tickets` 可看「這票實際跑哪個 profile」。
設計見 [design/selection.md](design/selection.md)。

## 人機協作(HIL)

**Q:agent 需要我時怎麼通知?**
A:在票上留言 `@mention` 你 + 附一次性表單連結(不改 assignee)。填完系統回寫 Jira 並讓
agent 續跑。詳 [使用者手冊 §7](user-guide.md)。

**Q:為什麼不讓我直接編 Jira description?**
A:free-text 易錯難處理。改用受控表單(前後端雙驗、schema 版本化)+ 系統單一寫入者 +
hash 稽核 → 乾淨可稽核。見 [決策 D8](decisions.md)。

**Q:grader 說成功,但我覺得沒完成?**
A:HIL(End) 有三訊號並列(grader / agent 自評 / 你的評分)。你可選「續跑」(解終態+重置
額度回進行中)或「關單」。

**Q:Jira 掛了我還能填表單嗎?**
A:能看能填,但送出會提示「稍後再試」且不落地(不做 queue,避免不同步)。Jira 恢復後
poller 自動解除降級。

**Q:一次性表單連結存哪?重啟會失效嗎?**
A:存 `runtime/harness.db` 的 `interactions` 表(**永久儲存,非記憶體**),表單服務無狀態、
每次用 token 查 DB;靠 status 狀態機保證一次性。重啟完全還原(未填可填、已填唯讀、逾期仍逾期)。
唯一風險是 wipe `runtime/`。設計見 [管理者手冊 §10](operator-guide.md)。

**Q:我可以把票交給另一個 agent/profile 嗎(handoff)?**
A:可以。在 `score_and_close` / `decision` 表單選「改派下一棒」→ 選**換手種類** + 下一棒 profile:
**同票換手(next)**(同一張票換 profile 接手 —— 重置 session、鎖定新 profile、依新 profile 的
template 重新佈建 workspace,**非 native resume**,脈絡全留在 Jira 票)或 **跨票換手(base)**
(系統自動另開新票交接,新票首次佈建時把本票脈絡複製進 `ws/BASE_<票>/`,本票收 ABORTED 為
交接非失敗)。也可在**指令台**下 `next <profile>` 做同票換手。沒填全換手種類 / profile →
fail-safe 降級續跑原 agent。見 [使用者手冊 §7](user-guide.md)、[design/architecture.md §4](design/architecture.md)。

**Q:我怎麼知道 agent 到底產了什麼?能拿到它的檔案嗎?**
A:agent 完成時回傳結構化產出。Jira comment 有「完成/未完成」自報 + 程式碼(Gerrit)連結 +
附件:**小檔(總和 <6MB)直接附到 Jira 票**、**大檔(≥6MB)給一次性下載連結**。評分表單頁更
完整(渲染成果敘事 + 可下載附件 + 花費 + Jira/transcript 連結)。實作靠 agent 在 workspace
寫 `OUTPUT.json`(格式由注入的守則指示);沒寫也會降級只顯示自報。見
[design/agent-output.md](design/agent-output.md)。

## 開發

**Q:怎麼跑測試?**
A:`uv run python tests/<test>.py`(從 repo root)。離線集(CI 跑)= 所有 `test_*.py` +
`harness_selftest` + `e2e_dashboard` + `e2e_form`。真 Jira 用 `smoke_jira.py`。詳
[開發者手冊](developer-guide.md)。

**Q:CI 為什麼用 `config.example.yaml`?**
A:`config.yaml` 的 openhands profiles 依賴 gitignored 的 venv,fresh checkout 沒有 →
`load_profiles` 會失敗。`config.example.yaml` 是 rawcli-only、無 venv 依賴。

**Q:怎麼加一個 backend / profile?**
A:見 [開發者手冊](developer-guide.md)「加一個 backend / profile」。
