# HANDOFF — headless agent 自動化 / ARCP

> 給「在此目錄開的新 session」的接手文件,零上下文也能無縫接上。最後更新:**2026-08-12**。
> 本檔是**地圖**:逐波實作詳情 → `CHANGELOG.md` + `docs/history/PLAN_*.md`;
> 待辦/決策全景 → `BACKLOG.md`;設計正本 → `docs/design/`;文件總覽 → `docs/index.md`。

## 0. 一句話目標

讓 `claude -p`、`codex exec` 這類 **headless coding agent** 能長時間可靠執行、
**可觀測(trace)**、**可控制(control)**,並能由 **Jira 事件驅動**:watch Jira →
issue 符合條件 → 建工作資料夾 → 裝 skills → headless 啟動 agent → 監控/回寫。
專案名 **ARCP**(Agent Runtime / Control Plane)。**OpenHands 只是候選方法之一,不是前提。**

## 1. 現況地圖(2026-08-12,全部已實作、CI 綠)

**結構**:`src/arcp/`(套件)+ `scripts/`(入口:`run_poller.py` 主迴圈、
`detail_server.py` dashboard、`run_trigger.py`…)+ `tests/`(離線免 token)+
`config/`(config.yaml+templates+skills)+ `vendor/`(離線資產)+ `runtime/`(gitignore)。

**核心鏈路**:Jira poll → route/**select 遞歸 triage**(script 可回任何 profile;
`notfound`→ABORTED 轉取消)→ workspace 佈建(template+skills+TICKET.md)→
claude/codex headless(rawcli 純 stdlib 為主線)→ grader 證據驗證 → 三態+UNKNOWN →
帶證據回寫 → 冪等不重派 → retention 回收。

**健壯性**:並行 dispatch、stall watchdog(reset-on-progress→killpg→resume)、
`/evict` 實時 killpg(不耗 attempt)、sid 預派 crash 偵測、Jira 降級/恢復、hot reload。

**HIL(6 態模型)**:`todo/running/queued/HIL(Middle)/HIL(End)/aborted`;
一次性 token 表單(need_info/decision/score_and_close;assignee 恆定、@mention 通知);
起點審批門;a2a 跨票 base 交接;人類評分 0–10 + agent 自評;profile `auto_close`。

**指令台**(取代 @agent 留言,2026-08-10):綁票表單 + REST 共用 `apply_command`
(run/retry/hold/stop/cancel/next/**set_email**);依狀態動態選單;破壞性二次確認。

**budget**(2026-08-10):token+usd **6 層上限**(per-ticket soft/hard + 月/agent + 全站);
soft 破→自助增額表單、其餘→管理者 hot reload;每輪 attempt 前 precheck。

**主題 J**(2026-08-11):job 泛化(週期/單次 → 開真 Jira 票 + pinned session)、
label 全庫 `arcp.` 前綴規範、select 軸 B/遞歸、全文件+web `/concepts` 對齊。

**主題 K 身分門禁**(2026-08-12,K1–K6):description 契約 `email`(**可逗號多個**)
→ `session.owner_email_list`;HIL/指令台提交必填 email 且 `∈ owners / ∈ admin_emails /
== approver` 才放行(選填 opt-in);IP+資料稽核;`set_email` 整組取代(預填現值、
留空=解除門禁、re-tag 每位);開票把 approver 加 Jira watcher。正本
`docs/design/identity-gate.md`。

**C5 時間軸視覺化**(2026-08-12,粗看+細看,使用者原則:分兩層、網頁上必須有說明):
- **粗看 `/timeline`**(導覽 Timeline tab):全域跨票,每票一列**狀態色帶**
  (藍=執行中、黃=等人、紫=排隊、灰=間歇,沿用全站六態色票)+ 關鍵事件點
  (🎬⏸🔀💥👥、✔/✘ 終點);時間窗 24h/7d/30d/all + 關鍵字過濾;**點列開側欄摘要**
  (狀態/負責人/用量/執行時間/可操作表單連結)→「完整詳情」進細看。
- **細看 `/ticket/<id>`**:**Session 駕駛艙卡**(DB session 全欄位含負責人/soft 上限/
  評分/驅逐,schema 新欄不漏列 + 執行/等人時間 + **TICKET.md 內容**摺疊);
  事件時間軸抽屜(L3 對話+生命週期合一)疊**同色系色帶**。
- **兩頁都有「📖 怎麼看這張圖」說明卡**(圖例・操作・判讀範例,可摺疊記憶)。
  核心判讀:長黃段=卡在等人、藍/灰反覆=重試、多列同時藍=並行度高。
  區間推導=`lane_segments`(journal→狀態段,純函式);實測已在真資料驗證
  (SCRUM-22/23 等人 6 天的卡點一眼可見)。

**觀測**:dashboard 七頁(Dashboard/DB Browser/**Timeline**/Control/Agent Detail/
Server/Introduction,明暗雙主題、離線零 CDN);L0–L3 四層 trace;transcript 打包/
可視化;REST `/api/v1/tickets`(ref=key/id/CR id;`?q=&field=&mode=match|regex`
過濾——**dashboard 過濾列同款**:三關鍵字框+「🔤 Regex」checkbox,預設一般字串
不分大小寫)+ vendored Swagger `/docs`;journal 事件字典(`gen_event_dict.py --check` 入 CI)。

## 2. 已敲定的決策(勿再問)

