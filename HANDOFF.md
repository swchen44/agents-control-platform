# HANDOFF — headless agent 自動化 / ARCP

> 給「在此目錄開的新 session」的接手文件。前一段對話的 session 綁在
> `/Users/swchen.tw/git/openhands`(改名為「headless agent 自動化」)。本檔讓
> 零上下文的新 session 也能無縫接上。最後更新:2026-08-05。

## ★ 最新進展(2026-08-05)— 從研究進入分波實作

研究/PoC(§1-6)之後已進入**分波實作**。優先級與波次見 `BACKLOG.md`;橫切設計(審批門
+ assignee=資源開關 + template→workspace)見 `harness/DESIGN_lifecycle.md`。

- **W1 地基 ✅(M10)** `harness/PLAN_wave1.md`:provision(template→workspace、resume-safe
  命名 `agent__key__issue_id`)、A3 Jira 限速退避、A4 budget 上限、G1 agent 結構化契約
  `{reason,status,next}`(claude `--json-schema`/codex `--output-schema`,真跑驗過)、
  F1 分層資源閘門(全局+per-engine+per-profile+QUEUED+inactive)。6/6 綠。
- **W2 全部完成 ✅(M11+M12)** `harness/PLAN_wave2.md`:W2.1 logging+ruff baseline、
  W2.2 分區段+hash(**已按 2026-08-05 定案版面重構**:ARCP 區塊置頂+human 前置+結束
  標記+全掃描驗 hash 並 log+區塊外不碰)、W2.3 起點審批門(plan 寫分區段/填表放行/
  退回迴圈/escalate)、W2.4 assignee=資源開關(交人=inactive 讓出額度、回機器人=
  resume;bot 身份 config/myself() 解析;審批中不誤標)、W2.5 F3 換手(`@agent next`
  +G1 next 驅動;**session pin 優先於 route**;換手到審批 profile 重走門)、
  W2.6 REST 控制面(內嵌 daemon:/status /health /pause /resume /reload,hot reload
  壞 config 不死)、W2.7 web dashboard(狀態徽章+FIFO 排隊位+C4 總覽卡+控制列+
  審批卡;獨立只讀頁 8788,control 8787,CORS 打通)。11 個 test_*.py + selftest +
  e2e_gate + e2e_dashboard 全綠,ruff clean。
- **W3 全部完成 ✅(M13+M14,2026-08-06)** `harness/PLAN_wave3.md`:W3.1 codex
  第二引擎真跑(envelope 同形+native resume+G1 雙引擎契約;揪出並修 OpenAI
  strict schema、瞬態 error 污染 envelope)、W3.2 A2 冪等分層
  (`DESIGN_idempotency.md` 9 路徑盤點;approval gate 先持久化後外寫)、
  W3.3 retention 回收(finished_at store 蓋章、270 天、poller 每小時掃)、
  W3.4 scheduled/oneshot 觸發源(pseudo-ticket 重用 provision、
  `{agent}__{run_name}__{ts}`、F1 額度共用、run_trigger.py oneshot CLI)、
  W3.5 C3 KPI(human_minutes_est→節省人時卡+時薪對比)、W3.6 D1 隔離介面
  (provider 可插拔、`DESIGN_isolation.md`、介面先行不實驗)。
  16 個 test_*.py + selftest + e2e_gate/dashboard/codex/contract 全綠。
- **W4 全部完成 ✅(M15+M16,2026-08-06)** `harness/PLAN_wave4.md`:transcript
  可視化閉環 —— vendor claude-code-log(MIT,`tools/cclog/`+NOTICE;cchv 因
  export 丟 sidechain 棄用)、`render_transcript.py` wrapper(claude+**72
  sub-agent HTML** 實測+codex)、close 打包 transcript.tgz(gzip -9)+dashboard
  下載(`/tfile`)、快照器(active 每 60s 可設定+四個離手點 final 定格)、
  dashboard 分頁/filter/新欄位(assignee/created/finished/換手起點)+
  auto-collapse bug 修(meta refresh→fetch 局部更新)、script trigger 萬用化
  (uvx/npx/.sh/.py,log 保存+run.tgz,session 註冊全設施重用)。19 個
  test_*.py + selftest + e2e 全綠。
