# PLAN_wave3 — codex 驗證 + 冪等分層 + 生命週期收尾 + KPI + 隔離設定檔

> 承 W2(M11/M12)+ 真 Jira 實測(TEST_real_jira.md,SCRUM-20/21/22 全 PASS)。
> 範圍 = 使用者 2026-08-05 圈選:D2 codex、A2 冪等分層、retention/scheduled、
> C3 人力估算、D1 隔離設定檔(全選)。
> **單線、小步、每步 commit+push。斷線 resume:讀本檔 checklist + git log。**
>
> 開發約定(W2 延續):測試 `test_*.py` pytest-compatible+自跑、免 token;
> e2e 花 token 的另立 `e2e_*.py`;核心 `arcp_harness/` ruff 嚴格 clean。

## W3 設計決策(新標 W)

| # | 決策 | 理由 |
|---|---|---|
| W17 | codex 驗證只走 rawcli(`engine: codex`),G1 用 `--output-schema <FILE>`(W1.4 已鋪);openhands-acp/server 的 codex 對照**不在本波**(quota/優先級) | D2:token 已可用;rawcli 是一級公民,先把第二引擎立起來驗「同 envelope 契約」 |
| W18 | A2 冪等 = **分層**:agent 層靠 native resume transcript 重放(現成);harness 層自有機制——先**盤點 crash 窗口**(每個「寫外部 + 寫 store」的間隙),再對高風險路徑補防重(comment 冪等 key、attempt 標記),用 fault-injection 驗 | 使用者:「有些利用 agent transcript,有些要自己的機制」;先盤點再補,不盲加 |
| W19 | retention:profile `retention_days`(default 270,config 可調);poller 每輪輕量掃(終態 + 過期 → 刪 workspace 目錄;store/journal 留著稽核);刪除記 journal | DESIGN §3(Q2 定案);工作區可拋、證據不可拋 |
| W20 | scheduled/oneshot = **內部觸發源**,不經 Jira 票:`triggers:` config(cron-like `every:` + oneshot CLI);**要求 run name**;folder=`{agent}__{run_name}__{timestamp}`;走同一條 provision→(審批略過)→fork 管線;結果進 journal/dashboard(無 Jira 面) | DESIGN §5(Q6 定案);審批門綁 Jira description,內部觸發無票面→不審(config 即授權) |
| W21 | C3 KPI:per-profile `human_minutes_est`(人做同任務的估時,config 給)+ 公式推算(可用 attempts/工具事件數修正);SUCCESS 時 journal 記 `human_minutes_saved`;dashboard 總覽卡加「節省人時 / 成本對比」 | 使用者:「計算若人作要花多久,可以用公式推算」;先簡單 config 估值,公式可迭代 |
| W22 | D1 隔離 = **設定檔介面先行,不實驗**:`agent.isolation: {provider: auto\|seatbelt\|landlock\|appcontainer\|docker, ...}`;`auto`=依 OS 選提供方;現行 `os_sandbox: true` 映射為 `provider: auto` 向後相容;docker 只留介面與文件 | 使用者:「先不驗,未來 linux/windows/macos,os 提供方優先,docker 給選項」 |

## Checklist

**Phase W3.1 — D2 codex 真跑驗證(rawcli 第二引擎)** ✅
- [x] routes.yaml `filechain-codex` route+profile(engine: codex、sandbox:
      workspace-write、**model 不填=帳號預設**——runner 修正:model 預設改
      engine 相依,haiku 只給 claude,不再塞給 codex)
- [x] `e2e_codex.py` 8/8 真跑 PASS:envelope 同形(completed/session_id=
      thread_id/truly_resumed)+ **native resume 續同 thread**(a2 靠 session
      上下文建 step2.txt=12);cost=None(codex 訂閱制不回報金額,契約允許)
- [x] `e2e_contract.py` 擴雙引擎:claude 4/4 + codex 4/4 PASS。實測揪出並修:
      (a) **CONTRACT_SCHEMA 改 OpenAI strict 形狀**(巢狀 object 都
      additionalProperties:false + required 列全欄位、選填用 nullable)——
      codex 後端否則 400 invalid_json_schema;claude 對超集合也接受,雙引擎共用;
      (b) **瞬態 error 不污染 envelope**:codex stream 斷線 Reconnecting 3/5
      自動救回後 turn.completed 到達 → 清 _error(turn.failed 終態不受影響)
- [x] 真 Jira 冒煙:略(選做;dispatcher 鏈路已由 claude 版真 Jira 三票驗證,
      envelope 同形即等價)
- [x] commit+push

**Phase W3.2 — A2 冪等分層(關一半副作用)** ✅
- [x] **盤點文件 `docs/design/idempotency.md`**:9 條路徑 × crash 窗口 × 防護/判定
      (at-most-once / at-least-once+冪等 handler);缺口只剩 #5 attempt 中途
      harness crash(sid 預派 + attempt_started → 缺 envelope 判 UNKNOWN,留 W4+)
- [x] 盤點結論:dispatcher 全部「先 upsert 再外寫」= at-most-once **本來就對**
      (W1 排序即防護,測試固化);真缺口在 **approval.gate**——外寫在 gate 內、
      session 靠 dispatcher 返回後才持久化 → 修:gate 每次變更先 upsert 再外寫
      (revisions/escalate 上限跨 crash 有效);首貼冪等 key = control 段存在
- [x] `test_idempotency.py`(4 tests):終態重跑不重派不重留言、revisions 跨
      crash 持久 + escalate 上限有效、首貼不重貼、指令重放狀態冪等 —— 全綠
