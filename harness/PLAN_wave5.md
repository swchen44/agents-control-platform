# PLAN_wave5 — 韌性收尾 + 後續候選

> 承 W4(M15/M16 + dashboard v2 + hot reload 文件)。W5 起採「單項小步」:
> 每項獨立 checklist、獨立 commit,不再整波綁定。

## W5 設計決策

| # | 決策 | 理由 |
|---|---|---|
| W29 | **sid 預派(claude)**:dispatcher 於 attempt **開跑前**持久化 `attempts+1` 與預派 session id(uuid),journal `attempt_started`;runner 收 `preassigned_session_id`(claude `--session-id`;codex thread id 由 CLI 自生無法預派) | 冪等盤點 #5:harness 於 attempt 中途死 → 原本重跑重花錢;預派後 claude 可 native resume 續跑;快照器首 attempt 也拿得到 sid |
| W30 | **crash 偵測** = `attempts>0 且 a{attempts}.envelope.json 不存在`(envelope 驅動,與三態同一philosophy):claude+sid → **退還該 attempt** + resume 續跑(transcript 重放不重工);codex/無 sid → **UNKNOWN 交人**(不能證明副作用) | 「loop on evidence」:有證據能續就續,不能證明就交人 |

## Checklist

**W5.1 — sid 預派 + crash 偵測(W29/W30)** ✅
- [x] inner_runner `run_attempt(..., preassigned_session_id=None)`;job 傳遞;
      rawcli runner:`session_id = resume or preassigned`、`resume=bool(resume)`
- [x] dispatcher while 迴圈:attempt+1 與預派 sid(rawcli+claude 且無 sid)
      **先 upsert 再 spawn**;journal `attempt_started`
- [x] dispatcher 進場 crash 偵測(`a{N}.envelope` 缺):**有 sid(任一引擎,
      codex resume 亦已實證)→ 退還 attempt + resume**;無 sid(codex 首跑)
      → UNKNOWN 交人 + comment(W30 較計畫放寬:sid 有無為準,非引擎為準)
- [x] snapshotter 限制解除(claude 首 attempt 即有 sid);DESIGN_idempotency
      #5 標已修(含殘邊角:persist→spawn 間 crash → resume 失敗以 error 收場,
      機率極低接受)
- [x] 測試:test_idempotency +3(預派 spawn 前已落 store、crash 有 sid 退還
      resume、無 sid UNKNOWN)全綠;9 個 fake fork 簽名 +**kw 同步;
      20 測檔 + selftest + e2e_gate/dashboard 全綠
- [x] commit+push

**W5.2 — dashboard 加欄(使用者 2026-08-06)** ✅
- [x] 停留時間(state/assignee 最後變動起算、close 凍結)、lifetime(create→
      close/現在)、人力$(預估×時薪連動);皆可排序;/data 加 last_change;
      server 初始渲染共用 build_data;e2e 31 checks

**W5.3 — E3 evict/實時 killpg** ✅
- [x] RawCLIAgent `evict_file` 看門狗(鏡射 stall watchdog:1s 輪詢 EVICT 檔
      → killpg CLI 進程組);runner `error_kind=evicted`;inner_runner 每
      attempt 帶 `attempts/EVICT` 約定路徑 + spawn 前清殘留
- [x] dispatcher:evicted **不消耗 attempt**、session 留 active(sid 在,
      下輪 native resume);comment + journal。與交人組合:下輪 external
      policy 標 inactive 自然擋住
- [x] 觸發 = `POST /evict/<iid>`(control API 線程——poll 於 attempt 期間
      阻塞,唯 control 可即時);active 才准(終態/哨值 404);ticket 頁
      Evict 按鈕
- [x] 測試:test_evict 3(端點寫檔/404/400、dispatcher 退還+resume、路徑
      約定)+ **e2e_evict 真 killpg 4/4**(sleep 90 於 t+9.3s 被終結、
      副作用中斷、error_kind=evicted);21 測檔全綠
- [x] DESIGN_lifecycle §6 標已落地(含 assignee 自動即時 kill 的同步 poll
      限制註記);commit+push

**W5.4 — openhands 系 codex 對照(使用者 2026-08-06 選做)** ✅
- [x] `e2e_codex_openhands.py`:backend openhands-acp / openhands-server ×
      acp_server=codex(SDK 釘死表 → `codex-acp@1.1.2` adapter;acp_model
      不給用帳號預設)真跑 filechain → **兩路皆 completed + session_id +
      grader 3/3**;cost $0(訂閱制不回報,best-effort 不判分)
