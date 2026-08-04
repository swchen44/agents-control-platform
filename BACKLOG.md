# BACKLOG — 待加強 + 待做整合清單(2026-08-04)

> 整合來源:qm 對比學到的(要加強)+ 各 PLAN/HANDOFF/COMPARISON/v3 §9.3 的
> 未做項。每項附**做法、effort、價值**。優先級由使用者圈選;文末有 AI 建議。

## 主題 A — 生產化健壯性(qm 更強 + v5 生產就緒)

| # | 項目 | 做法 | effort | 價值 |
|---|---|---|---|---|
| A1 | **持久化升級 SQLite→Postgres + leased queue** | 抄 qm 的 run-store 模型(lease/heartbeat/reaper);store 介面已抽象,換實作 | 中-高 | qm 最成熟的一塊;多 worker、崩潰重排的生產版(我們現在單機 SQLite) |
| A2 | **tool-output ledger(冪等重放)** | 記 (run,attempt,call)→output;重試時重放已完成工具不重跑(qm `tool-ledger.ts`) | 中 | qm 有我們缺;避免 resume 重複副作用 |
| A3 | **Jira rate limit 退避**(N8,已標未做) | 並行 add_comment 加指數退避重試;write_policy coarse | 低 | 並行/常駐時撞 rate limit 的保護 |
| A4 | **budget/成本上限**(v5 陷阱#3) | profile 加 max_budget_usd;超支→pending;cost 已在 envelope | 低-中 | 防失控燒錢(opus 8× 那次的教訓) |

## 主題 B — Jira 真實接入(從 demo 到能上班)

| # | 項目 | 做法 | effort | 價值 |
|---|---|---|---|---|
| B1 | **真實 Jira Server REST client**(公司環境) | source-adapter 加 Server 實作(v2 API + PAT);jira_source.py 介面已抽象(D6b) | 中(需公司環境) | 現在只驗過 Cloud;公司是 Server。研究轉產品分水嶺 |
| B2 | **Agent Status/Link 自訂欄位 + transition condition**(v5 P2) | Jira admin 建 2 欄位 + workflow 限制;harness 寫欄位(coarse) | 中(需 Jira admin) | v5 的欄位所有權模型落地;人可見進度 |
| B3 | **Resolve 自動轉狀態 + 帶證據 comment**(v5 D7) | grader 過→transition('done')+證據 comment;transition API 已有 | 低 | 現在只回 comment 不轉狀態;閉環 ticket 生命週期 |
| B4 | **常駐服務化 + detail page 拼 Jira 深連結** | run_poller→systemd/daemon;detail page 連回 Jira issue | 低-中 | 從「手動跑一輪」到「一直在跑」 |

## 主題 C — 證據/可觀測(強化差異化 IP)

| # | 項目 | 做法 | effort | 價值 |
|---|---|---|---|---|
| C1 | **grader 擴展:build/test/lint/schema**(現只有 files/command) | 加 verify step 類型;profile 宣告;複用 AllOf | 低-中 | 真實任務(改 repo)要跑測試驗證,不只查檔案 |
| C2 | **L0-L3 trace completeness CI**(v5 唯一 P1 硬 KPI) | 每個結束的 attempt 四層檔齊全,缺任一層告警 | 低-中 | v5 說唯一該 P1 就設 100% 硬目標;稽核基礎 |
| C3 | **KPI 框架**(first-pass Close rate + Goodhart 防護,v5 §10) | 從 journal 算北極星指標;效率指標配制衡指標 | 中 | v5 §10 整套;衡量「好不好」而非只「跑不跑」 |
| C4 | **聚合 dashboard**(cost/state/失敗率) | detail page 加彙總頁 or Grafana;讀 journal/store | 中 | v3 生產就緒清單缺的「監控」 |

## 主題 D — 隔離升級

| # | 項目 | 做法 | effort | 價值 |
|---|---|---|---|---|
| D1 | **Docker workspace 隔離** | 切 openhands-server backend(它有 docker)or 自建 rawcli docker 包裹 | 中-高 | 比 seatbelt 強(獨立核心/網路);跑破壞性測試/改公司 repo 才需要 |
| D2 | **codex --sandbox 端到端**(quota 後) | codex profile 驗 read-only 擋寫;已有 sandbox 欄位 | 低 | 補完 codex 側隔離(claude seatbelt 已驗) |

## 主題 E — 對照/研究補完

| # | 項目 | 做法 | effort | 價值 |
|---|---|---|---|---|
| E1 | **codex 對照點**(quota 8/31 後) | 一鍵 `compare_run.py a-codex b-codex` + `compare_abc.py C` | 低(等 quota) | 補齊 A/B/C 三方 codex 欄 |
| E2 | **長跑/大 context resume**(v5 深水區) | 30 分鐘+大 context 任務 crash→resume;--resume 對大 context 可靠性 | 高(token 貴、需防睡) | crash-safe 生產宣稱前最後硬證據 |
| E3 | **agent-server 閒置 Evict→rehydrate 對照** | 閒置 20 分→子進程關→再存取 rehydrate 續 | 低-中 | qm/OpenHands 的常態機制,我們只間接驗過 |
| E4 | **qm Jira adapter spike**(對比研究延伸) | 在 qm 寫個 surface="jira" adapter,實測 effort | 中 | 驗證「把我們功能搬 qm」的低 effort 判斷 |

## AI 建議(供參考,你決定)

**若目標是「盡快能上生產用」** → high: **B1**(真實 Jira)+ **B3**(Resolve 轉狀態)
+ **A3/A4**(rate limit/budget 保護)。B1 是分水嶺。

**若目標是「強化差異化(grader/證據)」** → high: **C1**(grader 擴展)+ **C2**
(trace CI,v5 唯一 P1 硬目標)。這是 qm 對比證明我們獨有、最該深耕的 IP。

**若目標是「生產級健壯」** → high: **A1**(Postgres,qm 證明的生產 recovery)+
**D1**(docker 隔離)。但 effort 高。

**便宜快見效(隨時可穿插)**:B3、A3、E1、D2、C2。

**我的單一首選**:若只挑一項 → **C1 grader 擴展(build/test/lint)**。理由:qm
對比剛證明「證據型停止」是我們對一個生產平台都獨有的差異化,而現在 grader 只會
查檔案/跑單一命令;擴展成能跑 build/test/lint 才能處理真實 repo 任務,直接放大
我們最有價值的資產,effort 又低。