- **W4 真 Jira 實測 ✅ + W4.5-4.7 追加 ✅ + W5.1-5.3 ✅(2026-08-06)**:
  W4 全鏈路實測 PASS(SCRUM-23/24,cron script trigger/close 打包/KPI/離手
  定格/graceful shutdown);`DESIGN_hotreload.md`(reload 範圍表+關閉語意,
  缺口補強+POST /shutdown);cron 排程(W4.6);dashboard v2(W4.7:過濾器
  置頂+時間圖/金錢圖+排序,/data 單一資料源)+ 三欄(W5.2:停留時間/
  lifetime/人力$);**W5.1 sid 預派**(冪等 #5 關閉:attempt 前持久化+crash
  偵測 resume/UNKNOWN;production 實證 SCRUM-27);**W5.3 E3 evict/實時
  killpg**(POST /evict → EVICT 檔 → 看門狗 killpg → 不耗 attempt resume;
  e2e 真測 9.3s 終結 sleep 90;ticket 頁按鈕)。21 個 test_*.py + e2e 全綠。
- **W5.4-5.5 ✅(2026-08-06)**:W5.4 openhands 系 codex 對照(三 backend ×
  雙引擎 6 格矩陣全綠,COMPARISON.md 補記);**W5.5 rawcli 脫離 OpenHands
  依賴**——`arcp_rawcli/agent.py` 重寫純 stdlib(去 AgentBase/Conversation/
  pydantic,`run(prompt,ws,on_event)` 取代 step,事件 dict 同 JSONL 形狀
  dashboard 零改);rawcli 主線不再需要 591MB openhands venv(系統 python 即
  跑,claude/codex/e2e_evict 真跑驗過);routes.yaml 四個 rawcli profile 移除
  venv;openhands-acp/server backend 仍選配(需 venv)。live poller hot reload
  帶入零重啟。
- **恢復起點(剩餘候選)**:(a) landlock/docker 隔離實作(W22 介面已就緒,
  等搬 Linux/部署前夕);(b) 量產 python 標準結構另開 repo(需使用者定 repo
  名/公開與否);(c) 異步架構(assignee 自動即時 kill + rehydrate,大工程另議);
  (d) 進一步剝離:openhands-acp/server backend 若確定不用可整個移除(六格對照
  已存證,維護價值低)。
- **開發約定**:測試在 `harness/` 下 `test_*.py`(免 token、pytest-compatible、亦自跑);
  venv=`examples/openhands-acp-poc/.venv/bin/python`;lint `ruff check .`(核心套件
  `arcp_harness/` 嚴格 clean、舊腳本 per-file 放寬);每 phase 單獨 commit。
- **真 Jira 實測 ✅(2026-08-05,SCRUM-20)**:審批門完整鏈路一次通過 —— ADF 往返
  保真(人 UI 編輯後機器段 hash 仍符)、approver/human_email email→accountId 解析、
  填表放行、fork claude($0.0544)、SUCCESS、冪等、審批中資源開關不誤標。
  詳 `harness/TEST_real_jira.md` 結果表。未實測(單元測有蓋):退回迴圈、ghost
  email 退回、G1 handoff kind=human 指派。

## 0. 一句話目標

讓 `claude -p`、`codex exec` 這類 **headless coding agent** 能長時間可靠執行、
**可觀測(trace)**、**可控制(control)**,並能由 **Jira 事件驅動**:watch Jira
Server → issue 符合 assignee/keyword → 建工作資料夾 → 依 JSON 規則裝 skills →
headless 啟動 agent → 監控狀態。專案暫名 **ARCP**(Agent Runtime / Control Plane)。
**OpenHands 只是候選方法之一,不是前提。**

## 1. 現有交付物(都在本 repo)

- **研究報告 v3**:`research/2026-08-agent-runtime-control-plane-research-v3.md`
  (455 行;需求規格 + 從零寫開工級設計 + 三路線優缺點/維護成本 + 實測)
- **前版 v2**:`research/2026-07-agent-runtime-control-plane-research.md`
  (市場缺口 106-agent 對抗式驗證;v3 引用不重驗)
- **可跑 PoC**:`examples/jira-agent-poc/`(~770 行、零依賴、已實測跑通)
- **Crash-recovery 矩陣 harness**:`examples/jira-agent-poc/recovery_test.py`
  (2026-08-01 實測 4/4 PASS,詳 §4 與 §6-1)
- **OpenHands ACP PoC(路線 B)**:`examples/openhands-acp-poc/`
  (PLAN.md 計畫+checklist、smoke_acp.py、compare_run.py、COMPARISON.md;
  原 jira-agent-poc 留存不動)

