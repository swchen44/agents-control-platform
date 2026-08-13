# 開發者手冊

給「想改 ARCP 程式」的人。跑法見 [使用者手冊](user-guide.md),目錄地圖見
[專案檔案介紹](project-overview.md)。

## 開發環境

```bash
uv sync --extra dev          # 相依 + ruff + pytest + editable arcp
uv run ruff check .          # lint(核心套件嚴格;測試/腳本 per-file 放寬)
```

啟用 pre-commit(每台機器一次):`git config core.hooksPath .githooks`
(對 staged `*.py` 跑 ruff;vendored 與 examples 不管)。

## 架構一句話

**差異化層 runtime-agnostic**:上游只吃正規化的 `Ticket` 模型 + 統一的 `envelope`
契約 `{completed, session_id, cost, error, …}`;換執行單元(rawcli / openhands-acp /
openhands-server × claude / codex)dispatcher 與 grader 零改動。詳
[設計/架構](design/architecture.md)(含分層圖 + 職責表 + node/edge graph)。

**核心原則**:證據型停止(grader 終審,非 agent 自稱)· 三態 outcome
(SUCCESS/FAILURE/UNKNOWN)· envelope 契約跨 backend 不變 · 內網零外部依賴 · 省電不
caffeinate。詳 [需求與理由](requirements.md)。

## 套件結構(`src/arcp/`)

分五層(輸入 → 決策 → 執行 → 人機協作 → 狀態·觀測·控制):

- **輸入**:`jira_source`(Jira Cloud 讀寫,唯一碰 Cloud 細節的檔)、`triggers`(排程)
- **決策**:`poller`(外圈輪詢 diff→journal→協調)、`routing`、`gate`(F1 分層額度閘)
- **執行**:`dispatcher`(派工/審批/證據迴路)、`inner_runner`、`workspace`+`isolation`、
  `contract`(envelope 契約)、`grader`
- **人機協作**:`approval`、`scoring`(HIL(End) 評分)、`commands`(指令台核心 `apply_command` + 離手政策)、
  `sections`(description 三方分段 + hash)、`interaction` + `hil` + `form_server`(W11 表單)、
  `output`(讀 agent 的 OUTPUT.json)+ `deliverables`(組交付物 ADF comment + 附件)+
  `adf`(精簡 ADF builder)—— agent 產出契約,見 [design/agent-output.md](design/agent-output.md)
- **狀態·觀測·控制**:`store`(SQLite + journal)、`control_api`、`transcript`、`retention`
  (`detail_server.py` 唯讀 dashboard 在 `scripts/`)

### 狀態怎麼推導(沒有 state 欄)

**DB 不存 `state`**;6 態由 `detail_server.canonical_state()` 從原始欄唯讀映射:
`outcome`(含 ABORTED)/`pending_reason`/`queued`/`inactive`/有無 session。**行為邏輯讀原始欄**
(dispatcher `outcome in (SUCCESS,ABORTED)`、gate 算 in-flight、ScoreGate 終態判定),
`canonical_state` 只是給 dashboard / `/api/v1/tickets` 的**讀模型**。**不要加權威 state 欄**
(雙源真相 → 漂移;見 [architecture §3.1](design/architecture.md))。triage 判不出 =
`outcome=ABORTED` + `profile=notfound` → 推導 `aborted`。

### triage(select)判不出 → 中止

`selection.select_profile` 的 script 模式:stdin JSON、**stdout 嚴格 JSON** `{profile,reason}`。
`profile=="notfound"`(`UNTRIAGEABLE`)→ `dispatcher._abort_untriageable`:寫 `outcome=ABORTED`
+ `profile=notfound` + journal `aborted(reason=untriageable)` + `source.transition(…,
prefer_status=cancel_status)`(Jira 取消,workflow 沒有則退回 done)。無效名/腳本錯 → fallback
main。設計見 [selection.md](design/selection.md)。

### budget(token / usd 上限)機制

