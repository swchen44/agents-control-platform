# BACKLOG — 待加強 + 待做整合清單(2026-08-04)

> 整合來源:qm 對比學到的(要加強)+ 各 PLAN/HANDOFF/COMPARISON/v3 §9.3 的
> 未做項。每項附**做法、effort、價值**。優先級由使用者圈選;文末有 AI 建議。

## ★★ 近期完成(2026-08-08 ~ 09)

| # | 項目 | 狀態 |
|---|---|---|
| **W10.3** | a2a 交接由 HIL 表單驅動:同票 `next`(reset+鎖定 profile)+ 跨票 `base`(系統 `create_ticket` 建新票 + 預建 session(鎖定 profile)`base_ref` + 本票 ABORTED + dispatcher 注入 base 脈絡到 `ws/BASE_<key>/`);fail-safe 降級續跑;新事件 `base_injected`(44 種) | ✅ 見 [architecture.md §4.1](docs/design/architecture.md);`test_handoff_hil.py` 32 檢查 |
| **triage 結果模型** | select stdout 改嚴格 JSON `{profile,reason}`;`notfound`→**ABORTED(untriageable)**(profile=notfound + journal `aborted` + Jira 轉 `cancel_status`)、無效/錯→fail-safe 回 main;state 維持推導(不加 state 欄,理由寫進 architecture §3.1/開發者手冊/概念頁);新事件 `aborted`(47 種);術語「pin」→「鎖定/寫入 session 的 profile」;新增 **label=入場券** 比喻(architecture §2.1 + 操作手冊) | ✅ 2026-08-10 `25de867` + 本次;`test_triage_abort.py` 6 檢查 |
| **指令台 command console** | 綁票表單取代 @agent comment 指令通道:人走指令台(依狀態動態選單 + email 稽核 + 破壞性確認)、自動化走 `POST /ticket/<id>/command`,共用 `apply_command`;連結佈建於 description control 段 + 指路 comment、close 失效;移除 CommandHandler/白名單/3 個 command_* 事件(45 種);`canonical_state` 抽 `lifecycle_state.py` 共用 | ✅ 2026-08-10 五增量 `6eb4bb1`→`f0c71c2`;`test_command_core/console/link` 46 檢查;見 [interaction §16](docs/design/interaction.md)、[walkthrough §9](docs/walkthrough-cr-to-agent.md) |
| **Budget / Max-Token 管理** | token+usd 6 層上限 {per-ticket, 月/agent, 全站}×{token,usd};per-ticket soft/hard(soft 破→`budget_increase` 表單自助增額≤hard、hard/月/全站→管理者改 config+hot reload);每輪 attempt 前 precheck、誰先破誰卡;token 從串流 usage 抽(CLI 無上限參數→harness 外部卡)、不可量 metric 用量讀 0;dashboard 用量卡;移除舊 max_budget_usd/budget_override | ✅ 2026-08-10 五增量 `cc83641`→本次;`test_budget*` 系列;見 [design/budget.md](docs/design/budget.md) |
| **W12** | 專業化打包:src-layout、pyproject/uv/MIT、GitHub CI(3.10–3.13)+CD、tests/ 與 scripts/ 分層 | ✅ CI 綠 |
| **W13** | 離線內網文件自足(AI 自我除錯):ai-debugging / troubleshooting / observability(journal 事件字典,`gen_event_dict --check` 入 CI)+ docs/history + docs/lessons | ✅ 見 §主題 H |
| **W14** | 研究策展 `docs/research/`(結論比較文 + 原始長文合併)+ **消除 harness/** → `config/` + `vendor/` + `runtime/`,路徑全走 `arcp.paths`;順修 W12.1 遺留 `_HARNESS_ROOT` bug | ✅ CI 綠 |
| **W15** | workspace 佈建三能力:`workspace_install`(安裝命令)/ `common_skills`(選子集)/ `inject_md`;統一目標解析;TICKET.md 加 goal/驗收/Jira 連結;12 檢查測試 + config 範例 | ✅ 見 [design/workspace.md](docs/design/workspace.md) |
| **C2** | trace 完整性自檢 `scripts/trace_lint.py`(L0–L3 齊全,UNKNOWN 依設計可缺)+ 6 情境測試 | ✅ 見 [design/observability.md](docs/design/observability.md) §6 |
| **A2** | tool-output ledger（冪等） | ✅**釐清後不建 ledger(重工)**:agent 工具靠 native resume、harness 副作用靠 at-most-once 寫入順序、HIL 靠一次性 token,A2 目標已達成;唯一殘缺 = W15 install 原子性,已用 `.arcp_provisioned` marker 補。見 [design/idempotency.md](docs/design/idempotency.md) A2 結論 |

## ★ 人機互動增修(group A,2026-08-09 ✅ 已實作)

Q9–Q13 逐題定案並落地(`tests/test_group_a.py` 12 檢查;設計見
[docs/design/interaction.md §13](docs/design/interaction.md)):

| # | 項目 | 定案 |
|---|---|---|
| Q9/Q12 | control/data path 模型 | prompt=control(主動提示 TICKET 更新)、TICKET.md=data、CLAUDE/AGENTS=行為守則;不違反原則 |
| Q13 | agent 自評 0–10 | 只在關單(score_and_close)時 resume+prompt 問一次(非每 attempt) |
| Q10 | HIL 表單自由 prompt 欄 | submit → 累加寫 TICKET.md「人類指示」段(sidecar `ws/.arcp_human.md`)+ resume 重讀 |
| Q11 | 人類強制中斷 → HIL(`@agent hold`) | comment 觸發 → 立即 evict(killpg)→ HIL(Middle)+need_info 表單(含 prompt 欄)→ submit 寫 TICKET.md + resume 排隊,不耗 attempt;硬殺限制寫進開發者手冊 FAQ |

✅ 已建:human_prompt 欄 + sidecar `.arcp_human.md` + TICKET.md 人類指示段(Q10)、
`@agent hold`→evict+HIL 表單→resume(Q11)、ScoreGate `self_score_fn` hook(Q13,真自評
呼叫留 live/V1)。橫跨 interaction/hil/scoring/commands/workspace/run_poller。

## ★ D 群 文件 + hooks(2026-08-09)

| # | 項目 | 狀態 |
|---|---|---|
| **Q8** | workspace hooks 佈建(`.claude/hooks` / `.agents/hooks`,比照 skills) | ✅ 已建:`config/hooks/` 庫 + profile `common_hooks` 選子集 + `_copy_bundle` 統一目標解析(見 C 群上方 commit) |
| **Q1** | 三視角操作手冊(使用者/管理者/開發者,各含日常操作+use case+FAQ) | ✅ 已建:新增 [管理者手冊](docs/operator-guide.md)(起停/控制/監控/備份還原/多實例/異常/安全 + Operator FAQ,含 Q4 backup runbook + Q2/3/6 現況);index/README 呈現三視角 |

## ★ C 群 效能監控(2026-08-09)

| # | 項目 | 狀態 |
|---|---|---|
| **Q5** | 效能監控(bottleneck 在哪/怎麼找 + dashboard 紅黃綠燈) | ✅ 已建:整合進 `/server` 頁 —— 8 燈(失敗率/排隊/最舊等待/evict/花費速率/錯誤事件/系統資源/journal 大小,門檻見 `perf_metrics`)+ per-profile 細節表 + bottleneck 說明。`perf_metrics` 純函式 + `test_perf.py`(13 檢查) |

## ★ B 群 profile 選擇 / config(2026-08-09)

| # | 項目 | 狀態 |
|---|---|---|
| **Q16** | profile A/B 測試 / 泛化 triage:main profile 加 `select`(candidates+method random\|script);首次派工選一次寫入 session 的 profile 欄(鎖定);script 吃 JSON stdin(ticket/crid/候選+yaml)→ stdout 回**嚴格 JSON `{profile,reason}`**(`notfound`→ABORTED、無效/錯→fail-safe 回 main) | ✅ 已建(`selection.py` + `test_selection.py`;見 [design/selection.md](docs/design/selection.md)) |
| **Q7** | triage(要不要人、選 profile) | ✅ **由 Q16 泛化涵蓋**:選到 require_approval 的 profile=要人、否則直跑;現行 per-profile require_approval 仍為基礎閘 |
| **Q15** | config 改名 / 拆檔(命名精準 + 分檔 owner) | ✅ 已建:`routes.yaml`→`config.yaml`(不相容,未 release);profile 可拆到 `config/profiles/<名>.yaml`(檔名=名),`load_profiles` 自動合併主檔 inline + 拆檔、同名 fail-fast;`Profile.source_yaml` 記來源(Q16 script 拿 per-profile yaml)。`test_profiles_split.py` 7 檢查 |

**仍待辦(需真 Jira/agent,我不能替跑):**

| # | 項目 | 做法 |
|---|---|---|
| **V1** | **真後端派工複驗** | 助手:`scripts/reverify_v1.py`。**免費部分已驗綠(2026-08-09)**:runner 路徑、config/profiles 載入、事件字典、**真 Jira 唯讀 auth+search(撈到 20 票)** —— 大重構未破真 Jira 接線。**剩付費部分**(真派一次 haiku 工,需充電):確認 runner spawn / Q16 select / W15 install / Q11 hold→resume / Q13 自評 / Q10 human-prompt 在真 agent 下如預期 + C3/C5 flaky。清單見 reverify_v1.py 輸出。 |

## ★ 實作現況對照(2026-08-09 校正 —— 下方優先級是 2026-08-04 原始規劃,多數已落地)

> ⚠️ 下面「使用者圈定優先級」與「主題 A–H」是**當時的規劃稿**,不是現況。多數 high
> 項已隨 W1–W15 實作。內網凍結版讀者請以本對照表 + [CHANGELOG](CHANGELOG.md) 為準。

| 原 ID | 現況 | 落點 |
|---|---|---|
| F1 分層資源閘門 | ✅ | `gate.py`(global + per_engine + per_profile,FIFO) |
| F2 QUEUED 排隊可視化 | ✅ | `ticket_session.queued/queued_at` + dashboard 徽章 |
| F3 換手進隊列 | ✅ | `@agent next`(指令)+ **W10.3 HIL 表單 handoff**(同票 next / 跨票 base) |
| G1 結構化契約 | ✅ | `contract.py` + dispatcher 解析 `status/next`(handoff 驅動 F3) |
| G2 可選 grader 雙保險 | ✅ | profile `verify`(files / cmd / **json**)可選;純自評亦可 |
| A3 Jira rate limit 退避 | ✅ | `jira_source` write_retry(指數退避) |
| A4 budget 花費上限 | ✅ | `dispatcher._budget_precheck`(單次 + human override + 月上限) |
| C1 grader 擴展 | ✅ | 2026-08-09 加 `JsonGrader`(JSON 形狀:file/require/types);build/test/lint 用 `cmd` |
| C2 L0–L3 trace 自檢 | ✅ | `scripts/trace_lint.py` + `test_trace_lint.py` |
| C3 KPI + 人力估算 | ✅ | `resolved` 事件帶 `human_minutes_saved`(profile.est_minutes)+ dashboard |
| C4 總覽 dashboard | ✅ | `/`(cost/狀態/失敗率)+ `/server`(8 燈效能) |
| B3 Resolve 轉狀態 | ⤳ 改設計 | 不做「grader 過即自動轉 Done」;改由 **HIL(End) 人關單** → `transition("done")`(W11) |
| B4 常駐服務化 | ◐ 部分 | run_poller 時間盒 + control API;systemd/daemon 化未做(operator 手冊有跑法) |
| A2 冪等 ledger | ✅ 結論不建 | native resume + at-most-once + 一次性 token 已達目標(見 idempotency.md) |
| D2 codex sandbox | ⏳ 真環境 | 需 codex quota;`--sandbox` 欄位已在 |
| B1/D1/E1/E2/A1 | ⏳ 真環境 | 真 Jira Server / docker / codex 對照 / 長跑 resume / Postgres —— 需真環境,我不能替跑 |
| V1 付費複驗 | ⏳ 真環境 | 免費部分已驗綠;付費(真派工)清單見 `scripts/reverify_v1.py` |

## ★ 使用者圈定優先級(2026-08-04,全 23 項逐項問過)

**下一階段 high(現在做)** — 15 項,構成一個連貫系統:「資源受控 + 自動值班 +
結構化闭環 + 證據可追溯 + 跨平台隔離」。

| # | 項目 | 使用者設計補充(這一輪新增) |
|---|---|---|
| **F1** | 分層資源閘門(全局+per-engine+per-profile) | 核心目的=機器 CPU/memory 有限要管制 |
| **F2** | QUEUED 排隊可視化 | 與 C4 合併成同一頁總覽 |
| **F3** | 換手進隊列(`@agent next`) | 換**人**不排 agent 隊列=pending:human |
| **G1** | agent 結構化契約 `{reason,status,next}` | `--json-schema`/`--output-schema`;next→F3 |
| **G2** | 重要任務可選 grader 雙保險 | 保留證據型停止 IP;一般任務純 G1 |
| **B3** | Resolve 自動轉狀態 + 帶證據 comment | 閉環 ticket 生命週期(便宜快見效) |
| **B4** | 常駐服務化 + detail page 拼 Jira 深連結 | 從「手動一輪」到「一直在跑」 |
| **A2** | 動作不重做(冪等重放) | **分層**:agent 工具調用靠 transcript(已有);<br>**harness 自己的副作用**(comment/transition/建 ws)靠自己的 ledger |
| **A3** | Jira rate limit 退避 | F1 並發 + B4 常駐的保護罩 |
| **A4** | budget 花費上限 | 防失控燒錢(opus 8× 教訓);錢也是資源 |
| **C2** | L0-L3 trace completeness 自檢 | v5 唯一 P1 硬目標;強化證據 IP |
| **C3** | KPI 框架 | **+ 人力節省估算**:用公式從過程(改幾檔/跑多久/幾步)<br>推算「人做要花多久」→量化省下的人力/ROI |
| **C4** | 總覽儀表板(cost/狀態/失敗率) | 跟 F2 合併;含排隊、花費、資源用量 |
| **D1** | **可插拔隔離層** | 跨平台(Linux/Win/macOS);**OS 原生優先**<br>(mac=seatbelt 已有、Linux=namespaces/bwrap、Win=沙盒);<br>docker 當跨平台後備;config 讓使用者選(auto/os-native/docker);<br>**先建抽象,不急端到端驗** |
| **D2** | codex 原生沙盒驗證(`--sandbox`) | codex token 已可用;D1「OS 原生優先」的 codex 那段實證 |

**排進計畫但不急(6 項)**:
- **B1** 真實 Jira Server REST client(需公司環境)
- **B2** Agent Status/Link 自訂欄位 + transition condition(需 Jira admin)
- **E1** codex 對照點(補齊 A/B/C 三方 codex 欄)
- **E2** 長跑/大 context crash resume(token 貴、需防睡)
- **E3** 閒置 evict→rehydrate 對照 —— 已被 N13 stall watchdog + resume 覆蓋;
  使用者觀察:**「有時卡住沒反應、不知原因,resume 就救回」= 非終態→resume 通用手段**

**先不做/降級(4 項)**:
- **A1** Postgres(單機 SQLite 夠用,要多機生產再說)
- **C1** 複雜確定性檢查(被 G1 agent 自評取代;grader 降為 G2 可選雙保險)
- **F4** max_awaiting_close 審查閘(與機器資源目的不同)
- **E4** qm Jira adapter spike(與主線「把自己 harness 做強」方向不同)

### 建議實作波次(依賴排序,待使用者確認)

- **Wave 1 地基(資源管制 + 契約=你的核心目的)**:F1 分層閘門(F2/F3 的基礎)、
  G1 結構化契約(F3 換手的輸入)、A3 rate limit、A4 budget(兩個便宜保護一起做)。
- **Wave 2 可視化 + 換手(建在 Wave 1 上)**:F2+C4 合併總覽、F3 換手進隊列(用 G1 的 next)。
- **Wave 3 閉環 + 值班(相對獨立,可與 W2 並行)**:B3 Resolve 轉狀態、B4 常駐服務、
  G2 可選 grader 雙保險。
- **Wave 4 證據強化 + 隔離**:C2 trace 自檢、A2 harness ledger、C3 KPI+人力估算、
  D1 可插拔隔離抽象、D2 codex 沙盒驗證。

### 橫切設計:agent 生命週期 + 審批門(2026-08-04,使用者提)

見 **[docs/design/lifecycle.md](docs/design/lifecycle.md)** — template(class)→
workspace(instance)、命名 resume-safe、起點審批門(description YAML 參數 + assignee 放行 +
退回迴圈)、**assignee=資源開關**(不在機器人手上就 killpg 釋放 CPU/memory)、無票定時源。
橫切 F1/F3/G1/A2/E3/N13;profile schema 變更與波次落點見該文 §8-9。

---

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

## 主題 F — Flow control / 資源閘門 / 排隊 / 換手(使用者 2026-08-04)

> 需求:最多幾個 agent(claude -p/codex exec)同時跑(怕系統不夠用)、不同 agent
> 不同上限、排隊中可在看板看到、換下一手(next agent/人類、assignee 換)時進排隊。
> 現況:conc.1 只有一個全局 `max_running`(ThreadPoolExecutor **隱式**排隊,看不到)。
> 對應 v5 D10(雙閘門 max_running/max_awaiting_close、per_profile、queue_policy: fifo)。

| # | 項目 | 做法 | effort | 價值 |
|---|---|---|---|---|
| F1 | **分層資源閘門(全局 + per-engine + per-profile)** | config `concurrency: {max_running, per_engine:{claude:N,codex:M}, per_profile:{...}}`;dispatch 前查 store 的 in-flight 數(該 engine/profile 正在跑的 session),額滿→不派、標 QUEUED。**顯式隊列取代 ThreadPool 隱式排隊** | 中 | 你的核心:防開太多 agent 撐爆系統;claude/codex 各自上限 |
| F2 | **QUEUED 狀態 + 排隊可視化** | ticket_session 加 `queued` 狀態 + 入隊時間;poll 每輪按 FIFO(created/入隊序)挑能跑的;detail page 顯示排隊位置/前面幾個;可選寫 Jira Agent Status=QUEUED(看板可見) | 低-中 | 排隊透明,看板/detail page 看得到「在排、排第幾」 |
| F3 | **換手(handoff)進隊列** | `@agent next <profile>`(換下一手 agent/engine)或 assignee 改人 → session 重置 QUEUED + 換 profile → 進新隊列排;換**人類**=assignee 改人→pending:human(不排 agent 隊列)。接既有 command channel + external_change_policy | 中 | 你要的:換下一手/換人時 assignee 換、重新入隊 |
| F4 | **max_awaiting_close 審查閘門**(v5 D10 第二閘) | Resolve 未 Close 的張數達上限→停派新工(瓶頸在人審查時自動節流) | 低-中 | v5:真正瓶頸是人審查頻寬,不是機器;配 B3 用 |

**設計決策點(你之後可定,或我給建議)**:
- 閘門層級:只做全局+per-engine(簡單) vs 加 per-profile(細,v5)?
- 隊列驅動:poll 每輪挑能跑的(簡單,與現架構一致) vs 事件驅動隊列(複雜)?→建議前者。
- 換手觸發:`@agent next` 指令 + assignee 監看(現成通道)即可。

## 主題 G — agent↔harness 結構化契約(使用者 2026-08-04,取代 C1 方向)

> 使用者:「太多檢查不好作,給 agent 判斷。準備 system prompt + claude -p/codex exec
> 定好回應 JSON schema,回給 control 和留在 Jira。至少要有 reason、status、next
> (建議下一位給誰)。」→ 用 **agent 結構化自評**取代複雜的確定性 grader。

| # | 項目 | 做法 | effort | 價值 |
|---|---|---|---|---|
| G1 | **agent 結構化回應契約(reason/status/next)** | system prompt 定角色+規則;claude `--json-schema`、codex `--output-schema` 強制結構化輸出;schema 至少 `{reason, status, next}`(status=完成/失敗/待人/…;next=下一手 agent 或人);harness 解析:status→outcome、**next→F3 換手**、reason→Jira comment | 中 | 定義 agent↔harness 契約;next 直接驅動 F3 換手;比堆確定性檢查簡單靈活 |
| G2 | **可選確定性雙保險(保留 grader IP)** | 高價值/破壞性 profile 可選加 grader(build/test/檔案)覆核 agent 自評;一般 profile 純靠 G1 | 低(grader 已有) | agent 自評是「信任」(loop on confidence);關鍵任務加確定性檢查防「自稱成功沒做對」(v5/qm 教訓) |

**張力(誠實)**:G1 是「信任 agent 判斷」,和我們一路的「證據型停止(loop on
evidence, not confidence)」方向相反。qm/v5 都踩過「agent 自稱完成但沒做對」。
好處是簡單、靈活、next 接換手;代價是純自評可能誤判成功 → 故 G2 保留確定性
grader 作關鍵任務的可選雙保險(profile 決定)。

## 主題 H — 離線內網文件自足性(W13,使用者 2026-08-08)

> **約束**:交付物會被下載進**公司內網當凍結 snapshot** —— 之後無法抓新版、無法連外、
> 無法問原作者。文件必須自足到「AI(和人)只靠 repo 內文件,就能理解系統、定位問題、
> 做分析」。現有 docs 擅長「理解/操作/設計理由」,但缺**除錯操作面**與**證據語意面**。

| # | 項目 | 狀態 | 交付 |
|---|---|---|---|
| H1 | **troubleshooting / runbook** | ✅ | `docs/troubleshooting.md`:症狀導向(票沒被處理/卡住/假完成/runner 失敗/resume 冪等/Jira 降級/指令/花費/dashboard),每條指向該看的 journal 事件與證據 |
| H2 | **journal 事件字典 + 證據地圖** | ✅ | `docs/design/observability.md`:證據地圖 + **42 事件字典**(`scripts/gen_event_dict.py` 自動列表 + 手寫語意分組)+ 典型事件序列 + 純 stdlib 離線查法 |
| H3 | **給離線 AI 分析者的入口** | ✅ | `docs/ai-debugging.md`:離線工作守則、標準除錯路徑、關鍵不變量、離線驗證怎麼跑;CLAUDE.md 已指向 |
| H4 | **索引整合** | ✅ | `harness/LESSONS.md` → `docs/lessons.md`;`docs/index.md` 加「除錯/可觀測」分區串起 H1-H3 |

**已定的決策**:事件字典 = **混合**(掃碼列表防漂移 + 手寫語意);LESSONS 搬進 docs/。

**後續可強化(非阻塞)**:
- observability §3 手寫語意目前是「分組概述 + 標異常訊號」,42 事件未逐一逐段展開;
  真需要時可再細化高風險事件。
- troubleshooting 的除錯範例可補**真實 journal 片段**(需真跑一次,連同 V1 複驗)。
- 可加 pre-commit hook 跑 `gen_event_dict.py --check`(目前只在 CI)。

## 主題 I — CR/ClearQuest 橋接收尾 + close→CQ 回寫(2026-08-10 定案,待資料/實作)

> triage 結果模型與 Jira 取消已落地(見近期完成)。**I2 已完成**(2026-08-10);
> 剩 I1(阻塞於使用者提供 CQ 端資訊)、I3(小增量)。

| # | 項目 | 做法 | 阻塞 / effort | 價值 |
|---|---|---|---|---|
| **I1** | **close→CQ 回寫**(所有 close 若 `clearquest_id` 有值 → 回 CQ 寫 Jira 連結 + 結果) | 擴充點 `cq_writeback` 已預留(`base_url` + 欄位 map);於**每個 close 路徑**(HIL 關單 / auto_close / ABORTED)呼叫;純設計已定,HTTP 未接 | ⛔ **等使用者給 CQ base_url + 欄位名**;接上約低-中 | CR 來源的閉環:CQ 端看得到 Jira 進度與結果 |
| **I2** | ~~`fire_agent_job` 寫入 `clearquest_id`~~ | ✅ **已完成**(2026-08-10):`task_script` 每筆輸出可帶 `crid` → `_resolve_tasks` 帶出 → `fire_agent_job` 寫進 `session.clearquest_id` + `job_fired` 事件帶 `crid`。`test_jobs` 覆蓋 | ✅ | I1 的前置;避免同一 CR 重複開票 |
| **I3** | CR-bridge「只建票 + 貼 label、不鎖定 profile」模式 | 讓 CR→Jira 的票走 **triage**(由 label 入場、profile 由 select 決定)而非建票即鎖定;job 增一個「不預鎖 profile」選項 | 低-中 | CR 票也能享用泛化 triage(A/B / 條件式選 profile),而非固定一個 profile |

**已定的決策**:label = **入場券**(poller 靠命中 route 的 label 撿票);profile =
進場後由 route/triage 決定並**鎖定在 session**(非 Jira label);「鎖定」取代舊詞 "pin"。
close→CQ 回寫對**所有** close 生效(不只 SUCCESS),因為 CQ 端需知道被取消/失敗的結果。

## 主題 J — job 泛化 + description 契約 + label 規範(2026-08-11 ✅ 全完成)

> I3 深化成「job 泛化」:agent-job = **像人一樣建票 → 走 poller route/triage**(不 pin、
> 可 A/B)。**J1–J5 全數完成(2026-08-11)**:J1+J2 `747b526`、J4 `a5de847`、
> J3 `77bb8a2`、J5 `7ac223d`。label 前綴定案 `arcp.`(點號命名空間)。

| # | 項目 | 做法 / 決策 | 現況 |
|---|---|---|---|
| **J1** | **agent-job 泛化(原 I3)** | 加 `trigger_type: agent-job\|script-job`;`task_script`→統一 `script`(和 script-job 共用「有 log 的執行」:cwd/stdout.log/stderr.log/run.tgz/dashboard);腳本放 `config/scripts/{subfolder}/`、cwd 進 subfolder;agent-job 跑 script→stdout JSON 任務→**像人 create_ticket**(不建 session、不 pin)→走 route/triage;非 JSON→`trigger_error`。**移除** job 的 `profile`/`task`/`prompt`、`fire_agent_job` 預建 session、legacy `run_trigger`(鎖定機制本身 base 交接仍用)| ✅ **完成**(2026-08-11,`747b526`;test_triggers/test_jobs) |
| **J2** | **description → session 的契約格式** | ✅ **定案+實作(2026-08-11,隨 J1)**:**yaml**(`key: value`)、放 description **最上面**、**人寫**(或 agent-job 腳本像人一樣寫)、**不放 ARCP section**(那三段是機器寫的紀錄,與人寫的分開)。欄位:`crid: WCNCR…`→session.clearquest_id(已實作);`prompt`/`email` 保留(email→tag + 檢查一次性連結填的 email,也可用一次性連結改)。harness 只認**已知 key**(`crid`/`prompt`/`email`)→ 到空行止、解析安全。`parse_ticket_meta` | ✅ 完成(crid) |
| **J3** | **label 命名規範** | ✅ **定案+實作(2026-08-11,`77bb8a2`)**。前綴用 **`arcp.`**(點號命名空間)。config route 11 個入場券 + agent-job 範例(scan.sh)全加前綴;載真 config 的測試同步(harness_selftest 斷言、e2e_* 開票、job fixture)。**只改「作為 Jira 入場券的 label 值」**(引號界定,避開概念字 "agent"、不誤傷 filechain-server);**刻意不動**第三方/自洽 fixture(`team-x`/`go`/`x`)以示範命名空間隔離;route/profile 名不變 | ✅ **完成** |
| **J4** | **select 泛化 / 遞歸** | ✅ **定案+實作(2026-08-11,`a5de847`)**。**軸 B**:`method=script` 可回**任何已定義 profile**(不限同族候選;`_parse_select` 對 script 模式放寬——candidates 選填、免 prefix;random 仍限同族+必填);stdin 加 `all_profiles`。**軸 A(遞歸)**:選中的 profile 若自己也有 `select` 就再跑一層 →多層 triage 樹;終止=無 select(葉)/回自己/繞圈(走過的)/fail-safe/**第 10 層截斷**,`notfound` 任一層即中止;meta 帶 `chain`。dispatcher 把票的 crid 傳進 select。test_selection 16 檢查全綠 | ✅ **完成** |
| **J5** | **全文件 + web introduction 總檢查更新** | ✅ **完成(2026-08-11,`7ac223d`)**。盤點 docs/ 26 檔(排 history/research)+ web 概念頁 + README + config 註解。**web `/concepts` 新增「進場·選型·排程·指令·額度」靜態概念節**;selection.md 大幅更新(軸 B/遞歸整節);12 處裸 label 加 `arcp.`;index/idempotency 的 @agent/comment 指令→指令台;pin 殘留清除;README budget→6 層。多數手冊前幾波已對齊,故實改集中少數過時處 | ✅ **完成** |

## AI 建議(供參考,你決定)

**若目標是「盡快能上生產用」** → high: **B1**(真實 Jira)+ **B3**(Resolve 轉狀態)
+ **A3/A4**(rate limit/budget 保護)。B1 是分水嶺。

**若目標是「強化差異化(grader/證據)」** → high: **C1**(grader 擴展)+ **C2**
(trace CI,v5 唯一 P1 硬目標)。這是 qm 對比證明我們獨有、最該深耕的 IP。

**若目標是「生產級健壯」** → high: **A1**(Postgres,qm 證明的生產 recovery)+
**D1**(docker 隔離)。但 effort 高。

**若目標是「flow control(資源保護/排隊透明)」** → high: **F1**(分層閘門)+
**F2**(排隊可視化)。F1 是「怕系統不夠用」的直接解;F3/F4 換手/審查閘可接續。
基礎已有(conc.1 max_running、store、detail page、command channel),effort 中。

**便宜快見效(隨時可穿插)**:B3、A3、E1、D2、C2。

**我的單一首選**:若只挑一項 → **C1 grader 擴展(build/test/lint)**。理由:qm
對比剛證明「證據型停止」是我們對一個生產平台都獨有的差異化,而現在 grader 只會
查檔案/跑單一命令;擴展成能跑 build/test/lint 才能處理真實 repo 任務,直接放大
我們最有價值的資產,effort 又低。