## 2. 已敲定的決策(前一段對話,勿再問)

| # | 決策 |
|---|---|
| D1 | 報告放 `research/` 出 v3;舊的 openhands 目錄評估已併入 v3 §6 並刪除 |
| D2 | example 要「設計級片段 + 可跑 PoC」兩者 |
| D3 | 「從零寫」寫到**開工級**(需求 + 架構 + 介面定義) |
| D4 | 需求範圍**整合全部**(v2 七大能力 + Jira pipeline + trace&control) |
| D5 | 實作選項:**兩條主線深入**(raw / OpenHands ACP)+ 第三方簡述 |
| D6 | PoC 放 `agents-control-platform/examples/` |
| D7 | PoC 優先 **`claude -p` 和 `codex exec`**(使用者主力) |
| D8 | **raw 路徑設一級公民**,OpenHands ACP 留彈性作對照;三路線優缺點/維護成本要講清楚 |

## 3. 三條路線的實作狀態(重要:別誤以為三種都跑過)

| 路線 | research 有寫 | PoC 能跑 / 跑過 |
|---|---|---|
| **A. Raw**(自寫 supervisor 包 claude -p / codex exec) | ✅ | ✅ **真的跑過**:claude+codex live + replay + 7/7 self-test + claude/codex crash→resume 矩陣 |
| **B. OpenHands ACP** | ✅ | ✅ **已實跑**(2026-08-03):SDK in-process(claude 全綠)+ **agent-server 模式(B+)**+ Jira 全鏈路(M1-M3)+ 視覺化收割;codex 對照待 quota |
| **C. RawCLIAgent**(OpenHands 骨架 + raw CLI,非 fork) | ✅ | ✅ **已實跑**(2026-08-03,`harness/arcp_rawcli/`):C.0 gate→C.5 三方對照全綠;**集大成**(保真≈A、語意乾淨勝 B、控制窗口/可視化兼得) |

**關鍵澄清(2026-08-03 更新)**:C 已重定義為 **RawCLIAgent(C2)**——在 OpenHands SDK
內以自製 AgentBase 子類直接 spawn `claude -p`/`codex exec`、解析 stream-json、發
細粒度事件,**不 fork adapter、不走 ACP**。舊定義(從零寫完整 runtime)已棄。
三個執行 backend(openhands-acp / openhands-server / rawcli)共用同一 envelope 契約,
harness dispatcher/grader/三態零改動即可切換。
能實跑對照的其實是兩條:自寫 supervisor(已跑)vs OpenHands ACP(未跑)。
report §4 的三方優缺點表,**只有 A 欄是實測撐,B/C 是原始碼+文件分析推論**
(§4.1 已加證據級別標註)。使用者選擇維持分析對照,**未接 OpenHands ACP 實跑**。

## 4. 用實測釘死的事實(本機 2026-08-01,非文件推論)

- CLI 版本:claude **2.1.206** / codex-cli **0.142.5** / opencode(有 `opencode acp`)。
- **終止語意不對稱**:`claude -p` 有明確 `result` 事件;`codex exec --json` 沒有,
  靠 `turn.completed` + process exit → 統一層用「事件 OR exit code」雙判據。
- `claude -p --session-id <uuid>` 可**預先指定 session id**(crash recovery 重接的關鍵)。
- `codex exec` 非 tty 會讀 stdin → supervisor 必須 `stdin=DEVNULL`。
- 真實事件流存於 `examples/jira-agent-poc/fixtures/*.jsonl`(協定回歸測試用)。
- **claude crash→resume 已實測可靠(2026-08-01,2×2 矩陣 4/4 PASS、16/16 判準)**:
  「尚無產出」/「工具執行中」× SIGTERM/SIGKILL 殺掉後,`claude -p "繼續..." --resume <預指定 id>`
  皆重接成功——事件流帶**同一** session id、記得進度、**不重工**(crash 前檔案 mtime 不變)、
  任務補完;SIGKILL 斷在 thinking 中 session 檔也不壞。單 case $0.03-0.07(haiku)。
  crash+resume 真實流:`fixtures/claude_p_{crash,resume}_real.jsonl`(replay 回歸驗證過)。