`dispatcher._budget_precheck` 在 **`while attempts` 迴圈內、每輪 attempt/resume 前**跑,
檢查 per-ticket(hard→soft)→ 月/agent → 全站,誰先破誰卡 → `pending:budget`
(`scope`)。**CLI 沒有 token/usd 上限的輸入參數**能中途硬停 → 靠 harness 外部 precheck。
**token 從串流 `usage` 抽**(`rawcli/agent._sum_tokens`→ envelope `tokens`→ `session.tokens`
+ `attempt_finished.tokens`);codex 可能只有 token 無 cost → **不可量的 metric 用量讀 0、不
誤卡**。soft 存 session(可經 `budget_increase` 表單調高≤hard)、hard 即時讀 profile。月/全站
用量 = `store._monthly_sum`(掃 journal)。完整見 [budget.md](design/budget.md)。

### agent↔agent 交接(W10.3)在哪

- **HIL 表單驅動**:`hil._do_handoff`(被 `apply_submission` 呼叫)。表單欄位(`handoff_kind`
  next/base + `next_profile` + `handoff_prompt`)定義在 `interaction.FORM_SCHEMAS` 的
  `score_and_close`/`decision`;下拉候選由 `scoring.ScoreGate.profiles_fn` 注入 payload。
- **同票換手(next)**:reset session + 鎖定 profile + `workspace="(handoff)"` 哨值(與
  `dispatcher` 裡 agent 自發換手同一套機制)。
- **跨票換手(base)**:`hil._do_handoff` 用 `source.create_ticket` 建新票 + 預建鎖定 profile 的 session
  (`store.TicketSession.base_ref` = 來源票 issue_id);`dispatcher._inject_base` 於新票首次
  佈建後呼 `workspace.inject_base_context` 複製脈絡進 `ws/BASE_<key>/`,一次性後清 `base_ref`。
- **測試**:`tests/test_handoff.py`(指令台 next / apply_command)+ `tests/test_handoff_hil.py`(HIL 表單
  next/base/fail-safe/注入,免真 Jira 用 FakeSource.create_ticket)。真 `create_ticket` 寫入
  屬 V1 付費路徑(見 `scripts/reverify_v1.py` 清單)。設計見 [design/architecture.md §4](design/architecture.md)。

### HIL(等人)機制程式碼地圖

「等人」統一模式:**寫 `sess.pending_reason` + `hil.request_human(schema_id)` 發
一次性表單 → 人提交 → `hil.apply_submission` 依 schema 分支處理 → 清 pending →
下輪 resume**。狀況全表(7 種 Middle reason + End 評分)見
[interaction.md §3.2](design/interaction.md);程式碼錨點:

- **值域**:`pending_reason ∈ {approval, security, budget, hold, human-decision,
  unknown, external}`(grep `pending_reason =`);HIL(End)=outcome 終態+
  `scoring.ScoreGate` 發 `score_and_close`。
- **觸發點**:`approval.gate`(審批)/ `dispatcher._security_gate`(安全審)/
  `dispatcher._budget_soft_form`(增額)/ `commands.apply_command`(hold/stop)/
  `dispatcher.handle`(handoff-human、unknown、infra→external)。
- **表單定義**:`interaction.FORM_SCHEMAS`(7 種;欄位驗證含 pattern)。
- **加一種新表單**:FORM_SCHEMAS 加 schema → 觸發處 `request_human(…, "你的id")`
  → `hil.apply_submission` 加分支 → `tests/test_interaction.py`+`test_hil.py` 補
  正負向 → journal 新事件跑 `gen_event_dict.py`(pre-commit 會擋 drift)。
- **不變量**:assignee 恆定(通知靠 @mention+表單,不切 assignee);表單提交=唯一
  resume 信號;email 門禁在 `apply_submission`/`apply_command` 入口驗。

### TICKET.md 與 description 變數的程式碼錨點

- 渲染:`workspace.render_ticket_md`(段落:head/目標/描述/人類指示/驗收);
  sidecars:`.arcp_human.md`(人類指示累加)、`.arcp_desc_override.md`(安全審
  修訂);交接:`workspace.inject_base_context`(`BASE_<key>/`)。
