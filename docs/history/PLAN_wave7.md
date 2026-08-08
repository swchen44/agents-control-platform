# PLAN_wave7 — 人類評分 + 效益 + per-profile 比較 + Agent Detail + 概念頁 + 預算預檢 + LLM REST API

> 承 W6(Server 頁 + transcript 事件化 + REST 文件 + 事件時間軸)+ 使用者
> 2026-08-07 口述 brainstorming(見 REQUIREMENTS §12,R1–R9)。
> **單線、小步、每 phase commit+push。不用 caffeinate。** 全部 additive,不動 Jira
> 原生(workflow/權限/jql/關票流程)。

## 已拍板決策(2026-08-07 一次一題確認,11 題)

- **關票**:維持人關票 + harness **軟性把關**(不硬鎖、不改 Jira 權限)。
- **評分**:0–10 整數(內部 ×10=%);SUCCESS **與** FAILURE 終態都交人評;放 human 段
  `score`;**關票前**捕捉(∵ Done 後票從 jql 消失)。
- **agent 目標**:新 `Profile.goal` → 寫 `agent:<profile>` 段;score 旁小註解。
- **效益**:`(score/10)×省時×時薪 − AI 花費`;`human_minutes_est` 未設預設 **240 分**;
  不用 Python 預測。
- **狀態分類**:8 態(待處理/進行中/排隊/等待人類/交人/成功/失敗/撤銷),依 DB 真實狀態。
- **預算**:新 `max_budget_monthly_usd`(日曆月);**spawn 前預檢**單次+月;單次放寬 human
  段 `budget_override`;月上限只能改 Profile。
- **Agent Detail**:新 tab(harness 設定 + 全 Profile),與 Server tab 分工。
- **REST API**:`/api/v1/` 唯讀;三合一解析器(Jira key/id/CR id);結構化 JSON + 原始檔
  可取;DB 加 `clearquest_id`。
- **ClearQuest 監控(R9)**:**future To-Do,只加欄位不實作**;一點待使用者確認(現有 vs 未來)。

## W7 決策表(接續 W39)

| # | 決策 | 理由 |
|---|---|---|
| W40 | 評分放 **human 段 `score`(0–10)**,每輪解析→`journal("human_score")`;交人時 harness 寫 `Profile.goal` 到 `agent:<profile>` 段 + seed human 段 `score:` placeholder + 小註解 | 沿用既有 section 機制(如 human_email);人要看到 agent 目標才好評 |
| W41 | 關票**維持人做**,harness 只**軟性把關**(交人一次留言 + 每~1h 最多一次提醒;沒填就關=dashboard 標「未評分」) | 硬鎖需 Jira workflow 權限、可能無權;使用者選最小改動 |
| W42 | 評分/交人適用 **SUCCESS + FAILURE 終態**(非中途 pending) | 失敗但有做部分,分數正好反映「幫多少/還有多少 gap」 |
| W43 | `Profile` +`goal`/`max_budget_monthly_usd`;`human_minutes_est` 給 **預設 240 分** | 目標描述 + 月預算 + 效益估時預設 |
| W44 | 效益 = `(score/10)×省時×時薪 − AI 花費`;未評分不計均;省時用 profile 值(不 Python 預測) | 客觀效益數據,先用靜態估時 |
| W45 | **spawn 前預算預檢**:此票累計 vs 單次(或 `budget_override`)+ 此 profile 當**日曆月**累計 vs 月上限;任一達標→pending:budget(不 spawn) | 跑前擋才不多燒一個 attempt(現況跑完才擋) |
| W46 | 月彙總資料源:`attempt_finished` journal 補 `cost`+`profile`(現只有 ts) | 要帶時間的花費才能算當月 per-profile 累計 |
| W47 | 單次放寬:human 段 `budget_override`(USD),per-ticket、下次 dispatch 吃;月上限只能改 Profile | 符使用者原意(填 human section);月上限要慎重故只走設定檔 |
| W48 | dashboard filter **加 profile 關鍵字**;三張 per-profile 圖(8態堆疊/花費+人力$+差值/平均完成度)+ 完成度欄 | 比較不同 profile 的量/成本/有用度 |
| W49 | **8 態顯示狀態**由 (outcome, pending_reason, inactive, queued, session 有無) 推導成單一 canonical state;圖與狀態機共用 | 精細才看得出卡在哪;一處定義兩處用 |
| W50 | 新 **Agent Detail tab**:`/agent/data`(harness config + profiles)+ 頁面;Server tab 維持機器/系統 | 設定只在 routes.yaml、網頁看不到 |
| W51 | 新 **概念/說明 tab**:Jira 生命週期 + 8 態狀態機(純 SVG 零依賴)+「狀態存哪」說明;同內容寫 README | 搞定資料流生命週期;一頁看懂設計 |
| W52 | DB 加 **`clearquest_id`**(nullable);API 三合一解析器(Jira key/id/CR id→同一票) | CQ 只是票上一欄(不取代 Jira);為 R9 預留、不破壞 API |
| W53 | **`/api/v1/`** 版本化唯讀 API:單票狀態 JSON + L3 事件 JSON + 原始 session/sub jsonl raw(`?tail=N`);納入 OpenAPI/Swagger | LLM 監控要程式化讀狀態/log;結構化省 token、原始檔深挖 |