- **codex crash→resume 亦實測可行(2026-08-02)**:thread id 從 `thread.started` **事後擷取**
  來得及 → `codex exec resume <id>` 重接成功(2×2 全時機過;midtool×SIGTERM 於
  2026-08-02 補測 2/2 乾淨 PASS)。fixtures:`codex_exec_{crash,resume}_real.jsonl`。
- **本輪新釘死的陷阱**:① codex 收 SIGTERM 優雅退場 **rc=0** →「事件 OR exit code」雙判據
  會把中斷 run 誤判 DONE,完成與否只能靠證據型 grader(§6-2 因此升級為必要);
  ② `codex exec resume` **不吃 `--sandbox`**(rc=2),要 `-c sandbox_mode="..."`(driver 已修);
  ③ kill 必須 **killpg**,否則 codex 的 zsh 子程序孤兒續跑偷偷完工;
  ④ codex 工具粒度/服從度變異大(單指令打包/逐步/無視 sleep 都出現過),事件流不可當進度真值;
  ⑤ codex 會載入使用者 plugin(superpowers)增加變異,對照實驗宜 `--ignore-user-config`(未實測);
  ⑥ **筆電系統睡眠會凍結 supervisor 計時器**產生假 stall/假 hang——live 實驗要防睡
  (caffeinate 只擋 idle sleep,蓋螢幕電池睡眠擋不住),長跑監督應跑在不睡的機器。
- 三路線的 API 呼叫方式:raw = spawn CLI 吃 stream-json;OpenHands = `POST /api/conversations`
  (`agent_kind:acp`, `acp_server:claude-code|codex` 或 `acp_command:["opencode","acp"]`)
  + 訂閱 WS `/sockets/events/{id}`。

## 5. PoC 怎麼跑

```bash
cd examples/jira-agent-poc
python3 replay_demo.py     # 離線、免 token:真實事件流過 normalize→狀態機→trace
python3 selftest.py        # 免 token:7 項(rule 引擎 + 事件正規化 + 狀態機)
python3 run_demo.py claude  # live、花 token:真實 claude -p 全流程
python3 run_demo.py codex "... bug ..."  # live:rule 命中→裝 skill→codex exec
python3 recovery_test.py   # live、~$0.2:claude crash→resume 2×2 矩陣 + 確定性判分
python3 recovery_test.py --agent codex   # 同矩陣走 codex(建議 caffeinate 防睡眠)
```
PoC 模組:`arcp_poc/{events,drivers,supervisor,rules,workspace,jira_watcher}.py`;
規則 `rules.json`;範例 skill `skills/jira-bugfix/SKILL.md`。詳見該目錄 `README.md`。

## 6. 下一步(report §9.3 的 PoC 實驗清單,尚未做)