- description 頂部 yaml 變數:`triggers.parse_ticket_meta`(**只認
  `crid`/`prompt`/`email` 三鍵**,到空行止;寫入端 `_ticket_meta_yaml`)。消費:
  `crid`→`session.clearquest_id`(+`store.find_by_crid` 去重)、`email`→
  `owner_email_list`(門禁)、`prompt`→不特別消費(隨描述段整段給 agent)。
- 插值:`workspace.interpolate`/`ticket_vars`(`{crid}` 等;文本類全做、
  verify cmd 不做);存證+結案回寫:`provenance.py`
  (`attach_ticket_md_if_changed` 在 dispatcher 刷新後;`finalize_provenance`
  五個收尾點:hil 終局/auto_close/cancel/untriageable)——正本
  [design/provenance.md](design/provenance.md)、測試 `test_provenance.py`。
- 改 TICKET.md 組成 → 同步 [design/workspace.md](design/workspace.md)(組成正本)
  + `tests/test_workspace_provision.py`;⚠️ 驗收段會「教」agent(e2e 教訓
  lesson #17)——別把測試專用的檢查渲染給真 agent。

## 測試

測試在 `tests/`(自訂 runner,亦 pytest-相容),從 repo root 執行:

```bash
uv run python tests/test_<name>.py                # 單支
for t in tests/test_*.py; do uv run python "$t"; done   # 全單元
uv run python tests/harness_selftest.py           # 路由/config/指令 冒煙
uv run python tests/e2e_dashboard.py              # dashboard 端到端(spawn detail_server)
uv run python tests/e2e_form.py                   # 互動服務端到端(fake Jira + 真 HTTP)
```

- **離線集**(CI 跑):所有 `tests/test_*.py` + `harness_selftest` + `e2e_dashboard` +
  `e2e_form`。免 token、免網、免真 agent。
- **需真依賴**(CI 不跑):`scripts/smoke_jira.py`(真 Jira)、`tests/e2e_c*` /
  `e2e_codex*`(openhands venv / 真 agent)。
- CI 用 `ARCP_CONFIG=config.example.yaml`(避免依賴本機才有的 openhands venv)。
- 腳本/設定/vendored/runner 由 `arcp.paths` 以 repo-root 相對解析,測試不綁 cwd;
  少數 import 腳本的測試(`test_kpi` / `test_hotreload`)靠 `tests/_env.py` 把
  `scripts/` 放進 `sys.path`。

真 Jira 冒煙(讀寫,測後還原):

```bash
uv run python scripts/smoke_jira.py                            # 唯讀
uv run python scripts/smoke_jira.py --write --ticket SCRUM-XX  # 含寫入(改測試票再還原)
```

## 重跑 integration / E2E(KP2,與正式**整組隔離**)

integration/E2E 用**獨立的一組 config + runtime(DB)+ port**,跑幾次都
**不會碰正式的 `config.yaml` / `runtime/`(線上 DB)**。隔離開關:

| 資源 | 正式 | 整測 | 怎麼分 |
|---|---|---|---|
| 設定 | `config/config.yaml` | `config/config.test.yaml` | CLI `--config`(純檔名=config/ 下) |
| DB/events/workspaces | `runtime/` | `runtime-test/`(gitignore) | test config 的 `source.runtime_dir`(或 CLI `--runtime` 覆寫) |
| port(control/dashboard/form) | 8787/8788/8790 | 8797/8798/8799 | test config + `--port` |
| Jira project | 正式 project | **KP2**(模擬內網 workflow) | test config 的 project/jql |
| scripts / profiles / skills | `config/scripts|profiles|skills/` 共用(唯讀資產,分 subfolder 不打架) | 同左 | — |

**步驟**(三個終端或背景):

```bash
# 1. 起整測 poller(KP2 + runtime-test + port 8797/8799)
uv run python scripts/run_poller.py --config config.test.yaml -m 30
# 2. 起整測 dashboard(8798)
uv run python scripts/detail_server.py --config config.test.yaml \
    --runtime runtime-test --port 8798 --host 127.0.0.1
# 3. REST integration(T1 完成流 / T2 job 分流 / T3 cancel / T4 審批 Pending;
#    T5 安全掃描需 scanner 已裝、T6 審批門放行全程 → 手動指定)
uv run python tests/it_kp2.py            # 預設 T1–T4
uv run python tests/it_kp2.py T5 T6      # 進階測項
# 4. browser E2E(看畫面,REST 驗不到的):照 tests/e2e_kp2_browser.md 逐項
```

注意:
- **會花錢**(真 agent,haiku 一輪 T1–T4 約 $0.1–0.2)與**真開 KP2 票**(標題帶
  `[it]`/`[job]`,測後留在看板供對照,可批次 Cancel)。
- agent-job(`kp2-tasks`,count=1)首輪自動 fire 一次;重測要歸零水位:
  `sqlite3 runtime-test/harness.db "DELETE FROM trigger_state WHERE name='kp2-tasks'"`。
- 正式與整測**可同時跑**(port/DB 全分離);唯一共用是 Jira 憑證(`~/.env`)
  與唯讀資產(config/scripts 等)。
- 隔離驗證法(改完相關程式碼後):記下 `stat -f %m runtime/harness.db`,跑完
  整測再比對 mtime 不變 = 沒碰正式 DB。

## 重跑 E 群真環境驗證(E1 對照數據點 / E2 crash→resume)

E 群是**需要真 CLI/真 token 的環境級驗證**,不進 CI(CI 只跑離線
`test_*.py`);每次大改 rawcli/inner loop、或 CLI 升版後,照下面重跑。

### E1 — A/B/C × claude/codex 對照數據點(~3 分鐘,幾美分 + codex 訂閱額度)

```bash
# 四格:A-raw / B-OpenHands ACP × claude / codex(examples PoC 層)
cd examples/openhands-acp-poc && .venv/bin/python compare_run.py
# 四路:A(raw supervisor)/ B(agent-server)/ C(rawcli)/ C×codex(生產鏈路層)
uv run python scripts/compare_abc.py A B C C-codex
```

- 判準:全路 `done + grader PASS`;數據(事件數/保真行/成本)貼回
  `examples/openhands-acp-poc/COMPARISON.md` 對應表,**形狀該保持**:
  C 路同時有乾淨蒸餾語意層與原生全保真、B 路(ACP adapter)原生保真=0。
- codex 訂閱 quota 是**跨路線共用資源**,額度用罄兩路一起紅——先跑
  `codex exec "Reply: QUOTA-OK"` 探路再燒對照。
- 2026-08-13 實測基線:compare_run 四格全綠(A-claude 176 事件/B-claude 8/
  A-codex 33/B-codex 22);compare_abc 四路全綠(A 86/86、B 17/0、
  C 13/121、C-codex 18/26)。

### E2 — 長跑/大 context crash→resume(`tests/it_e2_resume.py`,~$0.03)

crash-safe 宣稱的硬證據:大 context 任務 killpg 後 native resume
**(a) context 傳承 (b) 不重工 (c) 完成剩餘步驟**。

```bash
uv run python tests/it_e2_resume.py                  # claude+codex 兩格
uv run python tests/it_e2_resume.py --engine claude  # 單格
uv run python tests/it_e2_resume.py --lines 5000     # 放大 context 深測
```

設計要點(讀懂 FAIL 時需要):facts.txt 埋 SECRET_TOKEN → phase 1 寫
memo.txt 即被 `fault_kill_on_file` killpg(sleep 30 是 kill 窗)→
**刪 facts.txt**(封死重讀,token 唯一來源=session context)→ phase 2
resume 只說「繼續」→ 驗 final.txt=token 反轉、memo.txt mtime 不變。
phase 2 只給 Write/Read 工具:claude 曾把「sleep && echo > final.txt」
丟後台就收工,CLI 退出後台命令跟著死 → 誤判(不是 resume 壞)。

2026-08-13 實測基線:claude(haiku)與 codex 兩格皆 **6/6 PASS**
(codex 亮點:killpg 後 thread id 仍擷取得到,`codex exec resume` 同樣
傳承 context;dur ~49s/格)。

30 分鐘級超長跑:`--lines 50000` 並把 P1 的 sleep 拉長;**不用
caffeinate**(使用者明令)——接電源跑,異常先查 `pmset -g log`
(筆電睡眠會凍結計時器產生假 stall)。

## 加一個 backend

1. 在 `inner_*_runner.py` 加執行單元,產出符合 `contract` 的 envelope。
2. profile 的 `agent.backend` 指到它;dispatcher/grader **不用改**(契約不變)。

## 加一個 profile

在 `config.yaml` 的 `inner_loop.profiles` 加一項(見 `config.example.yaml` 範本):
`agent`(backend/engine/model/sandbox)、`verify`(確定性檢查)、`loop.max_attempts`、
`goal` / 預算 / `human_minutes_est`;再在 `outer_loop.routes` 加比對規則指到它。

`verify` 每步 `files` / `cmd` / `json` 擇一(grader 對應 `FileChecklistGrader` /
`CommandGrader` / `JsonGrader`,`AllOf` 組合)。build/test/lint = `cmd` 型別;`json`
(C1)= JSON 檔的形狀檢查(存在+可解析+必要鍵/型別),見 `tests/test_grader.py`。

profile 收尾政策 `auto_close: off|on_success|all`(`ScoreGate._auto_close`):自動關時
`human_score=agent_score`(contract `score`)、`transition("done")`、journal `closed(by=auto)`,
outcome 保留、不覆寫 handoff。與 `require_approval` 是人機光譜兩端。見
[design/agent-output.md §9](design/agent-output.md)。

## A/B 測試 / 自動選 profile

首次派工可自動選一個 profile(random 限同族;script 可回任何已定義 profile,並可遞歸至葉)。實作在
`src/arcp/selection.py`(`select_profile`),接線在 `dispatcher.handle` 的**首次派工**分支
(`sess is None` 且 main profile 有 `select`):選中的 profile 會 寫入 session,resume 不
重選。設定(`select` 區塊 random/script 範例)、fail-safe、與 triage 的關係、觀測方式見
[design/selection.md](design/selection.md)。

## 服務 CLI 參數

一律用 `uv run python scripts/<script>.py` 執行;兩支都 argparse、支援 `-h`、**無位置參數**
(全 flag)、**不讀 env**(除 `--log-level` 等同 `ARCP_LOG_LEVEL`):
- `run_poller.py`:`-m/--minutes`(`-m 0` = 無限常駐,靠外部排程 / Ctrl-C / `POST /shutdown` 停)、
  `-i/--interval`、`--control-port`、`--form-port`、`--log-level`。
- `detail_server.py`:`--port`、`--host`(`--host 127.0.0.1` 鎖本機)、`--runtime`、
  `--control-url`、`--log-level`。

## CI / CD

- `.github/workflows/ci.yml`:push/PR → Python 3.10–3.13 矩陣 → `uv sync --extra dev`
  → `ruff check` → `uv build` → 離線測試。
- `.github/workflows/cd.yml`:打 tag `v*` → `uv build` → GitHub Release 附 wheel/sdist
  (尚未發 PyPI)。

## 慣例

- 每個工作階段(wave)單獨 commit;commit 訊息帶 Why。
- 新需求/決策**先更新 [requirements.md](requirements.md)**(保存 Why),再動工。
- 核心套件 `src/arcp/` 維持 ruff 嚴格 clean。
- 貢獻流程見 [CONTRIBUTING](../CONTRIBUTING.md)。

## 已知限制 / 除錯 FAQ

- **強制中斷(evict / 指令台 `hold`)是立即 killpg,不是優雅停**:進行中的工具步驟會被
  硬殺。**不丟資料** —— 下輪 native resume 會從 session 接回、重跑被砍的那一步(檔案系統
  真值 + grader 保證正確)。未做「SIGTERM→10s→SIGKILL」優雅停,因 native resume 已保進度、
  grace 效益低。**debug 時若看到某工具步驟在 resume 後重跑一次,這是預期現象**,非 bug。
  設計見 [interaction §13.4](design/interaction.md)。