- [x] commit+push

**Phase W3.3 — retention 回收(W19)** ✅
- [x] profile `retention_days`(default 270;`0` = 不回收)
- [x] `arcp_harness/retention.py`:掃終態(含 UNKNOWN)+ 過期 → 刪**整個
      instance**(base 含 ws/ + attempts/;journal/store 留稽核);刪後
      workspace 置哨值 `(reclaimed)`(retry 時 health 失敗→重 provision);
      哨值路徑安全跳過;profile 不在(改名)→ default 270
- [x] store `finished_at`(migration ALTER):**由 upsert_session 統一蓋章**
      (終態自動 stamp、outcome 清空自動歸零——所有寫入路徑免各自記)
- [x] poller 首輪 + 每 240 輪(≈每小時)輕量掃;失敗不擋 poll
- [x] `test_retention.py`(8 tests):蓋章/歸零、過期刪整 instance、未過期留、
      0 不回收、非終態不動、哨值安全、未知 profile 用 default、舊庫 migration
      —— 全綠;全套 13 測檔 + selftest + e2e_gate 無回歸
- [x] commit+push

**Phase W3.4 — scheduled/oneshot 內部觸發源(W20)** ✅
- [x] routes.yaml `outer_loop.triggers`(name/profile/run_name/every N[mhd]/prompt;
      樣例註解入檔)+ oneshot CLI `run_trigger.py <名>`(忽略 every/last_run)
- [x] `arcp_harness/triggers.py`:fail-fast load(run_name [a-z0-9-] 防注入、
      profile 存在、every 格式)、due 判定(store trigger_state 水位;oneshot
      永不自動 due)、run_trigger 迷你派工:**pseudo-Ticket(id=timestamp、
      key=run_name)整包重用 provision** → folder 自然 = `{agent}__{run_name}__{ts}`
      + TICKET.md 渲染 prompt;證據迴圈同 dispatcher 語意(SUCCESS/FAILURE/
      UNKNOWN 不自動重試);**先記水位再跑 = at-most-once**(呼應 W3.2);
      session 存 TicketSession(timestamp id 不與 Jira 衝突)→ dashboard 可見、
      retention 照收
- [x] poller 每輪檢 due;與票**共用 F1 額度**(global+per-engine,額滿跳過本輪
      不記水位);paused 不跑;單一 trigger 壞不擋 poll
- [x] `test_triggers.py`(7 tests):載入/校驗、every 單位、due/oneshot、命名+
      水位冪等、失敗證據迴圈、UNKNOWN 停、額滿跳過 —— 全綠;14 測檔全綠
- [x] commit+push

**Phase W3.5 — C3 KPI 人力估算(W21)** ✅
- [x] profile `human_minutes_est`(選填);SUCCESS 時 journal `human_minutes_saved`
      (公式 v1:est 平計;dispatcher `resolved` + trigger `trigger_finished`
      都記;無 est 不加 key);routes.yaml filechain-rawcli 示範 est=15
- [x] dashboard:`saved_minutes(journal)` 彙總(只算 SUCCESS 事件)+ 總覽卡
      「節省人時」(顯示小時);時薪 env `ARCP_HOURLY_RATE` 選配 → 加
      「人力成本對比」卡(人力$ vs agent$);未設不顯金額
- [x] `test_kpi.py`(5 tests):dispatcher/trigger 記錄、無 est 不記、彙總只算
      SUCCESS、卡片與時薪對比 —— 全綠;e2e_dashboard 回歸 PASS
- [x] commit+push

**Phase W3.6 — D1 隔離設定檔介面(W22,不實驗)** ✅
- [x] `arcp_harness/isolation.py`:`requested_provider`(isolation 區塊優先,
      `os_sandbox: true` 映射 auto 向後相容)+ `resolve`(auto→darwin=seatbelt/
      linux=landlock 預留/win=appcontainer 預留;未實作→none+WARNING,接受設定
      不啟用);profiles loader 白名單 fail-fast
- [x] inner_runner 接線:`job.os_sandbox = (resolve(agent_cfg)=="seatbelt")`
      ——runner 端欄位不變,行為對現有 profile 完全等價(darwin 上 os_sandbox
      true → seatbelt 照舊)
- [x] `docs/design/isolation.md`:介面、各 OS 提供方路線表、docker 邊界(resume 綁
      cwd→volume 穩定路徑、CLI 憑證 mount、冷啟成本)、codex 例外(自帶 --sandbox)
- [x] `test_isolation_config.py`(8 tests):白名單拒絕、全 provider 可載、auto
      依平台、legacy 映射、未實作降級、seatbelt 限 darwin、顯式優先於 legacy、
      inner_runner 接線 —— 全綠;16 測檔 + selftest + e2e 全綠
- [x] commit+push

## W3 明確不做(留後續)

- openhands-acp / openhands-server 的 **codex 對照**(quota;rawcli 驗證優先)
- **真 docker 隔離實作**(W22 介面先行,使用者明示先不驗)
- **cron 表達式完整支援**(`every:` 級距夠用;複雜排程未來換)
- **C3 進階公式**(工具事件數/行數加權;v1 先 config 估值)
- 實時 killpg 長駐 agent(維持 W2 註記,異步架構未來)

## W3 里程碑

**M13 = 雙引擎 + 韌性**(W3.1/W3.2):codex 同 envelope 真跑、crash 窗口盤點+防重。
**M14 = 自主營運**(W3.3-W3.6):回收、定時/一次性任務、KPI 對比、隔離介面就緒。