1. **Crash recovery 實測**:✅ **claude 基線完成(2026-08-01,4/4)**、
   ✅ **codex 基線完成(2026-08-02,2×2 全時機,SIGTERM 補測 2/2)**——皆 `recovery_test.py`。
   ✅ **自動 recovery 迴路完成(2026-08-02)**——`arcp_poc/recovery_loop.py` +
   `loop_demo.py`:run→grade→梯度 resume(同 rung 不重試),live 驗證 claude 硬 crash
   與 codex rc=0 假完成皆自動修復(`initial:failed → native:done`)。
   ✅ **workspace 搬家情境完成(2026-08-02,`workspace_recovery_test.py` 4/4)**——
   釘死:claude session 綁啟動時 cwd,workspace `mv` 後原生 resume 死於
   `No conversation found`(#48835 的一般形式);**transcript 降級救回不重工**
   (ARCP journal 不綁 cwd)。git worktree 形式同機制未另測。
   **剩**:長跑/大 context resume。
2. **證據型停止**:✅ **已實作(2026-08-02)**——`arcp_poc/grader.py` + supervisor 整合,
   DONE 需過證據、不過覆寫 FAILED(證據高於自稱);selftest 14/14。
   (背景:實測證實 codex SIGTERM 退場 rc=0,exit code 不能證明任務完成,§4。)
3. **Claude permission 行為矩陣**:✅ **已實測(2026-08-02,`permission_matrix.py`)**——
   acceptEdits/bypass 放行雙探針(acceptEdits 連 Bash touch 都放,已隔離設定複驗);
   auto/manual/dontAsk 全拒;plan 只計畫。**headless 無任何 mode 掛住等核准**,
   拒絕即時 → waiting-permission 偵測要盯 denial 事件,不是偵測卡住。詳 v3 §9.3-3。
4. **OpenHands ACP 對照**:✅ **claude 側完成(2026-08-03)**——SDK in-process headless
   跑通、本機登入免 key、同任務同 grader 對照 **A 248 vs B 14 事件**;
   詳 `examples/openhands-acp-poc/{PLAN,COMPARISON}.md`。
   **B 路 resume 亦已實跑(2026-08-03)**:SIGKILL adapter → session/load 重接同
   session(兩輪 4/4);⚠️ adapter 批次執行無中途 kill 窗口(粗粒度影響控制面)。
   **剩**:codex 對照點(quota 8/31 重置後補)、agent-server 模式。
   (B 路成本落地已撤:使用者決策 2026-08-03——A/B 未來用同款 model,比較基準
   問題消失,不再需要。)
5. **opencode via ACP**:`acp_command:["opencode","acp"]` 實測相容性。
6. **waiting-permission → 開 Jira ticket 升級迴路**:✅ **已實作 + live 驗證(2026-08-02)**——
   `arcp_poc/escalation.py`(事件驅動,盯 denial 不偵測卡住)+ 真實 denial fixture 回歸
   + live demo(denial→開票→comment→結果回寫原 issue,含 permission_denials 結構化
   清單與 resume 指令)。Jira 端 DryRun outbox,REST 可替換。
7. **journal → transcript 降級 resume**:✅ **已實作 + live 驗證(2026-08-02)**——
   `arcp_poc/resume_transcript.py` + `recovery_test.py --resume-mode transcript`,
   claude midtool×SIGKILL 4/4 PASS(新 session 靠 transcript 續跑不重工)。
   三段梯度前兩階皆有實測;無 id 情境(codex 太早死)也由此路徑涵蓋。

## 6.5 使用者立場備忘(2026-08-02)

- ACP/OpenHands resume 語意已原始碼查證(v3 §6.4):ACP 有 `session/load` 但底層
  耐久性與 raw 同源;OpenHands 有「存對話注入新 session」的 bootstrap-prompt resume
  設計(未接線)。
- **未來不排除直接拿 OpenHands 修改作基底**(他們把坑都走過一遍)——**持續觀察,
  暫不決策**;Driver 介面保持可插拔以保留此選項。
- **改造可行性帳已算(2026-08-03,COMPARISON.md §5)**:模型控制/差異化層便宜可移植;
  細觀測/控制窗口卡 ACP 協定瓶頸(fork adapter = 更大維護面)——合理形態是**分工**
  (OpenHands 管例行任務 backend,A-raw 管需細觀測的任務,差異化層共用),非全面改造。
  重算訊號:ACP spec 出細粒度 notification 或官方 adapter 轉發細事件。
- **backlog 四項的目的**:agent-server 對照=併發/隔離/程式化核准的地基省多少;
  B 成本落地=同模型基準的真實 overhead(現 $0.45 vs $0.053 不同基準不可比);
  真實 Jira 接入=研究轉產品分水嶺(需使用者提供測試 instance);
  長跑 resume=生產級 crash-safe 宣稱前的最後硬證據(高 token 成本,需防睡環境)。

## 6.6 Jira Harness 設計文件整合(2026-08-03,重要)

使用者另行完成「Jira 驅動的 Agent Harness 選型研究與設計 v5」(Google Docs,
**含內部資訊,原文不入本 public repo**,連結在 session memory
`jira-harness-design-doc`)。**這份文件是正式實作的 requirement/design source**,
其 §8 是下次 design/code session 的起手 prompt。

- 兩份使用者研究**原文已入庫 research/**(同專案可直接看,含出處標頭):
  `2026-08-jira-agent-harness-design-v5.md`(v5 設計,**去識別化副本**,
  原文在 Google Docs 以其為準)與
  `2026-08-openhands-acp-claude-code-lifetime.md`(agent-server 行號級讀碼,
  原文照收)——多 workspace 併發原生支援、閒置 20 分 Evict→resume 常態化、
  cwd 變更即放棄 resume、bypassPermissions 一刀切、acp_isolate_data_dir、
  改造點行號座標。「agent-server 對照」backlog 因此縮為行為驗證 spike。
  詳整合分析 §3.5。
- 整合分析:`research/2026-08-jira-harness-integration.md` ——
  v5 決策 × ARCP 實測證據對照(「不走 ACP」被我們的 18:1 粒度/無控制窗口/
  cwd-key 實驗證實)、該採用的設計(UNKNOWN 三態、--fork-session 第四階、
  workspace health check、issue_id 主鍵、欄位所有權、L0-L3 trace、KPI 框架)、
  v5 開放問題中我們已回答的三項、P0-P4 與 ARCP 現況的缺口對映。
- **待使用者決策**:正式實作 repo(本 repo public vs 新 private)、PoC 元件
  直接演進 vs 另起爐灶、`--bare`+公司 API 合約情境切換時點。

## 6.7 路線策略定案(使用者決策 2026-08-03)

**short term 用 B(OpenHands+ACP+headless CLI+Jira)先做出可執行的;
long term 用 C(RawCLIAgent in OpenHands SDK,event 補到 A 級細粒度);
A(jira-agent-poc)留作對照 harness 不下場。**
完整 GAP/存活率/可行性分析:`research/2026-08-abc-roadmap-analysis.md`。
B 期三守則:消費端只依賴 OpenHands event stream+L2 封套、agent 設定獨立
YAML 區塊、A 對照當品質閘。
✅ **C 前置 spike 已完成(2026-08-03,4/4 PASS)**:`Conversation` 接受外部
AgentBase 子類——**C 不用 fork**;真 `claude -p` 已在 OpenHands Conversation
內跑通(`spike_rawcli_agent.py` ~80 行雛形)。C 的未知數清零,
剩下的是純工程(drivers.py 解析知識搬入)。

## 6.8 B 期 harness 進度(2026-08-03)

`harness/` 開工:**M1 + M2 里程碑皆達成(2026-08-03)**。
- Phase 0-1(M1 灰度):Jira Cloud source adapter + routing/watch/watermark,
  真實 Jira E2E 4/4 PASS(notify_only 只記不動、watermark 冪等)。
- Phase 2(M2 端到端):**第一張真票全鏈路 PASS**(SCRUM-2)——routing →
  workspace+TICKET.md → inner runner(openhands venv ACPAgent,haiku $0.045)
  → A 路 grader 驗證 → 帶證據回寫 comment → 冪等不重派。三態 outcome
  (UNKNOWN=行程消失→pending:unknown 只有人解)、ticket_session 對映表、
  agent 設定獨立區塊(B→C 只換 inner runner + 該區塊)。
計畫:`harness/PLAN_B.md`;教訓:`harness/LESSONS.md`。
環境:Atlassian Cloud swchen44,project **key=SCRUM**(名稱 AgentLifetimeBoardv1)、
憑證 `~/.env`(不入 repo)、issue type 中文(任務)。
- Phase 2 殘項:fault-injection E2E 6/6(retry+evidence+truly_resumed 實證、
  UNKNOWN→pending:unknown 只有人解)。
- Phase 3(M3 閉環):`@agent run/retry/stop/cancel` 指令通道 E2E 5/5
  (retry 同輪重派 = 人工解除 pending 機制)、external_change_policy、
  pending 三分類、harness_selftest 17 項離線全過。
**B 期 harness M1+M2+M3 全數達成(2026-08-03)。下一步(Phase 4,未排程)**:
Agent Status/Link 欄位、detail page、Resolve transition、D10 併發閘門、
常駐 poller。

## 6.9 C 期完成(RawCLIAgent,2026-08-03)

`harness/arcp_rawcli/` + `PLAN_C.md`。C=C2(RawCLIAgent,非 fork adapter):
- **C.0 gate PASS**:server 端可實例化自製 agent(啟動時 import 觸發註冊)→
  C 上 agent-server = 集大成(A 級細粒度 + B+ 可視化/持久化)。
- **C.1-C.2**:RawCLIAgent spawn `claude -p` stream-json,兩層事件策略
  (原生全保真 + 蒸餾有意義細粒度);claude+codex 雙引擎。
- **C.3 M5**:接進 harness(backend=rawcli),真票 SCRUM-11 SUCCESS,
  dispatcher/grader/三態零改動(三 backend 共用 envelope 契約)。
- **C.4**:crash→resume(--resume,對照 A 矩陣,4/4);順帶修 completed 判定
  (`_got_terminal` 而非進程結束,A 路 SIGTERM-rc=0 教訓的 RawCLIAgent 版)。
- **C.5 M6**:A/B/C 三方對照(COMPARISON §6):A 蒸餾93/保真93、B 蒸餾17/保真0、
  **C 蒸餾10/保真94** —— C 兩者兼得,保真≈A、語意乾淨勝 B、控制窗口 B 缺、
  可視化 A 缺。detail page(SCRUM-11)展示 C 的 💭/🔧/📋 細粒度 conversation。

**A/B/C 三線全部實跑落地**。

**執行隔離(無 docker,2026-08-03)**:RawCLIAgent `os_sandbox`——claude 用
macOS seatbelt(`sandbox-exec`)限制檔案寫入只到 workspace;codex 用內建
`--sandbox`。**端到端真票驗證(SCRUM-13)**:outcome=SUCCESS(正常任務不誤傷)+
`/tmp` 越界寫入被擋(隔離在完整鏈路生效)。`filechain-rawcli` profile 預設
`os_sandbox: true`。踩過 symlink 逃逸坑(白名單勿放 /private/tmp,lesson #15)。
ACP 隔著 adapter 隔離粒度較粗 —— 直接掌 CLI flag(A/C)比 ACP(B)易精確隔離。

**多票併發 demo(2026-08-03)**:`demo_concurrent.py`——1 個 agent-server
進程管 4 個 conversation 併發,wall-clock 37s ≈ 最慢單張(非 4× 串行),
grader 4/4 互不污染。這是 OpenHands agent-server 相對 in-process/rawcli 的
核心價值(統一生命週期 + 閒置 evict→rehydrate),詳 COMPARISON §7。

**harness 併發 + 健壯性(conc.1-3,2026-08-03,`PLAN_concurrent.md`)**:
- conc.1 M7 並行 dispatch(ThreadPoolExecutor+max_running,Store 加鎖執行緒安全):
  3 張並行 27.5s vs 串行 75s。
- conc.2 M8 **stall/hang exit+resume**(RawCLIAgent reset-on-progress watchdog,
  移植 A 路;無進展→killpg→resume 續;單元測 `test_stall.py` 免 token)。
- conc.3 M9 **長駐共享 server + 掛了重起續**(`ServerManager` 健康檢查+重起同
  persistence→rehydrate;infra→pending:external 不消耗 attempt;server 恢復自動
  解除→resume);故障注入 E2E 3/3(kill server→重起新 PID→續 SUCCESS 不漏)。
- non-normal 分析 N1-N13(使用者提)全數處理,詳 PLAN_concurrent。

剩 backlog(未排程):codex 對照點(quota 8/31)、Docker workspace 隔離、
harness dispatcher 接長駐共享 server(現每 attempt 自起)+ 並行 dispatch
(v5 D10 max_running)、detail page 拼 Jira 深連結、B+ resume 對照、
`--bare` 公司 API 情境。

## 7. 建議路線(report 定案)

**主線 A(raw 一級)+ 可選後端 B(OpenHands 對照)+ C 只做差異化層。**
先用 A 把 Jira pipeline 跑起來(數天可上線內部用),Driver 介面留可插拔;
需要 crash-safe/程式化核准/Docker 隔離時再加 OpenHands ACP driver 對照;
若要做開源專案,只做 v2 驗證過「沒人做」的 **git checkpoint 語意層** 與
**跨 CLI 卡住偵測/recovery**,疊在 A/B 之上,不重造 runtime。

## 8. 與使用者協作的規則(務必遵守)

存在 memory `questioning-protocol`,重點:
1. 決策樹建模,系統性走訪每個分支/edge case。
2. **一次只問一個問題**,每問一題就暫停等回應。
3. 事實靠查閱(能從程式碼/環境讀到的絕不問),**只有決策才發問**。
4. 每個問題**都給選項**、附 AI 建議答案與理由(讓使用者審提案)。
5. **確認所有分支後才動工**,不提前寫碼/改檔。

## 9. 關鍵參考材料

- ChatGPT「Claude headless 解決方案」RFC 討論(headless 七大痛點、四層架構、
  Jira/Jenkins connector、RFC-0001~0010、Responsibility Matrix)。
- 三層排障地圖:`~/git/knowledge_from_ai_summary/personal-kb-repo/AI/
  2026-07-19-AGENT-HARNESS-VS-LOOP-VS-GRAPH-ENGINEERING-THREE-LAYERS.md`
  (Harness 管環境 / Loop 管回饋 / Graph 管流程;"Do not loop on confidence, loop on evidence")。
- OpenHands 本機 clone:`~/git/openhands/{OpenHands,software-agent-sdk}`(路線 B 依據)。