## Checklist

**W7.0 — REQUIREMENTS §12 + PLAN_wave7**
- [x] requirements.md §12(R1–R9,含 Why + 決策脈絡)
- [x] PLAN_wave7.md(本檔)
- [ ] commit+push

**W7.1 — Profile 欄位 + clearquest_id(W43/W52)**
- [ ] `Profile` +`goal: str|None`、`max_budget_monthly_usd: float|None`
- [ ] `human_minutes_est` 預設改 240(None→240 於讀取/使用點,保留 None 語意亦可)
- [ ] loader 讀新欄位
- [ ] store:`ticket_session`(或 `ticket_watch`)加 `clearquest_id TEXT` + migration + row 映射
- [ ] 單元測(profiles + store migration)
- [ ] commit+push

**W7.2 — 人類評分(W40/W41/W42/W44)**
- [ ] 交人時:寫 `Profile.goal`(或 fallback)到 `agent:<profile>` 段;seed human 段
      `score:`(空)+ 小註解「0–10:對照上方目標的完成度」
- [ ] 每輪解析 human 段 `score`(0–10,容錯)→ 有值且未記過 → `journal("human_score", score=…)`
- [ ] 沒填提醒:交人當下一次 + 之後每 ~1h 最多一次(watermark/時間戳防洗頻)
- [ ] SUCCESS + FAILURE 終態都走交人+評分路徑
- [ ] 效益資料進 `/data`(score、完成度%);未評分標記
- [ ] 單元測 + e2e
- [ ] commit+push

**W7.3 — 預算 spawn 前預檢 + 月上限(W45/W46/W47)**
- [ ] `attempt_finished` journal 補 `cost`(本次增量)+`profile`
- [ ] 月彙總:sum(attempt_finished.cost) group by profile,filter 當日曆月
- [ ] dispatcher **spawn 前**:單次(此票累計 or budget_override)+ 月上限預檢 → pending:budget
- [ ] human 段 `budget_override`(USD)解析 + 套用(per-ticket、下次 dispatch)
- [ ] 單元測(單次/月上限/override/預檢時機)
- [ ] commit+push

**W7.4 — dashboard profile filter + 3 圖 + 完成度欄(W48/W49)**
- [ ] canonical 8 態推導函數(前端或 `/data` 端)
- [ ] filter 加 profile 關鍵字(sticky localStorage)
- [ ] 圖①縱 profile × 橫 Jira 數(8 態堆疊上色)
- [ ] 圖②縱 profile × 橫花費(AI/人力$/差值)
- [ ] 圖③縱 profile × 橫平均完成度%
- [ ] 表格加完成度欄
- [ ] e2e
- [ ] commit+push

**W7.5 — Agent Detail tab(W50)**
- [ ] `/agent/data`:harness config(routes.yaml source/concurrency/control/…)+ 全 profiles 參數
- [ ] `/agent` 頁 + 第 N tab;敏感值處理(不顯憑證)
- [ ] e2e
- [ ] commit+push

**W7.6 — 概念/狀態機頁 + README(W51)**
- [ ] `/concepts`(或 help)頁:Jira 生命週期 + 8 態狀態機(純 SVG)+「狀態存哪」說明
- [ ] 同內容寫進 README(repo 根)
- [ ] e2e(頁渲染 + 關鍵字)
- [ ] commit+push

**W7.7 — REST /api/v1(W52/W53)**
- [ ] issue-ref 解析器:Jira key / 內部 id / clearquest_id → ticket(CQ 目前查不到→not found)
- [ ] `GET /api/v1/tickets`(列表,精簡)、`/api/v1/tickets/{ref}`(單票狀態 JSON)
- [ ] `/api/v1/tickets/{ref}/events`(L3 aN.events.jsonl → JSON)
- [ ] `/api/v1/tickets/{ref}/logs`(可取清單)+ `/logs/{name}`(原始 session/sub jsonl raw,`?tail=N`)
- [ ] 納入 `/openapi.json` + Swagger
- [ ] e2e
- [ ] commit+push

## 未納入本波(future)
- **R9 ClearQuest 監控/建資料夾/套模板/開票**:只加 `clearquest_id` 欄,流程不做;
  待使用者確認「現有流程 vs ARCP 未來」。