| # | 決策 |
|---|---|
| D1-D8 | 研究/PoC 期定案(報告在 `docs/research/`、raw 一級公民、PoC 在 examples/)——歷史,詳 git 版 HANDOFF |
| 路線 | **A/B/C 三線全實跑**;主線=**rawcli**(OpenHands SDK 骨架+raw CLI,後已脫依賴純 stdlib);openhands-acp/server 選配對照 |
| 版面 | description 分區段+hash:ARCP 區塊置頂、human 段前置、區塊外不碰 |
| HIL | 表單取代人編 description;assignee 恆定;Jira 異常=暫停/恢復非 work queue |
| 指令 | 人走表單、自動化走 REST,共用 apply_command;舊 @agent comment 通道已移除 |
| email | owner 首建鎖定,只由指令台 set_email 改(description 後續改不同步) |
| label | 入場券一律 `arcp.` 前綴;route/profile 名不在此列 |
| 用語 | 不用「pin」,說「寫入/鎖定 session 的 profile」;狀態是推導的、沒有 state 欄 |

## 3. 實測釘死的關鍵事實(踩過的坑,勿重踩)

- `claude -p` 有 `result` 終止事件;`codex exec` 沒有 → 雙判據;但 **codex SIGTERM
  退場 rc=0** → 完成與否只能靠證據型 grader(exit code 不可信)。
- `claude -p --session-id` 可預指定(crash resume 關鍵);codex thread id 事後擷取來得及。
- kill 必須 **killpg**(否則 zsh 子程序孤兒續跑);codex 非 tty 要 `stdin=DEVNULL`;
  `codex exec resume` 不吃 `--sandbox` 要 `-c sandbox_mode=`。
- claude session 綁啟動 cwd,workspace 搬家後原生 resume 死 → transcript 降級救回。
- macOS seatbelt 隔離:白名單勿放 `/private/tmp`(symlink 逃逸)。
- **筆電睡眠凍結計時器**產生假 stall/假 hang;**不用 caffeinate**(使用者明令,耗電)——
  長跑靠 run_poller 迭代 timebox;異常先查 `pmset -g log`。
- headless 無任何 permission mode 會掛住等核准——拒絕即時,盯 denial 事件。
- **background job 勿用 `/tmp`**(並行 job 互相 clobber → 假輸出/假 commit);
  暫存寫 `$CLAUDE_JOB_DIR/tmp`;git 狀態用 object store 真值驗證
  (`cat .git/refs/heads/main`、`git show HEAD:<檔>`、`git ls-remote`)。
- Jira 環境:Atlassian **Cloud** swchen44、project key=SCRUM、憑證 `~/.env`;
  **內網生產是 Data Center**(無 accountId)→ 相容已做(主題 L,`jira_flavor: dc`);整測用 KP2 project(SCRUM 不再用)。

## 4. 怎麼跑

```bash
# 主迴圈(Jira poll→派工;config/config.yaml)
uv run python scripts/run_poller.py
# dashboard(唯讀觀測;/timeline 粗看、/ticket/<id> 細看)
uv run python scripts/detail_server.py --runtime runtime --port 8788 --host 127.0.0.1
# 離線測試集(CI 同款;免 token)
for f in tests/test_*.py; do uv run python "$f"; done
uv run python tests/e2e_dashboard.py
ruff check .
# 研究期 PoC(examples/jira-agent-poc/,replay/selftest 免 token)——詳該目錄 README
```

pre-commit hook:`git config core.hooksPath .githooks`(ruff + 動 src/scripts 時
`gen_event_dict.py --check`)。

## 5. 下一步(全景見 BACKLOG.md)

- ~~主題 L~~ **已完成**(2026-08-12,L1–L7+auth:`jira_flavor: dc` 一鍵切換;內網首次上線照 `docs/dc-first-run-checklist.md` 逐項勾)。
- **I1 close→CQ 回寫**:設計定案、config 擴充點已留,等使用者給 CQ base_url+欄位名。
- (C3 KPI 框架與 A/B 對照已完成 2026-08-13;先前誤標的「E4 A/B 報表」實為此項——BACKLOG 的 E4 是 qm adapter spike,另案。)
  (C2 trace CI 與主題 H 文件自足**已完成**;D1 Docker 隔離**不做**——使用者
  決策 2026-08-12,隔離維持 seatbelt/--sandbox,provider 介面留著。)
- ~~e2e_commands flaky~~ **已結案**(2026-08-13:三個真 bug 全修,lesson #17)。
- KP2 整測:`--config config.test.yaml` 整組隔離;測項 `tests/it_kp2.py` T1–T8(developer-guide「重跑 integration/E2E」)。
- E 群真環境:**E1 codex 對照**(compare_run 四格+compare_abc 四路全綠,數據入 COMPARISON.md)與 **E2 crash→resume**(`tests/it_e2_resume.py` 雙引擎 6/6:context 傳承+不重工)已完成 2026-08-13;重跑手冊=developer-guide「重跑 E 群真環境驗證」。

## 6. 與使用者協作的規則(務必遵守)

1. 決策樹建模,系統性走訪每個分支/edge case。
2. **一次只問一個問題**,每問一題就暫停等回應。
3. 事實靠查閱(能從程式碼/環境讀到的絕不問),**只有決策才發問**。
4. 每個問題**都給選項**、附 AI 建議答案與理由(讓使用者審提案)。
5. **確認所有分支後才動工**,不提前寫碼/改檔。

## 7. 關鍵參考

- 使用者 v5 設計文件(Google Docs,含內部資訊不入 repo;memory
  `jira-harness-design-doc`);整合分析 `docs/research/2026-08-jira-harness-integration.md`。
- 研究報告 v3:`docs/research/2026-08-agent-runtime-control-plane-research-v3.md`;
  A/B/C 對照 `examples/openhands-acp-poc/COMPARISON.md`。
- 除錯路徑:`docs/ai-debugging.md` → `docs/troubleshooting.md` →
  `docs/design/observability.md` → `docs/lessons.md`。
