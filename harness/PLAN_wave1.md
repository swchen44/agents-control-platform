# PLAN_wave1 — 地基:provision + 資源閘門 + 結構化契約 + 限速/花費保護

> 承 BACKLOG「下一階段 high」的 **Wave 1**(F1/G1/A3/A4)+ DESIGN_lifecycle 的
> provision 前置(§1/§2/§5)、F1 額度語意(§6)。**單線、小步、每步 commit+push。
> 斷線 resume:讀本檔 checklist + git log。** 最後更新:2026-08-04。
>
> 現況錨點(已核對):
> - `routes.yaml` `outer_loop.concurrency.max_running: 4`(全局隱式,ThreadPool 排隊)
> - `poller.py OuterLoop.poll_once`:watch 序列 + dispatch 並行;assignee_changed →
>   `external.on_assignee_changed` 已在
> - `dispatcher.py Dispatcher.handle`:get_session → provision → while attempts →
>   grade;`sess.cost_usd += res.cost_usd`;infra→pending:external 回滾 attempt
> - `inner_runner.py run_attempt`:job dict → venv runner → envelope-driven
>   `AttemptResult(raw_outcome/session_id/cost_usd/error_kind)`
> - `profiles.py Profile`:workspace_template/workspace_folder/skills/agent/verify/
>   max_attempts/on_unknown;`workspace.py provision`:root/folder.format(issue_id)/ws

## W1 目標(5 塊)

| 塊 | 做什麼 | DESIGN 對應 |
|---|---|---|
| **W1.1 provision** | template folder 整包複製 + resume-safe 命名 | §1/§2 |
| **W1.2 A3 rate limit** | Jira 寫入撞限速指數退避 | BACKLOG A3 |
| **W1.3 A4 budget** | profile `max_budget_usd`,超支→pending:budget | BACKLOG A4 |
| **W1.4 G1 契約** | system prompt + `{reason,status,next}` schema + envelope 帶回 | §4.2 |
| **W1.5 F1 閘門** | 分層額度(全局+per-engine+per-profile)+ QUEUED + **inactive 不占額度**語意 | §6 |

## W1 設計決策(沿用 v5/DESIGN;新標 W)

| # | 決策 | 理由 |
|---|---|---|
| W1 | 命名 `tickets/{agent}__{key}__{issue_id}/ws`,issue_id 為不變尾綴 | DESIGN §2:native resume 綁 cwd,path 不可變;summary 不入 path |
| W2 | template 複製:`shutil.copytree(template, ws)` 到臨時目錄再 rename,失敗清理標 infra | DESIGN §10:原子性,中途失敗不消耗 attempt |
| W3 | A3 退避只包 Jira **寫**(add_comment/transition),讀不退避(冪等 poll 自帶重試) | N8;寫是稀少而關鍵,讀有 watermark |
| W4 | A4 用既有 `sess.cost_usd` 累計;超支 = `pending:budget`(人解除,類 UNKNOWN) | cost 已在 envelope;on_unknown=pending 同款語意 |
| W5 | G1 schema 由 profile `agent.output_schema` 開關;claude `--json-schema`、codex `--output-schema`;runner 寫進 envelope `structured` | §4.2 與 description YAML 同源;向後相容(不開=現狀) |
| W6 | G1 的 `next`(換手)W1 **只解析+記 comment**,實際換手留 W2 F3 | 波次邊界:W1 立 schema,W2 接換手通道 |
| W7 | F1 閘門:dispatch 前查 store 「in-flight」數(該 engine/profile 正在跑的 session);額滿→標 QUEUED 不派。**顯式隊列取代 ThreadPool 隱式排隊** | DESIGN §6;F2 可視化(W2)接這個狀態 |
| W8 | F1 in-flight **不計** outcome 終態/`pending:*`/`inactive` 的 session | §6:不在機器人手上=inactive=不占額度;等審批/等人=不占 |
| W9 | F1 挑選:poll 每輪按 FIFO(created/入隊序)挑能跑的 → 與現架構一致 | BACKLOG F2 決策點:poll 挑 vs 事件驅動 → 前者 |

## Checklist

**Phase W1.1 — provision:template 複製 + resume-safe 命名**
- [ ] `profiles.py`:`workspace_template` 允許值除 `empty` 外加 **template folder path**
      (相對 harness 根;load 時檢查存在,不存在→ConfigError fail-fast)
- [ ] `workspace.py provision()`:template 是路徑時 `copytree` 到臨時目錄再 rename 進 ws
      (W2 原子性);`empty` 維持現狀(空建+注入 skill,向後相容)
- [ ] 命名:`workspace_folder` 預設改 `tickets/{agent}__{key}__{issue_id}`;`provision`
      的 `.format(...)` 傳 agent(profile.name)/key/issue_id 三參(缺省相容舊 `{issue_id}`)
- [ ] `dispatcher.handle`:`provision` 呼叫點傳齊三參(現在只有 ticket/profile,加 key)
- [ ] **單元測 `test_provision.py`(免 token)**:P1 template 路徑→ws 內容等於 template
      複本;P2 命名格式 `myagent__PROJ-1__10042`;P3 empty 仍空建+skill;P4 template
      不存在→ConfigError
- [ ] commit+push