- [x] **三 backend × 雙引擎 6 格矩陣全綠**(rawcli/acp/server × claude/
      codex 同 envelope 契約)——2026-08-03 被 quota 擋下的最後兩格補完;
      COMPARISON.md 補記
- [x] commit+push

**W5.5 — rawcli 脫離 OpenHands 依賴(使用者 2026-08-06)** ✅
- [x] `arcp_rawcli/agent.py` 重寫**純 stdlib**:去 AgentBase/Conversation/
      pydantic/LLM 鷹架;`step(conversation,…)` → `run(prompt, ws, on_event)`;
      `_emit` 產 dict(舊 SDK MessageEvent 的 JSONL 子集,dashboard 讀
      kind/source/llm_message.content 零改)。建指令/watchdog(stall/evict/
      fault)/ingest(claude+codex)/seatbelt 全數 stdlib 保留不動
- [x] `inner_rawcli_runner.py`:去 `Conversation`/`send_message`,直呼
      `agent.run()`;capture 直接 json.dumps(event 已是 dict)
- [x] venv 選配:inner_runner rawcli 無 venv → 系統 python(`sys.executable`);
      routes.yaml 四個 rawcli profile(filechain-rawcli/codex/approval-demo/
      handoff-demo)移除 venv;openhands-acp/server 仍需 venv
- [x] 真跑驗證(系統 python、零 OpenHands):claude(ok.txt+6 events 同形)、
      codex(step1.txt)、e2e_evict 真 killpg 9.1s;20 測檔 + selftest 全綠;
      ruff clean
- [x] **成效**:rawcli 主線不再需要 591MB openhands venv(196 套件);
      agent.py import 只剩 datetime/json/os/signal/subprocess/threading/
      time/uuid。commit+push

**W5.6 — dashboard 匯出 + SQLite 瀏覽器(使用者 2026-08-06)** ✅
- [x] 表格匯出 **CSV / JSON**(經 filter+sort 的資料,client-side Blob 下載;
      timestamp 轉 ISO、dwell/lifetime 天數、human_cost 依時薪)
- [x] **DB Browser tab**(頂部導覽 Dashboard/DB):左側表清單(名+筆數)、
      點表看內容(分頁)、**唯讀 SQL 查詢框**(⌘/Ctrl+Enter;SELECT/WITH/
      PRAGMA + 單語句 + `mode=ro` 連線三重保護,寫入引擎層擋);null 灰字、
      水平捲動
- [x] 端點:`GET /db/tables`、`GET /db/table/<name>`、`POST /db/query`
      (唯讀連線讀 WAL 實證;write→404 error);e2e_dashboard +7 全 PASS;
      Chrome 實測:表內容渲染、GROUP BY 聚合查詢、匯出按鈕
- [x] commit+push

**W5.7 — dashboard 表格欄寬可拖曳(使用者 2026-08-06)** ✅
- [x] 共用 `resizable(table,key)`(兩頁載入):表頭右緣 7px 拖把 → 拖動調欄寬;
      維持不變式「表寬=各欄寬總和」(`table-layout:fixed` 才不被瀏覽器重分配),
      拖寬即整表加寬(水平捲動)、鄰欄不動
- [x] 兩張表都套用:index Tickets(`#tix`,card 加 overflow-x:auto)+ DB
      Browser(表內容 + 查詢結果);cells nowrap+ellipsis(長值截斷可拖寬看)
- [x] 寬度存 **localStorage**(跨整頁重載留存)+ 凍結進 store(re-render/5s
      刷新不重置);拖把 stopPropagation(不誤觸排序)
- [x] e2e_dashboard +2(兩頁 resizable 存在);Chrome 實測:拖 status 欄變寬、
      鄰欄不動、經 5s 刷新仍保持;ruff clean。commit+push

## 後續候選(未排程,擇需)

- ~~E3 evict/實時 killpg~~ → W5.3 完成;剩 rehydrate 對照(異步架構再議)
- ~~openhands 系 codex 對照~~ → W5.4 完成
- ~~rawcli 脫 OpenHands 依賴~~ → W5.5 完成(openhands-acp/server 仍選配)
- openhands-acp/server 的 codex 對照(quota)
- landlock / docker 隔離實作(W22 介面已就緒)
- 量產 python 標準結構(另開 repo)
- dashboard 後續 UI(shutdown 按鈕等,湊批再做)