**Phase W1.2 — A3:Jira 寫入限速退避(N8)**
- [ ] `jira_source.py`:抽一個 `_write_with_backoff(fn)` 包 add_comment/transition;
      HTTP 429 或 5xx → 指數退避(如 1/2/4/8s,上限 5 次)重試;讀不動
- [ ] 退避可配:routes.yaml `outer_loop.source.write_retry: {max: 5, base_sec: 1}`
- [ ] **單元測 `test_ratelimit.py`(免 token,mock 429)**:R1 429 兩次後成功→總呼叫 3
      次、有退避;R2 連續 5 次 429→放棄拋錯(不無限);R3 200 一次過→不退避
- [ ] commit+push

**Phase W1.3 — A4:budget 花費上限**
- [ ] `profiles.py`:Profile 加 `max_budget_usd: float | None`(default None=不限)
- [ ] `dispatcher.handle`:每次 attempt 後 `sess.cost_usd` 累計後檢查;超 `max_budget_usd`
      → `outcome=None, pending_reason="budget"`,add_comment(帶累計 cost + resume_hint),
      journal `pending reason=budget`,return(不再 attempt)
- [ ] pending:budget 與 UNKNOWN 同款:只有人能解除(command channel `run`/`retry`)
- [ ] **單元測 `test_budget.py`(免 token,喂假 cost)**:B1 累計未超→續跑;B2 一次 attempt
      後超標→pending:budget、不再 attempt;B3 max_budget_usd=None→不檢查
- [ ] commit+push

**Phase W1.4 — G1:agent 結構化契約 {reason,status,next}**
- [ ] 定 schema(`arcp_harness/contract.py`):`{reason:str, status:enum[done/failed/
      need_human/handoff], next:{to:str|null, kind:enum[agent/human]}|null}`
- [ ] profile `agent.output_schema: true` 開關;`inner_runner.run_attempt` job 加
      `output_schema`;rawcli runner 傳 claude `--json-schema`/codex `--output-schema`
- [ ] runner 把 agent 的結構化輸出寫進 envelope `structured`;`AttemptResult` 加
      `structured: dict | None`
- [ ] `dispatcher`:有 structured 時,`reason` 併入 Jira comment;`status` 供參(不覆寫
      grader 的證據判定,G2 精神——grader 仍是終審);`next` **只 journal + comment 記錄**
      (實際換手 W2 F3)
- [ ] **單元測 `test_contract.py`(免 token)**:C1 schema 驗證合法/非法輸出;C2 envelope
      有 structured→dispatcher comment 含 reason;C3 無 output_schema→行為同現狀
- [ ] **E2E(少量 token)**:一張 rawcli 票開 output_schema,claude `--json-schema` 真跑,
      envelope structured 三欄齊全
- [ ] commit+push

**Phase W1.5 — F1:分層資源閘門 + QUEUED + inactive 語意**
- [ ] `routes.yaml` concurrency 擴展:`{max_running, per_engine:{claude:N,codex:M},
      per_profile:{<name>:K}}`;loader(routing.py)透出三層
- [ ] `store.py`:加 `count_in_flight(engine=None, profile=None)` —— 數 outcome 非終態
      **且** pending_reason 為空 **且** 非 inactive 的 session(W8)
- [ ] `TicketSession` 加 `queued: bool` + `queued_at`(FIFO 排序用);`inactive: bool`
      (W1 先加欄位;實際置位由 W2 assignee 監看 killpg 時設)
- [ ] `poller.poll_once`:dispatch 前對 `to_dispatch` 按 queued_at/created FIFO 排序,
      逐張查三層閘門(全局+該 engine+該 profile);**能跑的才進 ThreadPool**,額滿的標
      `queued=True` 不派、journal `queued`(位置資訊留 F2)
- [ ] **單元測 `test_gate.py`(免 token,假 session)**:G1 per_engine claude=1→第二張
      claude 排隊、codex 不受影響;G2 inactive/pending session 不計 in-flight(W8);
      G3 額度釋放後下輪 FIFO 挑最早的
- [ ] **E2E `e2e_gate.py`(rawcli filechain,可少 token 或假 runner)**:3 張同 engine、
      per_engine=2 → 同時最多 2 跑、1 QUEUED,下輪補上;store 無損
- [ ] commit+push

## W1 明確不做(留 W2/後續)

- **審批門主體**(DESIGN §4 貼計畫/填表 YAML/退回迴圈)→ W2(依賴 F3 換手通道)
- **assignee→inactive 的實際 killpg/resume 觸發**(DESIGN §6)→ W2(W1 只立
  `inactive` 欄位 + 閘門不計語意)
- **QUEUED 可視化/排隊位置/總覽儀表板**(F2/C4)→ W2
- **G1 的 next 實際換手**(W6)→ W2 F3
- **無票 scheduled/oneshot 觸發源**(DESIGN §5)→ 可 W1 尾聲或 W2(provision 命名已
  預留 run-name 分支;觸發器另立)

## W1 里程碑

**M10 = 地基就緒**:能從 template 複製出 resume-safe 命名的 workspace、Jira 寫入抗限速、
花費有上限、agent 回結構化 `{reason,status,next}`、資源閘門分層限流且認得「不在機器人
手上=不占額度」。**至此 W2(審批門 + assignee 換手 active/inactive + 排隊可視化)有地基可接。**
